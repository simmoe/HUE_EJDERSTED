"""Append-only Fossibot samples in Firestore (garden).

Latest snapshot: ``ejdersted/fossibot_garden``
History: ``ejdersted/fossibot_garden/samples/{yyyyMMddTHHmm}`` (UTC, 5-min bucket).
Policy events: ``ejdersted/fossibot_garden/events/{yyyyMMddTHHmmss}`` — one doc
each time the power policy presses the SwitchBot (``source``: floor at low
SoC, home when the camera sees someone, night after sunset when the hut is
empty, hold from a kiosk tap), so a dark hut can be explained afterwards.

Writes follow the same REST path as ``security_garden``. Client SDKs must not
write these documents. Read is for later interpretation (SoC, solar/AC/total in, out, AC/DC/USB).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LATEST_DOC = "ejdersted/fossibot_garden"
SAMPLES_COLLECTION = "ejdersted/fossibot_garden/samples"
EVENTS_COLLECTION = "ejdersted/fossibot_garden/events"
DEFAULT_LOG_SEC = 300
MAX_QUEUED_SAMPLES = 288  # 24 h at 5 min — Pi stays up on DC when AC/router dies
MAX_QUEUED_EVENTS = 64

_PORT_KEYS = ("online", "acOn", "dcOn", "usbOn", "charging", "kioskCharging")


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
        "acInWatts": status.get("acInWatts"),
        "inWatts": status.get("inWatts"),
        "outWatts": status.get("outWatts"),
        "usbOn": bool(status.get("usbOn")),
        "dcOn": bool(status.get("dcOn")),
        "acOn": bool(status.get("acOn")),
        "charging": bool(status.get("charging")),
        "kioskBatteryPercent": status.get("kioskBatteryPercent"),
        "kioskCharging": status.get("kioskCharging"),
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
    `source` is why: floor (low SoC), home (camera), night (after sunset,
    empty), or hold (a tap on the kiosk)."""
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


def _empty_outbox() -> dict[str, dict[str, Any]]:
    return {"samples": {}, "events": {}}


def load_outbox(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_outbox()
    if not isinstance(raw, dict):
        return _empty_outbox()
    samples = raw.get("samples") if isinstance(raw.get("samples"), dict) else {}
    events = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    return {"samples": dict(samples), "events": dict(events)}


def save_outbox(path: Path, box: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(box, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _trim(items: dict[str, Any], limit: int) -> dict[str, Any]:
    if len(items) <= limit:
        return items
    keep = sorted(items)[-limit:]
    return {key: items[key] for key in keep}


def queue_sample(path: Path, fields: dict[str, Any], *, sid: str) -> None:
    box = load_outbox(path)
    box["samples"][sid] = fields
    box["samples"] = _trim(box["samples"], MAX_QUEUED_SAMPLES)
    save_outbox(path, box)


def queue_event(path: Path, fields: dict[str, Any], *, eid: str) -> None:
    box = load_outbox(path)
    box["events"][eid] = fields
    box["events"] = _trim(box["events"], MAX_QUEUED_EVENTS)
    save_outbox(path, box)


def drop_outbox_keys(path: Path, *, samples: list[str] | None = None, events: list[str] | None = None) -> None:
    box = load_outbox(path)
    for key in samples or []:
        box["samples"].pop(key, None)
    for key in events or []:
        box["events"].pop(key, None)
    save_outbox(path, box)


async def flush_outbox(
    path: Path,
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
) -> bool:
    """Replay queued samples/events once DNS is back. Stops on the first miss."""
    box = load_outbox(path)
    if not box["samples"] and not box["events"]:
        return True
    sent_samples: list[str] = []
    sent_events: list[str] = []
    for eid in sorted(box["events"]):
        ok = await _patch_docs(
            [f"{EVENTS_COLLECTION}/{eid}"],
            box["events"][eid],
            firebase_config=firebase_config,
            http_client=http_client,
            label="event",
        )
        if not ok:
            drop_outbox_keys(path, samples=sent_samples, events=sent_events)
            return False
        sent_events.append(eid)
    for sid in sorted(box["samples"]):
        ok = await _patch_docs(
            [f"{SAMPLES_COLLECTION}/{sid}"],
            box["samples"][sid],
            firebase_config=firebase_config,
            http_client=http_client,
            label="log",
        )
        if not ok:
            drop_outbox_keys(path, samples=sent_samples, events=sent_events)
            return False
        sent_samples.append(sid)
    if sent_samples or sent_events:
        drop_outbox_keys(path, samples=sent_samples, events=sent_events)
        print(
            f"[fossibot] flushed {len(sent_samples)} sample(s), {len(sent_events)} event(s) to firestore"
        )
    return True


async def persist(
    status: dict[str, Any],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    now: datetime | None = None,
    outbox_path: Path | None = None,
) -> bool:
    fields = sample_fields(status, now=now)
    sid = sample_id(now)
    ok = await _patch_docs(
        [LATEST_DOC, f"{SAMPLES_COLLECTION}/{sid}"],
        fields,
        firebase_config=firebase_config,
        http_client=http_client,
        label="log",
    )
    if not ok and outbox_path is not None:
        queue_sample(outbox_path, fields, sid=sid)
    return ok


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
    outbox_path: Path | None = None,
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
    eid = event_id(now)
    if outbox_path is not None:
        queue_event(outbox_path, fields, eid=eid)
    ok = await _patch_docs(
        [f"{EVENTS_COLLECTION}/{eid}"],
        fields,
        firebase_config=firebase_config,
        http_client=http_client,
        label="event",
    )
    if ok and outbox_path is not None:
        drop_outbox_keys(outbox_path, events=[eid])
    return ok
