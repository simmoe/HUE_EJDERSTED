"""Append-only indoor/outdoor air samples in Firestore (garden).

Latest snapshot: ``ejdersted/air_garden``
History: ``ejdersted/air_garden/samples/{yyyyMMddTHHmm}`` (UTC, 5-min bucket).

Indoor from IKEA Vindstyrka; outdoor humidity from DMI via Open-Meteo at HF Musikbyen.
Same REST + outbox pattern as ``fossibot_log``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fossibot_log import sample_id

LATEST_DOC = "ejdersted/air_garden"
SAMPLES_COLLECTION = "ejdersted/air_garden/samples"
DEFAULT_LOG_SEC = 300
MAX_QUEUED_SAMPLES = 288

_SAMPLE_KEYS = ("online", "tempC", "humidity", "pm25", "voc", "outdoorHumidity", "outdoorSource")


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


def sample_fields(status: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(tz=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return {
        "ts": stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "online": bool(status.get("online")),
        "tempC": status.get("tempC"),
        "humidity": status.get("humidity"),
        "pm25": status.get("pm25"),
        "voc": status.get("voc"),
        "outdoorHumidity": status.get("outdoorHumidity"),
        "outdoorSource": status.get("outdoorSource") or "",
    }


def sample_key(status: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(status.get(key) for key in _SAMPLE_KEYS)


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
    if sample_key(status) != last_key:
        return True
    return (now - last_at) >= max(60.0, interval_sec)


def _bearer_token() -> str:
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
        print(f"[air] firestore {label} failed: {exc}")
        return False
    codes = [r.status_code for r in responses]
    if any(code >= 400 for code in codes):
        print(f"[air] firestore {label} HTTP {'/'.join(str(c) for c in codes)}")
        return False
    return True


def load_outbox(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"samples": {}}
    if not isinstance(raw, dict):
        return {"samples": {}}
    samples = raw.get("samples") if isinstance(raw.get("samples"), dict) else {}
    return {"samples": dict(samples)}


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


def drop_outbox_keys(path: Path, *, samples: list[str] | None = None) -> None:
    box = load_outbox(path)
    for key in samples or []:
        box["samples"].pop(key, None)
    save_outbox(path, box)


async def flush_outbox(
    path: Path,
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
) -> bool:
    box = load_outbox(path)
    if not box["samples"]:
        return True
    sent_samples: list[str] = []
    for sid in sorted(box["samples"]):
        ok = await _patch_docs(
            [f"{SAMPLES_COLLECTION}/{sid}"],
            box["samples"][sid],
            firebase_config=firebase_config,
            http_client=http_client,
            label="log",
        )
        if not ok:
            drop_outbox_keys(path, samples=sent_samples)
            return False
        sent_samples.append(sid)
    if sent_samples:
        drop_outbox_keys(path, samples=sent_samples)
        print(f"[air] flushed {len(sent_samples)} sample(s) to firestore")
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
