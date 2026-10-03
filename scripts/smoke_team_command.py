"""Offline registration, private entry points, and concurrent session checks."""
import asyncio
import sys
import json
from pathlib import Path
from types import SimpleNamespace
import discord
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sisyphus.teams import start_teams,SESSIONS
from sisyphus.commands import cmd_teams
from sisyphus.bot import bot

class Message:
    async def edit(self,**kwargs): pass

class Context:
    def __init__(self,uid=1,slash=True,blocked=False):
        self.sent=[];self.deferred=[];self.guild=SimpleNamespace(id=500,get_member=lambda _:None)
        self.interaction=SimpleNamespace(followup=SimpleNamespace(send=self.send)) if slash else None
        self.channel=None
        async def dm(**kwargs):
            if blocked: raise discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden',headers={}),{'message':'Blocked','code':50007})
            self.sent.append(kwargs);return Message()
        self.author=SimpleNamespace(id=uid,send=dm)
    async def send(self,*args,**kwargs): self.sent.append((args,kwargs));return Message()
    async def defer(self,**kwargs): self.deferred.append(kwargs)

async def main():
    assert bot.get_command('teams') is cmd_teams and bot.get_command('getteams') is None
    assert sum(c.name=='teams' for c in bot.tree.get_commands())==1
    state={'tracked':{},'links':{},'history':{}}
    c=Context();await start_teams(c,state)
    assert c.deferred==[{'ephemeral':True}] and c.sent[0][1]['ephemeral'] is True
    old=SESSIONS[(500,1)]
    await start_teams(Context(uid=2),state);assert len(SESSIONS)==2
    await start_teams(Context(),state);assert old.closed and len(SESSIONS)==2
    dm=Context(uid=3,slash=False);await start_teams(dm,state);assert 'embed' in dm.sent[0]
    failed=Context(uid=4,slash=False,blocked=True);await start_teams(failed,state)
    assert (500,4) not in SESSIONS and 'Use /teams' in failed.sent[0][0][0]
    noguild=Context(uid=5);noguild.guild=None;await start_teams(noguild,state)
    assert (500,5) not in SESSIONS
    receipts=Path('.automation/teams-deliveries');receipts.mkdir(parents=True,exist_ok=True)
    (receipts/'pending.json').write_text(json.dumps({'owner':6,'guild':500,'complete':False}))
    pending=Context(uid=6);await start_teams(pending,state)
    assert (500,6) not in SESSIONS and 'delivery verification' in pending.sent[0][0][0]
    for view in list(SESSIONS.values()): await view.finish()
    assert not SESSIONS
    print('Teams command smoke passed: single command, private slash/prefix, DM fallback, session replacement and concurrency.')

if __name__=='__main__': asyncio.run(main())
