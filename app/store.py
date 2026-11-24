"""Central in-memory state manager with bounded ring buffers."""
from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, List, Optional

from .models import CanonicalEvent, DlqEntry, Incident, SourceHealth, utcnow_iso

MAX_EVENTS = 500
MAX_INCIDENTS = 100
MAX_DLQ = 100

SOURCE_TYPES = ["ALARM", "CAMERA", "RFID", "ENV_SENSOR"]


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: Deque[CanonicalEvent] = deque(maxlen=MAX_EVENTS)
        self.incidents: Deque[Incident] = deque(maxlen=MAX_INCIDENTS)
        self.dlq: Deque[DlqEntry] = deque(maxlen=MAX_DLQ)
        self.health: Dict[str, SourceHealth] = {
            s: SourceHealth(source_type=s) for s in SOURCE_TYPES
        }
        self.risk_score: float = 0.0
        self.risk_updated_at: str = utcnow_iso()
        self.stats = {"ingested": 0, "correlated": 0, "dlq": 0}
        self.ws_clients: List = []

    # ---- events / dlq ----
    def add_event(self, ev: CanonicalEvent) -> None:
        with self._lock:
            self.events.appendleft(ev)
            h = self.health[ev.source_type]
            h.events_total += 1
            self.stats["ingested"] += 1

    def add_dlq(self, endpoint: str, raw_body, reason: str) -> None:
        with self._lock:
            self.dlq.appendleft(DlqEntry(endpoint=endpoint, raw_body=raw_body,
                                          error_reason=reason))
            self.stats["dlq"] += 1

    # ---- incidents ----
    def upsert_incident(self, inc: Incident) -> None:
        with self._lock:
            for i, existing in enumerate(self.incidents):
                if existing.incident_id == inc.incident_id:
                    self.incidents[i] = inc
                    return
            self.incidents.appendleft(inc)

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        with self._lock:
            for inc in self.incidents:
                if inc.incident_id == incident_id:
                    return inc
            return None

    # ---- health ----
    def touch_source(self, source_type: str) -> None:
        with self._lock:
            h = self.health[source_type]
            h.last_seen = utcnow_iso()
            if h.state == "OFFLINE":
                h.state = "ONLINE"
            h.last_error = None

    def set_health(self, source_type: str, state: str, error: Optional[str] = None) -> None:
        with self._lock:
            h = self.health[source_type]
            h.state = state
            h.last_error = error

    # ---- snapshots ----
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "risk_score": self.risk_score,
                "risk_updated_at": self.risk_updated_at,
                "events": [e.model_dump() for e in list(self.events)[:200]],
                "incidents": [i.model_dump() for i in list(self.incidents)[:50]],
                "dlq": [d.model_dump() for d in list(self.dlq)],
                "health": {k: v.model_dump() for k, v in self.health.items()},
                "stats": dict(self.stats),
            }


store = Store()
