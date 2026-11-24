# LatentShield

Unified Physical Security Information Management (PSIM) prototype: four incompatible
security subsystems (alarm panel, camera feed, RFID reader, environmental sensor) are
normalized into a canonical event schema, temporally correlated in 10-second sliding
windows, scored with a live 0–100 Threat Risk Score, hardened with graceful degradation
(ONLINE/DEGRADED/OFFLINE watchdog) and a Dead-Letter Queue for invalid payloads.

## Setup (2 steps)

```bash
pip install -r requirements.txt
python run.py
```

Open http://localhost:8000 — backend API and SPA are served by the same process.

## Live demo walkthrough

1. **Overview** — watch the ambient simulator feed events; the risk gauge and health bar update live via WebSocket.
2. **Controls → Forced Intrusion** — injects door-forced + person + denied badge in `SERVER_ROOM`; the correlator should raise a CRITICAL `INTRUSION_NO_BADGE` incident.
3. **Controls → Fire / Smoke** — raises a CRITICAL fire incident in `DATA_VAULT`.
4. **Controls → Corrupt Payloads → DLQ** — malformed payloads land in the **Dead-Letter Queue** page with error reasons; nothing crashes. Use **Replay** to retry.
5. **Fault Injection** — set `CAMERA` to OFFLINE, then run the intrusion scenario: the rule requiring camera input is skipped and surviving incidents show reduced confidence.
6. **Event Stream** — click the `</>` icon on any row to see the canonical-vs-raw JSON diff (forensic audit).
7. **Export JSON** — download a full session snapshot.

## API

- `POST /api/ingest/{rfid|camera|alarm|env}` — vendor-specific raw payloads
- `GET /api/state` `/api/events` `/api/incidents` `/api/dlq`
- `POST /api/incidents/{id}/resolve`, `POST /api/dlq/{id}/replay`
- `POST /api/sim/scenario/{intrusion|fire|bad_payload}`, `/api/sim/pause|resume`, `/api/sim/fault/{source}?state=OFFLINE`
- `GET /api/export` — JSON session export
- `WS /ws` — live state broadcasts

## Tests

```bash
pytest tests/
```
