"""Character Card V2 documents and local conversation preferences (JSON only)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, model_validator


class Extensible(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class CharacterBookEntry(Extensible):
    keys: list[str]
    content: str
    extensions: dict[str, JsonValue]
    enabled: bool
    insertion_order: int | float
    case_sensitive: bool | None = None
    name: str | None = None
    priority: int | float | None = None
    id: int | float | None = None
    comment: str | None = None
    selective: bool | None = None
    secondary_keys: list[str] | None = None
    constant: bool | None = None
    position: Literal["before_char", "after_char"] | None = None


class CharacterBook(Extensible):
    extensions: dict[str, JsonValue]
    entries: list[CharacterBookEntry]
    name: str | None = None
    description: str | None = None
    scan_depth: int | float | None = None
    token_budget: int | float | None = None
    recursive_scanning: bool | None = None


class CharacterData(Extensible):
    name: str = Field(min_length=1, max_length=200)
    description: str
    personality: str
    scenario: str
    first_mes: str
    mes_example: str
    creator_notes: str
    system_prompt: str
    post_history_instructions: str
    alternate_greetings: list[str]
    character_book: CharacterBook | None = None
    tags: list[str]
    creator: str
    character_version: str
    extensions: dict[str, JsonValue]

    @model_validator(mode="after")
    def nonblank_name(self):
        if not self.name.strip():
            raise ValueError("name_required")
        return self


class CharacterCard(Extensible):
    spec: Literal["chara_card_v2"]
    spec_version: Literal["2.0"]
    data: CharacterData


class JsonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CardQuery(JsonRequest):
    card_code: str = Field(min_length=1, max_length=128)


class CardCreate(JsonRequest):
    card: CharacterCard


class CardDelete(CardQuery):
    expected_revision: int = Field(ge=1)


class CardUpdate(CardDelete):
    card: CharacterCard


class CardSummary(BaseModel):
    is_default: bool = False
    card_code: str
    name: str
    usage_count: int
    revision: int
    created_at: datetime
    updated_at: datetime


class RoleCard(CardSummary):
    # Keep the stored JSON document intact, including absent optional fields.
    card: dict[str, JsonValue]


class CardList(BaseModel):
    items: list[CardSummary]


class CardDeleted(BaseModel):
    card_code: str
    deleted: Literal[True] = True


class PreferencesQuery(JsonRequest):
    account_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)


class PreferencesUpdate(PreferencesQuery):
    expected_revision: int = Field(ge=0)
    expected_memory_revision: int | None = Field(default=None, ge=0)
    ai_enabled: StrictBool | None = None
    role_card_code: str | None = Field(default=None, min_length=1, max_length=128)
    api_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    memory: str | None = None
    scan_interval_seconds: int | None = Field(default=None, strict=True, ge=1, le=3600)
    group_proactive_message_threshold: int | None = Field(default=None, strict=True, ge=0, le=1000)

    @model_validator(mode="after")
    def valid_patch(self):
        changes = self.model_fields_set & {"ai_enabled", "role_card_code", "api_profile_id", "memory", "scan_interval_seconds", "group_proactive_message_threshold"}
        if not changes or any(getattr(self, key) is None for key in changes - {"role_card_code", "api_profile_id"}):
            raise ValueError("invalid_preferences_patch")
        return self


class ConfigurationIssue(BaseModel):
    code: str
    message: str


class EffectiveConfiguration(BaseModel):
    role_card_code: str | None
    role_card_name: str | None
    api_profile_id: str | None
    api_profile_name: str | None
    role_source: Literal["explicit", "default"]
    api_source: Literal["explicit", "default"]
    ready: bool
    issues: list[ConfigurationIssue]


class ConversationPreferences(PreferencesQuery):
    scan_interval_seconds: int = 2
    group_proactive_message_threshold: int = 0
    ai_enabled: bool
    role_card_code: str | None
    api_profile_id: str | None = None
    effective: EffectiveConfiguration | None = None
    memory: str
    revision: int
    memory_revision: int = 0
