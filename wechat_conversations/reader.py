"""Account-bound, memory-only SQLCipher reader for the verified 4.1.15.56 schema.

SessionTable query reference: wechatauto-replica/wechatauto/db.py (Apache-2.0).
No upstream package is imported. Only summary and supporting contact/avatar rows
are queried. The existing exact-self account probe remains unchanged.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
import time

from wechat_account_probe.avatar import encode_avatar, MAX_AVATAR_BYTES
from wechat_account_probe.models import ProbeError

CONTAINERS = {
    "brandservicesessionholder": ("service_accounts", "服务号"),
    "brandsessionholder": ("official_accounts", "公众号"),
    "@placeholder_foldgroup": ("folded_chats", "折叠的聊天"),
}
SPECIAL = {
    "notifymessage": ("service_notifications", "服务通知"),
    "filehelper": ("file_transfer_assistant", "文件传输助手"),
    "gh_3dfda90e39d6": ("wechat_pay", "微信支付"),
    "weixin": ("system", "微信团队"),
}
IDENTITY_ERRORS = {"account_changed_during_read", "process_exited", "process_instance_changed",
                   "process_owner_changed", "process_time_unreadable", "account_not_found",
                   "account_configuration_missing_or_ambiguous", "account_directory_missing_or_ambiguous",
                   "client_version_unsupported", "client_binary_changed", "client_binary_unreadable",
                   "client_binary_invalid", "account_configuration_module_missing"}
MAX_SESSIONS = 20000
MAX_AVATARS_TOTAL = 24 * 1024 * 1024


def classify(username, contact, biz):
    if username in CONTAINERS:
        return CONTAINERS[username][0], True
    if username in SPECIAL:
        return SPECIAL[username][0], False
    if username.endswith("@openim"):
        return "enterprise_contacts", False
    if username.endswith("@chatroom"):
        flag = contact.get("flag")
        if not isinstance(flag, int):
            return "unknown", False
        return ("folded_chats" if flag & 0x10000000 else "group"), False
    if biz.get("type") in (0, 1):
        return ("official_accounts" if biz["type"] == 0 else "service_accounts"), False
    verify = contact.get("verify_flag")
    if not contact or username.startswith("gh_") or not isinstance(verify, int) or verify & 8:
        return "unknown", False
    return "contact", False


def text_value(value, maximum):
    return value if isinstance(value, str) and len(value) <= maximum and "\0" not in value else None


def project_sessions(sessions, contacts, businesses, titles=None):
    """Project the main list, never flatten classified container members."""
    items, issues, member_categories, container_categories = [], set(), set(), set()
    titles = titles or {}
    seen = set()
    for row in sessions:
        username = text_value(row.get("username"), 256)
        if not username or username in seen:
            raise ProbeError("session_identity_invalid")
        seen.add(username)
        if row.get("is_hidden") == 1:
            continue
        if row.get("is_hidden") != 0:
            issues.add("session_visibility_unknown")
            continue
        contact, biz = contacts.get(username, {}), businesses.get(username, {})
        category, container = classify(username, contact, biz)
        if category == "unknown":
            issues.add("conversation_classification_incomplete")
        if container:
            container_categories.add(category)
        elif category in {c[0] for c in CONTAINERS.values()}:
            member_categories.add(category)
            continue
        fixed = CONTAINERS.get(username) or SPECIAL.get(username)
        name = (fixed[1] if fixed else None) or text_value(contact.get("remark"), 512) or \
            text_value(contact.get("nick_name"), 512) or text_value(titles.get(username), 512)
        if not name:
            name = username
            issues.add("conversation_name_unavailable")
        summary = text_value(row.get("summary"), 16384)
        if summary is None and row.get("summary") is not None:
            issues.add("conversation_summary_unavailable")
        timestamp = row.get("last_timestamp")
        last_at = None
        try:
            if isinstance(timestamp, int) and timestamp > 0:
                last_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            elif timestamp not in (0, None):
                raise ValueError
        except (ValueError, OverflowError, OSError):
            issues.add("conversation_time_invalid")
        unread = row.get("unread_count")
        if not isinstance(unread, int) or unread < 0:
            unread = None
            issues.add("conversation_unread_unknown")
        sort = row.get("sort_timestamp")
        if not isinstance(sort, int):
            sort = 0
            issues.add("conversation_order_unknown")
        items.append(dict(source_id=username, name=name, category=category, is_container=container,
                          summary=summary, last_message_at=last_at, unread_count=unread,
                          avatar=None, sort_timestamp=sort))
    for category in member_categories - container_categories:
        issues.add(category + "_container_missing")
    items.sort(key=lambda item: (-item["sort_timestamp"], item["source_id"]))
    return items, issues


@contextmanager
def memory_database(image, tables, check):
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(image)
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA query_only=ON")
        for table, required in tables.items():
            columns = {r[1] for r in db.execute('PRAGMA table_info("' + table + '")')}
            if not required <= columns:
                raise ProbeError("conversation_schema_unsupported")
        db.row_factory = sqlite3.Row

        def authorize(action, table, column, _database, _trigger):
            allowed = (action == sqlite3.SQLITE_SELECT or
                       (action == sqlite3.SQLITE_READ and table in tables and column in tables[table]) or
                       (action == sqlite3.SQLITE_FUNCTION and column in ("length", "typeof")))
            return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

        db.set_authorizer(authorize)
        db.set_progress_handler(lambda: (check(), 0)[1], 1000)
        yield db
    except sqlite3.Error:
        check()
        raise ProbeError("conversation_query_failed") from None
    finally:
        db.close()


class SessionReader:
    def __init__(self, instance, wxid):
        self.instance, self.wxid = instance, wxid
        self.memory = None
        self.directory = None
        self.cache = None

    def close(self):
        if self.cache:
            self.cache.close()
        if self.memory:
            self.memory.close()
            self.memory = None

    def bind(self, deadline):
        from wechat_account_probe.native import ProcessMemory
        from wechat_account_probe.memory import current_account, bind_directory
        from wechat_compatibility import require_profile, bind_memory
        require_profile(self.instance, 'messages')
        if self.memory is None:
            self.memory = ProcessMemory(self.instance, deadline)
        elif self.memory.deadline != deadline:
            # The 32 MiB/s budget applies per command, not averaged over idle time.
            self.memory.bytes_read = 0
            self.memory.read_started = time.monotonic()
        self.memory.deadline = deadline
        paths = self.memory.mapped_paths()
        bind_memory(self.memory, paths, 'messages')
        current = current_account(self.memory, paths)
        if current != self.wxid:
            raise ProbeError("account_changed_during_read")
        directory = bind_directory(paths, current)
        if self.directory is not None and self.directory != directory:
            raise ProbeError("account_changed_during_read")
        self.directory = directory

    def image(self, relative):
        from wechat_messages.cache import DatabaseCache
        if self.cache is None:
            self.cache = DatabaseCache(self)
        return self.cache.image(relative)

    def check_identity(self, deadline):
        """Read a handful of cached identity pointers; never rescan keys or data."""
        from wechat_account_probe.memory import account_at_anchors
        anchors = getattr(self.memory, "configuration_anchors", None) if self.memory else None
        if not isinstance(anchors, tuple):
            self.bind(deadline)
            return
        self.memory.deadline = deadline
        self.memory.bytes_read = 0
        self.memory.read_started = time.monotonic()
        self.memory.check()
        if account_at_anchors(self.memory, anchors) != self.wxid:
            raise ProbeError("account_changed_during_read")

    def read(self, deadline):
        self.bind(deadline)
        check = self.memory.check
        session_columns = {"username", "unread_count", "summary", "last_timestamp", "sort_timestamp", "is_hidden"}
        with memory_database(self.image("session/session.db"),
                             {"SessionTable": session_columns,
                              "SessionNoContactInfoTable": {"username", "session_title"}}, check) as db:
            sessions = [dict(r) for r in db.execute(
                "SELECT username,unread_count,summary,last_timestamp,sort_timestamp,is_hidden "
                "FROM SessionTable LIMIT ?", (MAX_SESSIONS + 1,))]
            if len(sessions) > MAX_SESSIONS:
                raise ProbeError("conversation_count_limit")
            titles = {r[0]: r[1] for r in db.execute("SELECT username,session_title FROM SessionNoContactInfoTable")}
        names = [r["username"] for r in sessions]
        contacts, businesses = {}, {}
        with memory_database(self.image("contact/contact.db"),
                             {"contact": {"username", "remark", "nick_name", "flag", "verify_flag"},
                              "biz_info": {"username", "type"}}, check) as db:
            for offset in range(0, len(names), 400):
                batch = names[offset:offset + 400]
                placeholders = ",".join("?" for _ in batch)
                for table, columns, target in (
                    ("contact", "username,remark,nick_name,flag,verify_flag", contacts),
                    ("biz_info", "username,type", businesses),
                ):
                    for row in db.execute(f"SELECT {columns} FROM {table} WHERE username IN ({placeholders})", batch):
                        if row["username"] in target:
                            raise ProbeError("conversation_contact_ambiguous")
                        target[row["username"]] = dict(row)
        items, issues = project_sessions(sessions, contacts, businesses, titles)
        self.avatars(items, issues, min(deadline - 3, time.monotonic() + 15))
        self.bind(deadline)
        return dict(items=items, issues=sorted(issues), status="partial" if issues else "complete")

    def avatars(self, items, issues, deadline):
        def check():
            self.memory.check()
            if time.monotonic() >= deadline:
                raise ProbeError("avatar_timeout")
        try:
            check()
            with memory_database(self.image("head_image/head_image.db"),
                                 {"head_image": {"username", "image_buffer"}}, check) as db:
                total = 0
                for item in items:
                    check()
                    row = db.execute("SELECT length(image_buffer), CASE WHEN typeof(image_buffer)='blob' "
                                     "AND length(image_buffer)<=? THEN image_buffer ELSE NULL END "
                                     "FROM head_image WHERE username=?", (MAX_AVATAR_BYTES, item["source_id"])).fetchone()
                    if not row or not row[0]:
                        continue
                    if row[1] is None or total + row[0] > MAX_AVATARS_TOTAL:
                        issues.add("conversation_avatar_limit")
                        continue
                    try:
                        item["avatar"] = encode_avatar(row[1])
                        total += row[0]
                    except ProbeError:
                        issues.add("conversation_avatar_unavailable")
        except ProbeError as exc:
            if str(exc) in IDENTITY_ERRORS:
                raise
            issues.add("conversation_avatars_unavailable")
        except FileNotFoundError:
            issues.add("conversation_avatars_unavailable")
