"""Selected-conversation queries, shard-local sender identities and keyset paging."""
import hashlib
import json
import re
from datetime import datetime, timezone

from wechat_account_probe.models import ProbeError
from wechat_conversations.reader import IDENTITY_ERRORS, text_value
from .cache import DatabaseCache
from .content import MAX_CONTENT, parse_content

SHARD = re.compile(r"(?:biz_)?message_[0-9]+\.db\Z")
COLUMNS = {"local_id", "server_id", "local_type", "sort_seq", "real_sender_id", "create_time",
           "message_content", "compress_content", "packed_info_data"}
MAX_PAGE_BYTES = 16 * 1024 * 1024


def row_key(row):
    return row["sort_seq"], row["create_time"], row["shard"], row["local_id"]


class MessageReader:
    def __init__(self, session):
        self.session = session
        if getattr(session, "cache", None) is None:
            session.cache = DatabaseCache(session)
        self.cache = session.cache

    def close(self):
        self.cache.close()

    def changes(self, request, deadline):
        from .incremental import changes
        return changes(self, request, deadline)

    def shards(self):
        root = self.session.directory / "db_storage" / "message"
        paths = sorted(p for p in root.glob("*.db") if SHARD.fullmatch(p.name))
        if len(paths) > 64:
            raise ProbeError("message_shard_count_limit")
        if not paths:
            raise ProbeError("message_database_missing")
        return ["message/" + p.name for p in paths]

    @staticmethod
    def table(source):
        return "Msg_" + hashlib.md5(source.encode("utf-8")).hexdigest()

    def rows(self, db, table, shard, limit, boundary, direction, upper, lower_id=None, upper_id=None):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return []
        columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
        if not COLUMNS <= columns:
            raise ProbeError("message_schema_unsupported")
        names = {r[1] for r in db.execute('PRAGMA table_info("Name2Id")')}
        if "user_name" not in names:
            raise ProbeError("message_sender_schema_unsupported")
        params, where = [], []
        if lower_id is not None:
            where.append("m.local_id > ? AND m.local_id <= ?")
            params.extend([lower_id, upper_id])
        for anchor, operator in ((boundary, ">" if direction == "newer" else "<"), (upper, "<=")):
            if anchor is not None:
                where.append(f"(m.sort_seq,m.create_time,?,m.local_id) {operator} (?,?,?,?)")
                params.extend([shard, *anchor])
        clause = " WHERE " + " AND ".join(where) if where else ""
        order = "ASC" if direction == "newer" else "DESC"
        safe = ",".join(f"CASE WHEN length(m.{c})<={MAX_CONTENT} THEN m.{c} ELSE NULL END AS {c}"
                        for c in ("message_content", "compress_content", "packed_info_data"))
        query = (f"SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.real_sender_id,m.create_time,"
                 f"{safe}, n.user_name AS sender_username, "
                 f"(COALESCE(length(m.message_content),0)>{MAX_CONTENT} OR COALESCE(length(m.compress_content),0)>{MAX_CONTENT} OR COALESCE(length(m.packed_info_data),0)>{MAX_CONTENT}) AS oversized "
                 f'FROM "{table}" m LEFT JOIN Name2Id n ON m.real_sender_id=n.rowid{clause} '
                 f"ORDER BY m.sort_seq {order},m.create_time {order},m.local_id {order} LIMIT ?")
        for row in db.execute(query, (*params, limit+1)):
            item = dict(row, shard=shard)
            if any(not isinstance(item[c], int) for c in ("local_id", "sort_seq", "create_time", "local_type")):
                raise ProbeError("message_order_invalid")
            yield item

    def contacts(self, usernames, issues):
        result = {}
        if not usernames:
            return result
        try:
            with self.cache.database("contact/contact.db") as db:
                for table in ("contact", "stranger"):
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                    if not {"username", "alias", "nick_name", "remark"} <= columns:
                        continue
                    markers = ",".join("?" for _ in usernames)
                    for row in db.execute(f'SELECT username,alias,nick_name,remark FROM "{table}" WHERE username IN ({markers})', usernames):
                        result.setdefault(row["username"], dict(row))
        except (ProbeError, FileNotFoundError) as exc:
            if str(exc) in IDENTITY_ERRORS:
                raise
            issues.add("message_contacts_unavailable")
        return result

    def read(self, request, deadline):
        self.session.bind(deadline)
        source, limit = request["source_id"], request["page_size"]
        direction = request.get("direction", "latest")
        boundary, upper = request.get("boundary"), request.get("upper")
        watermarks, batch = request.get("watermarks"), request.get("batch_watermarks")
        targets = {}
        rows, issues, succeeded = [], set(), 0
        retry_required = False
        more = False
        for shard in self.shards():
            try:
                with self.cache.database(shard) as db:
                    table = self.table(source)
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                    target = (db.execute(f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"').fetchone()[0] if exists else 0)
                    targets[shard] = batch.get(shard, target) if batch is not None else target
                    lower_id = watermarks.get(shard, 0) if direction == "newer" and watermarks is not None else None
                    for row in self.rows(db, table, shard, limit, boundary, direction, upper, lower_id, targets[shard]):
                        row["generation"] = self.cache.generation(shard) if isinstance(self.cache, DatabaseCache) else "synthetic"
                        row["_bytes"] = sum(len(v.encode("utf-8")) if isinstance(v, str) else len(v) if isinstance(v, bytes) else 8 for v in row.values())
                        rows.append(row)
                        rows.sort(key=row_key, reverse=direction != "newer")
                        while len(rows) > limit+1 or (len(rows) > 1 and sum(r["_bytes"] for r in rows) > MAX_PAGE_BYTES):
                            rows.pop()
                            more = True
                succeeded += 1
            except (ProbeError, FileNotFoundError) as exc:
                targets.pop(shard, None)
                if str(exc) in IDENTITY_ERRORS:
                    raise
                retry_required = True
                issues.add(str(exc) if isinstance(exc, ProbeError) else "message_shard_missing")
        if not succeeded:
            raise ProbeError(next(iter(sorted(issues)), "message_read_failed"))
        rows.sort(key=row_key, reverse=direction != "newer")
        more = more or len(rows) > limit
        rows = rows[:limit]
        items, more = self.project(rows, issues, more)
        self.session.bind(deadline)
        return dict(items=items, has_more=more, issues=sorted(issues),
                    retry_required=retry_required, watermarks=targets,
                    status="partial" if issues or any(i["issues"] for i in items) else "complete")

    def project(self, rows, issues, more=False):
        usernames = sorted({r["sender_username"] for r in rows if text_value(r["sender_username"], 256)})
        contacts = self.contacts(usernames, issues)
        items, output_bytes = [], 0
        for row in rows:
            username = text_value(row["sender_username"], 256)
            contact = contacts.get(username, {})
            nickname = text_value(contact.get("nick_name"), 512) or None
            alias = text_value(contact.get("alias"), 256) or None
            remark = text_value(contact.get("remark"), 512) or None
            kind, content, errors = parse_content(row, username)
            if row.get("oversized"):
                errors.append("message_content_limit")
            if not username and kind != "system":
                errors.append("sender_identity_unavailable")
            if not nickname and kind != "system":
                errors.append("sender_nickname_unavailable")
            when = None
            try:
                if row["create_time"] > 0:
                    when = datetime.fromtimestamp(row["create_time"], timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                errors.append("message_time_invalid")
            is_self = username == self.session.wxid if username and kind != "system" else None
            item = dict(_key=list(row_key(row)),
                sort_key=f'{row["sort_seq"]+2**63:020d}.{row["create_time"]+2**63:020d}.{row["shard"].encode().hex()}.{row["local_id"]+2**63:020d}',
                _ref=dict(shard=row["shard"], local_id=row["local_id"], server_id=str(row["server_id"]), generation=row.get("generation", "legacy")),
                sent_at=when, type=kind, content=content, is_self=is_self,
                sender=dict(wxid=username, wechat_id=alias, nickname=nickname, remark=remark,
                            status="complete" if username and nickname else "partial" if username else "unknown"),
                issues=errors)
            size = len(json.dumps(item, ensure_ascii=True))
            if items and output_bytes + size > MAX_PAGE_BYTES:
                more = True
                break
            items.append(item)
            output_bytes += size
        items.sort(key=lambda item: item["_key"])
        return items, more

    def image(self, request, deadline):
        from .images import read_image
        from .stickers import read_sticker
        self.session.bind(deadline)
        reference = request["reference"]
        shard = reference["shard"]
        if not SHARD.fullmatch(shard.removeprefix("message/")) or not shard.startswith("message/"):
            raise ProbeError("message_reference_invalid")
        with self.cache.database(shard) as db:
            if isinstance(self.cache, DatabaseCache) and (reference.get("generation") or "legacy").split(":")[0] not in ("legacy", self.cache.generation(shard)):
                raise ProbeError("message_source_changed")
            table = self.table(request["source_id"])
            safe = ",".join(f"CASE WHEN length(m.{c})<={MAX_CONTENT} THEN m.{c} ELSE NULL END AS {c}" for c in ("message_content", "compress_content", "packed_info_data"))
            row = db.execute(f'SELECT m.local_type,{safe},m.create_time,m.server_id,n.user_name AS sender_username '
                             f'FROM "{table}" m LEFT JOIN Name2Id n ON m.real_sender_id=n.rowid WHERE m.local_id=?', (reference["local_id"],)).fetchone()
            if not row or str(row["server_id"]) != reference["server_id"] or row["local_type"] & 0xffffffff not in (3, 47, 11000):
                raise ProbeError("message_not_found")
            row = dict(row)
        # Release the message snapshot before the optional emoticon lookup/network wait.
        result = read_image(self, request["source_id"], row) if row["local_type"] & 0xffffffff == 3 else read_sticker(self, row)
        self.session.bind(deadline)
        return result
