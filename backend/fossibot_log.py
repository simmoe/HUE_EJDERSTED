"""Append-only Fossibot samples in Firestore (garden).

Latest snapshot: ``ejdersted/fossibot_garden``
History: ``ejdersted/fossibot_garden/samples/{yyyyMMddTHHmm}`` (UTC, 5-min bucket).
Policy events: ``ejdersted/fossibot_garden/events/{yyyyMMddTHHmmss}`` — one doc
each time the power policy presses the SwitchBot (``source``: floor at low
SoC, rule when charged, hold from a kiosk tap), so a dark hut can be explained
afterwards.

Writes follow the same REST path as ``security_garden``. Client SDKs must not
write these documents. Read is for later interpretation (SoC, solar, AC/USB).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

LATEST_DOC = "ejdersted/fossibot_garden"
SAMPLES_COLLECTION = "ejdersted/fossibot_garden/samples"
EVENTS_COLLECTION = "ejdersted/fossibot_garden/events"
DEFAULT_LOG_SEC = 300

_PORT_KEYS = ("online", "acOn", "usbOn", "charging")


def _firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def sample_id(now: datetime | None = None) -> str:
    stamp = now or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    stamp = stamp.astimezone(timezone.utc)
    minute = stamp.minute - (stamp.minute % 5)
    return stamp.replace(minute=minute, second=0, microsecond=0).strftime("%Y%m%dT%H%M")


def sample_fields(status: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return {
        "ts": stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "online": bool(status.get("online")),
        "socPercent": status.get("socPercent"),
        "solarWatts": status.get("solarWatts"),
        "outWatts": status.get("outWatts"),
        "usbOn": bool(status.get("usbOn")),
        "acOn": bool(status.get("acOn")),
        "charging": bool(status.get("charging")),
    }


def event_key(status: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(status.get(key) for key in _PORT_KEYS)


def event_id(now: datetime | None = None) -> str:
    stamp = now or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")


def policy_event_fields(
    status: dict[str, Any],
    *,
    want_ac_on: bool,
    threshold_percent: float | None,
    pressed: bool,
    source: str = "rule",
    error: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """One row per SwitchBot press the Pi makes. `kind` is what we wanted,
    `source` is why: floor (≤15 %), rule (≥25 %) or hold (a tap on the kiosk)."""
    stamp = now or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return {
        "ts": stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "ac_on" if want_ac_on else "ac_off",
        "source": source,
        "socPercent": status.get("socPercent"),
        "thresholdPercent": threshold_percent,
        "acOnBefore": bool(status.get("acOn")),
        "solarWatts": status.get("solarWatts"),
        "outWatts": status.get("outWatts"),
        "pressed": bool(pressed),
        "error": error or "",
    }


def should_log(
    status: dict[str, Any],
    *,
    last_at: float,
    last_key: tuple[Any, ...] | None,
    now: float,
    interval_sec: float = DEFAULT_LOG_SEC,
) -> bool:
    if last_key is None:
        return True
    if event_key(status) != last_key:
        return True
    return (now - last_at) >= max(60.0, interval_sec)


def _bearer_token() -> str:
    # Same as security_garden. Do not use GOOGLE_APPLICATION_CREDENTIALS —
    # the garden camera SA is Storage-only and Firestore returns 403.
    return os.getenv("FIREBASE_FIRESTORE_BEARER_TOKEN") or os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or ""


def _rest_target(firebase_config: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, str]] | None:
    project_id = str(firebase_config.get("projectId") or os.getenv("FIREBASE_PROJECT_ID") or "")
    if not project_id:
        return None
    api_key = str(firebase_config.get("apiKey") or os.getenv("FIREBASE_API_KEY") or "")
    token = _bearer_token()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, str] = {}
    if api_key:
        params["key"] = api_key
    base = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents"
    return base, params, headers


async def _patch_docs(
    paths: list[str],
    fields: dict[str, Any],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    label: str,
) -> bool:
    target = _rest_target(firebase_config)
    if target is None or http_client is None:
        return False
    base, params, headers = target
    body = {"fields": {key: _firestore_value(value) for key, value in fields.items()}}
    try:
        responses = [
            await http_client.patch(f"{base}/{path}", params=params, headers=headers, json=body, timeout=8)
            for path in paths
        ]
    except Exception as exc:
        print(f"[fossibot] firestore {label} failed: {exc}")
        return False
    codes = [r.status_code for r in responses]
    if any(code >= 400 for code in codes):
        print(f"[fossibot] firestore {label} HTTP {'/'.join(str(c) for c in codes)}")
        return False
    return True


async def persist(
    status: dict[str, Any],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    now: datetime | None = None,
) -> bool:
    return await _patch_docs(
        [LATEST_DOC, f"{SAMPLES_COLLECTION}/{sample_id(now)}"],
        sample_fields(status, now=now),
        firebase_config=firebase_config,
        http_client=http_client,
        label="log",
    )


async def persist_policy_event(
    status: dict[str, Any],
    *,
    want_ac_on: bool,
    threshold_percent: float | None,
    pressed: bool,
    source: str = "rule",
    error: str = "",
    firebase_config: dict[str, Any],
    http_client: Any,
    now: datetime | None = None,
) -> bool:
    fields = policy_event_fields(
        status,
        want_ac_on=want_ac_on,
        threshold_percent=threshold_percent,
        pressed=pressed,
        source=source,
        error=error,
        now=now,
    )
    return await _patch_docs(
        [f"{EVENTS_COLLECTION}/{event_id(now)}"],
        fields,
        firebase_config=firebase_config,
        http_client=http_client,
        label="event",
    )
