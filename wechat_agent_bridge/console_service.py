"""Deterministic M1 policy, scheduling and cancellation. UI/provider are ports."""

from dataclasses import asdict, replace
import threading
import time
import uuid

from .console_models import ConsoleError, Grant
from .console_policy import ConsolePolicy
from .console_store import digest
from . import media
from .providers import CompatibleProvider, DEFAULT_CONFIG, MockProvider, destination, validate_config
from .send_outcomes import PREWRITE_WAIT_REASONS, validate_send_outcome


class ConsoleService:
    def __init__(self, adapter, store, *, provider_factory=None, policy=None, clock=time.time, acceptance=None,
                 test_account=None, test_group=None, trial_slots=(), trial_trigger='prefix'):
        self.adapter, self.store, self.clock = adapter, store, clock
        self.acceptance = acceptance
        self.test_account, self.test_group = test_account, test_group
        self.trial_slots = tuple(trial_slots)
        self.trial_trigger = trial_trigger
        self.prefix_trial = None
        self.reply_scopes = {r['account_id']: r for r in store.values('reply_scope')}
        self.private_discovery = {}
        self.policy = policy or ConsolePolicy()
        self.lock = threading.RLock()
        self.desktop_lock = threading.RLock()
        self.work_lock = threading.Lock()
        self.lane_locks = {name: threading.Lock() for name in ('read', 'model', 'send')}
        self.lanes = {name: {'state': 'idle', 'reason': None} for name in self.lane_locks}
        self.threads = []
        self.provider_factory = provider_factory or (lambda c: MockProvider() if c["provider"] == "mock" else CompatibleProvider(c))
        self.config = validate_config(store.get("settings", "provider", DEFAULT_CONFIG))
        self.provider = self.provider_factory(self.config)
        self.grants = {item["account_id"]: Grant(**item["grant"]) for item in store.values("grant")}
        for grant in self.grants.values():
            grant.paused = True
        self.paused, self.epoch = True, 0
        self.pause_reason = 'startup_paused'
        self.accounts_cache, self.conversations_cache = {}, {}
        self.baselines, self.cursors, self.last_scan, self.last_memory, self.last_send = set(), {}, 0, {}, {}
        self.baseline_info = {}
        self.errors = []
        self.stop_event = threading.Event()
        self.thread = None
        self.identity_probe_task = None
        for probe in store.values('identity_probe'):
            if probe.get('state') in ('waiting','verifying','restoring'):
                probe.update(state='cancelled_on_restart',reason='restart_requires_new_identity_probe')
                store.put('identity_probe',probe['id'],probe)
        for job in store.values("job"):
            if job["status"] in ("pending", "processing", "ready", "sending"):
                if store.unresolved(job.get('transport_request_id', job["request_id"])):
                    store.put("send_block", digest(job["account_id"], job["conversation_id"]), {"reason": "unknown_send_requires_review"})
                job["status"] = "cancelled_on_restart"
                job.pop('reply_text', None)
                store.put("job", job["id"], job)

    def _error(self, reason):
        with self.lock:
            self.errors = (self.errors + [{"reason": reason, "time": self.clock()}])[-30:]

    def discover(self):
        with self.desktop_lock:
            accounts = self.adapter.discover()
        if len({a.id for a in accounts}) != len(accounts):
            raise ConsoleError("duplicate_account_identity")
        with self.lock:
            self.accounts_cache = {a.id: a for a in accounts}
            return [{**asdict(a), "grant": asdict(self.grants.get(a.id, Grant()))} for a in accounts]

    def configuration(self):
        with self.lock:
            return {**self.config, "api_key": "", "has_api_key": bool(self.config["api_key"])}

    def select_provider_preset(self, name):
        from .provider_presets import preset
        with self.lock:
            config = preset(name)
            if destination(config) != destination(self.config):
                config.update(api_key='', clear_api_key=True)
            elif all(self.config.get(k) == v for k, v in config.items()):
                self.pause()
                return self.configuration()
            return self.save_configuration(config)

    def accessibility(self, action, data):
        if not self.acceptance or not hasattr(self.adapter, "enable_accessibility"):
            raise ConsoleError("accessibility_patch_requires_acceptance")
        self.pause()
        with self.desktop_lock:
            if action == "bind":
                instance = data.get("instance_id")
                if instance not in {a.instance_id for a in self.accounts_cache.values()}:
                    raise ConsoleError("client_instance_missing")
                return self.adapter.bind_accessibility(instance, data.get("wechat_id", ""))
            if action == "enable":
                return self.adapter.enable_accessibility()
            if action == "restore":
                return self.adapter.restore_accessibility()
            raise ConsoleError("invalid_request")

    def bind_current_group(self, account, group_name=None):
        if not self.acceptance or not hasattr(self.adapter,'bind_current_group'):
            raise ConsoleError('bound_console_startup_scope_required')
        group = self.test_group or group_name
        if not isinstance(group, str) or not group.strip() or len(group) > 128:
            raise ConsoleError('bound_console_startup_scope_required')
        group = group.strip()
        if self.test_group and group_name is not None and group_name != self.test_group:
            raise ConsoleError('bound_console_target_mismatch')
        self.pause()
        with self.desktop_lock, self.lock:
            if self.identity_probe_task and self.identity_probe_task.running():
                raise ConsoleError('identity_probe_in_progress')
            if self.acceptance.account is not None:
                raise ConsoleError('acceptance_scope_already_bound')
            identity = self.accounts_cache.get(account)
            if not identity or not identity.identity_certain or (self.test_account and identity.wechat_id != self.test_account):
                raise ConsoleError('account_identity_uncertain')
            chat = self.adapter.bind_current_group(account, group)
            if chat.name != group or chat.kind != 'group' or chat.account_id != account:
                raise ConsoleError('bound_console_target_mismatch')
            self.acceptance.bind_group_reading(identity, chat)
            self.conversations_cache[(account,chat.id)] = chat
            return dict(conversation=asdict(chat), paused=True, read_only=True)

    def bind_read_channel(self, account, conversation, hwnd, root_id):
        self.pause()
        epoch = self._require(account, conversation)
        if not hasattr(self.adapter, 'bind_read_channel'):
            raise ConsoleError('detached_read_not_available')
        with self.desktop_lock:
            self._require(account, conversation, epoch=epoch)
            result = self.adapter.bind_read_channel(account, conversation, hwnd, root_id)
            self._require(account, conversation, epoch=epoch)
            self.cursors.pop((account, conversation), None)
        return result

    def enable_prefix_trial(self, account, conversation, mode=None):
        from .trial_reply import PREFIX, TRIGGER_MODES
        mode = mode or self.trial_trigger
        if mode not in TRIGGER_MODES:
            raise ConsoleError('invalid_reply_trigger_mode')
        self.pause()
        epoch = self._require(account, conversation, 'upload')
        if (not self.acceptance or (not self.trial_slots and self.acceptance.test_budget_enforced) or
                not hasattr(self.adapter, 'prepare_prefix_trial')):
            raise ConsoleError('console_trial_requires_explicit_startup')
        if not self.config['capabilities']['text']:
            raise ConsoleError('provider_text_test_required')
        def check():
            self._require(account, conversation, 'upload', epoch=epoch)
        with self.desktop_lock:
            report = self.adapter.prepare_prefix_trial(account, conversation, check)
            check()
            if (report.get('status') != 'preflight_verified' and
                    not (report.get('status') == 'armed' and report.get('preflight_per_send') is True)):
                raise ConsoleError('console_helper_preflight_unverified')
            self.acceptance.enable_prefix_trial(account, conversation, self.trial_slots)
            self.acceptance.next_trial_slot()
            self.prefix_trial = dict(account=account, conversation=conversation,
                trigger_mode=mode, prefix=PREFIX if mode == 'prefix' else None,
                sender_verified=False, mentions_verified=False)
            grant = self.grants[account]
            self.authorize(account, dict(read=True, upload=True, reply=True, paused=False,
                                         excluded=list(grant.excluded)))
            self.resume()
        return dict(enabled=True, trigger=PREFIX if mode == 'prefix' else 'all_new_text',
                    historical_replies=False, preflight=report)

    def configure_reply_scope(self, account, all_private, history='new_only'):
        if type(all_private) is not bool or history != 'new_only':
            raise ConsoleError('invalid_reply_scope')
        self._require(account, permission='upload')
        self.pause()
        with self.desktop_lock, self.lock:
            self._require(account, permission='upload')
            value = dict(account_id=account, all_private=all_private, history='new_only')
            self.store.put('reply_scope', account, value)
            self.reply_scopes[account] = value
        return dict(**value, paused=True)

    def _private_enabled(self, account):
        return self.reply_scopes.get(account, {}).get('all_private') is True

    def _new_private(self, account, conversation):
        chat = self.conversations_cache.get((account, conversation))
        return self._private_enabled(account) and chat is not None and chat.kind == 'private'

    def _private_floor(self, account, conversation):
        key = digest(account, conversation)
        floor = self.store.get('private_context_floor', key)
        if floor is None:
            floor = max((r['seq'] for r in self.store.messages(account, conversation)), default=0)
            self.store.put('private_context_floor', key, floor)
        return floor

    def send_preflight(self, account, conversation):
        if not self.paused or not self.prefix_trial or not hasattr(self.adapter, 'send_preflight'):
            raise ConsoleError('paused_armed_trial_required')
        epoch = self._require(account, conversation, 'reply')
        def check():
            self._require(account, conversation, 'reply', epoch=epoch)
            if not self.paused:
                raise ConsoleError('paused_armed_trial_required')
        with self.desktop_lock:
            check()
            result = self.adapter.send_preflight(account, conversation, check)
            check()
        return result

    def identity_probe(self, action, data):
        from .identity_probe import IdentityProbe
        if not self.acceptance or not hasattr(self.adapter,'observe_identity_probe'):
            raise ConsoleError('identity_probe_requires_windows_acceptance')
        if action=='cancel':
            if self.identity_probe_task:self.identity_probe_task.cancel()
            return self.identity_probe_status()
        if action=='status':return self.identity_probe_status()
        if action!='start':raise ConsoleError('invalid_request')
        instance=data.get('instance_id')
        with self.lock:
            if self.identity_probe_task and self.identity_probe_task.running():
                raise ConsoleError('identity_probe_already_running')
            if instance not in {a.instance_id for a in self.accounts_cache.values()}:
                raise ConsoleError('client_instance_missing')
        self.pause()
        # Follow the same desktop -> service order as an in-flight sync. Never
        # hold the service lock while waiting for another desktop operation.
        with self.desktop_lock,self.lock:
            if self.identity_probe_task and self.identity_probe_task.running():
                raise ConsoleError('identity_probe_already_running')
            if instance not in {a.instance_id for a in self.accounts_cache.values()}:
                raise ConsoleError('client_instance_missing')
            self.adapter.prepare_identity_probe(instance)
            self.accounts_cache={key:replace(a,identity_certain=False) if a.instance_id==instance else a
                                 for key,a in self.accounts_cache.items()}
            probe_id=uuid.uuid4().hex
            task=IdentityProbe(instance,lambda:self.adapter.observe_identity_probe(instance),
                self.adapter.restore_identity_probe,self.adapter.commit_identity_probe,
                lambda:self.adapter.check_identity_probe(instance))
            self.store.put('identity_probe',probe_id,dict(id=probe_id,**task.status()))
            self.identity_probe_task=task
            def run():
                with self.desktop_lock:result=task.run()
                with self.lock:
                    try:
                        self.store.put('identity_probe',probe_id,dict(id=probe_id,**result))
                    except Exception:
                        with task.lock:
                            task.state='failed'
                            task.reason='identity_probe_audit_failed'
                        self._error('identity_probe_audit_failed')
                    if result['state']!='verified':self._error(result['reason'])
            task.thread=threading.Thread(target=run,daemon=True,name='bridge-identity-probe')
            try:
                task.thread.start()
            except Exception:
                task.thread=None
                task.cancel()
                run()
                raise ConsoleError('identity_probe_start_failed') from None
        return task.status()

    def identity_probe_status(self):
        return self.identity_probe_task.status() if self.identity_probe_task else dict(state='idle')

    def save_configuration(self, changes):
        allowed = set(DEFAULT_CONFIG) - {"capabilities"}
        with self.lock:
            config = {**self.config, **{k: v for k, v in changes.items() if k in allowed}}
            if changes.get("api_key") == "" and not changes.get("clear_api_key"):
                config["api_key"] = self.config["api_key"]
            if changes.get("clear_api_key"):
                config["api_key"] = ""
            config["capabilities"] = {"text": False, "vision": False, "transcription": False}
            config = validate_config(config)
            if self.acceptance:
                self.acceptance.validate_provider(config)
            provider = self.provider_factory(config)
            self.store.put("settings", "provider", config)
            self.config, self.provider = config, provider
            self._invalidate()
        return self.configuration()

    def _invalidate(self, reason=None):
        if self.identity_probe_task:self.identity_probe_task.cancel()
        self.epoch += 1
        self.paused = True
        self.pause_reason = reason
        self.baselines.clear()
        for job in self.store.values("job"):
            if job["status"] in ("pending", "processing", "ready"):
                job["status"] = "cancelled"
                job.pop('reply_text', None)
                self.store.put("job", job["id"], job)

    def authorize(self, account, data):
        if getattr(self.adapter, 'metadata_only', False) is True:
            raise ConsoleError('account_discovery_read_only')
        with self.lock:
            if self.acceptance:
                self.acceptance.require(account)
            identity = self.accounts_cache.get(account)
            if not identity or not identity.identity_certain:
                raise ConsoleError("account_identity_uncertain")
            flags, excluded = self.policy.validate_grant(data)
            if self.acceptance and flags['reply']:
                self.acceptance.require(account, permission='reply')
            grant = Grant(**flags, destination=destination(self.config), excluded=excluded)
            self.store.put("grant", account, {"account_id": account, "grant": asdict(grant)})
            self.grants[account] = grant
            self._invalidate()
        return asdict(grant)

    def _require(self, account, conversation=None, permission="read", *, automatic=False, epoch=None):
        if getattr(self.adapter, 'metadata_only', False) is True:
            raise ConsoleError('account_discovery_read_only')
        with self.lock:
            chat = self.conversations_cache.get((account, conversation))
            if (chat and chat.kind == 'private' and account in self.reply_scopes
                    and not self._private_enabled(account)):
                raise ConsoleError('private_reply_scope_disabled')
            if self.identity_probe_task and self.identity_probe_task.running():
                raise ConsoleError('identity_probe_in_progress')
            if self.acceptance:
                self.acceptance.require(account, conversation, permission=permission)
            blocked = permission == "reply" and conversation is not None and self.store.get("send_block", digest(account, conversation))
            self.policy.require(self.grants.get(account), conversation, permission, destination=destination(self.config),
                                paused=self.paused, automatic=automatic, epoch=self.epoch, expected_epoch=epoch, send_blocked=blocked)
            identity=self.accounts_cache.get(account)
            if not identity or not identity.identity_certain or not identity.connected:
                raise ConsoleError('account_identity_uncertain')
            return self.epoch

    def pause(self, reason='manual_pause'):
        with self.lock:
            self._invalidate(reason)
        return {"paused": True}

    def resume(self):
        if getattr(self.adapter, 'metadata_only', False) is True:
            raise ConsoleError('account_discovery_read_only')
        with self.lock:
            if self.identity_probe_task and self.identity_probe_task.running():
                raise ConsoleError('identity_probe_in_progress')
            if self.acceptance:
                if not self.acceptance.account:
                    raise ConsoleError("acceptance_target_not_bound")
                if self.acceptance.test_budget_enforced:
                    usage = self.acceptance.ledger.status()
                    if usage["api_used"] >= usage["api_limit"]:
                        raise ConsoleError("acceptance_api_budget_exhausted")
                    if usage["send_used"] >= usage["send_limit"]:
                        raise ConsoleError("acceptance_send_budget_exhausted")
            self.epoch += 1
            self.baselines.clear()
            self.last_scan = 0
            self.paused = False
            self.pause_reason = None
        return {"paused": False}

    def conversations(self, account):
        epoch = self._require(account)
        with self.desktop_lock:
            self._require(account, epoch=epoch)
            chats = self.adapter.conversations(account)
            if self._private_enabled(account):
                if hasattr(self.adapter, 'discover_private_conversations'):
                    try:
                        private, report = self.adapter.discover_private_conversations(account)
                    except ConsoleError as exc:
                        private, report = [], dict(state='blocked', ready=0, reason=str(exc))
                    if any(c.account_id != account or c.kind != 'private' or
                           not c.identity_certain or not c.identity_source or not c.wechat_id for c in private):
                        raise ConsoleError('private_identity_unverified')
                    if self.acceptance:
                        self.acceptance.register_private(account, private)
                    by_id = {c.id: c for c in chats}
                    for c in private:
                        previous = self.conversations_cache.get((account, c.id))
                        if previous and (previous.wechat_id != c.wechat_id or previous.kind != c.kind):
                            raise ConsoleError('conversation_identity_mismatch')
                        if c.id in by_id and by_id[c.id] != c:
                            raise ConsoleError('conversation_identity_mismatch')
                        by_id[c.id] = c
                    chats = list(by_id.values())
                    self.private_discovery[account] = report
                elif self.adapter.simulation_only:
                    self.private_discovery[account] = dict(state='mock_ready', ready=sum(c.kind=='private' for c in chats))
                else:
                    self.private_discovery[account] = dict(state='blocked', reason='private_discovery_unverified', ready=0)
            if self.acceptance and any(chat.id not in self.acceptance.targets for chat in chats):
                raise ConsoleError("acceptance_adapter_scope_violation")
        self._require(account, epoch=epoch)
        if any(chat.account_id != account for chat in chats) or len({chat.id for chat in chats}) != len(chats):
            raise ConsoleError("conversation_identity_mismatch")
        with self.lock:
            for chat in chats:
                self.conversations_cache[(account, chat.id)] = chat
                if self._new_private(account, chat.id):
                    self._private_floor(account, chat.id)
        return [asdict(chat) for chat in chats if chat.id not in self.grants[account].excluded]

    def sync(self, account, conversation, *, older=False, automatic=False):
        epoch = self._require(account, conversation, automatic=automatic)
        key = (account, conversation)
        with self.desktop_lock:
            self._require(account, conversation, automatic=automatic, epoch=epoch)
            state = self.adapter.inspect(account, conversation)
            self._check_state(account, conversation, state, writing=False)
            cursor = self.cursors.get(key) if older else None
            if older and cursor is None:
                coverage = self.store.get('coverage', digest(*key), {})
                if not coverage.get('complete'):
                    raise ConsoleError('client_history_loading_unsupported')
                return {"complete": True}
            page = self.adapter.messages(account, conversation, cursor)
        with self.lock:
            self._require(account, conversation, automatic=automatic, epoch=epoch)
            first = key not in self.baselines
            if any(m.account_id != account or m.conversation_id != conversation for m in page.messages):
                raise ConsoleError("message_target_mismatch")
            chat = self.conversations_cache.get(key)
            if not chat or not chat.identity_certain:
                raise ConsoleError("conversation_identity_uncertain")
            for message in (reversed(page.messages) if older else page.messages):
                if self._new_private(account, conversation) and (first or older or message.historical):
                    message = replace(message, historical=True, upload_eligible=False)
                if self.acceptance and (first or older):
                    eligible = message.upload_eligible and not older and self.acceptance.targets[conversation] == "group"
                    message = replace(message, historical=True, upload_eligible=eligible)
                fresh = self.store.append(message, older=older)
                running = not self.paused and not self.grants[account].paused
                if self.prefix_trial and key == (self.prefix_trial['account'], self.prefix_trial['conversation']):
                    from .trial_reply import triggers
                    outbound = bool(self.store.get('outbound_echo', digest(account, conversation, message.text)))
                    triggered = triggers(message, self.prefix_trial.get('trigger_mode', 'prefix'), outbound=outbound)
                else:
                    outbound = bool(self.store.get('outbound_echo', digest(account, conversation, message.text)))
                    triggered = self.policy.triggers(chat, message) and not outbound
                if running and fresh and not first and not older and triggered:
                    self._enqueue(account, conversation, message.id)
            self.baselines.add(key)
            if first and not older:
                self.baseline_info[key] = dict(account=account, conversation=conversation,
                    established_at=self.clock(), message_count=len(page.messages), historical_replies=False)
            if older or key not in self.cursors:
                self.cursors[key] = page.cursor
            coverage = {"account_id": account, "conversation_id": conversation, "complete": page.complete,
                        "limitation": page.limitation, "synced_at": self.clock(), "has_older": self.cursors.get(key) is not None,
                        "loaded_count": page.loaded_count, "loaded_start": page.loaded_start, "loaded_end": page.loaded_end}
            self.store.put("coverage", digest(*key), coverage)
            return coverage

    def read_messages(self, account, conversation, before=None):
        self._require(account, conversation)
        messages = self.store.messages(account, conversation, before=before)
        for message in messages:
            for attachment in message["attachments"]:
                parsed = self.store.get("extraction", digest(account, conversation, attachment["id"], destination(self.config)))
                attachment["parse_status"] = "parsed" if parsed else "not_processed"
                attachment["parsed_text"] = parsed["text"] if parsed else ""
        return {"messages": messages, "epoch": self.epoch,
                "coverage": self.store.get("coverage", digest(account, conversation), {})}

    def _enqueue(self, account, conversation, event):
        existing = next((j for j in self.store.values("job") if j["account_id"] == account and j["conversation_id"] == conversation
                         and j["epoch"] == self.epoch and j["status"] == "pending" and not j.get('recovery_id')), None)
        if existing:
            existing["events"].append(event)
            existing["due"] = self.clock()+3
            self.store.put("job", existing["id"], existing)
        else:
            key = uuid.uuid4().hex
            self.store.put("job", key, {"id": key, "request_id": uuid.uuid4().hex, "account_id": account,
                           "conversation_id": conversation, "events": [event], "status": "pending", "due": self.clock()+3, "epoch": self.epoch})

    def recover_reply(self, account, conversation, events, recovery_id):
        """Explicitly recover an exact, operator-selected group batch once."""
        from .trial_reply import triggers
        from .console_models import ChatMessage
        if (not isinstance(recovery_id, str) or not 1 <= len(recovery_id) <= 128 or
                any(not c.isascii() or not (c.isalnum() or c in '_.:-') for c in recovery_id) or
                not isinstance(events, list) or not 1 <= len(events) <= 20 or
                any(not isinstance(e, str) or not e or len(e) > 256 for e in events) or
                len(set(events)) != len(events)):
            raise ConsoleError('invalid_reply_recovery')
        epoch = self._require(account, conversation, 'reply')
        self._require(account, conversation, 'upload', epoch=epoch)
        job_id = digest('reply-recovery', recovery_id)
        with self.desktop_lock, self.lock:
            self._require(account, conversation, 'reply', epoch=epoch)
            previous = self.store.get('job', job_id)
            if previous:
                if (previous.get('account_id') != account or previous.get('conversation_id') != conversation or
                        set(previous.get('events', ())) != set(events)):
                    raise ConsoleError('reply_recovery_id_conflict')
                return self._recovery_status(previous, replayed=True)
            self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
            if (not self.prefix_trial or (account, conversation) !=
                    (self.prefix_trial['account'], self.prefix_trial['conversation']) or
                    self.conversations_cache[(account, conversation)].kind != 'group' or
                    (account, conversation) not in self.baselines):
                raise ConsoleError('reply_recovery_bound_group_required')
            self._check_state(account, conversation, self.adapter.inspect(account, conversation), writing=False)
            rows = self.store.selected_messages(account, conversation, events)
            for row in rows:
                data = {k: v for k, v in row.items() if k not in ('seq', 'display_order')}
                message = replace(ChatMessage.from_dict(data), historical=False)
                if not triggers(message, 'all_new_text', outbound=bool(self.store.get(
                        'outbound_echo', digest(account, conversation, message.text)))):
                    raise ConsoleError('reply_recovery_message_ineligible')
            for current in self.store.values('job'):
                if (current['account_id'], current['conversation_id']) != (account, conversation):
                    continue
                if current.get('recovery_id') and current['status'] in ('pending', 'processing', 'ready', 'sending'):
                    raise ConsoleError('reply_recovery_conversation_busy')
                if set(current['events']) & set(events) and (current['status'] in ('ui_confirmed', 'unknown') or
                        (current.get('result') or {}).get('attempted')):
                    raise ConsoleError('reply_recovery_prior_attempt_requires_review')
            events = [row['id'] for row in rows]
            job = dict(id=job_id, request_id=digest('reply-recovery-send', recovery_id),
                recovery_id=recovery_id, account_id=account, conversation_id=conversation,
                events=events, status='pending', due=self.clock()+3, created_at=self.clock(), epoch=epoch,
                recovery_upto_seq=rows[-1]['seq'])
            job, replayed = self.store.register_reply_recovery(job)
            return self._recovery_status(job, replayed=replayed)

    @staticmethod
    def _recovery_status(job, *, replayed=False):
        return dict(job_id=job['id'], recovery_id=job['recovery_id'], status=job['status'],
                    message_count=len(job['events']), reason=job.get('wait_reason') or job.get('reason') or
                    (job.get('result') or {}).get('reason'), replayed=replayed)

    def _call(self, account, conversation, epoch, method, *args):
        with self.lock:
            self._require(account, conversation, "upload", automatic=True, epoch=epoch)
            if self.config["provider"] != "mock" and not self.config["capabilities"].get("text"):
                raise ConsoleError("provider_text_not_verified")
            provider = self.provider
            self._reserve_provider_call()
        result = getattr(provider, method)(*args)
        self._require(account, conversation, "upload", automatic=True, epoch=epoch)
        return result

    def _reserve_provider_call(self):
        from .provider_presets import is_deepseek
        if self.config['provider'] == 'compatible' and is_deepseek(self.config) and not self.config['api_key']:
            raise ConsoleError('provider_api_key_missing')
        if self.acceptance:
            self.acceptance.validate_provider(self.config)
        self.store.reserve_call(self.clock(), self.config["hourly_limit"], self.config["daily_limit"])
        if self.acceptance:
            try:
                self.acceptance.reserve_api(uuid.uuid4().hex)
            except ConsoleError as exc:
                self._error(str(exc))
                self._invalidate(str(exc))
                raise

    def test_provider(self, capability):
        if getattr(self.adapter, 'metadata_only', False) is True:
            raise ConsoleError('account_discovery_read_only')
        if capability not in ("text", "vision", "transcription"):
            raise ConsoleError("invalid_capability")
        # Testing is an explicit operation while automation stays paused.
        self.pause()
        from .adapters.console_mock import MockConsoleAdapter
        sample = None
        if capability != "text":
            fixtures = MockConsoleAdapter()
            for message in fixtures.events[("mock-a", "friend")]:
                if message.kind == ("image" if capability == "vision" else "voice"):
                    sample = fixtures.attachment("mock-a", "friend", message.attachments[0].id)
        with self.lock:
            provider, epoch = self.provider, self.epoch
            self._reserve_provider_call()
        try:
            provider.test(capability, sample)
        except Exception:
            with self.lock:
                if epoch == self.epoch:
                    self.config["capabilities"][capability] = False
                    self.store.put("settings", "provider", self.config)
            raise
        with self.lock:
            if epoch != self.epoch:
                raise ConsoleError("operation_cancelled")
            self.config["capabilities"][capability] = True
            self.store.put("settings", "provider", self.config)
        return {"capability": capability, "ok": True, "simulation_only": provider.simulation_only}

    def get_attachment(self, account, conversation, asset):
        epoch = self._require(account, conversation)
        cached = self.store.asset(account, conversation, asset)
        if cached:
            return cached
        # The caller cannot turn this endpoint into arbitrary local file access.
        found = None
        after = 0
        while found is None:
            rows = self.store.messages(account, conversation, limit=200, after=after)
            if not rows:
                break
            for message in rows:
                for attachment in message["attachments"]:
                    if attachment["id"] == asset:
                        found = attachment
            after = rows[-1]["seq"]
        if found is None or found["status"] != "available":
            raise ConsoleError("attachment_unavailable")
        if found["size"] > media.MAX_BYTES:
            raise ConsoleError("attachment_too_large")
        with self.desktop_lock:
            self._require(account, conversation, epoch=epoch)
            data = self.adapter.attachment(account, conversation, asset)
        with self.lock:
            self._require(account, conversation, epoch=epoch)
            if len(data) > media.MAX_BYTES:
                raise ConsoleError("attachment_too_large")
            self.store.asset(account, conversation, asset, data, found["mime"])
        return data, found["mime"]

    def _context(self, account, conversation, epoch, rows):
        rows = [row for row in rows if row.get('upload_eligible', True)]
        if self._new_private(account, conversation):
            floor = self._private_floor(account, conversation)
            rows = [r for r in rows if r['seq'] > floor and not r['historical']]
        if self.acceptance:
            rows = self.acceptance.filter_context(account, conversation, rows)
        result, remaining = [], 16000
        for row in rows:
            text = row["text"][:2000]
            for attachment in row["attachments"]:
                capability = None
                cache_key = digest(account, conversation, attachment["id"], destination(self.config))
                cached = self.store.get("extraction", cache_key)
                if cached:
                    text += "\n"+cached["text"]
                    continue
                try:
                    self._require(account, conversation, "upload", automatic=True, epoch=epoch)
                    data, mime = self.get_attachment(account, conversation, attachment["id"])
                    if mime in ("image/png", "image/jpeg", "image/webp"):
                        capability = "vision"
                        if self.config["provider"] != "mock" and not self.config["capabilities"].get("vision"):
                            raise ConsoleError("vision_not_verified")
                        extracted = self._call(account, conversation, epoch, "describe_image", data, mime)
                    elif mime in ("audio/wav", "audio/mpeg", "audio/mp4"):
                        capability = "transcription"
                        if self.config["provider"] != "mock" and not self.config["capabilities"].get("transcription"):
                            raise ConsoleError("transcription_not_verified")
                        extracted = self._call(account, conversation, epoch, "transcribe", data, attachment["name"], mime)
                    else:
                        extracted = media.extract(attachment["name"], data)
                    with self.lock:
                        self._require(account, conversation, "upload", automatic=True, epoch=epoch)
                        self.store.put("extraction", cache_key, {"account_id": account, "conversation_id": conversation, "text": extracted})
                    text += "\n附件解析："+extracted
                except ConsoleError as exc:
                    if str(exc) in ("paused", "operation_cancelled", "upload_not_authorized_for_provider"):
                        raise
                    if capability and str(exc).startswith("provider_"):
                        with self.lock:
                            if epoch == self.epoch:
                                self.config["capabilities"][capability] = False
                                self.store.put("settings", "provider", self.config)
                    self._error(str(exc))
                    text += "\n[附件未解析："+str(exc)+"；不得推测内容]"
            clipped = text[:remaining]
            result.append({"sender": row["sender"], "own": row["own"], "text": clipped+(" [已截断]" if len(text)>len(clipped) else ""),
                           "time": row["timestamp"], "seq": row["seq"]})
            remaining -= len(clipped)
            if remaining <= 0:
                break
        return result

    def _check_state(self, account, conversation, state, *, writing):
        self.policy.check_state(account, conversation, state, writing=writing)

    def _reply(self, job):
        """Deterministic one-turn entry used by offline callers."""
        if job['status'] == 'pending':
            self._generate_reply(job)
        if job['status'] == 'ready':
            self._send_ready(job)

    def _generate_reply(self, job):
        account, conversation, epoch = job["account_id"], job["conversation_id"], job["epoch"]
        self._require(account, conversation, "reply", automatic=True, epoch=epoch)
        key = (account, conversation)
        with self.lock:
            self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
            current = self.store.get('job', job['id'])
            if not current or current['status'] != 'pending':
                return
            job.update(current)
            job['status'] = 'processing'
            self.store.put('job', job['id'], job)
        memory = self.memory(account, conversation)
        rows = self.store.messages(account, conversation)
        if job.get('recovery_id'):
            selected = self.store.selected_messages(account, conversation, job['events'])
            rows = [r for r in rows if r['seq'] <= job['recovery_upto_seq']]
            if not set(job['events']).issubset({r['id'] for r in rows}):
                rows = selected
        # Prefer the newest messages if the input budget is exhausted.
        context = self._context(account, conversation, epoch, list(reversed(rows)))
        context.reverse()
        text = self._call(account, conversation, epoch, "reply", memory["summary"], context)
        if text is None:
            job["status"] = "silent"
        else:
            if not isinstance(text, str) or not text.strip() or len(text) > self.config["max_reply_chars"]:
                raise ConsoleError("invalid_reply_output")
            from .trial_reply import reply_text
            text = reply_text(text, self.config['max_reply_chars'])
            if not text:
                raise ConsoleError('invalid_reply_output')
            job.update(status='ready', reply_text=text, generated_at=self.clock(),
                       provider_destination=destination(self.config))
        with self.lock:
            self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
            self.store.put('job', job['id'], job)

    def _channel(self, account, conversation):
        if hasattr(self.adapter, 'channel_status'):
            return self.adapter.channel_status(account, conversation)
        # Existing mock / explicit ports retain their own transaction checks.
        return dict(send_ready=True, isolated_send=False, draft_isolation_verified=False,
                    concurrent_read=True, independent_read=False, reason=None)

    def _send_ready(self, job):
        account, conversation, epoch = job['account_id'], job['conversation_id'], job['epoch']
        key = (account, conversation)
        self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
        if self.clock() < job.get('retry_after', 0):
            return
        channel = self._channel(account, conversation)
        if channel.get('send_ready') is not True:
            job['wait_reason'] = channel.get('reason') or 'send_channel_unavailable'
            with self.lock:
                self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
                self.store.put('job', job['id'], job)
            return
        last = self.store.get('cooldown', digest(*key), 0)
        if self.clock()-last < self.config['send_interval']:
            return
        text = job['reply_text']
        if job.get('provider_destination') != destination(self.config):
            raise ConsoleError('operation_cancelled')
        if self.acceptance and self.acceptance.trial_slots:
            self.acceptance.next_trial_slot()
        if text:
            with self.desktop_lock:
                self._require(account, conversation, "reply", automatic=True, epoch=epoch)
                try:
                    self._check_state(account, conversation, self.adapter.inspect(account, conversation), writing=True)
                    if hasattr(self.adapter, 'prepare_send'):
                        self.adapter.prepare_send(account, conversation)
                except ConsoleError as exc:
                    # Only availability checks before any claim or UI mutation
                    # may defer this generated reply. Never retry an uncertain write.
                    if str(exc) not in ('existing_draft_or_unknown', 'human_takeover',
                                        'background_focus_unavailable'):
                        raise
                    job['wait_reason'] = str(exc)
                    with self.lock:
                        self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
                        self.store.put('job', job['id'], job)
                    return
                transport_id = job.get('transport_request_id', job['request_id'])
                prior = self.store.claim(transport_id, account, conversation, text)
                if prior:
                    outcome = prior
                else:
                    try:
                        self._require(account, conversation, "reply", automatic=True, epoch=epoch)
                        self._check_state(account, conversation, self.adapter.inspect(account, conversation), writing=True)
                        if self.acceptance:
                            self.acceptance.reserve_send(account, conversation, transport_id)
                    except ConsoleError as exc:
                        outcome = {"status": "failed", "reason": str(exc), "attempted": False}
                    else:
                        # Before any UI mutation, retain a scoped text digest to
                        # suppress our own echo even if sender semantics are absent.
                        self.store.put('outbound_echo', digest(account, conversation, text),
                            dict(request_id=transport_id, created=self.clock()))
                        try:
                            if hasattr(self.adapter, 'send_with_checkpoint'):
                                outcome = self.adapter.send_with_checkpoint(account, conversation, text, transport_id,
                                    lambda: self._require(account, conversation, 'reply', automatic=True, epoch=epoch))
                            else:
                                outcome = self.adapter.send(account, conversation, text, transport_id)
                            validate_send_outcome(outcome)
                            # Adapter reasons are not trusted log content.
                            outcome = {"status": outcome["status"], "attempted": outcome["attempted"],
                                       "reason": outcome['reason'] if outcome.get('reason') in PREWRITE_WAIT_REASONS else
                                           ("mock_" if self.adapter.simulation_only else "client_")+outcome["status"]}
                        except Exception:
                            outcome = {"status": "unknown", "reason": "send_outcome_uncertain", "attempted": True}
                    self.store.finish(transport_id, outcome)
                    if outcome["status"] == "unknown":
                        self.store.put("send_block", digest(*key), {"reason": "unknown_send_requires_review"})
                if outcome["attempted"]:
                    self.store.put("cooldown", digest(*key), self.clock())
            if outcome['status'] == 'deferred' and outcome['attempted'] is False:
                with self.lock:
                    self._require(account, conversation, 'reply', automatic=True, epoch=epoch)
                    attempt = job.get('transport_attempts', 0)+1
                    job.update(status='ready', wait_reason=outcome['reason'], transport_attempts=attempt,
                        transport_request_id=digest('send-attempt', job['request_id'], str(attempt)),
                        retry_after=self.clock()+max(10, self.config['send_interval']))
                    self.store.put('job', job['id'], job)
                return
            job["status"], job["result"] = outcome["status"], outcome
            job.pop('reply_text', None)
            job.pop('wait_reason', None)
        with self.lock:
            if epoch == self.epoch:
                self.store.put("job", job["id"], job)
        # The durable per-conversation send block stops further replies after
        # unknown. Reading and memory updates are still independent operations.

    def memory(self, account, conversation):
        self._require(account, conversation)
        value = self.store.get("memory", digest(account, conversation), {"account_id": account, "conversation_id": conversation,
                              "summary": "", "watermark": 0, "updated_at": None})
        if self._new_private(account, conversation):
            floor = self._private_floor(account, conversation)
            if value.get('source_start', 0) <= floor:
                return dict(account_id=account, conversation_id=conversation, summary='', watermark=floor, updated_at=None)
        return value

    def acknowledge_unknown(self, account, conversation):
        with self.lock:
            self._require(account, conversation)
            self._invalidate()
            self.store.put("send_block", digest(account, conversation), None)
        return {"paused": True, "retry_performed": False}

    def clear_memory(self, account, conversation=None):
        with self.lock:
            self._require(account, conversation)
            self._invalidate()
            self.store.clear(account, conversation)
        return {"cleared": True, "paused": True}

    def _summarize(self, account, conversation):
        epoch = self._require(account, conversation, "upload", automatic=True)
        current = self.memory(account, conversation)
        rows = self.store.messages(account, conversation, limit=200, after=current["watermark"])
        if self.acceptance:
            # Ineligible private baselines / manual history must not prevent
            # later eligible test messages from ever reaching a summary.
            while rows and not self.acceptance.filter_context(account, conversation, rows):
                rows = self.store.messages(account, conversation, limit=200, after=rows[-1]["seq"])
        if not rows:
            return
        context = self._context(account, conversation, epoch, rows)
        if not context:
            return
        summary = self._call(account, conversation, epoch, "summarize", current["summary"], context)
        with self.lock:
            self._require(account, conversation, "upload", automatic=True, epoch=epoch)
            self.store.put("memory", digest(account, conversation), {"account_id": account, "conversation_id": conversation,
                           "summary": summary, "watermark": context[-1]["seq"], "source_start": context[0]["seq"], "updated_at": self.clock()})

    def _job_error(self, job, exc):
        job['status'], job['reason'] = 'blocked', str(exc)
        job.pop('reply_text', None)
        with self.lock:
            if job['epoch'] == self.epoch:
                self.store.put('job', job['id'], job)
        self._error(str(exc))
        if str(exc) == 'acceptance_send_budget_exhausted':
            self.pause(str(exc))

    def _scan(self, now):
        if now-self.last_scan < 5:
            return
        self.last_scan = now
        for account, grant in list(self.grants.items()):
            if not grant.read or grant.paused:
                continue
            try:
                for chat in self.conversations(account):
                    self.sync(account, chat['id'], automatic=True)
                self.lanes['read'].update(reason=None, last_success=self.clock())
            except ConsoleError as exc:
                self.lanes['read']['reason'] = str(exc)
                self._error(str(exc))

    def _model_tick(self, now):
        for job in sorted(self.store.values('job'), key=lambda j: j.get('due', 0)):
            if job['status'] != 'pending' or job['due'] > now:
                continue
            # One generated answer per conversation may wait for transport.
            # New incoming messages coalesce into the next pending task, rather
            # than spending the entire API budget while a channel is unavailable.
            if any(j['account_id'] == job['account_id'] and j['conversation_id'] == job['conversation_id']
                   and j['epoch'] == job['epoch'] and j['status'] in ('ready', 'sending')
                   for j in self.store.values('job')):
                continue
            try:
                self._generate_reply(job)
            except ConsoleError as exc:
                self._job_error(job, exc)
        for key in list(self.conversations_cache):
            grant = self.grants.get(key[0], Grant())
            if not grant.upload or grant.paused or key[1] in grant.excluded:
                continue
            if key not in self.last_memory:
                self.last_memory[key] = now
            if now-self.last_memory[key] >= 180:
                self.last_memory[key] = now
                try:
                    self._summarize(*key)
                except ConsoleError as exc:
                    self._error(str(exc))

    def _send_tick(self, now):
        for job in self.store.values('job'):
            if job['status'] == 'ready':
                try:
                    self._send_ready(job)
                except ConsoleError as exc:
                    self._job_error(job, exc)

    def tick(self, lane=None):
        """Synchronous ticks for tests; production runs independent lane workers."""
        for name in ((lane,) if lane else ('read', 'model', 'send')):
            if self.paused or not self.lane_locks[name].acquire(blocking=False):
                continue
            try:
                if self.paused:
                    continue
                self.lanes[name]['state'] = 'running'
                {'read': self._scan, 'model': self._model_tick, 'send': self._send_tick}[name](self.clock())
            finally:
                self.lanes[name]['state'] = 'idle'
                self.lane_locks[name].release()
        if self.acceptance and self.acceptance.test_budget_enforced and not self.paused:
            usage = self.acceptance.ledger.status()
            if self.prefix_trial and self.acceptance.trial_slots:
                try:
                    self.acceptance.next_trial_slot()
                except ConsoleError as exc:
                    self.pause(str(exc))
            if usage['api_used'] >= usage['api_limit']:
                self.pause('acceptance_api_budget_exhausted')
            elif usage['send_used'] >= usage['send_limit']:
                self.pause('acceptance_send_budget_exhausted')

    def status(self):
        with self.lock:
            jobs = self.store.values('job')
            channels = []
            for account, conversation in self.conversations_cache:
                try:
                    channels.append(dict(account=account, conversation=conversation,
                                         **self._channel(account, conversation)))
                except ConsoleError as exc:
                    channels.append(dict(account=account, conversation=conversation,
                                         send_ready=False, reason=str(exc)))
            return {"paused": self.paused, "pause_reason": self.pause_reason,
                    "account_discovery": {"mode": getattr(self.adapter, 'discovery_mode', ''),
                                          "metadata_only": getattr(self.adapter, 'metadata_only', False)},
                    "simulation_only": self.adapter.simulation_only, "provider": self.config["provider"],
                    'execution': {'lanes': {k: dict(v) for k, v in self.lanes.items()}, 'channels': channels,
                                  'baselines': [v for k, v in self.baseline_info.items() if k in self.baselines],
                                  'reply_recoveries': [self._recovery_status(j) for j in sorted(
                                      (j for j in jobs if j.get('recovery_id')),
                                      key=lambda j: j.get('created_at', 0), reverse=True)][:10],
                                  'generating': sum(j['status']=='processing' for j in jobs),
                                  'ready': sum(j['status']=='ready' for j in jobs),
                                  'waiting': sorted({j['wait_reason'] for j in jobs
                                      if j['status']=='ready' and j.get('wait_reason')})},
                    "test_group": self.test_group,
                    "reply_scopes": list(self.reply_scopes.values()),
                    "private_discovery": dict(self.private_discovery),
                    "prefix_trial": self.prefix_trial,
                    "prefix_trial_available": bool(self.acceptance and
                        (self.trial_slots or not self.acceptance.test_budget_enforced)),
                    "trial_default_trigger": self.trial_trigger,
                    "identity_probe":self.identity_probe_status(),
                    "acceptance": self.acceptance.status() if self.acceptance else None,
                    "background": self.adapter.background_status() if hasattr(self.adapter, "background_status") else None,
                    "usage": self.store.usage(self.clock()), "errors": list(self.errors), "outcomes": self.store.outcomes(),
                    "pending": sum(j['status'] in ('pending','processing','ready') for j in jobs), "last_scan": self.last_scan}

    def start(self):
        if self.threads:
            raise ConsoleError('scheduler_already_started')
        def run(lane):
            while not self.stop_event.wait(0.5):
                try:
                    self.tick(lane)
                except Exception:
                    self._error("background_operation_failed")
                    self.pause()
        for lane in self.lane_locks:
            thread = threading.Thread(target=run, args=(lane,), daemon=True, name='bridge-'+lane)
            self.threads.append(thread)
            thread.start()
        self.thread = self.threads[0]

    def close(self):
        self.pause()
        self.stop_event.set()
        deadline = time.monotonic()+40
        for thread in self.threads:
            thread.join(timeout=max(0, deadline-time.monotonic()))
        if any(thread.is_alive() for thread in self.threads):
            raise ConsoleError('scheduler_cleanup_incomplete')
        if self.identity_probe_task and self.identity_probe_task.thread:
            self.identity_probe_task.thread.join(timeout=25)
            if self.identity_probe_task.thread.is_alive():
                raise ConsoleError('identity_probe_cleanup_incomplete')
        self.adapter.close()
