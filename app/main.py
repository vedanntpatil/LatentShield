"""LatentShield FastAPI application: ingestion, REST API, WebSocket broadcasts."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapters import safe_ingest
from .correlator import process_event
from .health_monitor import health_monitor
from .models import utcnow_iso
from .simulator import SCENARIOS, run_scenario, set_paused, start as sim_start, stop as sim_stop
from .store import store

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class WSManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        await ws.send_text(json.dumps({"type": "snapshot", "data": store.snapshot()}))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict | None = None) -> None:
        msg = json.dumps(payload or {"type": "state", "data": store.snapshot()})
        async with self._lock:
            dead = []
            for ws in self._clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)


ws_manager = WSManager()


async def broadcast_state() -> None:
    await ws_manager.broadcast()


async def _ingest_and_broadcast(endpoint: str, body: dict) -> dict:
    ev, err = safe_ingest(endpoint, body)
    if err:
        store.add_dlq(endpoint, body, err)
        await broadcast_state()
        return {"status": "quarantined", "dlq": True, "reason": err}
    store.add_event(ev)
    store.touch_source(ev.source_type)
    incidents = process_event(ev)
    await broadcast_state()
    return {"status": "accepted", "event_id": ev.event_id,
            "event_type": ev.event_type, "zone_id": ev.zone_id,
            "incidents": [i.incident_id for i in incidents]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await health_monitor.start()
    await sim_start()
    yield
    await sim_stop()
    await health_monitor.stop()


app = FastAPI(title="LatentShield PSIM", lifespan=lifespan)


# ---------------- Ingestion endpoints ----------------

@app.post("/api/ingest/rfid")
async def ingest_rfid(request: Request):
    body = await _safe_json(request)
    return await _ingest_and_broadcast("rfid", body)


@app.post("/api/ingest/camera")
async def ingest_camera(request: Request):
    body = await _safe_json(request)
    return await _ingest_and_broadcast("camera", body)


@app.post("/api/ingest/alarm")
async def ingest_alarm(request: Request):
    body = await _safe_json(request)
    return await _ingest_and_broadcast("alarm", body)


@app.post("/api/ingest/env")
async def ingest_env(request: Request):
    body = await _safe_json(request)
    return await _ingest_and_broadcast("env", body)


async def _safe_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return {"_malformed_json": True}


# ---------------- State / inspection ----------------

@app.get("/api/state")
async def get_state():
    return store.snapshot()


@app.get("/api/events")
async def get_events(limit: int = 100):
    with store._lock:
        return {"events": [e.model_dump() for e in list(store.events)[:limit]]}


@app.get("/api/incidents")
async def get_incidents():
    return {"incidents": [i.model_dump() for i in store.incidents]}


@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    inc = store.get_incident(incident_id)
    if not inc:
        return JSONResponse({"error": "not found"}, status_code=404)
    inc.resolved = True
    inc.updated_at = utcnow_iso()
    store.upsert_incident(inc)
    from .correlator import _update_risk
    _update_risk(0.0)
    await broadcast_state()
    return {"status": "resolved", "incident_id": incident_id}


@app.get("/api/dlq")
async def get_dlq():
    return {"dlq": [d.model_dump() for d in store.dlq]}


@app.post("/api/dlq/{dlq_id}/replay")
async def replay_dlq(dlq_id: str):
    """Re-attempt ingestion of a DLQ entry through its original endpoint."""
    with store._lock:
        entry = next((d for d in store.dlq if d.dlq_id == dlq_id), None)
    if not entry:
        return JSONResponse({"error": "not found"}, status_code=404)
    result = await _ingest_and_broadcast(entry.endpoint, entry.raw_body)
    if result.get("status") == "accepted":
        with store._lock:
            store.dlq = type(store.dlq)([d for d in store.dlq if d.dlq_id != dlq_id],
                                        maxlen=store.dlq.maxlen)
    return {"replay": result}


# ---------------- Simulation controls ----------------

@app.get("/api/sim/status")
async def sim_status():
    from .simulator import is_paused
    return {"paused": is_paused(), "scenarios": list(SCENARIOS)}


@app.post("/api/sim/pause")
async def sim_pause():
    set_paused(True)
    await broadcast_state()
    return {"paused": True}


@app.post("/api/sim/resume")
async def sim_resume():
    set_paused(False)
    await broadcast_state()
    return {"paused": False}


@app.post("/api/sim/scenario/{name}")
async def sim_scenario(name: str):
    if name not in SCENARIOS:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)
    result = run_scenario(name)
    await broadcast_state()
    return result


@app.post("/api/sim/fault/{source}")
async def sim_fault(source: str, state: str = "OFFLINE"):
    """Manually force a source health state to demo graceful degradation."""
    key = source.upper()
    if key not in store.health:
        return JSONResponse({"error": "unknown source"}, status_code=404)
    if state not in {"ONLINE", "DEGRADED", "OFFLINE"}:
        return JSONResponse({"error": "bad state"}, status_code=400)
    store.set_health(key, state)
    from .correlator import _update_risk
    _update_risk(0.0)
    await broadcast_state()
    return {"source": key, "state": state}


@app.get("/api/export")
async def export_session():
    return JSONResponse(store.snapshot())


# ---------------- WebSocket ----------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
    except Exception:
        await ws_manager.disconnect(ws)


# ---------------- Static SPA ----------------

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
