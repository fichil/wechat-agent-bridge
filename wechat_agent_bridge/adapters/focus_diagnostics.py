"""One diagnostic attempt, separate from production capabilities and send quotas.

No UI or debugger imports here. The operator must start the native coordinator;
this ledger never enables a production adapter profile.
"""
from dataclasses import asdict
import hashlib
import json
import sqlite3
import time
import uuid
import platform
from itertools import product

from ..console_models import ConsoleError

CAMPAIGN = "m1-focus-20261023"
REVISION = "focus-diagnostic-2"


def fingerprint_digest(fp):
    return hashlib.sha256(json.dumps(asdict(fp), sort_keys=True).encode()).hexdigest()


class DiagnosticLedger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS focus_attempt (campaign TEXT PRIMARY KEY, request TEXT UNIQUE, fingerprint TEXT, state TEXT, draft_attempted INTEGER, created REAL)")

    def reserve(self, fingerprint):
        request = uuid.uuid4().hex
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO focus_attempt VALUES (?,?,?,?,?,?)",
                            (CAMPAIGN, request, fingerprint, "reserved", 0, time.time()))
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError:
            self.db.execute("ROLLBACK")
            raise ConsoleError("diagnostic_attempt_already_used") from None
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return request

    def consume_draft(self, request, fingerprint):
        # Durable before COM. Timeout/crash cannot authorize another SetValue.
        cursor = self.db.execute("UPDATE focus_attempt SET draft_attempted=1,state='draft_attempted' WHERE campaign=? AND request=? AND fingerprint=? AND state='reserved' AND draft_attempted=0",
                                 (CAMPAIGN, request, fingerprint))
        if cursor.rowcount != 1:
            raise ConsoleError("diagnostic_draft_not_authorized")

    def finish(self, request, state):
        if state not in ("finished", "failed", "recovery_required"):
            raise ValueError("invalid_diagnostic_state")
        self.db.execute("UPDATE focus_attempt SET state=? WHERE campaign=? AND request=?", (state, CAMPAIGN, request))

    def status(self):
        row = self.db.execute("SELECT state,draft_attempted FROM focus_attempt WHERE campaign=?", (CAMPAIGN,)).fetchone()
        return {"attempt_used": row is not None, "state": row[0] if row else "unused",
                "draft_attempted": bool(row[1]) if row else False, "send_enabled": False}

    def close(self):
        self.db.close()


class TraceWindow:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()

    def collecting(self):
        return self.clock()-self.started < 45

    def remaining(self):
        return max(0, 60-(self.clock()-self.started))


def check_draft_target(expected, current):
    if not current.get("identity_verified") or current.get("target_index") != 0:
        raise ConsoleError("diagnostic_target_mismatch")
    for key in ("input_runtime_id", "session_runtime_id"):
        if not expected.get(key) or current.get(key) != expected[key]:
            raise ConsoleError("diagnostic_target_mismatch")
    if current.get("draft_empty") is not True or current.get("value_readonly") is not False:
        raise ConsoleError("diagnostic_existing_draft_or_unknown")


def classify_trace(events, *, instance_bound, focus_changed, queued_link_verified=False):
    """Temporal proximity alone must not become a root-cause claim."""
    if not instance_bound:
        return "instance_unverified"
    for event in events:
        if event.get("kind") != "activation" or not event.get("target_window"):
            continue
        if event.get("setter_on_stack"):
            return "synchronous_activation_observed"
        if queued_link_verified and event.get("queued_delivery_verified"):
            return "queued_activation_observed"
    return "trace_incomplete" if focus_changed else "no_activation_observed_not_compatibility_proof"


def require_native_recovery(report, fingerprints):
    if not report or report.get("fingerprints") != fingerprints:
        raise ConsoleError("diagnostic_selftest_missing_or_stale")
    if (report.get('schema') != 2 or report.get('backend') != 'dbgeng'
            or report.get('os') != platform.version() or report.get('coverage') != 'all_target_threads'
            or report.get('repetitions') != 10 or report.get('passed') is not True):
        raise ConsoleError("diagnostic_native_recovery_not_verified")
    required = set(product(('attached','breakpoint','cleared','detached'),
                           ('single','multi'),('normal','crash','watchdog'),range(10)))
    seen = set()
    for row in report.get('results', []):
        key = (row.get('stage'),row.get('topology'),row.get('mode'),row.get('repeat'))
        if key in seen or key not in required or not recovery_case_verified(row):
            raise ConsoleError('diagnostic_native_recovery_not_verified')
        seen.add(key)
    if seen != required:
        raise ConsoleError('diagnostic_native_recovery_not_verified')


def recovery_case_verified(row):
    """Alive/heartbeat alone cannot establish that a debugger released a target."""
    if any(row.get(k) is not True for k in ('passed','target_resumed','stable_two_seconds',
                                          'target_alive','all_threads_checked')):
        return False
    initial, final = row.get('initial_thread',{}), row.get('final_thread',{})
    if not initial or not final or final.get('debugger_present') is not False:
        return False
    before, after = row.get('initial_threads'), row.get('final_threads')
    if not isinstance(before,dict) or not before or before != after:
        return False
    if any(s.get('debugger_present') is not False or s.get('prior_suspend_count') != 0
           or s.get('debug_registers') != [0]*6 for s in before.values()):
        return False
    return (initial.get('prior_suspend_count') == final.get('prior_suspend_count') == 0
            and initial.get('debug_registers') == final.get('debug_registers') == [0]*6
            and row.get('target_exitcode') is None)
