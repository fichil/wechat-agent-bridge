"""Shared JSON-safe service error. Runtime services live in runtime.py."""


class ConversationError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(code, message, status)
