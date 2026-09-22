"""Garden light log: every AC edge, command, Zigbee read/report, bind.

JSONL at ``backend/var/lights.jsonl`` (survives restart). Journal gets the
same ``[lights]`` line. Read via GET /api/lights/log.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX = 160
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
        print(f"[lights] file write failed: {exc}", flush=True)


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
    print(f"[lights] {row['t']} {bits}", flush=True)
    return row


def recent(limit: int = 80) -> list[dict[str, Any]]:
    n = max(1, min(int(limit or 80), _MAX))
    return list(_events)[-n:]
