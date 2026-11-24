"""Pydantic v2 models: heterogeneous raw inputs -> canonical schema."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

ZONES = {"SERVER_ROOM", "DATA_VAULT", "LAB_A", "MAIN_LOBBY"}
SOURCE_TYPES = {"ALARM", "CAMERA", "RFID", "ENV_SENSOR"}
SEVERITIES = {"INFO", "WARNING", "CRITICAL"}
EVENT_TYPES = {
    "DOOR_FORCED", "DOOR_HELD", "GLASS_BREAK", "PANIC", "PANEL_DISARM",
    "PERSON_DETECTED", "MULTI_PERSON", "LOITERING", "CAMERA_TAMPER",
    "ACCESS_GRANTED", "ACCESS_DENIED", "ACCESS_EXPIRED", "HIGH_TEMP",
    "SMOKE_ALERT", "NORMAL_TELEMETRY",
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


# ---------- Raw vendor payloads (section 4.1) ----------

class RfidPayload(BaseModel):
    reader_id: str
    card_uid: str
    holder_name: str
    door_zone: str
    access_result: str  # GRANTED | DENIED | EXPIRED_CARD
    swipe_epoch: int


class CameraPayload(BaseModel):
    cam_code: str
    location_tag: str
    detected_class: str  # person | multiple_persons | motion_only | loitering | camera_tamper
    person_count: int = 1
    face_verified: bool = False
    confidence_pct: float = 0.0
    captured_at: str


class AlarmPayload(BaseModel):
    panel_serial: str
    area: str
    signal_code: str
    alarm_active: bool = True
    priority_level: int = 1
    ts_iso: str


class EnvPayload(BaseModel):
    node_id: str
    room: str
    temp_celsius: float
    smoke_ppm: float = 0.0
    humidity_pct: float = 0.0
    reading_time: str


# ---------- Canonical schema ----------

class CanonicalEvent(BaseModel):
    event_id: str = Field(default_factory=new_event_id)
    timestamp: str
    source_type: str
    source_id: str
    zone_id: str
    event_type: str
    severity: str = "INFO"
    summary: str
    metrics: dict[str, Any] = Field(default_factory=lambda: {
        "person_count": None, "temp_celsius": None,
        "smoke_ppm": None, "confidence": None,
    })
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("zone_id")
    @classmethod
    def zone_valid(cls, v: str) -> str:
        if v not in ZONES:
            raise ValueError(f"unknown zone {v}")
        return v


class Incident(BaseModel):
    incident_id: str = Field(default_factory=lambda: f"inc_{uuid.uuid4().hex[:10]}")
    zone_id: str
    title: str
    severity: str = "WARNING"
    confidence: float = 0.5
    rule_id: str
    created_at: str
    updated_at: str
    resolved: bool = False
    contributing_event_ids: list[str] = Field(default_factory=list)
    detail: str = ""


class SourceHealth(BaseModel):
    source_type: str
    state: str = "ONLINE"  # ONLINE | DEGRADED | OFFLINE
    last_seen: Optional[str] = None
    events_total: int = 0
    last_error: Optional[str] = None


class DlqEntry(BaseModel):
    dlq_id: str = Field(default_factory=lambda: f"dlq_{uuid.uuid4().hex[:10]}")
    endpoint: str
    raw_body: Any
    error_reason: str
    timestamp: str = Field(default_factory=utcnow_iso)
