"""File-stamp detection and bounded shard-local ingestion, without window access."""
import hashlib
import json
import re
import sqlite3
from .cache import fingerprint
from .content import MAX_CONTENT
from .reader import COLUMNS
from wechat_account_probe.models import ProbeError
from wechat_conversations.reader import IDENTITY_ERRORS

TABLE = re.compile(r"Msg_[a-f0-9]{32}\Z")


def changes(reader, request, deadline):
    session = reader.session
    if session.directory is None:
        session.bind(deadline)
    session.check_identity(deadline)
    root = session.directory / "db_storage"
    states = {(s["shard"], s["table_name"]): s for s in request["states"]}
    lane = request.get("lane", "all")
    if lane not in ("all", "realtime", "background"):
        raise ProbeError("worker_protocol_invalid")
    target_sources = set(request.get("source_ids", []))
    target_tables = {reader.table(source) for source in target_sources}
    scope = lane, tuple(sorted(target_sources))
    if getattr(reader, "lane_scope", None) != scope:
        reader.lane_scope, reader.lane_checked, reader.table_cursors = scope, {}, {}

    def owns(table):
        return (table in target_tables if lane == "realtime" else
                table not in target_tables if lane == "background" else True)

    def signature(relative):
        path = root / relative
        return json.loads(json.dumps([fingerprint(path), fingerprint(path.with_name(path.name+"-wal"))]))

    def previous(state):
        return json.loads(state["signature"]) if state and state.get("signature") else None

    def checkpoint(shard, table, generation, source, last, stamp, **extra):
        return dict(shard=shard, table_name=table, generation=generation, source_id=source,
                    last_id=last, signature=stamp, **extra)

    shards = reader.shards()
    initial = states.get(("@monitor", ""))
    initial_shards = previous(initial) if initial else shards
    if lane == "realtime" and not initial:
        return dict(groups=[], issues=[], unchanged=True, has_more=False)
    groups, issues, conversations = [], [], None
    session_stamp = [signature(p) for p in ("session/session.db", "contact/contact.db", "head_image/head_image.db")] if lane != "realtime" else None
    sessions_changed = lane != "realtime" and previous(states.get(("@sessions", ""))) != session_stamp
    unresolved_shards = {s["shard"] for s in states.values() if s["table_name"] and not s.get("source_id")}
    candidates = []
    for shard in shards:
        stamp = signature(shard)
        manifest = states.get((shard, ""))
        # The background owner establishes the first baseline before fast reads.
        if lane == "realtime" and shard in initial_shards and manifest is None:
            continue
        if previous(manifest) == stamp and not (sessions_changed and shard in unresolved_shards):
            continue
        checked = reader.lane_checked.get(shard)
        if checked and checked["stamp"] == stamp:
            def settled(table):
                old = states.get((shard, table))
                return old and old.get("source_id") and previous(old) == stamp and old.get("upper_id") is None
            if all(settled(t) for t in checked["tables"] if owns(t) or (shard in initial_shards and manifest is None)):
                # Only trust committed table checkpoints, never an uncommitted worker result.
                if lane != "realtime" and all(settled(t) for t in checked["tables"]):
                    groups.append(checkpoint(shard, "", checked["generation"], None, 0, stamp))
                continue
        candidates.append(shard)
    if not sessions_changed and not candidates:
        return dict(groups=groups, issues=[], unchanged=not groups, has_more=False)
    session.bind(deadline)
    if not initial:
        groups.append(checkpoint("@monitor", "", "", None, 0, initial_shards))
    if sessions_changed:
        conversations = session.read(deadline)
        # Retry partial metadata without replacing its last verified fingerprint.
        groups.append(checkpoint("@sessions", "", "", None, 0, session_stamp))
    names = {s["source_id"] for s in states.values() if s.get("source_id")} | target_sources
    if candidates and lane != "realtime":
        # Include raw sessions (also filtered/hidden conversations) and contacts;
        # filtering the UI must not filter monitoring.
        for relative, table, column in (("session/session.db", "SessionTable", "username"),
                                        ("contact/contact.db", "contact", "username")):
            with reader.cache.database(relative) as db:
                for row in db.execute(f'SELECT "{column}" FROM "{table}" LIMIT 100001'):
                    if isinstance(row[0], str) and 0<len(row[0])<=256 and "\0" not in row[0]:
                        names.add(row[0])
                    if len(names) > 100000:
                        raise ProbeError("monitor_contact_limit")
    sources = {reader.table(name): name for name in names}
    # Fair round-robin over changed shards, one shard per bounded worker command.
    cursor = getattr(reader, "next_shard", "")
    candidates.sort(key=lambda s: (s <= cursor, s))
    budget = 100
    has_more = len(candidates) > 1
    for shard in candidates[:1]:
        reader.next_shard = shard
        stamp = signature(shard)
        manifest = states.get((shard, ""))
        bootstrap = shard in initial_shards and manifest is None
        complete = True
        try:
            with reader.cache.database(shard) as db:
                # Name2Id also retains removed/hidden chats absent from contacts and sessions.
                for row in ([] if lane == "realtime" else db.execute("SELECT user_name FROM Name2Id LIMIT 100001")):
                    if isinstance(row[0], str) and 0<len(row[0])<=256 and "\0" not in row[0]:
                        sources[reader.table(row[0])] = row[0]
                if len(sources)>100000:
                    raise ProbeError("monitor_contact_limit")
                entry = reader.cache.entries.get(shard)
                identity = [stamp[0][:2], entry.salt.hex() if entry else "synthetic"]
                base_generation = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if TABLE.fullmatch(r[0])]
                if len(tables) > 20000:
                    raise ProbeError("monitor_table_limit")
                cursor = reader.table_cursors.get(shard, "")
                tables.sort(key=lambda table: (table <= cursor, table))
                for table in tables:
                    if not owns(table) and not bootstrap:
                        old = states.get((shard, table))
                        complete &= bool(old and old.get("source_id") and previous(old) == stamp and old.get("upper_id") is None)
                        continue
                    old = states.get((shard, table))
                    generation = old["generation"] if old and old["generation"].split(":")[0] == base_generation else base_generation
                    if old and old.get("source_id") and old["generation"] == generation and previous(old) == stamp:
                        continue
                    source = sources.get(table)
                    if not source:
                        complete = False
                        issues.append("monitor_source_unresolved")
                        maximum = db.execute(f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"').fetchone()[0]
                        low = old["last_id"] if old else maximum if bootstrap else 0
                        groups.append(checkpoint(shard,table,generation,None,low,stamp,
                            upper_id=maximum if maximum>low else None,baseline_id=old.get("baseline_id",low) if old else low))
                        continue
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                    if not COLUMNS <= columns:
                        complete = False
                        issues.append("message_schema_unsupported")
                        continue
                    maximum = db.execute(f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"').fetchone()[0]
                    reset = bool(old and (old["generation"] != generation or maximum < old["last_id"]))
                    if old and maximum < old["last_id"]:
                        generation = base_generation+":"+hashlib.sha256(json.dumps(stamp).encode()).hexdigest()[:16]
                    if bootstrap and old is None:
                        groups.append(checkpoint(shard, table, generation, source, maximum, stamp, items=[], baseline_id=maximum))
                        continue
                    if budget <= 0:
                        complete = False
                        has_more = True
                        continue
                    reader.table_cursors[shard] = table
                    low = old["last_id"] if old and not reset else 0
                    upper = old.get("upper_id") if old and not reset else None
                    upper = min(upper, maximum) if upper is not None else maximum
                    safe = ",".join(f"CASE WHEN length(m.{c})<={MAX_CONTENT} THEN m.{c} ELSE NULL END AS {c}" for c in ("message_content","compress_content","packed_info_data"))
                    source_columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
                    safe += (f",CASE WHEN length(m.source)<={MAX_CONTENT} THEN m.source ELSE NULL END AS source"
                             if "source" in source_columns else ",NULL AS source")
                    oversized = " OR ".join(f"COALESCE(length(m.{c}),0)>{MAX_CONTENT}" for c in ("message_content","compress_content","packed_info_data"))
                    projection = f"SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.create_time,m.real_sender_id,{safe},n.user_name AS sender_username,({oversized}) AS oversized FROM \"{table}\" m LEFT JOIN Name2Id n ON m.real_sender_id=n.rowid"
                    rows = [dict(r, shard=shard) for r in db.execute(projection+" WHERE m.local_id>? AND m.local_id<=? ORDER BY m.local_id LIMIT ?", (low, upper, budget))]
                    page_issues = set()
                    items, truncated = reader.project(rows, page_issues)
                    for item in items:
                        item["_ref"]["generation"] = generation
                    last = max([low]+[i["_ref"]["local_id"] for i in items])
                    budget -= len(items)
                    # Gaps in local IDs are valid. Empty successful queries exhaust the batch.
                    if not rows:
                        last = upper
                    done = last >= upper and not truncated
                    if done and upper < maximum:
                        # Rows arriving while an earlier bounded batch drained still need a pass.
                        upper, done = maximum, False
                    reconcile_ids = old.get("reconcile_ids", []) if old and not reset else []
                    reconciled = []
                    if done and reconcile_ids:
                        if budget < len(reconcile_ids):
                            done = False
                        else:
                            marks = ",".join("?" for _ in reconcile_ids)
                            recent = [dict(r, shard=shard) for r in db.execute(projection+f" WHERE m.local_id IN ({marks}) ORDER BY m.local_id", reconcile_ids)]
                            reconciled, clipped = reader.project(recent, page_issues)
                            done = not clipped
                            budget -= len(recent)
                            for item in reconciled:
                                item["_ref"]["generation"] = generation
                    groups.append(checkpoint(shard, table, generation, source, last, stamp if done else None,
                        upper_id=None if done else upper, items=items+reconciled, reset=reset,
                        baseline_id=old.get("baseline_id", 0) if old and not reset else 0))
                    complete &= done
                    has_more |= not done
                    issues.extend(page_issues)
                reader.lane_checked[shard] = dict(stamp=stamp, tables=tables, generation=base_generation)
                if lane != "realtime" and complete and signature(shard) == stamp:
                    groups.append(checkpoint(shard, "", base_generation, None, 0, stamp))
        except (ProbeError, FileNotFoundError, sqlite3.Error) as exc:
            if str(exc) in IDENTITY_ERRORS:
                raise
            issues.append(str(exc) if isinstance(exc, ProbeError) else "message_query_failed" if isinstance(exc, sqlite3.Error) else "message_shard_missing")
    session.bind(deadline)
    return dict(groups=groups, conversations=conversations, issues=sorted(set(issues)), unchanged=False, has_more=has_more)
