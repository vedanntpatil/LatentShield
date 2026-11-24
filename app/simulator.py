"""Background ambient telemetry generator + deterministic demo scenario injectors."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone

from .adapters import ADAPTERS
from .correlator import process_event
from .models import utcnow_iso
from .store import store

ZONES = ["SERVER_ROOM", "DATA_VAULT", "LAB_A", "MAIN_LOBBY"]
LOBBY_ZONES = ["MAIN_LOBBY", "LAB_A"]

_sim_task: asyncio.Task | None = None
_paused = False


def is_paused() -> bool:
    return _paused


def set_paused(v: bool) -> None:
    global _paused
    _paused = v


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ingest_raw(endpoint: str, payload: dict):
    """Same path as HTTP ingestion: validate -> adapt -> store -> correlate."""
    model_cls, fn = ADAPTERS[endpoint]
    try:
        p = model_cls.model_validate(payload)
        ev = fn(p)
    except Exception as e:
        store.add_dlq(endpoint, payload, f"{type(e).__name__}: {e}")
        return None
    store.add_event(ev)
    store.touch_source(ev.source_type)
    return process_event(ev)


async def _ambient_loop() -> None:
    """Emit realistic low-rate telemetry so the system feels alive."""
    while True:
        await asyncio.sleep(random.uniform(2.5, 5.0))
        if _paused:
            continue
        zone = random.choice(LOBBY_ZONES)
        roll = random.random()
        if roll < 0.45:
            _ingest_raw("env", {
                "node_id": f"ENV-THERM-{random.randint(1,9):02d}",
                "room": zone,
                "temp_celsius": round(random.uniform(21.0, 27.5), 1),
                "smoke_ppm": random.randint(5, 40),
                "humidity_pct": round(random.uniform(35, 55), 1),
                "reading_time": _now_iso(),
            })
        elif roll < 0.8:
            _ingest_raw("camera", {
                "cam_code": f"CAM-{random.randint(1,6):02d}",
                "location_tag": zone,
                "detected_class": random.choice(["person", "motion_only", "person"]),
                "person_count": 1,
                "face_verified": random.random() < 0.7,
                "confidence_pct": round(random.uniform(80, 99), 1),
                "captured_at": _now_iso(),
            })
        else:
            _ingest_raw("rfid", {
                "reader_id": f"RFID-{random.choice(['ENT','SRV','LAB'])}-01",
                "card_uid": f"EMP-{random.randint(1000,9999)}",
                "holder_name": random.choice(["Aarav Kulkarni", "Priya Nair", "Dev Patel", "Meera Iyer"]),
                "door_zone": zone,
                "access_result": "GRANTED",
                "swipe_epoch": int(datetime.now(timezone.utc).timestamp()),
            })


async def start() -> None:
    global _sim_task
    if _sim_task is None or _sim_task.done():
        _sim_task = asyncio.create_task(_ambient_loop())


async def stop() -> None:
    if _sim_task:
        _sim_task.cancel()
        try:
            await _sim_task
        except asyncio.CancelledError:
            pass


# ---------------- Deterministic demo scenarios ----------------

def scenario_intrusion() -> dict:
    """Forced door + person + denied badge in SERVER_ROOM -> critical incident."""
    t = datetime.now(timezone.utc)
    results = []
    results.append(_ingest_raw("alarm", {
        "panel_serial": "ALM-PNL-A", "area": "SERVER_ROOM",
        "signal_code": "DOOR_FORCED_OPEN", "alarm_active": True,
        "priority_level": 1, "ts_iso": t.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }))
    results.append(_ingest_raw("camera", {
        "cam_code": "CAM-02", "location_tag": "SERVER_ROOM",
        "detected_class": "person", "person_count": 1,
        "face_verified": False, "confidence_pct": 97.2,
        "captured_at": t.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }))
    results.append(_ingest_raw("rfid", {
        "reader_id": "RFID-SRV-01", "card_uid": "EMP-0000",
        "holder_name": "Unknown Badge", "door_zone": "SERVER_ROOM",
        "access_result": "DENIED", "swipe_epoch": int(t.timestamp()),
    }))
    return {"scenario": "intrusion", "events": 3, "incidents": sum(len(r or []) for r in results)}


def scenario_fire() -> dict:
    """Smoke + high temp + thermal alarm in DATA_VAULT -> critical incident."""
    t = datetime.now(timezone.utc)
    iso = t.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    results = [
        _ingest_raw("env", {"node_id": "ENV-THERM-04", "room": "DATA_VAULT",
                            "temp_celsius": 41.2, "smoke_ppm": 320.0,
                            "humidity_pct": 50.0, "reading_time": iso}),
        _ingest_raw("alarm", {"panel_serial": "ALM-PNL-B", "area": "DATA_VAULT",
                              "signal_code": "THERMAL_ALARM", "alarm_active": True,
                              "priority_level": 1, "ts_iso": iso}),
    ]
    return {"scenario": "fire", "events": 2, "incidents": sum(len(r or []) for r in results)}


def scenario_bad_payload() -> dict:
    """Send malformed payloads that must land in the DLQ, not crash."""
    bad = [
        ("camera", {"cam_code": "CAM-99", "location_tag": "MOON_BASE",
                    "detected_class": "alien", "captured_at": "not-a-date"}),
        ("alarm", {"panel_serial": "ALM-X", "area": "SERVER_ROOM",
                   "signal_code": "UNKNOWN_CODE", "ts_iso": "2026-13-99T99:99:99Z"}),
        ("rfid", {"reader_id": "RFID-X"}),  # missing required fields
    ]
    for ep, body in bad:
        _ingest_raw(ep, body)
    return {"scenario": "bad_payload", "dlq_entries_added": len(bad)}


SCENARIOS = {
    "intrusion": scenario_intrusion,
    "fire": scenario_fire,
    "bad_payload": scenario_bad_payload,
}


def run_scenario(name: str) -> dict:
    fn = SCENARIOS.get(name)
    if not fn:
        return {"error": f"unknown scenario {name}"}
    return fn()
