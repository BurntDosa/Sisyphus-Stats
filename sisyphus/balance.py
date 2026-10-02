"""Custom-game team balancing.

Pure logic only: no Discord, no network, no bot state. Ratings are the same
"total LP" scale the bot already stores in data["tracked"][id]["last_known_lp"]
(tier * 400 + division * 100 + LP), so tracked players need no API call.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from itertools import combinations, permutations

from .ranks import DIV_ORDER, TIER_ORDER

ROLES = ("top", "jgl", "mid", "adc", "supp")
ROLE_LABELS = {"top": "Top", "jgl": "Jungle", "mid": "Mid", "adc": "ADC", "supp": "Support"}
ROLE_ALIASES = {
    "top": "top",
    "jgl": "jgl", "jg": "jgl", "jungle": "jgl",
    "mid": "mid", "middle": "mid",
    "adc": "adc", "bot": "adc", "bottom": "adc",
    "supp": "supp", "sup": "supp", "support": "supp",
}

_TIERS = {
    "i": "IRON", "iron": "IRON",
    "b": "BRONZE", "bronze": "BRONZE",
    "s": "SILVER", "silver": "SILVER",
    "g": "GOLD", "gold": "GOLD",
    "p": "PLATINUM", "plat": "PLATINUM", "platinum": "PLATINUM",
    "e": "EMERALD", "em": "EMERALD", "emerald": "EMERALD",
    "d": "DIAMOND", "dia": "DIAMOND", "diamond": "DIAMOND",
    "m": "MASTER", "master": "MASTER",
    "gm": "GRANDMASTER", "grandmaster": "GRANDMASTER",
    "c": "CHALLENGER", "chall": "CHALLENGER", "challenger": "CHALLENGER",
}
_APEX = {"MASTER", "GRANDMASTER", "CHALLENGER"}
APEX_BASE = TIER_ORDER["MASTER"] * 400
_RANK_RE = re.compile(r"^([a-z]+?)\s*(\d+)?$")
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}


@dataclass(frozen=True)
class Player:
    label: str
    rating: int
    prefs: tuple[str, ...] = ()  # ordered role preferences, empty means no preference


@dataclass(frozen=True)
class Slot:
    player: Player
    role: str | None  # None when roles are not being assigned


@dataclass(frozen=True)
class Split:
    team_a: tuple[Slot, ...]
    team_b: tuple[Slot, ...]
    gap: int      # absolute difference of team rating totals
    score: float  # gap plus role mismatch penalty (lower is better)

    @property
    def roles_assigned(self) -> bool:
        return any(slot.role for slot in self.team_a)


def parse_rank(text: str) -> int:
    """Turn 'g2', 'gold 2', 'plat', 'master 150' into a total-LP rating.

    Divisions default to IV, and a division is rated at its midpoint (+50).
    Apex tiers use the bot's stored base (Master = 7 * 400) plus LP.
    """
    tokens = text.strip().lower().split()
    if len(tokens) > 1 and tokens[-1] in _ROMAN:  # 'diamond iv' -> 'diamond 4'
        tokens[-1] = _ROMAN[tokens[-1]]
    m = _RANK_RE.match(" ".join(tokens))
    tier = _TIERS.get(m.group(1)) if m else None
    if not tier:
        raise ValueError(f"Can't read rank {text!r}. Try `g2`, `plat 4`, or `master 150`.")
    num = m.group(2)
    if tier in _APEX:
        return APEX_BASE + int(num or 0)
    division = num or "4"
    if division not in ("1", "2", "3", "4"):
        raise ValueError(f"Division must be 1 to 4 in {text!r}.")
    return TIER_ORDER[tier] * 400 + DIV_ORDER[division] * 100 + 50


def parse_roles(text: str) -> tuple[str, ...]:
    roles: list[str] = []
    for word in re.split(r"[,/\s]+", text.strip().lower()):
        if not word:
            continue
        role = ROLE_ALIASES.get(word)
        if role is None:
            raise ValueError(f"Unknown role {word!r}. Use top, jgl, mid, adc, or supp.")
        if role not in roles:
            roles.append(role)
    return tuple(roles)


def _role_cost(player: Player, role: str) -> int:
    if not player.prefs:
        return 0
    if role in player.prefs:
        return player.prefs.index(role)
    return len(ROLES)


def _assign_roles(team: list[Player]) -> tuple[tuple[Slot, ...], int]:
    best_cost = None
    best_perm: tuple[str, ...] = ROLES
    for perm in permutations(ROLES):
        cost = sum(_role_cost(p, r) for p, r in zip(team, perm))
        if best_cost is None or cost < best_cost:
            best_cost, best_perm = cost, perm
    slots = [Slot(p, r) for p, r in zip(team, best_perm)]
    slots.sort(key=lambda s: ROLES.index(s.role))
    return tuple(slots), int(best_cost or 0)


def balance(
    players: list[Player],
    *,
    role_weight: int = 40,
    tolerance: int = 60,
    limit: int = 12,
) -> list[Split]:
    """Return the best splits, best first.

    Every split within `tolerance` score of the best (up to `limit`) is kept,
    so a reroll gives a different but still fair option.
    Roles are only assigned for 5v5 when at least one player gave preferences.
    """
    n = len(players)
    if n < 2 or n > 10 or n % 2:
        raise ValueError("Need an even number of players, from 2 to 10.")
    labels = [p.label for p in players]
    if len(set(labels)) != n:
        raise ValueError("Each player can only be listed once.")

    half = n // 2
    use_roles = half == len(ROLES) and any(p.prefs for p in players)
    scored: list[tuple[float, float, Split]] = []

    for rest in combinations(range(1, n), half - 1):  # player 0 fixed on team A
        a_idx = (0, *rest)
        a = [players[i] for i in a_idx]
        b = [players[i] for i in range(n) if i not in a_idx]
        gap = abs(sum(p.rating for p in a) - sum(p.rating for p in b))
        if use_roles:
            slots_a, cost_a = _assign_roles(a)
            slots_b, cost_b = _assign_roles(b)
            score = gap + role_weight * (cost_a + cost_b)
        else:
            slots_a = tuple(Slot(p, None) for p in sorted(a, key=lambda p: -p.rating))
            slots_b = tuple(Slot(p, None) for p in sorted(b, key=lambda p: -p.rating))
            score = float(gap)
        scored.append((score, random.random(), Split(slots_a, slots_b, gap, score)))

    scored.sort(key=lambda item: (item[0], item[1]))  # random tiebreak for variety
    best = scored[0][0]
    return [s for score, _, s in scored if score <= best + tolerance][:limit]
