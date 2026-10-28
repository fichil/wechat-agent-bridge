"""Bounded Windows UIA transport. No desktop access until explicitly selected.

Selector support is deliberately fail-closed. A detected executable version is
not a tested profile. See docs/M1_TEST_PLAN.md for promotion evidence.
"""

import multiprocessing
import os
import threading
import time

from ..console_models import Account, ChatMessage, Conversation, ConsoleError, MessagePage


def _worker(connection):
    from .windows_uia import WindowsUIA
    backend = WindowsUIA()
    while True:
        try:
            request = connection.recv()
        except EOFError:
            break
        try:
            if request['method'] == 'discover_own_accounts':
                result = backend.discover_own_accounts(
                    lambda receipt: connection.send({'discovery_restore': receipt}))
            else:
                result = getattr(backend, request["method"])(*request["args"])
            connection.send({"value": result})
        except ConsoleError as exc:
            connection.send({"error": str(exc)})
        except ImportError:
            connection.send({"error": "windows_dependency_missing"})
        except Exception as exc:
            import traceback
            # Exception messages can contain UI text; expose only type and line.
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            connection.send({"error": "windows_uia_operation_failed", "diagnostic":
                             type(exc).__name__+":"+os.path.basename(frame.filename)+":"+str(frame.lineno)})


class WindowsAdapter:
    simulation_only = False
    revision = "windows-uia-semantic-research-3"

    def __init__(self, timeout=10, *, allow_accessibility_patch=False, gate_store=None, trial_evidence=None,
                 trial_send_mode='isolated', auto_discovery=False, discovery_mode='profile'):
        if trial_send_mode not in ('isolated', 'main-window'):
            raise ConsoleError('invalid_reply_send_mode')
        if discovery_mode not in ('read-only', 'profile'):
            raise ConsoleError('invalid_discovery_mode')
        if auto_discovery and discovery_mode == 'read-only' and (allow_accessibility_patch or gate_store is not None):
            raise ConsoleError('read_only_discovery_conflicts_with_live_operations')
        self.timeout = timeout
        self.lock = threading.RLock()
        self.process = None
        self.connection = None
        self.allow_accessibility_patch, self.gate_store = allow_accessibility_patch, gate_store
        self.gate = self.native_gate = None
        self.bound_window = None
        self.capabilities = {}
        self.enrolled_account = None
        self.console_port = None
        self.console_group = None
        self.trial_evidence = trial_evidence
        self.trial_send_mode = trial_send_mode
        self.console_trial_sender = None
        self.console_send_checkpoint = lambda: None
        self.detached_read_binding = None
        self.private_catalog_cache = None
        self.private_catalog_at = 0
        self.auto_discovery = auto_discovery
        self.discovery_mode = discovery_mode
        self.metadata_only = auto_discovery and discovery_mode == 'read-only'
        if self.metadata_only:
            self.revision = 'windows-own-metadata-readonly-1'

    def _start(self):
        if os.name != "nt":
            raise ConsoleError("windows_required")
        ctx = multiprocessing.get_context("spawn")
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(target=_worker, args=(child,), daemon=True)
        self.process.start()
        child.close()

    def _call(self, method, *args):
        with self.lock:
            if not self.process or not self.process.is_alive():
                self._start()
            restoration = None
            try:
                self.connection.send({"method": method, "args": args})
                deadline = time.monotonic() + self.timeout
                while True:
                    if not self.connection.poll(max(0, deadline - time.monotonic())):
                        self._stop()
                        raise ConsoleError("windows_operation_timeout")
                    response = self.connection.recv()
                    if method == 'discover_read_only_accounts' and 'discovery_restore' in response:
                        self._stop()
                        raise ConsoleError('read_only_discovery_protocol_error')
                    if method == 'discover_own_accounts' and 'discovery_restore' in response:
                        restoration = response['discovery_restore']
                        continue
                    break
            except (EOFError, BrokenPipeError, OSError):
                self._stop()
                raise ConsoleError("windows_worker_disconnected") from None
            finally:
                if restoration is not None:
                    from .own_account_windows import restore_window
                    try:
                        restore_window(restoration)
                    except Exception:
                        self._stop()
                        raise ConsoleError('account_discovery_restore_failed') from None
            if "error" in response:
                if method in ("current_target_metadata", "target_control_probe") and "diagnostic" in response:
                    raise ConsoleError(response["error"]+":"+response["diagnostic"])
                raise ConsoleError(response["error"])
            return response["value"]

    def discover(self):
        method = ('discover_read_only_accounts' if self.metadata_only else
                  'discover_own_accounts' if self.auto_discovery else 'discover')
        accounts=[Account(**item) for item in self._call(method)]
        account=self.enrolled_account
        verified=False
        if account is not None and self.bound_window and self.native_gate:
            from dataclasses import asdict
            fp=self.bound_window['fingerprint']
            if self.native_gate.fingerprint()==fp and account.fingerprint==asdict(fp):
                accounts=[account if a.instance_id==account.instance_id else a for a in accounts]
                verified=True
            else:
                self.enrolled_account=None
        if self.bound_window and not verified:
            from dataclasses import replace
            fp=self.bound_window['fingerprint']
            accounts=[replace(a,name='待核验微信窗口',wechat_id=None,identity_certain=False,
                              identity_source='',verified=False,fingerprint={})
                      if a.instance_id==f'{fp.pid}:{fp.hwnd}' else a for a in accounts]
        if verified and self.detached_read_binding:
            # A known conversation window is not another logged-in account.
            child = f"{self.detached_read_binding['pid']}:{self.detached_read_binding['window']}"
            accounts = [a for a in accounts if a.instance_id != child]
        return accounts

    def probe_account(self, instance_id):
        if self.metadata_only:
            raise ConsoleError('account_discovery_read_only')
        if self.gate and self.gate.state in ("enabled", "already_enabled") and self.bound_window:
            from dataclasses import asdict, replace
            fp = self.bound_window["fingerprint"]
            if instance_id != f"{fp.pid}:{fp.hwnd}" or self.native_gate.fingerprint() != fp:
                raise ConsoleError("client_instance_mismatch")
            account = Account(**self._call("verify_own_profile", fp.pid, fp.hwnd, self.bound_window["expected_wechat_id"]))
            if (self.native_gate.fingerprint() != fp or account.instance_id != instance_id or
                    account.wechat_id != self.bound_window["expected_wechat_id"] or
                    not account.identity_certain or
                    account.identity_source != "uia_own_profile_exact_id_user_selected_window"):
                raise ConsoleError("account_identity_uncertain")
            account = replace(account, fingerprint=asdict(fp))
            # The acceptance binder owns binding/account (including its old
            # process epoch). Console discovery must never overwrite it with
            # the incompatible flat Account schema or implicitly rebind it.
            self.gate_store.put("console_identity", instance_id, asdict(account))
            self.bound_window["identity_source"] = account.identity_source
            self.enrolled_account=account
            return account
        return Account(**self._call("probe_account", instance_id))

    def prepare_identity_probe(self, instance_id):
        from dataclasses import asdict
        if not self.bound_window or not self.gate or self.gate.state not in ('enabled','already_enabled'):
            raise ConsoleError('identity_probe_requires_enabled_bound_window')
        fp=self.bound_window['fingerprint']
        if instance_id!=f'{fp.pid}:{fp.hwnd}' or self.native_gate.fingerprint()!=fp:
            raise ConsoleError('client_instance_mismatch')
        if (not self.gate.record or self.gate.record.get('fingerprint')!=asdict(fp) or
                self.gate_store.get('patch','pending')!=self.gate.record):
            raise ConsoleError('identity_probe_patch_owner_mismatch')
        self._identity_probe_pending_check()
        self.enrolled_account=None
        self.bound_window['identity_source']='user_selected_window_pending_uia_profile'

    def _identity_probe_pending_check(self):
        for bucket in ('thread_hook','draft_probe','development_navigation','development_window_restore',
                       'development_exact_search','account_rebind'):
            pending = self.gate_store.get(bucket,'pending')
            if (bucket == 'thread_hook' and pending and self.console_trial_sender and
                    self.console_trial_sender.owns_pending(pending)):
                continue
            if pending:
                raise ConsoleError('identity_probe_prior_recovery_required')

    def check_identity_probe(self, instance_id):
        from .background import snapshot
        if not self.bound_window:
            raise ConsoleError('client_instance_mismatch')
        fp=self.bound_window['fingerprint']
        if instance_id!=f'{fp.pid}:{fp.hwnd}' or self.native_gate.fingerprint()!=fp:
            raise ConsoleError('client_instance_mismatch')
        if snapshot(fp.pid).locked:
            raise ConsoleError('desktop_locked_or_unavailable')
        self._identity_probe_pending_check()
        if self.gate_store.get('patch','pending')!=self.gate.record:
            raise ConsoleError('identity_probe_patch_owner_mismatch')

    def restore_identity_probe(self):
        # Never clear another operation's recovery record on an ownership race.
        if self.gate_store.get('patch','pending')!=self.gate.record:
            raise ConsoleError('identity_probe_patch_owner_mismatch')
        return self.restore_accessibility()

    def observe_identity_probe(self, instance_id):
        """Two current own-card reads; no persistence until restoration."""
        from dataclasses import asdict,replace
        from .background import snapshot,input_timestamp
        self.prepare_identity_probe(instance_id)
        fp=self.bound_window['fingerprint']
        before=snapshot(fp.pid)
        input_before=input_timestamp()
        if before.locked:raise ConsoleError('desktop_locked_or_unavailable')
        observed=[]
        for _ in range(2):
            account=Account(**self._call('read_own_profile',fp.pid,fp.hwnd,self.bound_window['expected_wechat_id']))
            if (self.native_gate.fingerprint()!=fp or account.instance_id!=instance_id or
                    account.wechat_id!=self.bound_window['expected_wechat_id'] or not account.identity_certain or
                    account.identity_source!='uia_own_profile_exact_id_user_selected_window'):
                raise ConsoleError('account_identity_uncertain')
            observed.append(replace(account,fingerprint=asdict(fp)))
        # Enrollment deliberately expects the user to open the card. Once the
        # read begins, focus, clipboard and both exact identities must agree.
        if observed[0]!=observed[1] or snapshot(fp.pid)!=before or input_timestamp()!=input_before:
            raise ConsoleError('identity_probe_observation_changed')
        return observed[0]

    def commit_identity_probe(self, account):
        from dataclasses import asdict
        self.check_identity_probe(account.instance_id)
        fp=self.bound_window['fingerprint']
        if (self.gate.record is not None or self.gate.state not in ('restored','unchanged') or
                account.fingerprint!=asdict(fp) or account.wechat_id!=self.bound_window['expected_wechat_id'] or
                account.identity_source!='uia_own_profile_exact_id_user_selected_window' or not account.identity_certain):
            raise ConsoleError('identity_probe_restoration_required')
        self.gate_store.put('console_identity',account.instance_id,asdict(account))
        self.bound_window['identity_source']=account.identity_source
        self.enrolled_account=account

    def bind_accessibility(self, instance_id, expected_wechat_id):
        from dataclasses import asdict
        from .accessibility_gate import NativeGate
        import re
        if not self.allow_accessibility_patch or self.gate_store is None:
            raise ConsoleError("accessibility_patch_not_enabled")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", expected_wechat_id):
            raise ConsoleError("account_identity_invalid")
        with self.lock:
            if self.gate and self.gate.record:
                raise ConsoleError("patch_restore_before_rebinding")
            self.enrolled_account=None
            pid, hwnd = (int(part) for part in instance_id.split(":"))
            if self.native_gate:
                self.native_gate.close()
            native = NativeGate(hwnd)
            try:
                fp = native.fingerprint()
                if fp.pid != pid:
                    raise ConsoleError("client_instance_mismatch")
                self._call("bind_window", pid, hwnd)
            except BaseException:
                native.close()
                raise
            self.native_gate = native
            self.bound_window = {"fingerprint": fp, "expected_wechat_id": expected_wechat_id,
                                 "identity_source": "user_selected_window_pending_uia_profile"}
            self.console_port = None
            self.console_group = None
            self.detached_read_binding = None
            from .accessibility_gate import AccessibilityGate
            self.gate = AccessibilityGate(native, allowed=True,
                save=lambda record: self.gate_store.put("patch", "pending", record))
            pending = self.gate_store.get("patch", "pending")
            if pending:
                self.gate.recover(pending)
            # Reuse only a native enrollment for this exact still-running
            # process instance, never an identity from another client lifetime.
            saved = self.gate_store.get('console_identity', instance_id)
            if saved:
                account = Account(**saved)
                if (account.wechat_id == expected_wechat_id and account.instance_id == instance_id and
                        account.fingerprint == asdict(fp) and account.identity_certain and
                        account.identity_source == 'uia_own_profile_exact_id_user_selected_window'):
                    self.enrolled_account = account
                    self.bound_window['identity_source'] = account.identity_source
            return self.background_status()

    def enable_accessibility(self):
        with self.lock:
            if not self.gate or not self.bound_window:
                raise ConsoleError("patch_window_not_bound")
            fp = self.bound_window["fingerprint"]
            def verify():
                self.capabilities = self._call("background_structure", fp.pid, fp.hwnd)
                return self.capabilities["controls_exposed"]
            self.gate.enable(fp, verify)
            return self.background_status()

    def restore_accessibility(self):
        with self.lock:
            if self.gate:
                self.gate.restore()
            self.capabilities = {}
            return self.background_status()

    def background_status(self):
        from dataclasses import asdict
        limitations = self.gate_store.get("capability_evidence", "background", {}) if self.gate_store else {}
        candidate_evidence = self.gate_store.get("capability_evidence", "semantic_candidates", {}) if self.gate_store else {}
        return {"revision": self.revision, "patch_allowed": self.allow_accessibility_patch,
                "bound": self.bound_window is not None,
                "semantic_candidate_evidence": candidate_evidence,
                "identity_source": self.bound_window["identity_source"] if self.bound_window else "unverified",
                "fingerprint": asdict(self.bound_window["fingerprint"]) if self.bound_window else None,
                "patch": self.gate.status() if self.gate else {"state": "disabled", "recovery_required": bool(self.gate_store and self.gate_store.get("patch", "pending"))},
                "capabilities": {**{k: v for k, v in self.capabilities.items() if k not in ("controls", "windows")},
                                 **limitations}}

    def conversations(self, account_id):
        if self.console_port:
            return self.console_port.conversations(account_id)
        return [Conversation(**item) for item in self._call("conversations", account_id)]

    def bind_current_group(self, account_id, name):
        from dataclasses import asdict
        from .focus_diagnostics import check_draft_target
        from ..console_store import digest
        with self.lock:
            account = self.enrolled_account
            if (not account or account.id != account_id or not account.identity_certain or
                    not self.bound_window or not self.gate or self.gate.state not in ('enabled','already_enabled')):
                raise ConsoleError('bound_console_identity_required')
            fp = self.bound_window['fingerprint']
            self._identity_probe_pending_check()
            if account.fingerprint != asdict(fp) or self.native_gate.fingerprint() != fp:
                raise ConsoleError('bound_console_binding_changed')
            first = self._call('current_group_target', fp.pid, fp.hwnd, [name])
            check_draft_target(first, first)
            second = self._call('current_group_target', fp.pid, fp.hwnd, [name])
            if first != second or not first.get('group_header_runtime_id'):
                raise ConsoleError('bound_console_group_changed')
            conversation = Conversation(account.id, 'bound-group-'+digest(account.id,str(fp.created),
                str(first['session_runtime_id'])), name, 'group',
                identity_source='exact_group_header_selected_row_current_process')
            receipt = dict(account_id=account.id, fingerprint=asdict(fp), target=first)
            self.console_group = receipt
            def check():
                if self.console_group != receipt:
                    raise ConsoleError('bound_console_group_changed')
            try:
                port = self.bound_group_console(conversation, first, scope_guard=check)
            except BaseException:
                self.console_group = None
                raise
            self.console_port = port
            return conversation

    def discover_private_conversations(self, account_id):
        import time
        if not self.console_port or not self.bound_window:
            raise ConsoleError('bound_console_identity_required')
        self.console_port._scope(account_id)
        if self.private_catalog_cache is None or time.monotonic()-self.private_catalog_at >= 30:
            fp = self.bound_window['fingerprint']
            value = self._call('private_catalog_metadata', fp.pid, fp.hwnd)
            self.console_port._scope(account_id)
            self.private_catalog_cache = value
            self.private_catalog_at = time.monotonic()
        value = self.private_catalog_cache
        # A row's display name / runtime id does not prove a private contact ID,
        # incoming direction, or a safe background route. Never promote it.
        return [], dict(state='blocked', ready=0, candidates=len(value['rows']),
                        complete=value['complete'], reason=value['reason'],
                        background_selection=False, incoming_direction=False,
                        private_send=False)

    def bound_group_console(self, conversation, expected, *, scope_guard):
        """Attach the already selected group reader to the console service.

        Explicit binding only: no discovery, navigation, privilege or send grant.
        scope_guard belongs to the acceptance owner and checks its live receipt.
        """
        from copy import deepcopy
        from dataclasses import asdict
        from .bound_console import BoundConsoleAdapter
        from .bound_read import console_message_page, check_read_target
        from .focus_diagnostics import check_draft_target
        from .background import desktop_locked
        from ..trial_reply import LoadedTail
        tail = LoadedTail()
        account = self.enrolled_account
        if (account is None or not self.bound_window or not self.native_gate or
                conversation.account_id != account.id or conversation.kind != 'group' or
                not conversation.identity_certain or not conversation.identity_source):
            raise ConsoleError('bound_console_current_group_required')
        fp = self.bound_window['fingerprint']
        expected = deepcopy(expected)
        check_draft_target(expected, expected)

        def guard():
            scope_guard()
            self._identity_probe_pending_check()
            if (self.enrolled_account != account or account.fingerprint != asdict(fp) or
                    self.native_gate.fingerprint() != fp or not self.gate or
                    self.gate.state not in ('enabled','already_enabled') or
                    self.gate_store is None or not self.gate.record or
                    self.gate_store.get('patch','pending') != self.gate.record):
                raise ConsoleError('bound_console_binding_changed')
            if desktop_locked():
                raise ConsoleError('desktop_locked_or_unavailable')

        def inspector(account_id, conversation_id):
            guard()
            value = self._call('current_group_target', fp.pid, fp.hwnd, [conversation.name])
            check_read_target(expected, value)
            if value.get('group_header_runtime_id') != expected.get('group_header_runtime_id'):
                raise ConsoleError('bound_console_group_changed')
            guard()
            return dict(account_id=account_id, conversation_id=conversation_id,
                        identity_certain=True, connected=True,
                        user_active=None,
                        has_draft=value.get('draft_empty') is not True)

        def reader(account_id, conversation_id, cursor):
            guard()
            value = self._call('bound_group_capture', fp.pid, fp.hwnd, [conversation.name], expected, cursor)
            guard()
            page = console_message_page(value, account, conversation, fp.hwnd)
            return tail.page(value, page, older=cursor is not None)

        guard()
        return BoundConsoleAdapter(account, [conversation], guard=guard, reader=reader,
                                     inspector=inspector, close=self.close)

    def prepare_prefix_trial(self, account, conversation, checkpoint):
        if not self.trial_evidence or not self.console_port or conversation not in self.console_port.chats:
            raise ConsoleError('console_trial_requires_explicit_startup')
        port = self.console_port
        port._scope(account, conversation)
        checkpoint(); port.inspect(account, conversation); checkpoint()
        if self.trial_send_mode == 'main-window':
            if self.detached_read_binding:
                raise ConsoleError('main_window_send_requires_main_reader')
            from dataclasses import asdict
            from .console_send_trial import ConsoleSendTrial
            from scripts.probe_bound_thread_hook import require_recovery
            require_recovery(self.trial_evidence, bound_send=True)
            checkpoint()
            self.console_trial_sender = ConsoleSendTrial(asdict(self.bound_window['fingerprint']),
                port.chats[conversation].name, self.console_group['target'], self.trial_evidence,
                port.guard, self.gate_store)
            port.sender = lambda a, c, text, request: self.console_trial_sender.run(
                text, request, self.console_send_checkpoint)
            return dict(status='armed', attempted=False, preflight_per_send=True,
                        send_ready=True, send_mode='main-window')
        # No implicit fallback: main-composer sending requires explicit startup
        # selection. Each actual send keeps the original mode-zero checks.
        self.console_trial_sender = None
        port.sender = None
        return dict(status='armed', attempted=False, preflight_per_send=True,
                    send_ready=False, reason='isolated_send_unverified')

    def channel_status(self, account, conversation):
        if not self.console_port or (account, conversation) != (
                self.console_port.account.id, next(iter(self.console_port.chats))):
            raise ConsoleError('bound_console_target_mismatch')
        ready = bool(self.trial_send_mode == 'main-window' and self.console_trial_sender and
                     self.console_port.sender and not self.detached_read_binding)
        return dict(concurrent_read=True, independent_read=bool(self.detached_read_binding), isolated_send=False,
                    draft_isolation_verified=False, send_ready=ready, send_mode=self.trial_send_mode,
                    reason=None if ready else 'isolated_send_unverified',
                    draft_isolation_candidate=bool(self.detached_read_binding))

    def prepare_send(self, account, conversation):
        """Cheap pre-write availability; no claims, helper load, or UI mutation."""
        if self.channel_status(account, conversation)['send_ready'] is not True:
            raise ConsoleError('isolated_send_unverified')
        from .background import snapshot, unchanged
        from ..console_policy import ConsolePolicy
        state = self.console_port.inspect(account, conversation)
        ConsolePolicy.check_state(account, conversation, state, writing=True)
        before = snapshot(self.bound_window['fingerprint'].pid)
        unchanged(before, before)

    def send_preflight(self, account, conversation, checkpoint):
        self.prepare_send(account, conversation)
        return self.console_trial_sender.run(checkpoint=checkpoint)

    def bind_read_channel(self, account, conversation, hwnd, root_id):
        if self.trial_send_mode == 'main-window':
            raise ConsoleError('main_window_send_requires_main_reader')
        from .detached_read import require_selection, check_metadata
        from .bound_read import console_message_page
        from ..trial_reply import LoadedTail
        if not self.console_port or not self.bound_window:
            raise ConsoleError('bound_console_target_mismatch')
        port = self.console_port
        port._scope(account, conversation)
        # Establish linkage while the original selected group is still bound.
        port.inspect(account, conversation)
        fp = self.bound_window['fingerprint']
        require_selection(fp.hwnd, hwnd, root_id)
        chat = port.chats[conversation]
        first = self._call('detached_group_metadata', fp.pid, hwnd, root_id, chat.name)
        second = self._call('detached_group_metadata', fp.pid, hwnd, root_id, chat.name)
        check_metadata(first, second)
        port.guard()
        tail = LoadedTail()
        def inspect(a, c):
            port._scope(a, c)
            actual = self._call('detached_group_metadata', fp.pid, hwnd, root_id, chat.name)
            check_metadata(first, actual)
            return dict(account_id=a, conversation_id=c, connected=True, identity_certain=True,
                        user_active=False, has_draft=None)  # Never authorizes a send.
        def read(a, c, cursor):
            port._scope(a, c)
            value = self._call('detached_group_capture', fp.pid, hwnd, root_id, chat.name, first, cursor)
            port.guard()
            return tail.page(value, console_message_page(value, port.account, chat, hwnd), older=cursor is not None)
        with port.lock:
            port.inspector, port.reader, port.sender = inspect, read, None
            self.detached_read_binding = first
        return dict(bound=True, read_only=True, isolated_send=False)

    def send_with_checkpoint(self, account, conversation, text, request_id, checkpoint):
        with self.lock:
            self.console_send_checkpoint = checkpoint
            try:
                return self.send(account, conversation, text, request_id)
            finally:
                self.console_send_checkpoint = lambda: None

    def messages(self, account_id, conversation_id, cursor=None):
        if self.console_port:
            return self.console_port.messages(account_id, conversation_id, cursor)
        value = self._call("messages", account_id, conversation_id, cursor)
        return MessagePage(tuple(ChatMessage.from_dict(item) for item in value["messages"]), value.get("cursor"),
                           value["complete"], value.get("limitation", ""))

    def attachment(self, *args):
        if self.console_port:
            return self.console_port.attachment(*args)
        return self._call("attachment", *args)

    def inspect(self, *args):
        if self.console_port:
            return self.console_port.inspect(*args)
        return self._call("inspect", *args)

    def send(self, *args):
        if self.console_port:
            if self.channel_status(*args[:2])['send_ready']:
                return self.console_port.send(*args)
            return dict(status='failed', attempted=False, reason='isolated_send_unverified')
        if self.gate_store and self.gate_store.get("capability_evidence", "background", {}).get("set_value") == "blocked_focus_changed":
            return {"status": "failed", "reason": "background_write_changes_focus", "attempted": False}
        return self._call("send", *args)

    def _stop(self):
        if self.process:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=2)
        if self.connection:
            self.connection.close()
        self.process = self.connection = None
        if self.gate:
            self.gate.restore()

    def close(self):
        with self.lock:
            try:
                self._stop()
            finally:
                if self.native_gate:
                    self.native_gate.close()
