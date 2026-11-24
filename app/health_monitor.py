"""Async heartbeat watchdog: ONLINE -> DEGRADED -> OFFLINE transitions.

Each source is expected to emit at least one event every HEARTBEAT_SECS.
- no event for DEGRADED_AFTER secs  -> DEGRADED
- no event for OFFLINE_AFTER secs   -> OFFLINE
Sources that never reported start as ONLINE (fresh boot) and age out.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .store import SOURCE_TYPES, store

DEGRADED_AFTER = 20.0   # seconds without events
OFFLINE_AFTER = 45.0    # seconds without events
WATCH_INTERVAL = 5.0


def _parse_ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


class HealthMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._boot_time = datetime.now(timezone.utc).timestamp()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(WATCH_INTERVAL)
            now = datetime.now(timezone.utc).timestamp()
            changed = False
            for st in SOURCE_TYPES:
                h = store.health[st]
                last = _parse_ts(h.last_seen)
                if last == 0.0:
                    # never seen: grace period from boot, then degrade
                    age = now - self._boot_time
                else:
                    age = now - last
                if age >= OFFLINE_AFTER:
                    new = "OFFLINE"
                elif age >= DEGRADED_AFTER:
                    new = "DEGRADED"
                else:
                    new = "ONLINE"
                if h.state != new:
                    store.set_health(st, new)
                    changed = True
            # correlator reads store.health each call; notify on transitions
            if changed:
                from .main import broadcast_state  # late import avoids cycle
                await broadcast_state()


health_monitor = HealthMonitor()
