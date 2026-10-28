"""Console port for explicitly bound conversations and native operation ports.

No desktop discovery or capability promotion happens here. The binding owner
supplies current identity checks and only native operations it has qualified.
Application policy, persistence, budgets and AI remain in ConsoleService.
"""
import threading

from ..console_models import ConsoleError, MessagePage
from ..console_policy import ConsolePolicy
from ..send_outcomes import validate_send_outcome


class BoundConsoleAdapter:
    simulation_only = False

    def __init__(self, account, conversations, *, guard, reader, inspector,
                 sender=None, attachment_reader=None, close=None):
        conversations = tuple(conversations)
        if (not account.identity_certain or not account.wechat_id or
                not conversations or any(c.account_id != account.id or
                    not c.identity_certain for c in conversations) or
                len({c.id for c in conversations}) != len(conversations)):
            raise ConsoleError('bound_console_identity_required')
        self.account = account
        self.chats = {c.id: c for c in conversations}
        self.guard, self.reader, self.inspector = guard, reader, inspector
        self.sender, self.attachment_reader = sender, attachment_reader
        self.closer = close
        self.lock = threading.RLock()

    def _scope(self, account, conversation=None):
        if account != self.account.id or (conversation is not None and conversation not in self.chats):
            raise ConsoleError('bound_console_target_mismatch')
        self.guard()

    def discover(self):
        with self.lock:
            self.guard()
            return [self.account]

    def conversations(self, account):
        with self.lock:
            self._scope(account)
            return list(self.chats.values())

    def inspect(self, account, conversation):
        with self.lock:
            self._scope(account, conversation)
            state = self.inspector(account, conversation)
            self._scope(account, conversation)
            ConsolePolicy.check_state(account, conversation, state, writing=False)
            return state

    def messages(self, account, conversation, cursor=None):
        with self.lock:
            self.inspect(account, conversation)
            page = self.reader(account, conversation, cursor)
            self._scope(account, conversation)
            if (not isinstance(page, MessagePage) or any(m.account_id != account or
                    m.conversation_id != conversation for m in page.messages)):
                raise ConsoleError('message_target_mismatch')
            return page

    def attachment(self, account, conversation, attachment):
        with self.lock:
            self.inspect(account, conversation)
            if self.attachment_reader is None:
                raise ConsoleError('client_attachment_acquisition_unverified')
            value = self.attachment_reader(account, conversation, attachment)
            self._scope(account, conversation)
            return value

    def send(self, account, conversation, text, request_id):
        with self.lock:
            if self.sender is None:
                return dict(status='failed', attempted=False, reason='bound_console_send_unavailable')
            state = self.inspect(account, conversation)
            ConsolePolicy.check_state(account, conversation, state, writing=True)
            if not isinstance(text, str) or not text.strip() or len(text) > 500 or not request_id:
                raise ConsoleError('invalid_reply_output')
            # Once the native port is entered, failure is uncertain. The service
            # has already persisted the request claim and never retries unknown.
            try:
                value = self.sender(account, conversation, text, request_id)
                self._scope(account, conversation)
                validate_send_outcome(value)
                return value
            except Exception:
                return dict(status='unknown', attempted=True, reason='send_outcome_uncertain')

    def close(self):
        with self.lock:
            if self.closer:
                closer, self.closer = self.closer, None
                closer()
