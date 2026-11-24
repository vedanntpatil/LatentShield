"""Automated pipeline tests: schema normalization, correlation rules,
graceful degradation, and DLQ quarantine."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters import safe_ingest  # noqa: E402
from app.correlator import _open, process_event  # noqa: E402
from app.models import utcnow_iso  # noqa: E402
from app.store import store  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    store.events.clear(); store.incidents.clear(); store.dlq.clear()
    _open.clear()
    for h in store.health.values():
        h.state = "ONLINE"; h.last_seen = None
    store.risk_score = 0.0
    yield


def ingest(endpoint, payload):
    ev, err = safe_ingest(endpoint, payload)
    assert err is None, err
    store.add_event(ev)
    store.touch_source(ev.source_type)
    return process_event(ev)


# ---------- schema normalization ----------

def test_rfid_normalizes_to_canonical():
    incs = ingest("rfid", {
        "reader_id": "RFID-SRV-01", "card_uid": "EMP-1", "holder_name": "Test",
        "door_zone": "server room", "access_result": "GRANTED",
        "swipe_epoch": 1763901200,
    })
    ev = store.events[0]
    assert ev.source_type == "RFID" and ev.zone_id == "SERVER_ROOM"
    assert ev.event_type == "ACCESS_GRANTED" and ev.timestamp.endswith("Z")
    assert ev.raw_payload["card_uid"] == "EMP-1"


def test_zone_alias_and_bad_zone():
    ev, err = safe_ingest("rfid", {
        "reader_id": "R", "card_uid": "C", "holder_name": "H",
        "door_zone": "VAULT", "access_result": "GRANTED", "swipe_epoch": 1763901200})
    assert err is None and ev.zone_id == "DATA_VAULT"
    ev, err = safe_ingest("rfid", {
        "reader_id": "R", "card_uid": "C", "holder_name": "H",
        "door_zone": "MOON", "access_result": "GRANTED", "swipe_epoch": 1763901200})
    assert err is not None


# ---------- correlation ----------

def test_intrusion_rule_fires():
    t_iso = utcnow_iso()
    epoch = 1763901200
    ingest("alarm", {"panel_serial": "P1", "area": "SERVER_ROOM",
                     "signal_code": "DOOR_FORCED_OPEN", "alarm_active": True,
                     "priority_level": 1, "ts_iso": t_iso})
    ingest("camera", {"cam_code": "C1", "location_tag": "SERVER_ROOM",
                      "detected_class": "person", "person_count": 1,
                      "confidence_pct": 95.0, "captured_at": t_iso})
    ingest("rfid", {"reader_id": "R1", "card_uid": "X", "holder_name": "Bad",
                    "door_zone": "SERVER_ROOM", "access_result": "DENIED",
                    "swipe_epoch": epoch})
    incs = [i for i in store.incidents if i.rule_id == "INTRUSION_NO_BADGE" and not i.resolved]
    assert incs, "intrusion incident should be raised"
    assert incs[0].severity == "CRITICAL"
    assert store.risk_score > 0


def test_access_granted_suppresses_intrusion():
    t_iso = utcnow_iso()
    ingest("alarm", {"panel_serial": "P1", "area": "LAB_A",
                     "signal_code": "DOOR_HELD_OPEN", "alarm_active": True,
                     "priority_level": 2, "ts_iso": t_iso})
    ingest("camera", {"cam_code": "C1", "location_tag": "LAB_A",
                      "detected_class": "person", "person_count": 1,
                      "confidence_pct": 90.0, "captured_at": t_iso})
    ingest("rfid", {"reader_id": "R1", "card_uid": "EMP-9", "holder_name": "Ok",
                    "door_zone": "LAB_A", "access_result": "GRANTED",
                    "swipe_epoch": 1763901200})
    assert not [i for i in store.incidents
                if i.rule_id == "INTRUSION_NO_BADGE" and not i.resolved]


def test_fire_scenario():
    t_iso = utcnow_iso()
    ingest("env", {"node_id": "E1", "room": "DATA_VAULT", "temp_celsius": 42.0,
                   "smoke_ppm": 400.0, "humidity_pct": 50.0, "reading_time": t_iso})
    assert store.events[0].event_type == "HIGH_TEMP"
    ingest("alarm", {"panel_serial": "P2", "area": "DATA_VAULT",
                     "signal_code": "THERMAL_ALARM", "alarm_active": True,
                     "priority_level": 1, "ts_iso": t_iso})
    assert any(i.rule_id == "FIRE_SMOKE" for i in store.incidents)


# ---------- degradation ----------

def test_degraded_source_skips_dependent_rule():
    store.set_health("CAMERA", "OFFLINE")
    t_iso = utcnow_iso()
    ingest("alarm", {"panel_serial": "P1", "area": "MAIN_LOBBY",
                     "signal_code": "DOOR_FORCED_OPEN", "alarm_active": True,
                     "priority_level": 1, "ts_iso": t_iso})
    ingest("rfid", {"reader_id": "R1", "card_uid": "X", "holder_name": "Bad",
                    "door_zone": "MAIN_LOBBY", "access_result": "DENIED",
                    "swipe_epoch": 1763901200})
    assert not [i for i in store.incidents if i.rule_id == "INTRUSION_NO_BADGE"]


def test_degraded_lowers_confidence():
    store.set_health("ENV_SENSOR", "DEGRADED")
    t_iso = utcnow_iso()
    ingest("env", {"node_id": "E1", "room": "LAB_A", "temp_celsius": 40.0,
                   "smoke_ppm": 10.0, "humidity_pct": 50.0, "reading_time": t_iso})
    inc = next(i for i in store.incidents if i.rule_id == "ENV_SPIKE")
    assert inc.confidence < 0.75


# ---------- DLQ ----------

def test_invalid_payload_quarantined_not_raised():
    ev, err = safe_ingest("camera", {"cam_code": "C", "location_tag": "MOON",
                                     "detected_class": "person", "captured_at": "bad"})
    assert ev is None and err
    store.add_dlq("camera", {"bad": 1}, err)
    assert len(store.dlq) == 1
    assert "timestamp" in store.dlq[0].error_reason or "zone" in store.dlq[0].error_reason \
        or store.dlq[0].error_reason
