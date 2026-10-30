"""Provider configuration metadata only; no model requests are implemented."""
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from .role_models import JsonRequest

Protocol = Literal["openai_responses", "openai_chat_completions", "anthropic_messages", "gemini_generate_content"]
Provider = Literal["openai", "anthropic", "gemini", "grok", "deepseek", "custom"]

PRESETS = [
    dict(provider="openai", name="OpenAI", options=[
        dict(protocol="openai_responses", base_url="https://api.openai.com/v1"),
        dict(protocol="openai_chat_completions", base_url="https://api.openai.com/v1")]),
    dict(provider="anthropic", name="Anthropic", options=[
        dict(protocol="anthropic_messages", base_url="https://api.anthropic.com/v1")]),
    dict(provider="gemini", name="Gemini", options=[
        dict(protocol="gemini_generate_content", base_url="https://generativelanguage.googleapis.com/v1beta"),
        dict(protocol="openai_chat_completions", base_url="https://generativelanguage.googleapis.com/v1beta/openai")]),
    dict(provider="grok", name="Grok", options=[
        dict(protocol="openai_responses", base_url="https://api.x.ai/v1"),
        dict(protocol="openai_chat_completions", base_url="https://api.x.ai/v1")]),
    dict(provider="deepseek", name="DeepSeek", options=[
        dict(protocol="openai_chat_completions", base_url="https://api.deepseek.com"),
        dict(protocol="anthropic_messages", base_url="https://api.deepseek.com/anthropic/v1")]),
    dict(provider="custom", name="自定义", options=[dict(protocol=p, base_url="") for p in
        ("openai_responses", "openai_chat_completions", "anthropic_messages", "gemini_generate_content")]),
]


class ProfileFields(JsonRequest):
    name: str = Field(min_length=1, max_length=200)
    provider: Provider
    protocol: Protocol
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    anthropic_version: str = Field(default="2026-10-21", pattern=r"^\d{4}-\d{2}-\d{2}$")
    workspace_id: str = Field(default="", max_length=200, pattern=r"^[^\s\r\n]*$")

    @model_validator(mode="after")
    def valid_connection(self):
        self.name, self.model = self.name.strip(), self.model.strip()
        if not self.name or not self.model or any(ord(c) < 32 for c in self.model):
            raise ValueError("required_fields")
        self.base_url = self.base_url.strip().rstrip("/")
        url = urlsplit(self.base_url)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or "%" in url.netloc
                or any(c.isspace() or ord(c) < 32 for c in self.base_url)):
            raise ValueError("invalid_base_url")
        _ = url.port  # Reject malformed ports, too.
        # Base URLs must not embed credentials in a path or an encoded query.
        from urllib.parse import unquote
        lowered = unquote(self.base_url).lower()
        if any(part in lowered for part in ("sk-", "api_key", "apikey", "api-key", "access_token", "?", "#", "\\")):
            raise ValueError("credential_in_url")
        options = next(p["options"] for p in PRESETS if p["provider"] == self.provider)
        if self.protocol not in {o["protocol"] for o in options}:
            raise ValueError("provider_protocol_mismatch")
        return self


class ProfileCreate(ProfileFields):
    api_key: SecretStr = Field(min_length=1, max_length=8192)

    @model_validator(mode="after")
    def valid_key(self):
        key = self.api_key.get_secret_value() if self.api_key is not None else ""
        if not key.strip() or any(c.isspace() or ord(c) < 32 for c in key):
            raise ValueError("invalid_key")
        return self


class ProfileQuery(JsonRequest):
    profile_id: str = Field(min_length=1, max_length=128)


class ProfileDelete(ProfileQuery):
    expected_revision: int = Field(ge=1)


class ProfileUpdate(ProfileFields, ProfileDelete):
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)

    @model_validator(mode="after")
    def valid_optional_key(self):
        if "api_key" in self.model_fields_set:
            ProfileCreate.valid_key(self)
        return self


from .ai_execution_models import Verification


class ApiProfile(ProfileFields):
    model_config = ConfigDict(extra="forbid", strict=False)
    profile_id: str
    revision: int
    created_at: datetime
    updated_at: datetime
    is_default: bool
    usage_count: int
    has_key: bool
    key_readable: bool
    verification: Verification = Field(default_factory=Verification)


class PresetOption(BaseModel):
    protocol: Protocol
    base_url: str


class Preset(BaseModel):
    provider: Provider
    name: str
    options: list[PresetOption]


class ProfileList(BaseModel):
    items: list[ApiProfile]
    presets: list[Preset]


class ProfileDeleted(BaseModel):
    profile_id: str
    deleted: Literal[True] = True


class AiDefaults(BaseModel):
    role_card_code: str | None
    api_profile_id: str | None
    revision: int


class DefaultsUpdate(JsonRequest):
    expected_revision: int = Field(ge=0)
    role_card_code: str | None = Field(default=None, min_length=1, max_length=128)
    api_profile_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_patch(self):
        fields = self.model_fields_set & {"role_card_code", "api_profile_id"}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError("default_selection_required")
        return self
