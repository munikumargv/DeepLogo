"""Wire formats shared by the edge device and the server.

The device alert is the *only* thing that ever leaves a SafeSound device.
Every model forbids unknown fields, so a payload that tries to carry audio,
a transcript or any other free text is rejected by the server.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_ALERT_BYTES = 4096  # alerts are tiny; anything larger is refused


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeywordCategory(str, Enum):
    drug_name = "drug_name"
    drug_slang = "drug_slang"
    deal_phrase = "deal_phrase"
    hiding_phrase = "hiding_phrase"


class SensorKind(str, Enum):
    vape = "vape"
    smoke = "smoke"
    aerosol_spike = "aerosol_spike"  # small spike, e.g. air freshener / deodorant


class LocalRisk(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class KeywordHit(Strict):
    """A match against the school's configured list. Carries the term id only,
    never the words around it."""

    term_id: int
    category: KeywordCategory
    count: int = Field(1, ge=1, le=50)


class SensorHit(Strict):
    kind: SensorKind
    confidence: float = Field(ge=0, le=1)
    duration_s: int = Field(ge=0, le=3600)
    peak_pm2_5: float = Field(ge=0, le=5000)
    peak_voc_index: float = Field(ge=0, le=500)


class DeviceAlert(Strict):
    """type + time + location + risk level, and nothing else."""

    device_id: str = Field(max_length=64)
    ts: datetime
    location_id: str = Field(max_length=64)
    types: list[Literal["keyword", "smoke", "vape", "aerosol_spike"]] = Field(min_length=1, max_length=4)
    local_risk: LocalRisk
    keywords: list[KeywordHit] = Field(default_factory=list, max_length=20)
    sensor: SensorHit | None = None
    keyword_list_version: int = 0


class VideoCue(str, Enum):
    """Behaviour cues produced by video analytics on cameras *outside*
    sensitive areas (corridors, washroom entrances, yards, bus aisle)."""

    group_entry = "group_entry"          # 3+ students enter together
    long_dwell = "long_dwell"            # stay 10+ minutes
    hand_exchange = "hand_exchange"
    small_group = "small_group"
    gathering = "gathering"
    fast_exit = "fast_exit"              # group exits quickly
    back_seat = "back_seat"              # student at back of bus
    normal_activity = "normal_activity"
    cleaner_entry = "cleaner_entry"      # cleaning staff entering - explains aerosol spikes


class VideoEvent(Strict):
    camera_id: str = Field(max_length=64)
    ts: datetime
    cue: VideoCue
    confidence: float = Field(0.9, ge=0, le=1)
