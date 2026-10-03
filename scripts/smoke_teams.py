"""Focused /teams checks. Synthetic state only; no providers or Discord sends."""
import asyncio
import copy
import itertools
import json
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sisyphus.balance import Player, TIERS, ROLES, balance, parse_tier
from sisyphus.team_roster import Roster, history_lanes, refresh_ranks
from sisyphus.teams import TeamsView, RosterModal, PlayerModal, unresolved_deliveries

def raises(call):
    try: call()
    except ValueError: return
    raise AssertionError('Expected rejection')

def embed_ok(e):
    assert len(e)<=6000
    assert len(e.fields)<=25
    assert all(len(f.value)<=1024 and len(f.name)<=256 for f in e.fields)

class Response:
    def __init__(self): self.calls=[]
    async def send_message(self,*args,**kwargs): self.calls.append((args,kwargs))
    async def edit_message(self,**kwargs): self.calls.append(kwargs)
    async def defer(self,**kwargs): self.calls.append(kwargs)

def interaction(user=1):
    return SimpleNamespace(user=SimpleNamespace(id=user),response=Response(),followup=Response())

class Message:
    author=SimpleNamespace(id=999)
    jump_url='https://example.invalid/message'
    next_id=100
    def __init__(self):
        self.edits=[];self.id=Message.next_id;Message.next_id+=1
        self.nonce=None
    async def edit(self,**kwargs): self.edits.append(kwargs)

class Destination:
    id=500
    def __init__(self): self.messages=[];self.calls=0;self.fail_at=None;self.scan_fail=False;self.return_nonce=True
    async def send(self,**kwargs):
        self.calls+=1
        assert 'view' not in kwargs and 'content' not in kwargs
        assert kwargs['allowed_mentions'].everyone is False
        msg=Message();msg.nonce=kwargs.get('nonce') if self.return_nonce else None
        msg.embed=kwargs['embed'];self.messages.append(msg)
        if self.calls==self.fail_at: raise TimeoutError('uncertain send')
        return msg
    async def fetch_message(self,message_id):
        return next(msg for msg in self.messages if msg.id==message_id)
    async def history(self,**kwargs):
        if self.scan_fail: raise RuntimeError('no read history')
        for msg in self.messages: yield msg

async def main():
    assert parse_tier('Iron')=='IRON' and parse_tier('g2')=='GOLD'
    assert parse_tier('Grandmaster')=='GRANDMASTER'
    raises(lambda:parse_tier('unknown')); raises(lambda:parse_tier('Silver 5'))
    for n in (8,10):
        players=[Player(str(i),'Same',TIERS[i%10],(ROLES[i%5],ROLES[(i+1)%5])) for i in range(n)]
        splits=balance(players,omitted='supp')
        minimum=min(abs(sum(players[i].rating for i in a)-sum(players[i].rating for i in range(n) if i not in a)) for a in itertools.combinations(range(n),n//2))
        assert all(s.gap==minimum and s.score==splits[0].score for s in splits)
        rosters=set()
        for s in splits:
            assert {x.player.identity for x in (*s.team_a,*s.team_b)}=={p.identity for p in players}
            assert len({x.role for x in s.team_a})==n//2
            assert len({x.role for x in s.team_b})==n//2
            if n==8: assert all(x.role!='supp' for x in (*s.team_a,*s.team_b))
            partition=tuple(sorted(x.player.identity for x in s.team_a));assert partition not in rosters;rosters.add(partition)
    raises(lambda:balance([Player('x','A','IRON')]*10))
    raises(lambda:balance([Player(str(i),'A','IRON') for i in range(6)]))
    iron=balance([Player(str(i),'Same','IRON') for i in range(10)])
    assert iron and iron[0].gap==0
    rows=[{'match_id':str(i),'position':lane,'result':'WIN','date':f'2026-09-{i+1:02}'} for i,lane in enumerate(('TOP','JUNGLE','TOP','JUNGLE'))]
    rows+= [rows[-1],{'match_id':'remake','position':'MID','result':'DRAW'},{'match_id':'flex','position':'MID','result':'WIN','queue':'RANKED_FLEX_SR'},{'match_id':'unknown','result':'WIN'}]
    assert history_lanes(rows)==('jgl','top')
    state={'tracked':{'Same#ONE':{'puuid':'one','last_known_tier':'IRON'},'Same#TWO':{'puuid':'two','last_known_tier':'GOLD'}},'links':{'1':'Same#ONE','2':'Same#TWO','3':'stale','4':'Same#ONE'},'history':{'Same#ONE':rows}}
    before=json.dumps(state,sort_keys=True); roster=Roster(state)
    members=[SimpleNamespace(id=i,display_name=f'Player {i}',bot=False,voice=None) for i in range(1,24)]
    lookup=lambda i:next((m for m in members if m.id==i),None)
    e1=roster.resolve(user_id=1,name='Same');e2=roster.resolve(user_id=2,name='Same')
    assert e1.identity!=e2.identity and e1.tier=='IRON'
    assert roster.key_for_user(3) is None and state['links']['3']=='stale'
    extras=roster.extras('Same#ONE, Same#TWO, Guest=Silver, Gold',[],[],lookup)
    assert len(extras)==4 and extras[-1].name=='Guest 1'
    raises(lambda:roster.extras('Same#ONE, <@1>',[],[],lookup))
    raises(lambda:roster.extras('1=Gold',[],[],lookup))
    raises(lambda:roster.extras('Somebody',[],[],lookup))
    async def failure(info): return None
    await refresh_ranks([e1,e2],state,failure)
    assert not e1.confirmed and e1.source=='Saved tier — confirm'
    async def unranked(info): return []
    await refresh_ranks([e1],state,unranked);assert e1.tier is None
    async def fresh(info): return [{'queueType':'RANKED_SOLO_5x5','tier':'IRON','leaguePoints':0}]
    await refresh_ranks([e1],state,fresh);assert e1.tier=='IRON' and e1.confirmed
    e2.source='Manual estimate';e2.confirmed=True
    await refresh_ranks([e2],state,failure);assert e2.tier=='GOLD' and e2.confirmed
    guild=SimpleNamespace(id=100,get_member=lookup)
    dest=Destination();v=TeamsView(1,guild,dest,state,fetch=fresh,fixture_members=members)
    v.message=Message()
    private=[]
    async def private_send(**kwargs):
        assert 'view' not in kwargs
        msg=Message();private.append(msg);return msg
    v.result_sender=private_send
    assert len(v.children)==2
    assert v.deadline-time.monotonic()<=600
    assert not await v.interaction_check(interaction(2))
    await v.action(interaction(),'vc');assert len(v.candidates)==23
    modal=RosterModal(v);assert len(modal.children)==5
    assert len(modal.checks.options)==10
    await v.action(interaction(),'next'); assert v.page==1
    modal2=RosterModal(v);assert len(modal2.checks.options)==10
    await v.action(interaction(),'next');assert len(RosterModal(v).checks.options)==3
    assert not await modal.interaction_check(interaction(2))
    v.entries=[roster.resolve(name=('X'*60)+str(i)) for i in range(10)]
    for e in v.entries: e.tier='GOLD';e.confirmed=True;e.source='Manual estimate'
    v.stage='review';v.rebuild();embed_ok(v.embed())
    assert len(v.children)==3
    assert 'GOLD' not in str(v.embed().to_dict()).upper()
    assert 'Manual estimate' not in str(v.embed().to_dict())
    v.entries[1].confirmed=False
    v.rebuild();assert any(c.label=='Review needed' for c in v.children)
    assert '1 player needs a tier check' in v.embed().description
    v.selected=v.entries[0].identity
    await v.action(interaction(),'exceptions');assert 'Confirm saved tier' in v.embed().description
    assert v.selected==v.entries[1].identity
    selector=next(c for c in v.children if hasattr(c,'options'))
    assert [o.value for o in selector.options]==[v.entries[1].identity]
    assert selector.options[0].default
    # Filtered selector values remain stable identities, not filtered indexes.
    v.entries[3].confirmed=False;v.stage='review';v.rebuild()
    assert '2 players need a tier check' in v.embed().description
    await v.action(interaction(),'exceptions')
    selector=next(c for c in v.children if hasattr(c,'options'))
    assert {o.value for o in selector.options}=={v.entries[1].identity,v.entries[3].identity}
    selector._values=[v.entries[3].identity]
    await selector.callback(interaction())
    assert v.selected==v.entries[3].identity and v.stage=='player'
    assert 'Gold' in v.embed().description and any(getattr(c,'label',None)=='Use saved tier' for c in v.children)
    await v.action(interaction(),'confirm');assert v.entries[3].confirmed and v.stage=='review'
    await v.action(interaction(),'exceptions')
    selector=next(c for c in v.children if hasattr(c,'options'))
    assert [o.value for o in selector.options]==[v.entries[1].identity]
    selector._values=[v.entries[1].identity];await selector.callback(interaction())
    await v.action(interaction(),'confirm');assert v.entries[1].confirmed and v.stage=='review'
    assert len(PlayerModal(v,'tier').children)==1
    assert len(PlayerModal(v,'lanes').children)==2
    await v.action(interaction(),'generate');assert v.splits
    embed_ok(v.embed())
    assert len(private)==2 and len(v.children)==3
    for i,embed in enumerate(v.team_embeds()):
        embed_ok(embed);assert embed.title==f'Team {i+1}'
        assert not embed.fields and not embed.footer and not embed.timestamp
        assert len(embed.description.splitlines())==5
        assert all(' — ' in line for line in embed.description.splitlines())
        assert 'GOLD' not in str(embed.to_dict()).upper()
    first=v.splits[v.index];await v.action(interaction(),'reroll');assert v.splits[v.index]!=first
    assert len(private)==2 and all(msg.edits for msg in private)
    await v.action(interaction(),'swap');assert v.flipped
    v.selected=v.entries[0].identity
    stale=PlayerModal(v,'tier');await v.action(interaction(),'confirm')
    assert not await stale.interaction_check(interaction()) and not v.splits
    v.entries=v.entries[:8];v.size=8;v.omitted='top';v.entries[0].roles=('top','mid')
    await v.action(interaction(),'generate');assert any('omitted primary' in n for n in v.notices())
    dest.fail_at=2
    try: await v.publish()
    except TimeoutError: pass
    else: raise AssertionError('uncertain second send not simulated')
    assert dest.calls==2 and len(dest.messages)==2
    assert v.delivery['teams'][0]['message_id']==dest.messages[0].id
    assert unresolved_deliveries(1,100)
    assert not await v.interaction_check(SimpleNamespace(user=SimpleNamespace(id=1),response=Response(),data={'custom_id':v.cid('reroll')}))
    recovered=await v.publish();assert len(recovered)==2 and dest.calls==2
    assert not unresolved_deliveries(1,100)
    assert len({slot['nonce'] for slot in v.delivery['teams']})==2
    # Missing nonce cannot establish an uncertain accepted send: fail closed,
    # including after the nonce duplicate-protection interval has passed.
    uncertain=TeamsView(1,guild,Destination(),state,fixture_members=members)
    uncertain.message=Message();uncertain.entries=copy.deepcopy(v.entries);uncertain.size=8
    uncertain.splits=v.splits;uncertain.destination.fail_at=1;uncertain.destination.return_nonce=False
    try: await uncertain.publish()
    except TimeoutError: pass
    uncertain.delivery['teams'][0]['attempted_at']-=4000
    try: await uncertain.publish()
    except RuntimeError: pass
    else: raise AssertionError('must refuse resend without verified identity')
    assert uncertain.destination.calls==1 and unresolved_deliveries(1,100)
    uncertain.destination.scan_fail=True
    try: await uncertain.publish()
    except RuntimeError: pass
    else: raise AssertionError('must refuse resend without history verification')
    assert uncertain.destination.calls==1
    v.deadline=time.monotonic()-1
    assert not await v.interaction_check(interaction())
    await v.finish();assert all(c.disabled for c in v.children)
    p=TeamsView(1,guild,dest,state,preview=True,fixture_members=members)
    assert p.ns!=v.ns
    preview_messages=[]
    async def preview_send(**kwargs):
        m=Message();preview_messages.append(m);return m
    p.result_sender=preview_send;p.message=Message()
    journals_before={f.name:f.read_bytes() for f in Path('.automation/teams-deliveries').glob('*.json')}
    p.entries=copy.deepcopy(v.entries);p.size=8;p.omitted='top';p.stage='review'
    await p.action(interaction(),'generate');assert len(preview_messages)==2
    await p.action(interaction(),'reroll');assert len(preview_messages)==2 and all(m.edits for m in preview_messages)
    await p.action(interaction(),'swap');assert all(len(m.edits)>=2 for m in preview_messages)
    await p.action(interaction(),'post');assert dest.calls==2
    assert journals_before=={f.name:f.read_bytes() for f in Path('.automation/teams-deliveries').glob('*.json')}
    # Actual hard timer, independent of interactions extending discord View timeouts.
    p.deadline=time.monotonic()+0.01
    await p.expire();assert p.closed
    assert len(private)==2
    assert all(not hasattr(msg,'children') for msg in private)
    assert json.dumps(state,sort_keys=True)==before
    assert 'sisyphus.state' not in sys.modules
    print('Teams smoke passed: inputs, optimality, lanes, rank failures, paging, ownership, stale forms, send recovery, preview expiry, state integrity.')

if __name__=='__main__': asyncio.run(main())
