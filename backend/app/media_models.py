from typing import Literal
from pydantic import BaseModel, Field, StrictBool
from .role_models import JsonRequest, PreferencesQuery


class MediaSummary(BaseModel):
    media_id: str
    mime_type: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    frame_count: int


class MediaData(MediaSummary):
    data_base64: str


class Sticker(BaseModel):
    asset_id: str
    name: str
    description: str
    enabled: bool
    builtin: bool
    source: str | None = None
    revision: int
    original: MediaSummary
    media: MediaSummary


class StickerDetail(Sticker):
    original: MediaData
    media: MediaData


class StickerList(BaseModel):
    items: list[Sticker]


class StickerQuery(JsonRequest):
    asset_id: str = Field(min_length=1, max_length=160)


class StickerCreate(JsonRequest):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    enabled: StrictBool = True
    mime_type: str = Field(max_length=64)
    data_base64: str = Field(min_length=1, max_length=11184900)


class StickerUpdate(StickerQuery):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    enabled: StrictBool


class StickerDelete(StickerQuery):
    expected_revision: int = Field(ge=1)


class StickerDeleted(BaseModel):
    asset_id: str
    deleted: bool


class ReplyPart(BaseModel):
    index: int
    kind: Literal['text', 'image', 'sticker']
    text: str | None = None
    name: str | None = None
    media: MediaSummary | None = None


class ReplyMediaQuery(PreferencesQuery):
    run_id: str = Field(min_length=1, max_length=128)
    part_index: int = Field(ge=0)
