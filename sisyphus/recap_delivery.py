"""Production adapters for the shared renderer; previews do not import this."""
from __future__ import annotations

import discord
import asyncio
from datetime import datetime, timedelta, timezone
from weakref import WeakValueDictionary

from .match_recap import SharedRecapView, members, recap_digest
from .match_processing import parsed

_AUTOMATIC_VIEWS = WeakValueDictionary()


async def remember_own_result(interaction, snapshot, message):
    from .profiles import MemoryNameModal, user_owns_player

    owned = [m for m in members(snapshot) if user_owns_player(interaction.user.id, m["riot_id"])]
    if len(owned) != 1:
        await interaction.response.send_message(
            "Only a linked player in this recap can save their own result.", ephemeral=True)
        return
    await interaction.response.send_modal(MemoryNameModal(owned[0]["riot_id"],
        str(snapshot["match"]["metadata"]["matchId"]), recap_url=getattr(message, "jump_url", None)))


async def build_recap_view(snapshot, session, *, automatic=False):
    if len(snapshot["members"]) > 1:
        view = SharedRecapView(snapshot, remember=remember_own_result)
    else:
        from .views import ScoreboardView
        from .summoner_icons import get_profile_icon_url

        m = snapshot["members"][0]
        view = ScoreboardView(snapshot["match"], m["puuid"], m["riot_id"],
            m.get("tier") or "UNRANKED", m.get("rank") or "", m.get("lp"),
            m.get("old_lp") if m.get("lp_status") == "known" else None,
            m.get("total_lp") if m.get("lp_status") == "known" else None,
            lp_status=m.get("lp_status", "unavailable"),
            profile_icon_url=await get_profile_icon_url(session, m["riot_id"]))
        view.delivery_marker = f"recap:{recap_digest(snapshot['key'])}:"
        for child in view.children:
            child.custom_id = view.delivery_marker + str(child.custom_id)
    await view.prepare(session)
    if automatic:
        old = _AUTOMATIC_VIEWS.get(snapshot["key"])
        if old:
            old.stop()
        _AUTOMATIC_VIEWS[snapshot["key"]] = view
    return view


async def restore_recent_views(receipts, bot, destination, session):
    """Restore still-active buttons after a restart without reposting anything."""
    if not destination:
        return
    for key, receipt in receipts.items():
        if key in _AUTOMATIC_VIEWS or receipt.get("status") != "sent" or not receipt.get("match") or not receipt.get("message_id"):
            continue
        attempt = parsed(receipt.get("first_attempt_at"))
        remaining = (attempt + timedelta(seconds=300) - datetime.now(timezone.utc)).total_seconds() if attempt else 0
        if remaining <= 0:
            continue
        try:
            message = await destination.fetch_message(int(receipt["message_id"]))
            view = await build_recap_view(receipt, session, automatic=True)
            view.timeout = None
            view.message = message
            bot.add_view(view, message_id=message.id)
            async def expire(restored_view, seconds):
                await asyncio.sleep(seconds)
                await restored_view.on_timeout()
                restored_view.stop()
            asyncio.create_task(expire(view, remaining))
        except discord.HTTPException as exc:
            print(f"[recap] button recovery deferred for {key}: {type(exc).__name__}")


def enrich_history(riot_id, row):
    from .community import praise_lines, update_records
    from .profiles import ensure_player_milestones, recap_headline

    row["story_headline"] = recap_headline(riot_id, row)
    labels = update_records(riot_id, row)
    if labels:
        row["record_labels"] = sorted(set(row.get("record_labels", []) + labels))
    row["spotlights"] = praise_lines(riot_id, row, row.get("record_labels"))
    ensure_player_milestones(riot_id)
