"""Outdoor humidity at HF Musikbyen (Tudsemindevej 25).

DMI Harmonie via Open-Meteo — a professional point reading, not a hobby
station. Cached so the garden SIM is not hit on every kiosk poll.
"""

from __future__ import annotations

import time
from typing import Any

# HF Musikbyen, between Tippen and Valby Parken.
LAT = 55.6408
LON = 12.5080
CACHE_S = 600.0
STALE_S = 2700.0
URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=relative_humidity_2m"
    "&timezone=Europe/Copenhagen"
    "&models=dmi_seamless"
)

_cache: dict[str, Any] = {}
_cache_at = 0.0


def reset_cache() -> None:
    global _cache, _cache_at
    _cache = {}
    _cache_at = 0.0


def parse_humidity(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    raw = current.get("relative_humidity_2m")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not 0 <= value <= 100:
        return None
    return round(value)


def snapshot_fields(humidity: float | None) -> dict[str, Any]:
    return {
        "outdoorHumidity": humidity,
        "outdoorSource": "dmi" if humidity is not None else "",
    }


async def snapshot(http_client: Any) -> dict[str, Any]:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache and now - _cache_at < CACHE_S:
        return dict(_cache)
    humidity = await _fetch(http_client)
    if humidity is None and _cache and now - _cache_at < STALE_S:
        return dict(_cache)
    _cache = snapshot_fields(humidity)
    _cache_at = now
    return dict(_cache)


async def _fetch(http_client: Any) -> float | None:
    if http_client is None:
        return None
    try:
        response = await http_client.get(URL, timeout=6)
    except Exception as exc:
        print(f"[air] outdoor humidity failed: {exc}", flush=True)
        return None
    if getattr(response, "status_code", 0) >= 400:
        print(f"[air] outdoor humidity HTTP {response.status_code}", flush=True)
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    return parse_humidity(payload)
