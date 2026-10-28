"""Persistent M1 budgets with separately audited operator-authorized extensions.

No chat message, model output or console request can grant an extension.
"""

from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .console_models import ConsoleError
from .console_store import digest


CAMPAIGN = "m1-background-20261023"
FIXED_TEXT = "微信 Agent Bridge M1 验证"
MODEL = "gpt-4.1-mini-2025-04-14"


def acceptance_directory():
    # Independent of cwd / --data-dir. No reset endpoint or configurable campaign.
    return Path(__file__).resolve().parents[1]/".local/live-acceptance"


class AcceptanceLedger:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS reservations (campaign TEXT, kind TEXT, slot TEXT, request TEXT, created REAL, PRIMARY KEY(campaign,kind,slot), UNIQUE(campaign,kind,request))")
        self.db.execute("CREATE TABLE IF NOT EXISTS extensions (campaign TEXT, kind TEXT, grant_id TEXT, amount INTEGER, authorization TEXT, reason TEXT, created REAL, PRIMARY KEY(campaign,kind,grant_id))")
        self.db.commit()

    def extend(self, kind, grant_id, amount, *, authorization, reason):
        """Local developer action only, idempotent for the same immutable grant.

        A send grant creates exactly one new slot; it cannot reopen an old slot
        or reuse a request, even when the old result remains unknown.
        """
        import re
        if (kind not in ('api','send') or not isinstance(grant_id,str) or
                not re.fullmatch(r'[a-z0-9-]{1,80}',grant_id) or type(amount) is not int or
                not 1 <= amount <= (10 if kind=='api' else 1) or
                not isinstance(authorization,str) or not 8 <= len(authorization) <= 200 or
                not isinstance(reason,str) or not 8 <= len(reason) <= 300):
            raise ConsoleError('acceptance_invalid_extension')
        with self.lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                old=self.db.execute('SELECT amount,authorization,reason FROM extensions WHERE campaign=? AND kind=? AND grant_id=?',
                    (CAMPAIGN,kind,grant_id)).fetchone()
                if old is not None and old!=(amount,authorization,reason):
                    raise ConsoleError('acceptance_extension_changed')
                if old is None:
                    self.db.execute('INSERT INTO extensions VALUES (?,?,?,?,?,?,?)',
                        (CAMPAIGN,kind,grant_id,amount,authorization,reason,time.time()))
                self.db.commit()
            except BaseException:
                self.db.rollback(); raise
        return 'extra:'+grant_id if kind=='send' else grant_id

    def _limit(self, kind):
        extra=self.db.execute('SELECT coalesce(sum(amount),0) FROM extensions WHERE campaign=? AND kind=?',
                              (CAMPAIGN,kind)).fetchone()[0]
        return (10 if kind=='api' else 3)+extra

    def reserve(self, kind, request_id, slot=None, *, enforce_limit=True):
        if (type(enforce_limit) is not bool or kind not in ("api", "send") or
                (kind == "send" and not isinstance(slot,str))):
            raise ConsoleError("acceptance_invalid_reservation")
        slot = slot if kind == "send" else uuid.uuid4().hex
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                if kind=='send' and slot not in ('fixed','group_reply','private_reply'):
                    continuous = not enforce_limit and slot == 'continuous:'+digest(request_id)
                    if not continuous and (not slot.startswith('extra:') or not self.db.execute(
                        'SELECT 1 FROM extensions WHERE campaign=? AND kind=? AND grant_id=? AND amount=1',
                        (CAMPAIGN,'send',slot[6:])).fetchone()):
                        raise ConsoleError('acceptance_invalid_reservation')
                prior = self.db.execute("SELECT 1 FROM reservations WHERE campaign=? AND kind=? AND (slot=? OR request=?)",
                                        (CAMPAIGN, kind, slot, digest(request_id))).fetchone()
                if prior:
                    raise ConsoleError("acceptance_attempt_already_reserved")
                used = self.db.execute("SELECT count(*) FROM reservations WHERE campaign=? AND kind=?", (CAMPAIGN, kind)).fetchone()[0]
                if enforce_limit and used >= self._limit(kind):
                    raise ConsoleError("acceptance_"+kind+"_budget_exhausted")
                self.db.execute("INSERT INTO reservations VALUES (?,?,?,?,?)", (CAMPAIGN, kind, slot, digest(request_id), time.time()))
                self.db.commit()  # A timeout/crash does not refund the reservation.
            except BaseException:
                self.db.rollback()
                raise

    def status(self):
        with self.lock:
            counts = dict(self.db.execute("SELECT kind,count(*) FROM reservations WHERE campaign=? GROUP BY kind", (CAMPAIGN,)))
            slots = [row[0] for row in self.db.execute("SELECT slot FROM reservations WHERE campaign=? AND kind='send'", (CAMPAIGN,))]
            api_limit,send_limit=self._limit('api'),self._limit('send')
            grants=self.db.execute('SELECT kind,grant_id,amount,authorization,reason FROM extensions WHERE campaign=? ORDER BY created,grant_id',
                                  (CAMPAIGN,)).fetchall()
        return {"campaign": CAMPAIGN, "api_used": counts.get("api", 0),
                "send_used": counts.get("send", 0), "send_limit": send_limit, "reserved_send_slots": slots,
                "api_limit":api_limit,"extensions":[dict(zip(('kind','grant_id','amount','authorization','reason'),row)) for row in grants]}

    def close(self):
        self.db.close()


class AcceptanceSession:
    """Scope must be rebound to verified identities on every process restart.

    No account ID or chat name is baked into source/fixtures. The runtime scope
    consists of adapter-verified identities, not names typed into a browser.
    """
    def __init__(self, ledger, *, provider_profile='openai', test_budget_enforced=True):
        if provider_profile not in ('openai', 'deepseek'):
            raise ConsoleError('acceptance_provider_profile_invalid')
        self.provider_profile = provider_profile
        if type(test_budget_enforced) is not bool:
            raise ConsoleError('acceptance_budget_mode_invalid')
        self.test_budget_enforced = test_budget_enforced
        self.ledger, self.account, self.targets = ledger, None, {}
        self.read_only = False
        self.trial_slots = ()

    def enable_prefix_trial(self, account, conversation, slots):
        self.require(account, conversation)
        if (self.targets.get(conversation) != 'group' or
                any(kind != 'private' for cid, kind in self.targets.items() if cid != conversation) or
                (not slots and self.test_budget_enforced)
                or len(set(slots)) != len(slots)):
            raise ConsoleError('acceptance_verified_group_required')
        # These slots are passed by the local launcher, never by chat or HTTP.
        if any(slot not in ('group_reply',) and not slot.startswith('extra:') for slot in slots):
            raise ConsoleError('acceptance_invalid_reservation')
        self.trial_slots = tuple(slots)
        self.read_only = False

    def bind(self, account, conversations):
        if self.account is not None:
            raise ConsoleError("acceptance_scope_already_bound")
        if (not account.identity_certain or not account.identity_source or not account.fingerprint
                or len(conversations) != 2 or {c.kind for c in conversations} != {"group", "private"}
                or len({c.id for c in conversations}) != 2
                or any(c.account_id != account.id or not c.identity_certain or not c.identity_source for c in conversations)):
            raise ConsoleError("acceptance_verified_scope_required")
        self.account = account.id
        self.targets = {c.id: c.kind for c in conversations}

    def bind_group_reading(self, account, conversation):
        """One verified group for staged read/summary acceptance; sending denied.

        This explicit local entry does not relax the two-target full acceptance
        binding. The console exposes it only with an explicit startup scope.
        """
        if self.account is not None:
            raise ConsoleError('acceptance_scope_already_bound')
        if (not account.identity_certain or not account.identity_source or not account.fingerprint
                or conversation.account_id != account.id or conversation.kind != 'group'
                or not conversation.id or not conversation.identity_certain or not conversation.identity_source):
            raise ConsoleError('acceptance_verified_group_required')
        self.account, self.targets, self.read_only = account.id, {conversation.id:'group'}, True

    def require(self, account, conversation=None, *, permission='read'):
        if self.account is None or account != self.account or (conversation is not None and conversation not in self.targets):
            raise ConsoleError("acceptance_target_not_bound")
        if self.read_only and permission == 'reply':
            raise ConsoleError('acceptance_read_only_scope')

    def filter_context(self, account, conversation, rows):
        self.require(account, conversation)
        rows = [row for row in rows if row.get("upload_eligible", True)]
        if self.targets[conversation] == "private":
            return [row for row in rows if not row["historical"]]
        return rows[:50]

    def register_private(self, account, conversations):
        self.require(account)
        for c in conversations:
            if (c.account_id != account or c.kind != 'private' or not c.identity_certain or
                    not c.wechat_id or not c.identity_source or
                    self.targets.get(c.id, 'private') != 'private'):
                raise ConsoleError('private_identity_unverified')
        self.targets.update({c.id: 'private' for c in conversations})

    def reserve_send(self, account, conversation, request_id, *, fixed=False):
        self.require(account, conversation, permission='reply')
        kind = self.targets[conversation]
        if not self.test_budget_enforced and not fixed:
            self.ledger.reserve('send', request_id, 'continuous:'+digest(request_id), enforce_limit=False)
            return
        if self.trial_slots and not fixed:
            self.ledger.reserve('send', request_id, self.next_trial_slot())
            return
        if fixed and kind != "group":
            raise ConsoleError("acceptance_fixed_requires_group")
        self.ledger.reserve("send", request_id, "fixed" if fixed else kind+"_reply",
                            enforce_limit=self.test_budget_enforced)

    def next_trial_slot(self):
        if not self.test_budget_enforced:
            return None
        used = self.ledger.status()['reserved_send_slots']
        available = [slot for slot in self.trial_slots if slot not in used]
        if not available:
            raise ConsoleError('acceptance_send_budget_exhausted')
        return available[0]

    def reserve_api(self, request_id):
        self.ledger.reserve('api', request_id, enforce_limit=self.test_budget_enforced)

    def validate_provider(self, config):
        if self.provider_profile == 'deepseek':
            from .provider_presets import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
            if (config['provider'] != 'compatible' or config['base_url'].rstrip('/') != DEEPSEEK_BASE_URL or
                    config['model'] != DEEPSEEK_MODEL or config['vision_model'] not in ('', DEEPSEEK_MODEL) or
                    config['transcription_model'] != ''):
                raise ConsoleError('acceptance_provider_locked')
            return
        if (config["provider"] != "compatible" or config["base_url"].rstrip("/") != "https://api.openai.com/v1"
                or config["model"] != MODEL or config["vision_model"] not in ("", MODEL)
                or config["transcription_model"] not in ("", "whisper-1")):
            raise ConsoleError("acceptance_provider_locked")

    def status(self):
        ledger = self.ledger.status()
        remaining = sum(slot not in ledger['reserved_send_slots'] for slot in self.trial_slots)
        return {**ledger, "test_budget_enforced": self.test_budget_enforced,
                "trial_send_remaining": remaining if self.trial_slots and self.test_budget_enforced else None,
                "scope_bound": self.account is not None,
                "bound_conversations": len(self.targets), "read_only": self.read_only,
                "provider_profile": self.provider_profile}
