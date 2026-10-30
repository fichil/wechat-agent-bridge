"""Persistent group eligibility and bounded, resumable conversation rounds.

All helpers run on Store's SQL owner thread, inside the caller's transaction.
Message identity counts once; a round owns a fixed snapshot, never later arrivals.
"""
import json
import time
from uuid import uuid4

from .conversations import ConversationError


GROUP_SCHEMA = (
    "ALTER TABLE conversation_preferences ADD scan_interval_seconds INTEGER NOT NULL DEFAULT 2",
    "ALTER TABLE conversation_preferences ADD group_proactive_message_threshold INTEGER NOT NULL DEFAULT 0",
    "CREATE TABLE group_message_eligibility(message_id TEXT PRIMARY KEY REFERENCES messages ON DELETE CASCADE, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, epoch INTEGER NOT NULL, observed_at REAL NOT NULL, consumed_run_id TEXT)",
    "CREATE INDEX group_pending ON group_message_eligibility(account_id,conversation_id,epoch,consumed_run_id)",
    "CREATE TABLE group_rounds(round_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, epoch INTEGER NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE UNIQUE INDEX group_one_round ON group_rounds(account_id,conversation_id) WHERE status='active'",
    "CREATE TABLE group_round_messages(round_id TEXT NOT NULL REFERENCES group_rounds, message_id TEXT NOT NULL REFERENCES messages, ordinal INTEGER NOT NULL, fingerprint TEXT NOT NULL, media_digest TEXT, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(round_id,message_id))",
)


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class GroupRoundStoreMixin:
    def _is_group(self, aid, cid):
        return self._conversation(aid, cid)["source_id"].endswith("@chatroom")

    def _group_round(self, aid, cid):
        row = self.db.execute("SELECT * FROM group_rounds WHERE account_id=? AND conversation_id=? AND status='active'", (aid, cid)).fetchone()
        return {**dict(row), **json.loads(row["payload"])} if row else None

    def _group_note_live(self, aid, cid, item):
        # Called only after live timestamp, enable watermark and identity checks.
        inserted = self.db.execute("INSERT OR IGNORE INTO group_message_eligibility VALUES(?,?,?,?,?,NULL)",
            (item["message_id"], aid, cid, self._ai_session(aid, cid)["epoch"], time.time())).rowcount
        if inserted:
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
            self.db.execute("UPDATE ai_message_state SET status='pending',memorized_at=NULL WHERE message_id=? AND status='memorized'", (item["message_id"],))
        return bool(inserted)

    def _group_ready(self, aid, cid):
        vision = self._ai_vision(aid, cid)
        ready = []
        for row in self.db.execute("SELECT m.* FROM group_message_eligibility e JOIN messages m USING(message_id) WHERE e.account_id=? AND e.conversation_id=? AND e.epoch=? AND e.consumed_run_id IS NULL ORDER BY m.sort_key,m.message_id", (aid, cid, self._ai_session(aid, cid)["epoch"])):
            if self._ai_item(row, vision)[3]["status"] in ("pending", "failed"):
                ready.append(row["message_id"])
        return ready

    def _group_reason(self, aid, cid):
        active = self._group_round(aid, cid)
        if active:
            return active["reason"]
        vision = self._ai_vision(aid, cid)
        for row in self.db.execute("SELECT m.* FROM reply_triggers t JOIN messages m USING(message_id) WHERE t.account_id=? AND t.conversation_id=? AND t.epoch=? AND t.consumed_run_id IS NULL", (aid, cid, self._ai_session(aid, cid)["epoch"])):
            if self._ai_item(row, vision)[3]["status"] in ("pending", "failed"):
                return "mention"
        threshold = self._preferences(aid, cid)["group_proactive_message_threshold"]
        if threshold and len(self._group_ready(aid, cid)) >= threshold:
            return "group_threshold"
        return None

    def _group_debouncing(self, aid, cid):
        if self._group_round(aid, cid):
            return False
        # A fixed one-second window begins when the threshold is reached; a busy
        # group cannot postpone an already eligible round indefinitely.
        threshold = self._preferences(aid, cid)["group_proactive_message_threshold"]
        if not threshold:
            return False
        ready = self._group_ready(aid, cid)
        if len(ready) < threshold:
            return False
        ready = set(ready)
        count = 0
        for row in self.db.execute("SELECT message_id,observed_at FROM group_message_eligibility WHERE account_id=? AND conversation_id=? AND consumed_run_id IS NULL ORDER BY observed_at,message_id", (aid,cid)):
            if row[0] in ready:
                count += 1
                if count == threshold:
                    return row[1] > time.time() - 1
        return False

    def _group_prepare_round(self, aid, cid):
        active = self._group_round(aid, cid)
        if active:
            return active
        reason = self._group_reason(aid, cid)
        if not reason:
            return None
        vision = self._ai_vision(aid, cid)
        selected = []
        for row in self.db.execute("SELECT * FROM messages WHERE account_id=? AND conversation_id=? ORDER BY sort_key,message_id", (aid, cid)):
            value, fingerprint, digest, state = self._ai_item(row, vision)
            if state["status"] in ("pending", "failed"):
                selected.append((value, fingerprint, digest))
        if not selected:
            return None
        rid = uuid4().hex
        targets = self._reply_ids(aid, cid, {item[0]["message_id"] for item in selected})
        payload = dict(reply_trigger_ids=targets, trigger_messages=[v for v, _, _ in selected if v["message_id"] in targets],
                       message_count=len(selected), batch_count=0)
        self.db.execute("INSERT INTO group_rounds VALUES(?,?,?,?,?,'active',?)", (rid, aid, cid, self._ai_session(aid, cid)["epoch"], reason, packed(payload)))
        self.db.executemany("INSERT INTO group_round_messages VALUES(?,?,?,?,?,?,0)",
            ((rid, value["message_id"], index, fingerprint, digest, packed(value)) for index, (value, fingerprint, digest) in enumerate(selected)))
        return self._group_round(aid, cid)

    def _group_batch(self, group, include_media=True):
        selected, images = [], 0
        rows = self.db.execute("SELECT * FROM group_round_messages WHERE round_id=? AND done=0 ORDER BY ordinal", (group["round_id"],)).fetchall()
        for row in rows:
            value = json.loads(row["payload"])
            is_image = value["type"] in ("image", "sticker")
            if len(selected) >= 50 or (is_image and images >= 4):
                break
            if is_image and include_media:
                media = self.db.execute("SELECT data,mime_type FROM media_cache WHERE cache_key=? AND digest=?", ("image:" + row["message_id"], row["media_digest"])).fetchone()
                if not media:
                    raise ConversationError("ai_snapshot_media_unavailable", "本轮图片缓存已变化，请恢复图片后重试，已有进度保留。", 409)
                value["content"].update(_image_bytes=bytes(media["data"]), _image_mime=media["mime_type"])
            selected.append((value, row["fingerprint"], row["media_digest"], row["ordinal"]))
            images += int(is_image)
        return selected, len(rows) > len(selected)

    def _group_run_allowed(self, run):
        if not run.get("round_id"):
            return True
        group = self._group_round(run["account_id"], run["conversation_id"])
        return bool(group and group["round_id"] == run["round_id"] and group["epoch"] == run["epoch"] and
                    (group["reason"] != "group_threshold" or self._preferences(run["account_id"], run["conversation_id"])["group_proactive_message_threshold"] > 0))

    def _group_run_fields(self, run, group, final):
        done = self.db.execute("SELECT count(*) FROM group_round_messages WHERE round_id=? AND done=1", (group["round_id"],)).fetchone()[0]
        run.update(round_id=group["round_id"], trigger_reason=group["reason"], batch_index=group["batch_count"] + 1,
                   round_message_count=group["message_count"], round_completed_count=done, round_final=final,
                   reply_trigger_ids=group["reply_trigger_ids"], trigger_messages=group["trigger_messages"])

    def _group_finish_batch(self, run):
        # Successfully summarized raw messages count once, including explicit
        # manual processing. Corrections can update memory but never add a count.
        self.db.execute("UPDATE group_message_eligibility SET consumed_run_id=? WHERE consumed_run_id IS NULL AND message_id IN (SELECT message_id FROM ai_run_messages WHERE run_id=?)", (run["run_id"], run["run_id"]))
        if not run.get("round_id"):
            return
        group = self._group_round(run["account_id"], run["conversation_id"])
        self.db.execute("UPDATE group_round_messages SET done=1 WHERE round_id=? AND message_id IN (SELECT message_id FROM ai_run_messages WHERE run_id=?)", (group["round_id"], run["run_id"]))
        payload = json.loads(self.db.execute("SELECT payload FROM group_rounds WHERE round_id=?", (group["round_id"],)).fetchone()[0])
        payload["batch_count"] += 1
        run["round_completed_count"] = self.db.execute("SELECT count(*) FROM group_round_messages WHERE round_id=? AND done=1", (group["round_id"],)).fetchone()[0]
        self.db.execute("UPDATE group_rounds SET status=?,payload=? WHERE round_id=?", ("completed" if run["round_final"] else "active", packed(payload), group["round_id"]))
        self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (run["account_id"], run["conversation_id"]))
        run["chose_silence"] = bool(run["round_final"] and not run.get("reply", "").strip())

    def _group_preferences_changed(self, previous, current):
        aid, cid = current["account_id"], current["conversation_id"]
        if previous["group_proactive_message_threshold"] != current["group_proactive_message_threshold"]:
            self.db.execute("UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?", (aid, cid))
        disabled = previous["ai_enabled"] and not current["ai_enabled"]
        stopped = previous["group_proactive_message_threshold"] > 0 and current["group_proactive_message_threshold"] == 0
        if not (disabled or stopped):
            return
        group = self._group_round(aid, cid)
        if group and (disabled or group["reason"] == "group_threshold"):
            self.db.execute("UPDATE group_rounds SET status='cancelled' WHERE round_id=?", (group["round_id"],))
            for row in self.db.execute("SELECT payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status IN ('queued','running')", (aid, cid)).fetchall():
                run = json.loads(row[0])
                if run.get("round_id") != group["round_id"]:
                    continue
                from .ai_execution_storage import stamp
                run.update(status="cancelled", completed_at=stamp(), message="群聊主动调用已关闭，本轮后续处理与主动发送已取消。")
                self._ai_write_run(run)
                self.db.execute("UPDATE ai_message_state SET status='pending' WHERE run_id=? AND status='processing'", (run["run_id"],))
        if stopped:
            self.db.execute("UPDATE deliveries SET status='cancelled',code='group_proactive_disabled' WHERE account_id=? AND conversation_id=? AND status IN ('queued','failed') AND run_id IN (SELECT run_id FROM ai_runs WHERE json_extract(payload,'$.trigger_reason')='group_threshold')", (aid, cid))
