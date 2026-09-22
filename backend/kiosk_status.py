"""Kiosk tablet heartbeat. Pi probes ADB and PATCHes Firestore.

Latest: ``ejdersted/kiosk_{site}`` on the hub's Firebase project (same REST
path as fossibot_garden). GET /api/kiosk/status is the fast local copy.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any

import fossibot_log
import kiosk_battery

DOC = "ejdersted/kiosk_{site}"
INTERVAL_S = 60.0

_cache: dict[str, Any] = {"adb": False, "error": "not probed"}


def cached() -> dict[str, Any]:
    return dict(_cache)


def probe(serial: str, *, now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    ts = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not serial:
        return {"ts": ts, "adb": False, "serial": "", "error": "no serial"}
    connect = _run(["adb", "connect", serial], timeout=5.0)
    state = _run(["adb", "-s", serial, "get-state"], timeout=5.0)
    adb = (state.get("out") or "").strip() == "device"
    bat = kiosk_battery.read_via_adb(serial) if adb else None
    error = ""
    if not adb:
        error = (state.get("err") or connect.get("err") or state.get("out") or "unreachable")[:160]
    return {
        "ts": ts,
        "adb": adb,
        "serial": serial,
        "state": (state.get("out") or "").strip()[:40],
        "batteryPercent": None if not bat else bat.get("percent"),
        "charging": None if not bat else bat.get("charging"),
        "error": error,
    }


def _run(cmd: list[str], *, timeout: float) -> dict[str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"out": "", "err": str(exc)[:160]}
    return {"out": proc.stdout or "", "err": (proc.stderr or "")[-160:]}


def remember(row: dict[str, Any]) -> dict[str, Any]:
    global _cache
    _cache = dict(row)
    return cached()


async def persist(
    row: dict[str, Any],
    *,
    site: str,
    firebase_config: dict[str, Any],
    http_client: Any,
) -> bool:
    path = DOC.format(site=site)
    return await fossibot_log._patch_docs(
        [path],
        row,
        firebase_config=firebase_config,
        http_client=http_client,
        label="kiosk",
    )
