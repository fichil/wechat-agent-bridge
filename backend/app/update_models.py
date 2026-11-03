from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class UpdateRelease(BaseModel):
    version: str
    release_id: int
    tag: str
    published_at: str
    notes: str
    url: str
    ready: bool
    reason: str | None = None


class UpdateTask(BaseModel):
    task_id: str
    version: str
    stage: str
    message: str = ''
    downloaded: int = 0
    total: int = 0
    updated_at: float


class UpdateState(BaseModel):
    mode: Literal['source', 'managed', 'portable']
    current_version: str
    current_commit: str
    releases_url: str
    can_install: bool
    checked_at: float | None = None
    checking: bool = False
    error: str | None = None
    latest: UpdateRelease | None = None
    task: UpdateTask | None = None


class UpdateInstall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(pattern=r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')
    release_id: int = Field(gt=0)
