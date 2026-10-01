"""Offline regression checks for private champion role rolls."""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sisyphus import commands as bot_commands, ddragon, role_roll  # noqa: E402
from sisyphus.opgg import STAT_SHARD_NAMES  # noqa: E402


def _valid_build() -> dict:
    return {
        "runes": {
            "primary_page": "Resolve",
            "primary_ids": [8437, 8446, 8473, 8451],
            "primary_names": ["Grasp", "Demolish", "Bone Plating", "Overgrowth"],
            "secondary_page": "Inspiration",
            "secondary_ids": [8304, 8345],
            "secondary_names": ["Magical Footwear", "Biscuit Delivery"],
            "stat_ids": [5005, 5008, 5001],
            "stat_names": ["Attack Speed", "Adaptive Force", "Health Scaling"],
        },
        "spell_ids": [4, 12],
        "starter": {"ids": [1054, 2003], "names": ["Doran's Shield", "Health Potion"]},
        "boots": {"ids": [3047], "names": ["Plated Steelcaps"]},
        "core": {"ids": [3068, 3075, 6665], "names": ["Sunfire Aegis", "Thornmail", "Jak'Sho"]},
        "later_items": {
            "4th": [
                {"ids": [3143], "names": ["Randuin's Omen"]},
                {"ids": [2525], "names": ["Protoplasm Harness"]},
            ],
            "5th": [{"ids": [3075], "names": ["Thornmail"]}],
            "6th": [{"ids": [6665], "names": ["Jak'Sho"]}],
        },
        "skills": ["Q", "W", "E", "W", "W", "R"],
        "skill_masteries": ["W", "Q", "E"],
    }


async def _image_checks() -> None:
    async def fake_rune(_session, rune_id, size):
        return Image.new("RGBA", (size, size), (rune_id % 255, 50, 70, 255))

    async def fake_spell(_session, spell_id, size):
        return Image.new("RGBA", (size, size), (20, spell_id % 255, 90, 255))

    async def fake_item(_session, _version, item_id, size):
        return Image.new("RGBA", (size, size), (80, item_id % 255, 20, 255))

    old_rune = ddragon._rune_image
    old_spell = ddragon._spell_image
    old_item = ddragon._item_or_placeholder
    old_version = ddragon.get_ddragon_version
    try:
        ddragon._rune_image = fake_rune
        ddragon._spell_image = fake_spell
        ddragon._item_or_placeholder = fake_item

        async def fake_version(_session):
            return "test"

        ddragon.get_ddragon_version = fake_version
        runes = await ddragon.build_role_runes_image(
            SimpleNamespace(), [8437, 8446, 8473, 8451], [8304, 8345], [5005, 5008, 5001]
        )
        build = await ddragon.build_role_build_image(
            SimpleNamespace(), [4, 12], [("Start", [1054]), ("Core", [3068, 3075, 6665])]
        )
        for file in (runes, build):
            raw = file.fp.read()
            image = Image.open(io.BytesIO(raw))
            assert image.width > 100 and image.height > 80
            assert image.getbbox() is not None
    finally:
        ddragon._rune_image = old_rune
        ddragon._spell_image = old_spell
        ddragon._item_or_placeholder = old_item
        ddragon.get_ddragon_version = old_version


async def _cache_check() -> None:
    role_roll.reset_role_roll_state()
    calls = []
    old_analysis = role_roll.get_champion_analysis

    async def fake_analysis(_session, champion, position):
        calls.append((champion, position))
        return _valid_build(), None

    try:
        role_roll.get_champion_analysis = fake_analysis
        first, error = await role_roll.get_role_build(SimpleNamespace(), "Ornn", "top")
        second, second_error = await role_roll.get_role_build(SimpleNamespace(), "Ornn", "top")
        assert not error and not second_error
        assert first == second
        assert calls == [("Ornn", "top")]
    finally:
        role_roll.get_champion_analysis = old_analysis
        role_roll.reset_role_roll_state()


async def _private_delivery_check() -> None:
    class FakeAuthor:
        id = 1001

        def __init__(self):
            self.dms = []

        async def send(self, **kwargs):
            self.dms.append(kwargs)

    class FakeMessage:
        def __init__(self):
            self.reactions = []

        async def add_reaction(self, value):
            self.reactions.append(value)

    class FakeContext:
        def __init__(self, interaction):
            self.interaction = interaction
            self.author = FakeAuthor()
            self.message = FakeMessage()
            self.sent = []
            self.deferred = False

        async def defer(self, **_kwargs):
            self.deferred = True

        async def send(self, content=None, **kwargs):
            self.sent.append((content, kwargs))

    old_choose = bot_commands.choose_champion
    old_build = bot_commands.get_role_build
    old_message = bot_commands.build_role_roll_message

    async def fake_build(_session, _champion, _role):
        return _valid_build(), None

    async def fake_message(_session, _roll):
        return [bot_commands.discord.Embed(title="Private role roll")] * 3, []

    try:
        bot_commands.choose_champion = lambda _role: "Ornn"
        bot_commands.get_role_build = fake_build
        bot_commands.build_role_roll_message = fake_message

        role_roll.reset_role_roll_state()
        prefix = FakeContext(None)
        await bot_commands.cmd_role.callback(prefix, "top")
        assert len(prefix.author.dms) == 1
        assert not prefix.sent
        assert prefix.message.reactions == ["📬"]

        role_roll.reset_role_roll_state()
        slash = FakeContext(SimpleNamespace())
        await bot_commands.cmd_role.callback(slash, "wild")
        assert slash.deferred
        assert len(slash.author.dms) == 1
        assert len(slash.sent) == 1
        assert slash.sent[0][1].get("ephemeral") is True
    finally:
        bot_commands.choose_champion = old_choose
        bot_commands.get_role_build = old_build
        bot_commands.build_role_roll_message = old_message
        role_roll.reset_role_roll_state()


def main() -> None:
    assert STAT_SHARD_NAMES[5008] == "Adaptive Force"
    assert STAT_SHARD_NAMES[5001] == "Health Scaling"
    assert STAT_SHARD_NAMES[5002] == "Armor"
    assert STAT_SHARD_NAMES[5003] == "Magic Resist"
    assert len(role_roll.WILD_POOL) == role_roll.ROLE_SNAPSHOT_CHAMPION_COUNT == 173
    assert len(set(role_roll.WILD_POOL)) == len(role_roll.WILD_POOL)
    for role, pool in role_roll.ROLE_POOLS.items():
        assert len(pool) == len(set(pool)), f"{role} pool contains duplicates"
        assert pool and set(pool) <= set(role_roll.WILD_POOL)
    assert role_roll.normalize_role("JUNGLE") == "jgl"
    assert role_roll.normalize_role("bottom") == "adc"
    assert role_roll.normalize_role("SUP") == "supp"
    assert role_roll.normalize_role("wild") == "wild"
    assert role_roll.normalize_role("fill") is None

    original_choice = role_roll.secrets.choice
    try:
        role_roll.secrets.choice = lambda pool: pool[-1]
        assert role_roll.choose_champion("wild") == role_roll.WILD_POOL[-1]
        assert role_roll.choose_champion("top") == role_roll.ROLE_POOLS["top"][-1]
    finally:
        role_roll.secrets.choice = original_choice

    role_roll.reset_role_roll_state()
    assert role_roll.begin_role_roll(42) is None
    assert "still being prepared" in (role_roll.begin_role_roll(42) or "")
    role_roll.finish_role_roll(42, successful=True)
    assert "Wait" in (role_roll.begin_role_roll(42) or "")
    role_roll.reset_role_roll_state()

    asyncio.run(_cache_check())
    asyncio.run(_image_checks())
    asyncio.run(_private_delivery_check())
    print("Role roll smoke checks passed.")


if __name__ == "__main__":
    main()
