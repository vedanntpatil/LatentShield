"""Vendor-specific adapters normalizing heterogeneous payloads to CanonicalEvent.

Each adapter preserves the original raw payload for forensic auditing and
raises exceptions that main.py routes into the Dead-Letter Queue (never 500).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    AlarmPayload, CameraPayload, CanonicalEvent, EnvPayload, RfidPayload,
    utcnow_iso,
)

HIGH_TEMP_C = 35.0
SMOKE_ALERT_PPM = 150.0


def _epoch_to_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(s: str) -> str:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except Exception as e:
        raise ValueError(f"unparseable timestamp {s!r}: {e}")


def _normalize_zone(raw: str) -> str:
    z = (raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {"SERVERROOM": "SERVER_ROOM", "VAULT": "DATA_VAULT",
               "LABA": "LAB_A", "LOBBY": "MAIN_LOBBY"}
    z = aliases.get(z, z)
    if z not in {"SERVER_ROOM", "DATA_VAULT", "LAB_A", "MAIN_LOBBY"}:
        raise ValueError(f"unknown zone {raw!r}")
    return z


def adapt_rfid(p: RfidPayload) -> CanonicalEvent:
    result = p.access_result.strip().upper()
    if result == "GRANTED":
        etype, sev, summary = "ACCESS_GRANTED", "INFO", f"Access granted: {p.holder_name} ({p.card_uid}) at {p.door_zone}"
    elif result == "EXPIRED_CARD":
        etype, sev, summary = "ACCESS_EXPIRED", "WARNING", f"Expired card swipe by {p.holder_name} at {p.door_zone}"
    elif result == "DENIED":
        etype, sev, summary = "ACCESS_DENIED", "WARNING", f"Access denied for {p.holder_name} ({p.card_uid}) at {p.door_zone}"
    else:
        raise ValueError(f"unknown access_result {p.access_result!r}")
    return CanonicalEvent(
        timestamp=_epoch_to_iso(p.swipe_epoch),
        source_type="RFID", source_id=p.reader_id,
        zone_id=_normalize_zone(p.door_zone),
        event_type=etype, severity=sev, summary=summary,
        metrics={"person_count": None, "temp_celsius": None, "smoke_ppm": None,
                 "confidence": 1.0 if result == "GRANTED" else 0.7},
        raw_payload=p.model_dump(),
    )


def adapt_camera(p: CameraPayload) -> CanonicalEvent:
    cls = p.detected_class.strip().lower()
    mapping = {
        "person": ("PERSON_DETECTED", "INFO", f"Person detected in {p.location_tag} (conf {p.confidence_pct}%)"),
        "multiple_persons": ("MULTI_PERSON", "WARNING", f"Multiple persons detected in {p.location_tag}"),
        "motion_only": ("PERSON_DETECTED", "INFO", f"Motion detected in {p.location_tag}"),
        "loitering": ("LOITERING", "WARNING", f"Loitering detected in {p.location_tag}"),
        "camera_tamper": ("CAMERA_TAMPER", "CRITICAL", f"Camera tamper/obstruction at {p.cam_code}"),
    }
    if cls not in mapping:
        raise ValueError(f"unknown detected_class {p.detected_class!r}")
    etype, sev, summary = mapping[cls]
    return CanonicalEvent(
        timestamp=_parse_iso(p.captured_at),
        source_type="CAMERA", source_id=p.cam_code,
        zone_id=_normalize_zone(p.location_tag),
        event_type=etype, severity=sev, summary=summary,
        metrics={"person_count": p.person_count, "temp_celsius": None,
                 "smoke_ppm": None, "confidence": p.confidence_pct / 100.0},
        raw_payload=p.model_dump(),
    )


_ALARM_MAP = {
    "DOOR_FORCED_OPEN": ("DOOR_FORCED", "CRITICAL", "Door forced open"),
    "DOOR_HELD_OPEN": ("DOOR_HELD", "WARNING", "Door held open"),
    "GLASS_BREAK": ("GLASS_BREAK", "CRITICAL", "Glass break detected"),
    "PANIC_BUTTON": ("PANIC", "CRITICAL", "Panic button activated"),
    "THERMAL_ALARM": ("THERMAL_ALARM", "CRITICAL", "Thermal alarm"),
    "PANEL_DISARMED": ("PANEL_DISARM", "WARNING", "Panel disarmed"),
}


def adapt_alarm(p: AlarmPayload) -> CanonicalEvent:
    code = p.signal_code.strip().upper()
    if code not in _ALARM_MAP:
        raise ValueError(f"unknown signal_code {p.signal_code!r}")
    etype, sev, label = _ALARM_MAP[code]
    if not p.alarm_active and etype != "PANEL_DISARM":
        sev = "INFO"
    summary = f"{label} at {p.area} (panel {p.panel_serial}, priority {p.priority_level})"
    return CanonicalEvent(
        timestamp=_parse_iso(p.ts_iso),
        source_type="ALARM", source_id=p.panel_serial,
        zone_id=_normalize_zone(p.area),
        event_type=etype, severity=sev, summary=summary,
        metrics={"person_count": None, "temp_celsius": None, "smoke_ppm": None,
                 "confidence": 0.95},
        raw_payload=p.model_dump(),
    )


def adapt_env(p: EnvPayload) -> CanonicalEvent:
    ts = _parse_iso(p.reading_time)
    if p.temp_celsius >= HIGH_TEMP_C or p.smoke_ppm >= SMOKE_ALERT_PPM:
        etype = "HIGH_TEMP" if p.temp_celsius >= HIGH_TEMP_C else "SMOKE_ALERT"
        sev = "CRITICAL"
        summary = (f"Environmental alert in {p.room}: temp={p.temp_celsius}C, "
                   f"smoke={p.smoke_ppm}ppm, humidity={p.humidity_pct}%")
    else:
        etype, sev = "NORMAL_TELEMETRY", "INFO"
        summary = f"Telemetry from {p.node_id} in {p.room}: temp={p.temp_celsius}C, smoke={p.smoke_ppm}ppm"
    return CanonicalEvent(
        timestamp=ts, source_type="ENV_SENSOR", source_id=p.node_id,
        zone_id=_normalize_zone(p.room),
        event_type=etype, severity=sev, summary=summary,
        metrics={"person_count": None, "temp_celsius": p.temp_celsius,
                 "smoke_ppm": p.smoke_ppm, "confidence": 0.9},
        raw_payload=p.model_dump(),
    )


ADAPTERS = {
    "rfid": (RfidPayload, adapt_rfid),
    "camera": (CameraPayload, adapt_camera),
    "alarm": (AlarmPayload, adapt_alarm),
    "env": (EnvPayload, adapt_env),
}


def safe_ingest(endpoint: str, body: dict):
    """Validate + normalize; returns (event, error). Never raises to caller."""
    try:
        model_cls, fn = ADAPTERS[endpoint]
        payload = model_cls.model_validate(body)
        return fn(payload), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
