"""Pure custom-game balancing, adapted from Saarthak-Khandelwal's PR #1.

Enumerate equal teams with player zero fixed on one side; assign lanes using
permutations. Rank fairness precedes lane fit. No state or network access.
"""
from dataclasses import dataclass
from itertools import combinations, permutations
import re

TIERS = ('IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD', 'DIAMOND', 'MASTER', 'GRANDMASTER', 'CHALLENGER')
ROLES = ('top', 'jgl', 'mid', 'adc', 'supp')
ROLE_LABELS = dict(zip(ROLES, ('Top', 'Jungle', 'Mid', 'ADC', 'Support')))
ROLE_ALIASES = {r: r for r in ROLES} | {'jg': 'jgl', 'jungle': 'jgl', 'middle': 'mid', 'bot': 'adc', 'bottom': 'adc', 'support': 'supp', 'sup': 'supp', 'utility': 'supp'}
TIER_ALIASES = dict(zip(('i','b','s','g','p','e','d','m','gm','c'), TIERS)) | {t.lower(): t for t in TIERS} | {'plat': 'PLATINUM', 'dia': 'DIAMOND', 'em': 'EMERALD', 'chall': 'CHALLENGER'}

def parse_tier(text):
    match = re.fullmatch(r'([a-z]+)(?:\s*(?:[1-4]|iv|iii|ii|i))?', text.strip().lower())
    tier = TIER_ALIASES.get(match[1]) if match else None
    if tier is None:
        raise ValueError('Use a tier from Iron through Challenger. Divisions and LP are ignored.')
    return tier

def parse_roles(text):
    if text.strip().lower() in ('flexible', 'fill', 'wild', ''):
        return ()
    roles = []
    for token in re.split(r'[/,\s]+', text.strip().lower()):
        role = ROLE_ALIASES.get(token)
        if role is None:
            raise ValueError('Use Top, Jungle, Mid, ADC, Support, or Flexible.')
        if role not in roles:
            roles.append(role)
    if len(roles) > 2:
        raise ValueError('Choose at most a primary and a secondary lane.')
    return tuple(roles)

@dataclass(frozen=True)
class Player:
    identity: str
    label: str
    tier: str
    prefs: tuple[str, ...] = ()

    @property
    def rating(self):
        return TIERS.index(self.tier)

@dataclass(frozen=True)
class Slot:
    player: Player
    role: str

    @property
    def fit(self):
        if not self.player.prefs:
            return 'Flexible'
        if self.role == self.player.prefs[0]:
            return 'Primary'
        return 'Secondary' if self.role in self.player.prefs else 'Off-role'

@dataclass(frozen=True)
class Split:
    team_a: tuple[Slot, ...]
    team_b: tuple[Slot, ...]
    score: tuple[int, int, int]

    @property
    def gap(self):
        return self.score[0]

def _assign_roles(team, roles):
    best = None
    slots = None
    for perm in permutations(roles):
        candidate = tuple(Slot(p, r) for p, r in zip(team, perm))
        cost = (sum(s.fit == 'Off-role' for s in candidate), sum(s.fit == 'Secondary' for s in candidate))
        if best is None or cost < best:
            best, slots = cost, candidate
    return tuple(sorted(slots, key=lambda s: ROLES.index(s.role))), best

def balance(players, *, omitted=None):
    if len(players) not in (8, 10):
        raise ValueError('Select exactly 8 players for 4v4 or 10 for 5v5.')
    if len({p.identity for p in players}) != len(players):
        raise ValueError('The same player is selected more than once.')
    if len(players) == 8 and omitted not in ROLES:
        raise ValueError('Choose the omitted lane for 4v4.')
    if any(p.tier not in TIERS or len(p.prefs) > 2 or any(r not in ROLES for r in p.prefs) for p in players):
        raise ValueError('Each player needs a valid tier and lane preferences.')
    roles = tuple(r for r in ROLES if len(players) == 10 or r != omitted)
    # Omitting a primary promotes the remaining secondary; absent both is fill.
    players = [Player(p.identity, p.label, p.tier, tuple(r for r in p.prefs if r in roles)) for p in players]
    cache = {}
    def assign(indices):
        key = tuple(indices)
        if key not in cache:
            cache[key] = _assign_roles([players[i] for i in key], roles)
        return cache[key]
    best, result = None, []
    for rest in combinations(range(1, len(players)), len(players)//2-1):
        a = (0, *rest)
        b = tuple(i for i in range(len(players)) if i not in a)
        sa, ca = assign(a); sb, cb = assign(b)
        gap = abs(sum(s.player.rating for s in sa)-sum(s.player.rating for s in sb))
        score = (gap, ca[0]+cb[0], ca[1]+cb[1])
        if best is None or score < best:
            best, result = score, []
        if score == best:
            result.append(Split(sa, sb, score))
    return result
