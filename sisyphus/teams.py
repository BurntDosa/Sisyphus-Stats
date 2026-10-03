"""Shared private /teams setup; no League-client or persistent-state writes."""
import asyncio
import copy
import time
import uuid
import json
import os
from pathlib import Path

import discord
from discord import ui

from .balance import TIERS, ROLES, ROLE_LABELS, balance
from .team_roster import Roster, refresh_ranks

SESSIONS = {}

def clean(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))

async def live_rank_fetch(info):
    import aiohttp
    from .opgg import get_ranked_stats
    async with aiohttp.ClientSession() as session:
        return await get_ranked_stats(session, info.get('game_name'), info.get('tag_line'))

class OwnedModal(ui.Modal):
    def __init__(self, parent, title):
        super().__init__(title=title, timeout=min(300, max(1, parent.deadline-time.monotonic())), custom_id=parent.cid('modal:'+uuid.uuid4().hex[:8]))
        self.parent = parent
        self.revision = parent.revision

    async def interaction_check(self, interaction):
        if not await self.parent.interaction_check(interaction): return False
        if self.revision != self.parent.revision:
            await interaction.response.send_message('The setup changed while this form was open. Open a fresh form.', ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        print('[teams] modal error:', type(error).__name__)
        if not interaction.response.is_done():
            await interaction.response.send_message('That form could not be processed. Reopen it and try again.', ephemeral=True)

class RosterModal(OwnedModal):
    def __init__(self, parent):
        super().__init__(parent, 'Choose players')
        self.page_entries = parent.candidates[parent.page*10:parent.page*10+10]
        self.mode = ui.RadioGroup(options=[discord.RadioGroupOption(label='5v5 · 10 players', value='10', default=parent.size==10), discord.RadioGroupOption(label='4v4 · 8 players', value='8', default=parent.size==8)])
        self.add_item(ui.Label(text='Game format', component=self.mode))
        if self.page_entries:
            ids = {e.identity for e in parent.entries}
            self.checks = ui.CheckboxGroup(required=False, min_values=0, max_values=len(self.page_entries), options=[discord.CheckboxGroupOption(label=f'{parent.page*10+i+1}. {e.name}'[:100],value=str(i),default=e.identity in ids) for i,e in enumerate(self.page_entries)])
            self.add_item(ui.Label(text=f'Players · page {parent.page+1}', component=self.checks))
        else:
            self.checks = None
        self.users = ui.UserSelect(required=False, min_values=0, max_values=10)
        self.add_item(ui.Label(text='Add server members', description='Linked accounts use Solo/Duo tiers; others need estimates.', component=self.users))
        self.extra = ui.TextInput(required=False, max_length=1000, placeholder='Alex=Gold, Silver, @mention, GameName#TAG, 2=Platinum')
        self.add_item(ui.Label(text='Guests, tracked Riot IDs, or tier overrides', component=self.extra))
        self.omit = ui.Select(options=[discord.SelectOption(label=ROLE_LABELS[r],value=r,default=r==parent.omitted) for r in ROLES])
        self.add_item(ui.Label(text='Omitted lane · used only for 4v4', component=self.omit))

    async def on_submit(self, interaction):
        p = self.parent
        try:
            page_ids = {e.identity for e in self.page_entries}
            entries = [copy.deepcopy(e) for e in p.entries if e.identity not in page_ids]
            old = {e.identity:e for e in p.entries}
            for i in self.checks.values if self.checks else ():
                candidate = self.page_entries[int(i)]
                entries.append(copy.deepcopy(old.get(candidate.identity, candidate)))
            for user in self.users.values:
                member = p.guild.get_member(user.id)
                if member is None or member.bot:
                    raise ValueError('Choose human members of this server.')
                entry = p.roster.resolve(user_id=member.id,name=member.display_name)
                existing = next((e for e in entries if e.identity==entry.identity), None)
                if existing and existing.discord_id != member.id:
                    raise ValueError('Two selected Discord accounts link to the same Riot player.')
                if not existing: entries.append(entry)
            entries = p.roster.extras(self.extra.value, entries, p.candidates, p.guild.get_member)
            if len({e.identity for e in entries}) != len(entries):
                raise ValueError('Two selected members link to the same Riot player.')
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True); return
        p.entries, p.size, p.omitted = entries, int(self.mode.value), self.omit.values[0]
        p.invalidate()
        p.busy = True
        await interaction.response.defer()
        try:
            await refresh_ranks(p.entries,p.roster.state,p.fetch)
            p.stage = 'review'
        finally:
            p.busy = False
        if not p.closed: await p.render()

class PlayerModal(OwnedModal):
    def __init__(self, parent, kind):
        super().__init__(parent, 'Edit player '+kind)
        self.kind = kind
        self.identity = parent.selected
        e = next(e for e in parent.entries if e.identity==self.identity)
        if kind == 'tier':
            self.tier = ui.Select(options=[discord.SelectOption(label=t.title(),value=t,default=t==e.tier) for t in TIERS])
            self.add_item(ui.Label(text=f'{e.name} · estimated tier'[:45],component=self.tier))
        else:
            self.primary = ui.Select(options=[discord.SelectOption(label='Flexible',value='flexible',default=not e.roles)]+[discord.SelectOption(label=ROLE_LABELS[r],value=r,default=bool(e.roles) and e.roles[0]==r) for r in ROLES])
            self.secondary = ui.Select(options=[discord.SelectOption(label='None',value='none',default=len(e.roles)<2)]+[discord.SelectOption(label=ROLE_LABELS[r],value=r,default=len(e.roles)>1 and e.roles[1]==r) for r in ROLES])
            self.add_item(ui.Label(text='Primary lane',component=self.primary))
            self.add_item(ui.Label(text='Secondary lane',component=self.secondary))

    async def on_submit(self, interaction):
        p = self.parent
        e = next(e for e in p.entries if e.identity==self.identity)
        if self.kind == 'tier':
            e.tier, e.source, e.confirmed = self.tier.values[0], 'Manual estimate', True
        else:
            primary, secondary = self.primary.values[0], self.secondary.values[0]
            if primary != 'flexible' and primary == secondary:
                await interaction.response.send_message('Primary and secondary must be different.',ephemeral=True); return
            e.roles = () if primary=='flexible' else (primary,) if secondary=='none' else (primary,secondary)
        p.invalidate(); p.stage='review'
        await interaction.response.defer()
        await p.render()

class TeamsView(ui.View):
    def __init__(self, owner_id, guild, destination, state, *, fetch=live_rank_fetch, preview=False, duration=None, fixture_members=None):
        super().__init__(timeout=None)
        self.owner_id, self.guild, self.destination = owner_id,guild,destination
        self.roster, self.fetch, self.preview = Roster(state),fetch,preview
        self.ns = ('teams-preview:' if preview else 'teams:')+uuid.uuid4().hex[:12]
        self.deadline = time.monotonic()+(duration or (1800 if preview else 600))
        self.entries, self.candidates, self.splits = [],[],[]
        self.size,self.omitted,self.page,self.index = 10,'supp',0,0
        self.stage,self.selected,self.revision = 'start',None,0
        self.flipped,self.closed,self.busy = False,False,False
        self.message = self.timer = None
        self.fixture_members = fixture_members
        self.delivery_pending = False
        self.team_messages = [None, None]
        self.result_sender = None
        self.receipt_dir = Path(os.environ.get('TEAMS_DELIVERY_DIR', '.automation/teams-deliveries'))
        self.delivery = {'session': self.ns, 'owner': owner_id, 'guild': guild.id, 'channel': getattr(destination, 'id', None), 'complete': False, 'teams': [
            {'nonce': str(uuid.uuid4().int % (2**63)), 'status': 'new', 'message_id': None} for _ in range(2)]}
        self.rebuild()

    def cid(self, action): return self.ns+':'+action

    def invalidate(self):
        self.revision += 1
        self.splits=[]; self.index=0; self.flipped=False

    def vc_members(self):
        if self.fixture_members is not None: return self.fixture_members
        owner = self.guild.get_member(self.owner_id)
        vc = getattr(getattr(owner,'voice',None),'channel',None)
        return None if vc is None else [m for m in vc.members if not m.bot]

    async def interaction_check(self, interaction):
        reason = None
        if interaction.user.id != self.owner_id: reason='Only the organiser can change this setup.'
        elif self.closed or time.monotonic() >= self.deadline: reason='This setup has ended. Run /teams to start again.'
        elif self.busy: reason='An update is in progress. Please wait a moment.'
        elif self.delivery_pending and (getattr(interaction,'data',None) or {}).get('custom_id')!=self.cid('post'):
            reason='Verify the previous Post teams delivery before changing this setup.'
        if reason:
            await interaction.response.send_message(reason,ephemeral=True); return False
        return True

    def add_button(self,label,action,row=0,disabled=False,primary=False):
        button=ui.Button(label=label,custom_id=self.cid(action),row=row,disabled=disabled or self.closed,style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary)
        async def callback(interaction): await self.action(interaction,action)
        button.callback=callback; self.add_item(button)

    def rebuild(self):
        self.clear_items()
        if self.stage == 'start':
            self.add_button('Use my VC','vc',disabled=self.vc_members() is None,primary=True)
            self.add_button('Add players manually','manual')
        elif self.stage == 'review':
            self.add_button('Edit roster','roster')
            self.add_button('Edit player','players',disabled=not self.entries)
            self.add_button('Review needed' if self.rank_exceptions() else 'Generate teams',
                            'exceptions' if self.rank_exceptions() else 'generate',primary=True)
        elif self.stage == 'results':
            if len(self.splits)>1: self.add_button('Reroll','reroll')
            self.add_button('Edit','edit')
            self.add_button('Post teams','post',primary=True)
        elif self.stage == 'roster':
            self.add_button('Choose players','select',primary=True)
            if len(self.candidates)>10:
                self.add_button('Previous page','prev',disabled=self.page==0)
                self.add_button('Next page','next',disabled=(self.page+1)*10>=len(self.candidates))
            self.add_button('Back','back',row=1)
        elif self.stage in ('players','exceptions','player'):
            available=self.rank_exceptions() if self.stage=='exceptions' else self.entries
            if available:
                if self.selected not in {e.identity for e in available}: self.selected=available[0].identity
                select=ui.Select(placeholder='Choose a player',custom_id=self.cid('player'),options=[
                    discord.SelectOption(label=e.name[:100],value=e.identity,default=e.identity==self.selected)
                    for e in available])
                async def choose(interaction):
                    self.selected=select.values[0]
                    self.stage='player'
                    await interaction.response.edit_message(embed=self.embed(),view=self.rebuild())
                select.callback=choose; self.add_item(select)
                if self.stage=='player':
                    entry=next(e for e in self.entries if e.identity==self.selected)
                    self.add_button('Edit tier','tier',row=1)
                    self.add_button('Edit lanes','lanes',row=1)
                    if entry.tier and not entry.confirmed: self.add_button('Use saved tier','confirm',row=1)
            self.add_button('Back','back',row=2)
        elif self.stage=='edit':
            options=[('Edit roster','roster'),('Edit player','players'),('Refresh roster','refresh'),('Refresh ranks','ranks')]
            if self.splits: options.append(('Swap sides','swap'))
            if self.notices() or (self.splits and self.splits[self.index].score[1]): options.append(('Review notices','notices'))
            select=ui.Select(placeholder='What would you like to change?',custom_id=self.cid('edit-options'),
                options=[discord.SelectOption(label=label,value=action) for label,action in options])
            async def edit_choice(interaction): await self.action(interaction,select.values[0])
            select.callback=edit_choice;self.add_item(select)
            self.add_button('Back','back',row=1)
        elif self.stage=='notices': self.add_button('Back','back')
        for child in self.children:
            if self.closed or (self.delivery_pending and child.custom_id!=self.cid('post')): child.disabled=True
        return self

    def rank_exceptions(self):
        return [e for e in self.entries if not e.tier or not e.confirmed]

    def notices(self):
        notices=[]
        current=self.vc_members()
        if current is not None:
            ids={m.id for m in current}
            departed=[e.name for e in self.entries if e.discord_id and e.discord_id not in ids]
            if departed: notices.append('Outside your current VC: '+', '.join(clean(n) for n in departed))
        if self.size==8:
            for e in self.entries:
                if e.roles and e.roles[0]==self.omitted:
                    fallback=next((ROLE_LABELS[r] for r in e.roles[1:] if r!=self.omitted),'Flexible')
                    notices.append(f'{clean(e.name)}: omitted primary → {fallback}')
        return notices

    def team_embeds(self):
        split=self.splits[self.index]
        teams=(split.team_b,split.team_a) if self.flipped else (split.team_a,split.team_b)
        return [discord.Embed(title=f'Team {i+1}',color=color,
                    description='\n'.join(f'**{clean(slot.player.label)}** — {ROLE_LABELS[slot.role]}' for slot in slots))
                for i,(slots,color) in enumerate(zip(teams,(0x5865F2,0xED4245)))]

    def embed(self):
        title={'start':'Set up your teams','review':'Playing roster','roster':'Choose players',
               'players':'Edit a player','player':'Player details','exceptions':'Review needed',
               'edit':'Edit setup','notices':'Review notices','results':'Teams ready'}[self.stage]
        e=discord.Embed(title=title,color=0x5865F2)
        if self.stage=='start':
            e.description='Choose your VC or add players manually.'
            if self.vc_members() is None: e.description+=' Join a VC to use its roster.'
        elif self.stage=='results':
            e.description='Review the two teams, then post them.'
            warnings=self.notices()
            split=self.splits[self.index]
            if warnings or split.score[1]: e.description+=' Some lane assignments need review in Edit.'
            if self.delivery_pending: e.description='Delivery is being verified. This setup is frozen.'
        elif self.stage=='player':
            x=next(x for x in self.entries if x.identity==self.selected)
            e.description=f'**{clean(x.name)}**\nTier: {x.tier.title() if x.tier else "Estimate needed"}\n{x.source}\nLanes: '+(' / '.join(ROLE_LABELS[r] for r in x.roles) or 'Flexible')
        elif self.stage=='exceptions':
            e.description='Choose a player to confirm a saved tier or enter an estimate.\n\n'+'\n'.join(
                f'**{clean(x.name)}** — {"Confirm saved tier" if x.tier else "Estimate needed"}' for x in self.rank_exceptions())
        elif self.stage=='notices':
            notes=self.notices()
            if self.splits:
                notes += [f'{clean(s.player.label)}: assigned {ROLE_LABELS[s.role]} outside preferred lanes'
                          for s in (*self.splits[self.index].team_a,*self.splits[self.index].team_b) if s.fit=='Off-role']
            e.description='\n'.join(notes) or 'No review notices.'
        elif self.stage=='edit': e.description='Choose one change. You can return to your teams afterwards.'
        else:
            e.description=f'**{len(self.entries)}/{self.size} selected**'
            if self.stage=='review' and self.rank_exceptions():
                count=len(self.rank_exceptions());e.description+=f' · {count} player'+('s need' if count!=1 else ' needs')+' a tier check'
            if self.stage=='roster': e.description+=f' · Page {self.page+1}/{max(1,(len(self.candidates)+9)//10)}'
            lines=[f'**{clean(x.name)}** — '+(' / '.join(ROLE_LABELS[r] for r in x.roles) or 'Flexible') for i,x in enumerate(self.entries)]
            for offset in range(0,max(1,len(lines)),5):
                e.add_field(name='Players' if offset==0 else 'Players · continued',value='\n'.join(lines[offset:offset+5]) or 'No players selected yet.',inline=False)
        return e

    async def render(self):
        if self.stage=='results' and self.splits:
            embeds=self.team_embeds()
            for i,embed in enumerate(embeds):
                if self.team_messages[i]:
                    await self.team_messages[i].edit(embed=embed,allowed_mentions=discord.AllowedMentions.none())
                elif self.result_sender:
                    self.team_messages[i]=await self.result_sender(embed=embed,allowed_mentions=discord.AllowedMentions.none())
        if self.message:
            await self.message.edit(embed=self.embed(),view=self.rebuild(),allowed_mentions=discord.AllowedMentions.none())

    async def expire(self):
        await asyncio.sleep(max(0,self.deadline-time.monotonic()))
        await self.finish()

    async def finish(self):
        self.closed=True
        self.rebuild()
        if self.message:
            try: await self.message.edit(view=self)
            except discord.HTTPException: pass
        if SESSIONS.get((self.guild.id,self.owner_id)) is self: SESSIONS.pop((self.guild.id,self.owner_id),None)
        self.stop()
        if self.timer and self.timer is not asyncio.current_task(): self.timer.cancel()

    async def action(self, interaction, action):
        if action in ('vc','manual'):
            members=self.vc_members() if action=='vc' else []
            if members is None:
                await interaction.response.send_message('Join a VC first, or use manual entry.',ephemeral=True); return
            self.candidates=[self.roster.resolve(user_id=m.id,name=m.display_name) for m in sorted(members,key=lambda m:m.id)]
            self.stage='roster'; self.page=0; self.invalidate()
            await interaction.response.edit_message(embed=self.embed(),view=self.rebuild())
            return
        if action in ('roster','players','exceptions','edit','notices'):
            self.stage=action
            if action=='exceptions' and self.rank_exceptions(): self.selected=self.rank_exceptions()[0].identity
            await interaction.response.edit_message(embed=self.embed(),view=self.rebuild()); return
        if action=='back':
            self.stage='results' if self.splits else 'review'
            await interaction.response.defer(); await self.render(); return
        if action=='select': await interaction.response.send_modal(RosterModal(self)); return
        if action in ('tier','lanes'): await interaction.response.send_modal(PlayerModal(self,action)); return
        if action in ('prev','next'):
            self.page=max(0,min(max(0,(len(self.candidates)-1)//10),self.page+(-1 if action=='prev' else 1)))
        elif action=='refresh':
            members=self.vc_members()
            if members is None:
                await interaction.response.send_message('Join a VC to refresh its roster.',ephemeral=True); return
            by_id={e.discord_id:e for e in self.candidates}
            # Preserve old numbering and selected departures; append newcomers.
            for m in sorted(members,key=lambda m:m.id):
                if m.id not in by_id: self.candidates.append(self.roster.resolve(user_id=m.id,name=m.display_name))
            self.invalidate();self.stage='review'
        elif action=='ranks':
            self.invalidate(); self.busy=True
            await interaction.response.defer()
            try: await refresh_ranks(self.entries,self.roster.state,self.fetch)
            finally: self.busy=False
            self.stage='review'
            if not self.closed: await self.render()
            return
        elif action=='confirm':
            e=next(e for e in self.entries if e.identity==self.selected)
            if e.tier and not e.confirmed: e.confirmed=True; e.source='Saved tier · confirmed'
            self.invalidate();self.stage='review'
        elif action=='generate':
            if len(self.entries)!=self.size:
                await interaction.response.send_message(f'Select exactly {self.size} players first.',ephemeral=True); return
            missing=[e.name for e in self.entries if not e.tier or not e.confirmed]
            if missing:
                await interaction.response.send_message('Confirm or estimate tiers for: '+', '.join(clean(n) for n in missing),ephemeral=True); return
            self.splits=balance([e.player() for e in self.entries],omitted=self.omitted)
            self.index=0; self.flipped=False;self.stage='results'
        elif action=='reroll':
            if len(self.splits)<2:
                await interaction.response.send_message('There is only one equally best split.',ephemeral=True); return
            self.index=(self.index+1)%len(self.splits)
        elif action=='swap': self.flipped=not self.flipped;self.stage='results'
        elif action=='post':
            if self.preview:
                await interaction.response.send_message('Preview only — nothing was posted to the server.',ephemeral=True); return
            if not self.splits:
                await interaction.response.send_message('Generate teams first.',ephemeral=True); return
            self.busy=True
            await interaction.response.defer(ephemeral=True)
            try:
                messages=await self.publish()
            except Exception as exc:
                print('[teams] publication failed:',type(exc).__name__)
                await interaction.followup.send('Posting could not be confirmed. Retry Post teams to check saved references. If delivery remains uncertain, ask the maintainer to verify it; this setup stays frozen.',ephemeral=True)
                if not self.closed: await self.render()
            else:
                await interaction.followup.send('Teams posted: '+' · '.join(m.jump_url for m in messages),ephemeral=True)
                await self.finish()
            finally: self.busy=False
            return
        await interaction.response.defer()
        await self.render()

    def save_delivery(self):
        # Session-owned files prevent concurrent organisers overwriting receipts.
        self.receipt_dir.mkdir(parents=True,exist_ok=True)
        path=self.receipt_dir/(self.ns.replace(':','-')+'.json')
        temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(self.delivery,indent=2))
        temporary.replace(path)

    async def recover_slot(self, slot):
        if slot['message_id'] is not None:
            # A missing/deleted accepted message is not permission to send again.
            return await self.destination.fetch_message(slot['message_id'])
        # Discord may return the nonce in history. Only that unique identity,
        # never content similarity, can establish an accepted uncertain send.
        from datetime import datetime,timezone
        after=datetime.fromtimestamp(slot['attempted_at']-5,timezone.utc)
        async for msg in self.destination.history(limit=None,after=after):
            if str(getattr(msg,'nonce',None))==slot['nonce'] and msg.author.id==self.message.author.id:
                slot.update(status='accepted',message_id=msg.id)
                self.save_delivery()
                return msg
        raise RuntimeError('Delivery uncertain; no verified message reference. Do not resend.')

    async def publish(self):
        self.delivery_pending=True
        embeds=self.team_embeds()
        messages=[]
        for slot,embed in zip(self.delivery['teams'],embeds):
            if slot['status']!='new':
                messages.append(await self.recover_slot(slot)); continue
            slot.update(status='attempting',attempted_at=time.time(),embed=embed.to_dict())
            self.save_delivery()  # Persist intent before any network side effect.
            try:
                message=await self.destination.send(embed=embed,nonce=slot['nonce'],allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                slot['status']='uncertain';self.save_delivery();raise
            slot.update(status='accepted',message_id=message.id)
            self.save_delivery()
            messages.append(message)
        self.delivery['complete']=True
        self.save_delivery()
        return messages

def unresolved_deliveries(owner_id,guild_id):
    directory=Path(os.environ.get('TEAMS_DELIVERY_DIR','.automation/teams-deliveries'))
    if not directory.exists(): return []
    pending=[]
    for path in directory.glob('*.json'):
        receipt=json.loads(path.read_text())
        if receipt.get('owner')==owner_id and receipt.get('guild')==guild_id and not receipt.get('complete'):
            pending.append(receipt)
    return pending

async def start_teams(ctx, state):
    if ctx.guild is None:
        await ctx.send('Run /teams in your server so you can select its members.'); return
    if ctx.interaction: await ctx.defer(ephemeral=True)
    try: pending=unresolved_deliveries(ctx.author.id,ctx.guild.id)
    except (OSError,ValueError):
        await ctx.send('Delivery records could not be checked. Ask the maintainer to verify them before starting another setup.',**({'ephemeral':True} if ctx.interaction else {})); return
    if pending:
        await ctx.send('A previous team post needs delivery verification. Ask the maintainer to inspect its saved message references before starting another setup.',**({'ephemeral':True} if ctx.interaction else {})); return
    key=(ctx.guild.id,ctx.author.id)
    old=SESSIONS.get(key)
    if old: await old.finish()
    view=TeamsView(ctx.author.id,ctx.guild,ctx.channel,state)
    try:
        if ctx.interaction:
            view.message=await ctx.send(embed=view.embed(),view=view,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        else:
            view.message=await ctx.author.send(embed=view.embed(),view=view,allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        await ctx.send('I could not DM the setup. Use /teams here for a private setup.'); return
    if ctx.interaction:
        async def result_sender(**kwargs): return await ctx.interaction.followup.send(**kwargs,ephemeral=True,wait=True)
        view.result_sender=result_sender
    else: view.result_sender=ctx.author.send
    SESSIONS[key]=view
    view.timer=asyncio.create_task(view.expire())
