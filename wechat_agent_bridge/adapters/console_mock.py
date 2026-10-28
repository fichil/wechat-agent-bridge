"""Synthetic multi-account desktop. No desktop or network imports."""

import base64
from dataclasses import replace
from datetime import datetime, timezone
import io
import threading
import uuid
import wave

from ..console_models import Account, Attachment, ChatMessage, Conversation, ConsoleError, MessagePage


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class MockConsoleAdapter:
    simulation_only = True

    def __init__(self):
        self.lock = threading.RLock()
        self.accounts = [Account("mock-a", "演示账号 A", "synthetic_a", "mock-window-a", "mock-1", verified=True),
                         Account("mock-b", "演示账号 B", "synthetic_b", "mock-window-b", "mock-1", verified=True)]
        self.chats = {}
        self.events = {}
        self.assets = {}
        self.states = {}
        self.sent = []
        self.outcome = "ui_confirmed"
        for account in self.accounts:
            self.chats[account.id] = [Conversation(account.id, "friend", "产品讨论", "private", "synthetic_friend", 2),
                                      Conversation(account.id, "group", "项目协作群", "group", unread=1)]
            for chat in self.chats[account.id]:
                self.events[(account.id, chat.id)] = [
                    ChatMessage(account.id, chat.id, "initial-1", "协作者", "这是合成聊天。授权后可在下方注入新消息，验证触发和记忆。", timestamp(), historical=True),
                    ChatMessage(account.id, chat.id, "initial-2", "我", "收到，我会查看计划。", timestamp(), own=True, historical=True)]
                self.states[(account.id, chat.id)] = {"connected": True, "identity_certain": True, "has_draft": False, "user_active": False}
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6eZYAAAAASUVORK5CYII=")
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\0"*1600)
        for kind, name, mime, data in (("image", "合成图片.png", "image/png", png),
                                      ("voice", "合成静音.wav", "audio/wav", audio.getvalue()),
                                      ("file", "合成任务.csv", "text/csv", "任务,状态\n控制台,开发中\n".encode())):
            self.inject("mock-a", "friend", "合成附件", kind=kind, attachment=(name, mime, data), historical=True, event_id="initial-"+kind)

    def _check(self, account, conversation=None):
        if account not in self.chats or (conversation is not None and (account, conversation) not in self.events):
            raise ConsoleError("target_mismatch")

    def discover(self):
        return list(self.accounts)

    def conversations(self, account_id):
        self._check(account_id)
        with self.lock:
            return [replace(chat, preview=self.events[(account_id, chat.id)][-1].text,
                            updated_at=self.events[(account_id, chat.id)][-1].timestamp) for chat in self.chats[account_id]]

    def messages(self, account_id, conversation_id, cursor=None):
        self._check(account_id, conversation_id)
        with self.lock:
            events = self.events[(account_id, conversation_id)]
            end = int(cursor) if cursor is not None else len(events)
            start = max(0, end-50)
            return MessagePage(tuple(events[start:end]), str(start) if start else None, start == 0)

    def attachment(self, account_id, conversation_id, attachment_id):
        self._check(account_id, conversation_id)
        try:
            return self.assets[(account_id, conversation_id, attachment_id)]
        except KeyError:
            raise ConsoleError("attachment_unavailable") from None

    def inspect(self, account_id, conversation_id):
        self._check(account_id, conversation_id)
        return {"account_id": account_id, "conversation_id": conversation_id, **self.states[(account_id, conversation_id)]}

    def send(self, account_id, conversation_id, text, request_id):
        state = self.inspect(account_id, conversation_id)
        if not state["connected"] or not state["identity_certain"] or state["has_draft"] or state["user_active"]:
            return {"status": "failed", "reason": "preflight_changed", "attempted": False}
        self.sent.append(request_id)
        if self.outcome == "timeout":
            raise TimeoutError()
        if self.outcome == "ui_confirmed":
            self.inject(account_id, conversation_id, text, own=True)
        return {"status": self.outcome, "reason": "mock_"+self.outcome, "attempted": True}

    def inject(self, account_id, conversation_id, text, *, own=False, mentions=False, kind="text", attachment=None, historical=False, event_id=None):
        self._check(account_id, conversation_id)
        attachments = ()
        with self.lock:
            if attachment:
                name, mime, data = attachment
                key = event_id+"-asset" if event_id else uuid.uuid4().hex
                self.assets[(account_id, conversation_id, key)] = data
                attachments = (Attachment(key, name, mime, len(data)),)
            message = ChatMessage(account_id, conversation_id, event_id or uuid.uuid4().hex, "我" if own else "协作者", text,
                                  timestamp(), own, kind, mentions, attachments, historical)
            self.events[(account_id, conversation_id)].append(message)
            return message

    def close(self):
        pass
