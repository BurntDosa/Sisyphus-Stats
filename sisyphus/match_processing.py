"""Per-player history and per-team delivery, with explicit injected services."""
from __future__ import annotations

from collections import defaultdict
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from .match_recap import recap_digest


def parsed(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def lp_value(value):
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def outcome(participant, seconds, delta=None):
    if 0 < seconds < 120:
        return "DRAW"
    code = str(participant.get("result_code") or "").upper()
    if code == "WIN":
        return "WIN"
    if code in {"LOSE", "LOSS", "DEFEAT"}:
        return "LOSS"
    if code in {"DRAW", "TIE", "REMAKE", "VOID"}:
        return "WIN" if delta and delta > 0 else "LOSS" if delta and delta < 0 else "DRAW"
    return None


def tracked_participants(state, match):
    result = {}
    parts = match.get("info", {}).get("participants", [])
    for riot_id, info in state.get("tracked", {}).items():
        puuid = info.get("puuid")
        choices = [p for p in parts if puuid and p.get("puuid") == puuid]
        if not choices:
            game, _, tag = riot_id.partition("#")
            game = str(info.get("game_name") or game).casefold()
            tag = str(info.get("tag_line") or tag).casefold()
            choices = [p for p in parts if str(p.get("gameName") or "").casefold() == game and str(p.get("tagLine") or "").casefold() == tag]
        if len(choices) == 1 and choices[0].get("teamId") in {100, 200}:
            result[riot_id] = choices[0]
    return result


def history_row(state, riot_id, match_id):
    return next((r for r in state.get("history", {}).get(riot_id, []) if str(r.get("match_id")) == str(match_id)), None)


class MatchProcessor:
    def __init__(self, state, *, save, history_builder, view_factory, on_history=None,
                 settle=None, settle_record=None, market_selector=None, now=None, max_age_hours=6, lp_wait_seconds=120):
        self.state = state
        self.save = save
        self.history_builder = history_builder
        self.view_factory = view_factory
        self.on_history = on_history
        self.settle = settle
        self.settle_record = settle_record
        self.market_selector = market_selector
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_age_hours = max_age_hours
        self.lp_wait_seconds = lp_wait_seconds

    @property
    def receipts(self):
        return self.state.setdefault("match_recaps", {})

    def stage(self, match, ranks, baselines, counts):
        info = match.get("info", {})
        match_id = str(match.get("metadata", {}).get("matchId") or "")
        created = parsed(info.get("gameCreation"))
        if info.get("queueId") != 420 or not match_id or not created:
            return []
        participants = info.get("participants", [])
        identities = {(str(p.get("gameName") or "").casefold(), str(p.get("tagLine") or "").casefold()) for p in participants}
        if (len(participants) != 10 or len(identities) != 10 or ("", "") in identities
                or Counter(p.get("teamId") for p in participants) != {100: 5, 200: 5}):
            print(f"[recap] incomplete participant roster for {match_id}; retrying")
            return []
        groups = defaultdict(list)
        for riot_id, participant in tracked_participants(self.state, match).items():
            groups[participant["teamId"]].append((riot_id, participant))
        records = []
        added = []
        for team_id, pair in groups.items():
            key = f"{match_id}:{team_id}"
            if key in self.receipts:
                records.append(self.receipts[key])
                continue
            results = {outcome(p, int(info.get("gameDuration") or 0)) for _, p in pair}
            if None in results or len(results) != 1:
                print(f"[recap] incomplete or conflicting team result for {key}; retrying")
                continue
            result = results.pop()
            seconds = int(info.get("gameDuration") or 0)
            if result == "DRAW" and seconds >= 120:
                movements = set()
                for rid, _ in pair:
                    rank = ranks.get(rid) or {}
                    before, current = lp_value(baselines.get(rid)), lp_value(rank.get("total_lp"))
                    prior_time = parsed(self.state["tracked"][rid].get("last_match_created_at"))
                    if (counts.get(rid, 1) == 1 and rank.get("ready_for_match")
                            and before is not None and current is not None and current != before
                            and (not prior_time or created >= prior_time)):
                        movements.add("WIN" if current > before else "LOSS")
                if len(movements) == 1:
                    result = movements.pop()
            roster = []
            legacy = None
            for riot_id, p in sorted(pair, key=lambda x: x[0].casefold()):
                rank = ranks.get(riot_id) or {}
                before = lp_value(baselines.get(riot_id))
                tracked = self.state["tracked"][riot_id]
                prior = history_row(self.state, riot_id, tracked.get("last_match_id")) or {}
                prior_time = parsed(tracked.get("last_match_created_at") or prior.get("match_created_at") or prior.get("recorded_at"))
                eligible = (counts.get(riot_id, 1) == 1 and before is not None
                    and (not prior_time or created >= prior_time or tracked.get("last_match_id") == match_id))
                current = lp_value(rank.get("total_lp"))
                ready = bool(rank.get("ready_for_match"))
                delta = current - before if eligible and ready and current is not None else None
                if delta == 0 and result != "DRAW":
                    delta = None
                if result == "DRAW" and seconds >= 120 and delta == 0:
                    delta = None
                if delta is not None and result in {"WIN", "LOSS"} and ((result == "WIN" and delta < 0) or (result == "LOSS" and delta > 0)):
                    delta = None
                row = history_row(self.state, riot_id, match_id)
                member = {"riot_id": riot_id, "puuid": p.get("puuid"), "participant": deepcopy(p),
                    "tier": rank.get("tier"), "rank": rank.get("rank"), "lp": rank.get("lp"),
                    "old_lp": before if eligible else None,
                    "total_lp": current if delta is not None else None, "lp_delta": delta,
                    "lp_status": "known" if delta is not None else "pending" if eligible else "unavailable",
                    "can_reconcile": eligible and not (row or {}).get("recovered")}
                if row:
                    member.update(old_lp=row.get("lp_before"), total_lp=row.get("lp_total"),
                                  lp_delta=lp_value(row.get("lp_change")),
                                  lp_status=row.get("lp_status", "known" if lp_value(row.get("lp_change")) is not None else "unavailable"))
                    if row.get("recap_message_id"):
                        legacy = row
                else:
                    row = self.history_builder(match, p, riot_id, result, delta or 0, member["old_lp"], member["total_lp"])
                    row.update(lp_change=f"{delta:+d}" if delta is not None else None,
                        lp_status=member["lp_status"], reconciled=delta is not None,
                        match_created_at=info["gameCreation"], team_id=team_id, recap_key=key)
                    self.state.setdefault("history", {}).setdefault(riot_id, []).append(row)
                    self.state["history"][riot_id].sort(key=lambda r: str(r.get("match_created_at") or r.get("date") or ""))
                    added.append((riot_id, row))
                row.setdefault("team_id", team_id)
                row["recap_key"] = key
                roster.append(member)
                tracked = self.state["tracked"][riot_id]
                if p.get("puuid"):
                    tracked["puuid"] = p["puuid"]
                previous = history_row(self.state, riot_id, tracked.get("last_match_id"))
                previous_time = parsed(tracked.get("last_match_created_at") or (previous or {}).get("match_created_at") or (previous or {}).get("recorded_at"))
                if not previous_time or created >= previous_time:
                    tracked["last_match_id"] = match_id
                    tracked["last_match_created_at"] = info["gameCreation"]
                    if delta is not None and current is not None:
                        tracked["last_known_lp"] = current
                        self.state.setdefault("daily_lp", {}).setdefault(riot_id, {})[self.now().astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()] = current
                if self.on_history is None:
                    member["story_headline"] = row.get("story_headline")
            record = {"key": key, "match": deepcopy(match), "match_id": match_id, "team_id": team_id,
                "outcome": result, "members": roster, "status": "pending", "settled": False,
                "first_seen_at": self.now().isoformat(),
                "lp_deadline": (self.now() + timedelta(seconds=self.lp_wait_seconds)).isoformat()}
            if self.market_selector:
                record["market_ids"] = self.market_selector(match, roster)
            if (self.now() - created).total_seconds() > self.max_age_hours * 3600:
                record["status"] = "skipped"
            if legacy:
                record.update(status="legacy", channel_id=legacy.get("recap_channel_id"),
                    message_id=legacy.get("recap_message_id"), jump_url=legacy.get("recap_jump_url"), settled=True)
                self._link(record)
            self.receipts[key] = record
            records.append(record)
        # Persist every participant and the pending delivery together before side effects.
        if records:
            self.save(self.state)
        for riot_id, row in added:
            if self.on_history:
                self.on_history(riot_id, row)
            record = self.receipts.get(row.get("recap_key"))
            if record:
                member = next(m for m in record["members"] if m["riot_id"] == riot_id)
                member["story_headline"] = row.get("story_headline")
                member["spotlights"] = row.get("spotlights", [])
        if added:
            self.save(self.state)
        return records

    def _link(self, record):
        for member in record["members"]:
            row = history_row(self.state, member["riot_id"], record["match_id"])
            if row:
                row.update(recap_channel_id=str(record.get("channel_id") or ""),
                    recap_message_id=str(record.get("message_id") or ""), recap_jump_url=record.get("jump_url"))

    async def refresh_lp(self, fetch_rank):
        for record in list(self.receipts.values()):
            if record["status"] not in {"pending", "sending", "sent"} or not record.get("match"):
                continue
            created = parsed(record["match"]["info"].get("gameCreation"))
            if not created or (self.now() - created).total_seconds() > self.max_age_hours * 3600:
                continue
            for member in record["members"]:
                if member["lp_status"] == "known" or not member.get("can_reconcile"):
                    continue
                # Once another match is recorded, current LP cannot be attributed to this one.
                if self.state["tracked"].get(member["riot_id"], {}).get("last_match_id") != record["match_id"]:
                    member.update(lp_status="unavailable", can_reconcile=False)
                    self._update_lp_row(record, member)
                    continue
                rank = await fetch_rank(member["riot_id"], record["match_id"])
                if not rank:
                    continue
                member.update({k: rank.get(k) for k in ("tier", "rank", "lp")})
                current = lp_value(rank.get("total_lp"))
                before = lp_value(member.get("old_lp"))
                if current is None or before is None or not rank.get("ready_for_match"):
                    continue
                delta = current - before
                seconds = int(record["match"]["info"].get("gameDuration") or 0)
                if record["outcome"] == "DRAW" and seconds >= 120 and delta:
                    record["outcome"] = "WIN" if delta > 0 else "LOSS"
                    if record["status"] == "sent":
                        record["dirty"] = True
                    for teammate in record["members"]:
                        row = history_row(self.state, teammate["riot_id"], record["match_id"])
                        if row:
                            row["result"] = record["outcome"]
                valid = (record["outcome"] == "DRAW" and seconds < 120
                    or record["outcome"] == "WIN" and delta > 0 or record["outcome"] == "LOSS" and delta < 0)
                if valid:
                    member.update(total_lp=current, lp_delta=delta, lp_status="known")
                    self._update_lp_row(record, member)
                    tracked = self.state["tracked"][member["riot_id"]]
                    tracked["last_known_lp"] = current
                    self.state.setdefault("daily_lp", {}).setdefault(member["riot_id"], {})[self.now().astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()] = current
            self.save(self.state)

    def _update_lp_row(self, record, member):
        row = history_row(self.state, member["riot_id"], record["match_id"])
        if row:
            delta = member.get("lp_delta")
            row.update(lp_change=f"{delta:+d}" if delta is not None else None,
                       lp_before=member.get("old_lp"), lp_total=member.get("total_lp"),
                       lp_status=member["lp_status"], reconciled=member["lp_status"] == "known")
            if member["lp_status"] == "known" and self.on_history:
                self.on_history(member["riot_id"], row)
        if record["status"] == "sent":
            record["dirty"] = True

    async def recover_message(self, destination, record):
        if record.get("message_id"):
            return await destination.fetch_message(int(record["message_id"]))
        since = parsed(record.get("first_attempt_at"))
        if not since:
            return None
        marker = f"recap:{recap_digest(record['key'])}:"
        async for message in destination.history(after=since - timedelta(seconds=5), oldest_first=True, limit=None):
            if getattr(message.author, "id", None) != getattr(getattr(destination, "_state", None), "self_id", None):
                continue
            ids = [getattr(child, "custom_id", "") or "" for row in message.components for child in getattr(row, "children", [])]
            if any(cid.startswith(marker) for cid in ids) or str(getattr(message, "nonce", "")) == recap_digest(record["key"]):
                return message
        return None

    async def flush(self, destination):
        for record in list(self.receipts.values()):
            if not record.get("match") or record["status"] in {"legacy", "missing"}:
                continue
            if record["status"] == "sent" and not record.get("dirty"):
                continue
            if (record["outcome"] == "DRAW" and record["match"]["info"].get("gameDuration", 0) >= 120
                    and any(m["lp_status"] == "pending" for m in record["members"])
                    and self.now() < parsed(record["lp_deadline"])):
                continue
            if not record.get("settled"):
                if self.settle_record:
                    await self.settle_record(record)
                elif self.settle:
                    for member in record["members"]:
                        await self.settle(member["riot_id"], "VOID" if record["outcome"] == "DRAW" else record["outcome"])
                record["settled"] = True
                self.save(self.state)
            created = parsed(record["match"]["info"].get("gameCreation"))
            if record["status"] != "sent" and created and (self.now() - created).total_seconds() > self.max_age_hours * 3600:
                if record["status"] == "sending":
                    try:
                        recovered = await self.recover_message(destination, record) if destination else None
                        if recovered:
                            record.update(status="sent", dirty=False, channel_id=str(recovered.channel.id),
                                message_id=str(recovered.id), jump_url=getattr(recovered, "jump_url", None))
                            self._link(record)
                        elif destination:
                            record["status"] = "skipped"
                    except Exception as exc:
                        record["last_error"] = f"Recovery deferred: {type(exc).__name__}"
                else:
                    record["status"] = "skipped"
                self.save(self.state)
                continue
            if record["status"] == "skipped":
                continue
            pending = [m for m in record["members"] if m["lp_status"] == "pending"]
            if pending and self.now() < parsed(record["lp_deadline"]):
                continue
            for member in pending:
                member["lp_status"] = "unavailable"
                self._update_lp_row(record, member)
            if destination is None:
                continue
            try:
                message = None
                if record["status"] in {"sending", "sent"}:
                    message = await self.recover_message(destination, record)
                view = await self.view_factory(record)
                payload = view.get_overview_kwargs()
                if message:
                    payload["attachments"] = [payload.pop("file")] if "file" in payload else []
                    await message.edit(**payload)
                else:
                    record.update(status="sending", first_attempt_at=record.get("first_attempt_at") or self.now().isoformat())
                    self.save(self.state)
                    message = await destination.send(**payload, nonce=recap_digest(record["key"]))
                view.message = message
                record.update(status="sent", dirty=False, channel_id=str(message.channel.id),
                              message_id=str(message.id), jump_url=getattr(message, "jump_url", None))
                record.pop("last_error", None)
                self._link(record)
                self.save(self.state)
            except Exception as exc:
                # A failed fetch must not trigger another send after uncertain delivery.
                record["last_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
                self.save(self.state)
                print(f"[recap] delivery deferred for {record['key']}: {record['last_error']}")
        retired = False
        for record in self.receipts.values():
            if record.get("match") and record["status"] in {"sent", "skipped", "legacy"}:
                created = parsed(record["match"]["info"].get("gameCreation"))
                if created and (self.now() - created).total_seconds() > 86400:
                    retired = True
                    record.pop("match", None)
                    for member in record["members"]:
                        member.pop("participant", None)

        if retired:
            self.save(self.state)
