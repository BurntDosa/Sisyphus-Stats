"""Smoke test for /teams roster parsing and embeds (no network, fake bot state)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sisyphus import ranks, teams  # noqa: E402
from sisyphus.balance import APEX_BASE, parse_rank  # noqa: E402

FAKE = {
    "tracked": {
        # Master with and without the legacy phantom division in the stored total
        "Mast#1": {"last_known_lp": 3250, "last_known_tier": "MASTER", "last_known_rank": "1", "last_known_raw_lp": 150},
        "MastNoDiv#1": {"last_known_lp": 2950, "last_known_tier": "MASTER", "last_known_rank": "", "last_known_raw_lp": 150},
        "GM#1": {"last_known_lp": 4100, "last_known_tier": "GRANDMASTER", "last_known_rank": "1", "last_known_raw_lp": 600},
        "Chal#1": {"last_known_lp": 5000, "last_known_tier": "CHALLENGER", "last_known_rank": "1", "last_known_raw_lp": 1100},
        "Gold#1": {"last_known_lp": 1440, "last_known_tier": "GOLD", "last_known_rank": "2", "last_known_raw_lp": 40},
        "Unranked#1": {"last_known_lp": 0, "last_known_tier": "UNRANKED", "last_known_rank": "", "last_known_raw_lp": 0},
        "Hide on bush#KR1": {"last_known_lp": 2640, "last_known_tier": "DIAMOND", "last_known_rank": "2", "last_known_raw_lp": 40},
    },
    "links": {"111": "Mast#1", "222": "GM#1", "333": "Gone#9"},  # 333 points at an untracked id
}
teams.data = FAKE  # never touch the real bot state


def expect_error(raw: str, fragment: str = "") -> None:
    try:
        teams.build_roster(raw)
    except ValueError as exc:
        assert fragment.lower() in str(exc).lower(), (raw, str(exc))
        return
    raise AssertionError(f"{raw!r} should have failed")


def rating(raw: str) -> int:
    return teams.build_roster(raw)[0].rating


def check_stored_ratings() -> None:
    # Apex totals are read from raw LP, so every stored shape agrees with typing the rank
    assert rating("Mast#1") == rating("MastNoDiv#1") == parse_rank("m150") == 2950
    assert rating("GM#1") == parse_rank("gm 600") == APEX_BASE + 600
    assert rating("Chal#1") == parse_rank("c 1100") == APEX_BASE + 1100
    assert rating("Gold#1") == 1440
    assert rating("Hide on bush#KR1") == 2640  # names with spaces
    assert rating("Mast#1=g2") == parse_rank("g2")  # explicit rank overrides stored
    assert rating("<@111>") == 2950 and rating("<@!111>") == 2950  # mention forms
    assert rating("<@999>=plat 4") == parse_rank("p4")  # unlinked user, rank typed
    # the bot may return Master but with a stale total, raw LP still wins
    assert rating("Mast#1:mid") == 2950


def check_errors() -> None:
    expect_error("Unranked#1", "no recorded rank")
    expect_error("<@999>", "isn't tracked or linked")
    expect_error("<@333>", "isn't tracked or linked")  # link to a player that no longer exists
    expect_error("Nobody#1", "isn't tracked")
    expect_error("Bob=", "missing a rank")
    expect_error("Bob=banana", "can't read rank")
    expect_error("Bob=g2:carry", "unknown role")
    expect_error("", "list the players")
    expect_error(" , ,, ", "list the players")
    # same person twice, however they are written
    expect_error("<@111>, Mast#1", "more than once")
    expect_error("Mast#1, Mast#1", "more than once")
    expect_error("Bob=g2, bob=p1", "more than once")
    expect_error("<@999>=g2, <@999>=p1", "more than once")


def check_parsing_shapes() -> None:
    players = teams.build_roster("A=g2:mid,top; B=Diamond IV\nC=gm 600, D=s4: jgl ")
    assert [p.label for p in players] == ["A", "B", "C", "D"]
    assert players[0].prefs == ("mid", "top") and players[3].prefs == ("jgl",)
    assert players[1].rating == parse_rank("d4") and players[2].rating == APEX_BASE + 600
    assert teams.build_roster("Trailing=g1,")[0].label == "Trailing"
    # role lists written with commas stay attached to their player
    commas = teams.build_roster("A=g2:mid,top,jgl, B=p1:supp/adc, C=g3")
    assert [p.label for p in commas] == ["A", "B", "C"]
    assert commas[0].prefs == ("mid", "top", "jgl") and commas[1].prefs == ("supp", "adc")
    # a name that looks like a role is still a player when it has a rank
    assert [p.label for p in teams.build_roster("A=g2:mid, Top=p1")] == ["A", "Top"]


def check_rank_text() -> None:
    ranks._apex_cutoffs = None
    assert "Diamond 1" in teams._rank_text(parse_rank("d1"))
    assert "Gold 2" in teams._rank_text(parse_rank("g2"))
    assert "Master (150 LP)" in teams._rank_text(2950)
    assert "Master (0 LP)" in teams._rank_text(2800)
    ranks.set_apex_cutoffs(challenger=900, grandmaster=500)
    try:
        assert "Master (150 LP)" in teams._rank_text(2950)
        assert "Grandmaster (600 LP)" in teams._rank_text(APEX_BASE + 600)
        assert "Challenger (1100 LP)" in teams._rank_text(APEX_BASE + 1100)
    finally:
        ranks._apex_cutoffs = None


async def check_view() -> None:
    roster = ", ".join(
        f"{name}={rank}" for name, rank in zip(
            "ABCDEFGHIJ", ("g2", "p4", "d1", "m", "gm 600", "c 1100", "s3", "e2", "b1", "i4"))
    )
    view = teams.make_teams_message(42, roster)
    first = view.current_embed()
    assert [f.name.split()[0] for f in first.fields] == ["\U0001F7E6", "\U0001F7E5"]
    assert "avg rating gap" in first.footer.text and not first.description
    before = first.fields[0].value
    view.flipped = True
    assert view.current_embed().fields[1].value == before  # swap sides moves the teams
    assert len(view.splits) > 1 and not view.reroll.disabled

    # two players: only one split is possible, so Reroll is disabled
    duo = teams.make_teams_message(42, "A=g2, B=p1")
    assert duo.reroll.disabled and len(duo.splits) == 1

    # roles on a non-5v5 roster are ignored with a visible note
    noted = teams.make_teams_message(42, "A=g2:mid, B=p1, C=g3, D=g4")
    assert "ignored" in noted.current_embed().description

    # roles on a 5v5 roster show up on every line
    roles = teams.make_teams_message(42, roster.replace("A=g2", "A=g2:mid"))
    for field in roles.current_embed().fields:
        assert all(r in field.value for r in ("Top", "Jungle", "Mid", "ADC", "Support"))

    # very long names and mentions stay inside Discord's embed limits
    long_names = ", ".join(f"{'N' * 300}{i}=g{(i % 4) + 1}" for i in range(5))
    long_names += ", " + ", ".join(f"<@{10**17 + i}>=p{(i % 4) + 1}" for i in range(5))
    big = teams.make_teams_message(42, long_names.replace("N" * 300, "N" * 300)).current_embed()
    assert all(len(f.value) <= 1024 for f in big.fields), [len(f.value) for f in big.fields]
    assert len(big) <= 6000
    assert "<@" in big.fields[0].value + big.fields[1].value  # mentions are never cut in half

    # an all Master+ lobby renders with LP values
    apex = teams.make_teams_message(42, ", ".join(f"P{i}=m{i * 150}" for i in range(10))).current_embed()
    assert "LP)" in apex.fields[0].value


def main() -> None:
    check_stored_ratings()
    check_errors()
    check_parsing_shapes()
    check_rank_text()
    asyncio.run(check_view())
    print("smoke_teams: ok")


if __name__ == "__main__":
    main()
