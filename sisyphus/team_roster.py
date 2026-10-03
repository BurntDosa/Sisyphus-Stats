"""Read-only roster resolution. Callers supply snapshots and rank providers."""
import asyncio
import copy
import re
from dataclasses import dataclass, field
from .balance import Player, TIERS, ROLES, ROLE_ALIASES, parse_tier

MENTION = re.compile(r'<@!?(\d+)>')

def history_lanes(rows):
    counts, recent, seen = {}, {}, set()
    for index, row in enumerate(sorted(rows, key=lambda r: str(r.get('timestamp') or r.get('recorded_at') or r.get('date') or ''))):
        match = row.get('match_id')
        if match and match in seen:
            continue
        if match:
            seen.add(match)
        if row.get('result') not in ('WIN', 'LOSS') or row.get('queue', 'RANKED_SOLO_5x5') != 'RANKED_SOLO_5x5':
            continue
        duration = row.get('duration')
        if isinstance(duration,(int,float)) and 0 < duration < 120:
            continue
        role = ROLE_ALIASES.get(str(row.get('position', '')).lower())
        if role:
            counts[role] = counts.get(role, 0)+1; recent[role] = index
    return tuple(sorted(counts, key=lambda r: (-counts[r], -recent[r], ROLES.index(r)))[:2])

@dataclass
class Entry:
    identity: str
    name: str
    key: str | None = None
    discord_id: int | None = None
    tier: str | None = None
    source: str = 'Needs tier'
    confirmed: bool = False
    roles: tuple[str, ...] = ()
    notices: list[str] = field(default_factory=list)

    def player(self):
        return Player(self.identity, self.name, self.tier, self.roles)

class Roster:
    def __init__(self, state):
        self.state = copy.deepcopy({k:state.get(k,{}) for k in ('tracked','links','history')})

    def key_for_user(self, user_id):
        key = self.state.get('links', {}).get(str(user_id))
        return key if key in self.state.get('tracked', {}) else None

    def resolve(self, *, user_id=None, name=None, key=None):
        if user_id is not None:
            key = self.key_for_user(user_id)
        info = self.state.get('tracked', {}).get(key, {})
        identity = ('riot:'+str(info.get('puuid') or key).casefold()) if key else ('discord:'+str(user_id) if user_id is not None else 'guest:'+name.casefold())
        tier = info.get('last_known_tier')
        return Entry(identity, (name or key or 'Guest')[:80], key, user_id,
                     tier if tier in TIERS else None, 'Saved tier — confirm' if tier in TIERS else 'Needs tier',
                     roles=history_lanes(self.state.get('history', {}).get(key, [])))

    def extras(self, text, entries, numbered, member_lookup):
        result = copy.deepcopy(entries)
        for token in filter(None, (t.strip() for t in text.split(','))):
            target, sep, tier_text = token.partition('=')
            target = target.strip()
            tier = parse_tier(tier_text) if sep else None
            mention = MENTION.fullmatch(target)
            if target.isdigit():
                i = int(target)-1
                if not 0 <= i < len(numbered):
                    raise ValueError('That player number is outside the displayed roster.')
                entry = copy.deepcopy(numbered[i])
            elif mention:
                user = member_lookup(int(mention[1]))
                if user is None or user.bot:
                    raise ValueError('Choose a human member of this server.')
                entry = self.resolve(user_id=user.id, name=user.display_name)
            elif target in self.state.get('tracked', {}):
                entry = self.resolve(key=target)
            elif sep:
                if not target:
                    raise ValueError('Give the guest a name before =.')
                if len(target)>60:
                    raise ValueError('Guest names must be at most 60 characters.')
                matches = [e for e in result if e.name.casefold() == target.casefold()]
                if len(matches) > 1:
                    raise ValueError('That name is ambiguous. Use the player number.')
                entry = copy.deepcopy(matches[0]) if matches else self.resolve(name=target)
            else:
                tier = parse_tier(target)
                number = 1
                while any(e.name == f'Guest {number}' for e in result): number += 1
                entry = self.resolve(name=f'Guest {number}')
            existing = next((e for e in result if e.identity == entry.identity), None)
            if existing and tier is None:
                raise ValueError(f'{entry.name} is already selected.')
            if tier:
                entry.tier, entry.source, entry.confirmed = tier, 'Manual estimate', True
                if existing:
                    existing.tier, existing.source, existing.confirmed = tier, entry.source, True
                    continue
            result.append(entry)
        if len(result) > 10:
            raise ValueError('Select at most ten players.')
        return result

async def refresh_ranks(entries, state, fetch):
    semaphore = asyncio.Semaphore(3)
    async def one(entry):
        if not entry.key or entry.source == 'Manual estimate': return
        info = state.get('tracked', {}).get(entry.key, {})
        try:
            async with semaphore:
                ranks = await asyncio.wait_for(fetch(info), 20)
        except (Exception,):
            ranks = None
        if ranks is None:
            entry.source = 'Saved tier — confirm' if entry.tier in TIERS else 'Needs tier'
            entry.confirmed = False
        else:
            solo = next((r for r in ranks if r.get('queueType') == 'RANKED_SOLO_5x5'), {})
            tier = solo.get('tier')
            entry.tier = tier if tier in TIERS else None
            entry.source = 'Current Solo/Duo' if entry.tier else 'Unranked — estimate needed'
            entry.confirmed = entry.tier is not None
    await asyncio.gather(*(one(e) for e in entries))
