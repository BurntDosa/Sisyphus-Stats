"""Smoke test for custom-game team balancing (no Discord, no network)."""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sisyphus.balance import ROLES, Player, balance, parse_rank, parse_roles


def check_parse() -> None:
    assert parse_rank("g2") == 3 * 400 + 2 * 100 + 50
    assert parse_rank("Gold 2") == parse_rank("g2")
    assert parse_rank("plat") == 4 * 400 + 0 * 100 + 50  # defaults to IV
    assert parse_rank("d1") == 6 * 400 + 3 * 100 + 50
    assert parse_rank("master 150") == 7 * 400 + 300 + 150
    assert parse_rank("m150") == parse_rank("master 150")
    for bad in ("banana", "g9", ""):
        try:
            parse_rank(bad)
        except ValueError:
            continue
        raise AssertionError(f"parse_rank accepted {bad!r}")
    assert parse_roles("mid, Top") == ("mid", "top")
    assert parse_roles("bot/sup") == ("adc", "supp")


def best_possible_gap(players: list[Player]) -> int:
    n, half = len(players), len(players) // 2
    best = None
    for rest in combinations(range(1, n), half - 1):
        a = (0, *rest)
        gap = abs(
            sum(players[i].rating for i in a)
            - sum(players[i].rating for i in range(n) if i not in a)
        )
        best = gap if best is None else min(best, gap)
    return best or 0


def check_ratings_only() -> None:
    ratings = [3100, 2900, 2500, 2450, 2000, 1900, 1500, 1200, 800, 450]
    players = [Player(f"P{i}", r) for i, r in enumerate(ratings)]
    splits = balance(players)
    assert splits and splits[0].gap == best_possible_gap(players)
    for split in splits:
        names = [s.player.label for s in split.team_a + split.team_b]
        assert sorted(names) == sorted(p.label for p in players)
        assert len(split.team_a) == len(split.team_b) == 5
        assert not split.roles_assigned


def check_roles() -> None:
    ratings = [2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000]
    prefs = [("top",), ("top",), ("jgl",), ("jgl",), ("mid",), ("mid",), ("adc",), ("adc",), ("supp",), ("supp",)]
    players = [Player(f"P{i}", r, p) for i, (r, p) in enumerate(zip(ratings, prefs))]
    best = balance(players)[0]
    assert best.roles_assigned
    for team in (best.team_a, best.team_b):
        assert sorted(s.role for s in team) == sorted(ROLES)
        for slot in team:  # everyone gets their first choice in this setup
            assert slot.role == slot.player.prefs[0], (slot.player.label, slot.role)


def check_small_and_errors() -> None:
    assert len(balance([Player("A", 1000), Player("B", 1200)])[0].team_a) == 1
    four = [Player(c, r) for c, r in zip("ABCD", (2000, 1800, 1000, 800))]
    assert balance(four)[0].gap == 0 + abs((2000 + 800) - (1800 + 1000))
    for bad in (
        [Player("A", 1000)] * 1,
        [Player(str(i), 1000) for i in range(3)],
        [Player("A", 1000), Player("A", 1100)],
    ):
        try:
            balance(bad)
        except ValueError:
            continue
        raise AssertionError("balance accepted invalid roster")


def main() -> None:
    check_parse()
    check_ratings_only()
    check_roles()
    check_small_and_errors()
    print("smoke_balance: ok")


if __name__ == "__main__":
    main()