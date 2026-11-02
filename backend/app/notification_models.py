"""Administrator-only SMTP configuration; secrets are write-only."""
import re
from typing import Literal
from pydantic import BaseModel, Field, SecretStr, StrictBool, model_validator
from .role_models import JsonRequest


class NotificationFields(JsonRequest):
    enabled: StrictBool = False
    recipient: str = Field(default='', max_length=254)
    sender: str = Field(default='', max_length=254)
    host: str = Field(default='', max_length=253)
    port: int = Field(default=465, strict=True, ge=1, le=65535)
    username: str = Field(default='', max_length=254)
    security: Literal['ssl', 'starttls'] = 'ssl'

    @model_validator(mode='after')
    def valid(self):
        for field in ('recipient', 'sender', 'host', 'username'):
            value = getattr(self, field).strip()
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('invalid_mail_settings')
            setattr(self, field, value)
        for value in (self.recipient, self.sender):
            if value and not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value):
                raise ValueError('invalid_email')
        if self.host and not re.fullmatch(r'[A-Za-z0-9.-]+', self.host):
            raise ValueError('invalid_smtp_host')
        if self.enabled and not all((self.recipient, self.sender, self.host, self.username)):
            raise ValueError('incomplete_mail_settings')
        return self


class NotificationUpdate(NotificationFields):
    expected_revision: int = Field(ge=0)
    password: SecretStr | None = Field(default=None, max_length=8192)


class NotificationResult(BaseModel):
    notification_id: str
    status: Literal['queued', 'sending', 'sent', 'failed', 'unknown', 'cancelled']
    attempts: int = 0
    message: str | None = None
    next_at: float | None = None


class NotificationSettings(NotificationFields):
    revision: int
    has_password: bool
    password_readable: bool
    latest: NotificationResult | None = None


class NotificationTest(JsonRequest):
    expected_revision: int = Field(ge=0)
