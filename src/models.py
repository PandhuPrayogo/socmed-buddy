from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class CanonicalItem(BaseModel):
    id: int | None = None
    target_id: int
    platform: Literal["youtube", "tiktok", "instagram"]
    platform_item_id: str
    url: str
    author: str
    published_at: str
    title: str | None = None
    text: str | None = None
    duration_s: int | None = None
    metrics: dict[str, int] = Field(default_factory=dict)
    raw_json: dict = Field(default_factory=dict)
    content_hash: str = ""


class Claim(BaseModel):
    text: str
    item_ids: list[str] = Field(min_length=1)


class ItemAnalysis(BaseModel):
    item_id: str
    platform: Literal["youtube", "tiktok", "instagram"]
    topic: str
    summary: str
    hook_type: str | None = None
    format: str | None = None
    notable_signals: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class DigestSection(BaseModel):
    title: str
    claims: list[Claim]


class Digest(BaseModel):
    window_start: datetime
    window_end: datetime
    sections: list[DigestSection]
    items_analyzed: int
    items_skipped: int
