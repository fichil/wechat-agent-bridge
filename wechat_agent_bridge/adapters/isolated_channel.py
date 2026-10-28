"""Qualification contract for a dedicated composer, never a main-window fallback.

No live Windows implementation is promoted by importing this module. Evidence
is supplied by a native binding owner, not by a console/HTTP capability flag.
"""
from dataclasses import dataclass

from ..console_models import ConsoleError


@dataclass(frozen=True)
class IsolationProof:
    fingerprint: tuple
    account: str
    conversation: str
    source_window: int
    channel_window: int
    source_input: tuple
    channel_input: tuple
    observations: frozenset
    build: str

    def require(self):
        required = {'drafts_both_directions', 'caret_selection_preserved', 'ime_preserved',
                    'source_navigation_preserved', 'focus_preserved', 'clipboard_untouched',
                    'send_echo', 'cleanup', 'same_build_recovery'}
        if (not self.fingerprint or not self.account or not self.conversation or not self.build or
                not self.source_window or not self.channel_window or self.source_window == self.channel_window or
                not self.source_input or not self.channel_input or self.source_input == self.channel_input or
                not required.issubset(self.observations)):
            raise ConsoleError('isolated_send_unverified')


class IsolatedChannel:
    """Only the dedicated port can be touched; human composer is not an argument."""
    def __init__(self, proof, *, current_binding, inspector, sender):
        proof.require()
        self.proof, self.current_binding = proof, current_binding
        self.inspector, self.sender = inspector, sender

    def _check(self, account, conversation):
        self.proof.require()
        if ((account, conversation) != (self.proof.account, self.proof.conversation) or
                self.current_binding() != self.proof):
            raise ConsoleError('isolated_channel_binding_changed')

    def status(self, account, conversation):
        self._check(account, conversation)
        return dict(concurrent_read=True, independent_read=True, isolated_send=True,
                    draft_isolation_verified=True, send_ready=True, reason=None)

    def send(self, account, conversation, text, request_id, checkpoint=lambda: None):
        self._check(account, conversation)
        checkpoint()
        state = self.inspector()
        if (state.get('input_runtime_id') != self.proof.channel_input or
                state.get('window') != self.proof.channel_window or
                state.get('account') != account or state.get('conversation') != conversation):
            raise ConsoleError('isolated_channel_binding_changed')
        if state.get('draft_empty') is not True:
            raise ConsoleError('isolated_channel_draft_conflict')
        self._check(account, conversation)
        # The sender must implement its own atomic target/draft checks and
        # durable-before-write checkpoints. No generic SetValue here.
        try:
            result = self.sender(text, request_id, checkpoint)
            self._check(account, conversation)
            if (not isinstance(result, dict) or result.get('status') not in ('failed','unknown','ui_confirmed') or
                    type(result.get('attempted')) is not bool or
                    (result['status'] in ('unknown','ui_confirmed') and not result['attempted'])):
                raise ValueError('invalid_send_outcome')
            return result
        except Exception:
            return dict(status='unknown', attempted=True, reason='isolated_send_outcome_uncertain')
