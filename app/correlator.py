"""10-second sliding-window temporal correlation + dynamic risk scoring.

Correlation runs per zone. If a source is DEGRADED/OFFLINE the rules that
depend on it are skipped and incident confidence is lowered transparently.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone

from .models import CanonicalEvent, Incident, utcnow_iso
from .store import store

WINDOW_SECS = 10.0
INCIDENT_TTL_SECS = 120.0

# incident_id per zone+rule to allow updates within TTL
_open: dict[str, Incident] = {}
_zone_events: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
_zone_rule_seen: dict[str, dict[str, float]] = defaultdict(dict)

# rule -> (sources required, base severity, base confidence)
RULES = {
    "INTRUSION_NO_BADGE": {
        "needs": {"ALARM", "CAMERA", "RFID"},
        "severity": "CRITICAL", "confidence": 0.9,
        "title": "Forced entry without authorized badge",
        "detail": "Door forced/held open + person detected with no valid RFID grant in window.",
        "match": lambda evs: any(e.event_type in {"DOOR_FORCED", "DOOR_HELD", "GLASS_BREAK"} for e in evs)
        and any(e.event_type in {"PERSON_DETECTED", "MULTI_PERSON", "LOITERING"} for e in evs)
        and not any(e.event_type == "ACCESS_GRANTED" for e in evs),
    },
    "FIRE_SMOKE": {
        "needs": {"ENV_SENSOR", "ALARM"},
        "severity": "CRITICAL", "confidence": 0.95,
        "title": "Fire / smoke emergency",
        "detail": "Smoke or high-temperature reading correlated with thermal alarm.",
        "match": lambda evs: any(e.event_type in {"SMOKE_ALERT", "HIGH_TEMP", "THERMAL_ALARM"} for e in evs)
        and any(e.source_type in {"ENV_SENSOR", "ALARM"} for e in evs),
    },
    "BURGLARY_SUSPECT": {
        "needs": {"ALARM", "CAMERA"},
        "severity": "WARNING", "confidence": 0.6,
        "title": "Suspicious activity after hours",
        "detail": "Alarm + camera activity without corroborating access grant.",
        "match": lambda evs: any(e.event_type in {"DOOR_HELD", "GLASS_BREAK", "PANEL_DISARM"} for e in evs)
        and any(e.source_type == "CAMERA" for e in evs),
    },
    "ENV_SPIKE": {
        "needs": {"ENV_SENSOR"},
        "severity": "WARNING", "confidence": 0.75,
        "title": "Environmental threshold breach",
        "detail": "Temperature or smoke reading exceeded safe threshold.",
        "match": lambda evs: any(e.event_type in {"SMOKE_ALERT", "HIGH_TEMP"} for e in evs),
    },
    "ACCESS_ANOMALY": {
        "needs": {"RFID"},
        "severity": "WARNING", "confidence": 0.55,
        "title": "Access anomalies cluster",
        "detail": "Repeated denied/expired swipes in short window.",
        "match": lambda evs: sum(1 for e in evs if e.event_type in {"ACCESS_DENIED", "ACCESS_EXPIRED"}) >= 2,
    },
    "CAM_TAMPER": {
        "needs": {"CAMERA"},
        "severity": "WARNING", "confidence": 0.8,
        "title": "Camera tamper / obstruction",
        "detail": "Camera reported tamper or obstruction.",
        "match": lambda evs: any(e.event_type == "CAMERA_TAMPER" for e in evs),
    },
}


def _parse_iso(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return datetime.now(timezone.utc).timestamp()


def _ts(ev) -> float:
    return _parse_iso(getattr(ev, "timestamp", getattr(ev, "updated_at", "")))


def _source_state(source_type: str) -> str:
    h = store.health.get(source_type)
    return h.state if h else "ONLINE"


def _degraded_penalty() -> float:
    """Lower confidence transparently when feeds are degraded/offline."""
    penalty = 0.0
    for h in store.health.values():
        if h.state == "DEGRADED":
            penalty += 0.15
        elif h.state == "OFFLINE":
            penalty += 0.35
    return min(penalty, 0.6)


def process_event(ev: CanonicalEvent) -> list[Incident]:
    """Add event to zone window, evaluate rules, update incidents + risk."""
    now = _ts(ev)
    zone = ev.zone_id
    dq = _zone_events[zone]
    dq.append(ev)
    cutoff = now - WINDOW_SECS
    while dq and _ts(dq[0]) < cutoff:
        dq.popleft()

    degraded = _degraded_penalty()
    new_or_updated: list[Incident] = []

    # a valid badge grant clears open intrusion incidents in this zone
    if ev.event_type == "ACCESS_GRANTED":
        for key, inc in list(_open.items()):
            if inc.zone_id == zone and inc.rule_id == "INTRUSION_NO_BADGE" and not inc.resolved:
                inc.resolved = True
                inc.updated_at = utcnow_iso()
                inc.detail += " Cleared: authorized badge presented."
                store.upsert_incident(inc)
                del _open[key]
        _update_risk(now)
        return []

    for rule_id, rule in RULES.items():
        needed = rule["needs"]
        # skip rule entirely if any required source is OFFLINE (cannot verify)
        if any(_source_state(s) == "OFFLINE" for s in needed):
            continue
        # if a needed source is DEGRADED, run but penalize confidence
        if not rule["match"](list(dq)):
            continue
        conf = max(0.05, rule["confidence"] - degraded)
        sev = rule["severity"]
        if degraded >= 0.3:
            sev = "WARNING" if sev == "INFO" else sev  # keep CRITICAL visible
        key = f"{zone}:{rule_id}"
        existing = _open.get(key)
        if existing and not existing.resolved and \
                (now - _ts(existing) < INCIDENT_TTL_SECS):
            existing.updated_at = utcnow_iso()
            existing.confidence = max(existing.confidence, conf)
            if ev.event_id not in existing.contributing_event_ids:
                existing.contributing_event_ids.append(ev.event_id)
                existing.contributing_event_ids = existing.contributing_event_ids[-20:]
            store.upsert_incident(existing)
            new_or_updated.append(existing)
        else:
            inc = Incident(
                zone_id=zone, title=rule["title"], severity=sev,
                confidence=round(conf, 2), rule_id=rule_id,
                created_at=utcnow_iso(), updated_at=utcnow_iso(),
                contributing_event_ids=[ev.event_id], detail=rule["detail"],
            )
            _open[key] = inc
            store.upsert_incident(inc)
            new_or_updated.append(inc)
            store.stats["correlated"] += 1

    # expire stale open incidents
    for key, inc in list(_open.items()):
        if inc.resolved:
            continue
        if now - _parse_iso(inc.updated_at) > INCIDENT_TTL_SECS:
            inc.resolved = True
            inc.updated_at = utcnow_iso()
            store.upsert_incident(inc)
            del _open[key]

    _update_risk(now)
    return new_or_updated


def _update_risk(now: float) -> None:
    """Threat Risk Score 0-100 from active unresolved incidents + health penalties."""
    score = 0.0
    for inc in store.incidents:
        if inc.resolved:
            continue
        age = max(0.0, now - _ts(inc))
        decay = max(0.0, 1.0 - age / INCIDENT_TTL_SECS)
        weight = {"CRITICAL": 45.0, "WARNING": 22.0, "INFO": 8.0}[inc.severity]
        score += weight * decay * max(0.3, inc.confidence)
    # sensor health penalties: degraded/offline feeds reduce coverage -> risk up slightly
    for h in store.health.values():
        if h.state == "DEGRADED":
            score += 4.0
        elif h.state == "OFFLINE":
            score += 10.0
    store.risk_score = round(min(100.0, score), 1)
    store.risk_updated_at = utcnow_iso()
