"""Playback log for both hubs.

Each site keeps a JSONL file (survives restart) and mirrors events to
``ejdersted/audio_{site}/events/{id}`` on the hub Firebase project.
Read via GET /api/audio/log. Journal gets the same ``[audio]`` line.
"""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX = 80
_events: deque[dict[str, Any]] = deque(maxlen=_MAX)
_path: Path | None = None
_site = ""


def reset() -> None:
    _events.clear()


def configure(path: Path | str, site: str = "") -> None:
    global _path, _site
    _path = Path(path)
    _site = site
    _path.parent.mkdir(parents=True, exist_ok=True)
    _load()


def _load() -> None:
    _events.clear()
    if not _path or not _path.is_file():
        return
    try:
        lines = _path.read_text(encoding="utf-8").splitlines()[-_MAX:]
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event"):
            _events.append(row)


def _append_file(row: dict[str, Any]) -> None:
    if not _path:
        return
    try:
        with _path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if _path.stat().st_size > 1_000_000:
            keep = _path.read_text(encoding="utf-8").splitlines()[-_MAX:]
            _path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[audio] file write failed: {exc}", flush=True)


def log(event: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "t": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "event": event,
    }
    if _site:
        row["site"] = _site
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            row[key] = value
        elif isinstance(value, (int, float)):
            row[key] = value
        else:
            row[key] = str(value)[:160]
    _events.append(row)
    _append_file(row)
    bits = " ".join(f"{key}={row[key]}" for key in row if key != "t")
    print(f"[audio] {row['t']} {bits}", flush=True)
    return row


def recent(limit: int = 40) -> list[dict[str, Any]]:
    n = max(1, min(int(limit or 40), _MAX))
    return list(_events)[-n:]


def event_doc_id(row: dict[str, Any]) -> str:
    stamp = str(row.get("t") or "").replace(":", "").replace("-", "").replace("+", "")
    event = str(row.get("event") or "event").replace(".", "-")
    tail = str(row.get("uri") or row.get("title") or "")[-10:]
    return f"{stamp}-{event}-{tail}"[:80] or "event"


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


def _bearer_token() -> str:
    return os.getenv("FIREBASE_FIRESTORE_BEARER_TOKEN") or os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or ""


async def persist_remote(
    row: dict[str, Any],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    site: str,
) -> bool:
    project_id = str(firebase_config.get("projectId") or os.getenv("FIREBASE_PROJECT_ID") or "")
    if not project_id or http_client is None or not site:
        return False
    api_key = str(firebase_config.get("apiKey") or os.getenv("FIREBASE_API_KEY") or "")
    body = {"fields": {key: _firestore_value(value) for key, value in row.items()}}
    token = _bearer_token()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, str] = {}
    if api_key:
        params["key"] = api_key
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
        f"/documents/ejdersted/audio_{site}/events/{event_doc_id(row)}"
    )
    try:
        response = await http_client.patch(url, params=params, headers=headers, json=body, timeout=8)
    except Exception as exc:
        print(f"[audio] firestore log failed: {exc}", flush=True)
        return False
    if response.status_code >= 400:
        print(f"[audio] firestore log HTTP {response.status_code}", flush=True)
        return False
    return True
