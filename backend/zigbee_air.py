"""IKEA VINDSTYRKA on the garden Zigbee PAN. PM2.5, temp, humidity, VOC."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import zigbee_lights

KNOWN_VINDSTYRKA = "34:8d:13:ff:fe:64:7a:ff"
POLL_S = 60.0

TEMP_CLUSTER = 0x0402
HUM_CLUSTER = 0x0405
PM25_CLUSTER = 0x042A
VOC_CLUSTER = 0xFC7E

_cache: dict[str, Any] = {"ok": True, "online": False}
_poll_task: asyncio.Task[None] | None = None


def is_air(model: str) -> bool:
    return "vindstyrka" in (model or "").casefold()


def _invalid_raw(raw: Any) -> bool:
    if raw is None:
        return True
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return True
    return value in {0x8000, 0xFFFF, -32768}


def scale_reading(raw: Any, divisor: float = 1) -> float | None:
    if _invalid_raw(raw):
        return None
    value = float(raw) / divisor
    if divisor == 1:
        return value
    return round(value, 1)


def public_status(
    *,
    online: bool = False,
    pm25: float | None = None,
    temp_c: float | None = None,
    humidity: float | None = None,
    voc: float | None = None,
    updated_at: float | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "ok": True,
        "online": online,
        "pm25": pm25,
        "tempC": temp_c,
        "humidity": humidity,
        "voc": voc,
        "updatedAt": updated_at,
        "error": error,
    }


def status() -> dict[str, Any]:
    return dict(_cache)


def find_device() -> Any | None:
    hub = zigbee_lights.hub()
    app = getattr(hub, "_app", None) if hub is not None else None
    if app is None:
        return None
    for device in getattr(app, "devices", {}).values():
        ieee = str(getattr(device, "ieee", "") or "")
        model = str(getattr(device, "model", "") or "")
        if ieee == KNOWN_VINDSTYRKA or is_air(model):
            return device
    return None


async def _read_cluster(device: Any, cluster_id: int, divisor: float) -> float | None:
    ep = getattr(device, "endpoints", {}).get(1)
    if ep is None:
        return None
    cluster = getattr(ep, "in_clusters", {}).get(cluster_id)
    if cluster is None:
        return None
    result = await asyncio.wait_for(cluster.read_attributes([0], allow_cache=False), 8)
    raw = zigbee_lights.attr_value(result, "measured_value", None)
    if raw is None:
        raw = zigbee_lights.attr_value(result, "measuredValue", None)
    return scale_reading(raw, divisor)


async def refresh() -> dict[str, Any]:
    global _cache
    device = find_device()
    if device is None:
        _cache = public_status(error="ingen VINDSTYRKA")
        return _cache
    try:
        pm25 = await _read_cluster(device, PM25_CLUSTER, 1)
        temp_c = await _read_cluster(device, TEMP_CLUSTER, 100)
        humidity = await _read_cluster(device, HUM_CLUSTER, 100)
        voc = await _read_cluster(device, VOC_CLUSTER, 1)
        _cache = public_status(
            online=True,
            pm25=pm25,
            temp_c=temp_c,
            humidity=humidity,
            voc=voc,
            updated_at=time.time(),
        )
    except Exception as exc:
        _cache = public_status(error=str(exc)[:160])
    return _cache


async def _poll_loop() -> None:
    while True:
        await refresh()
        await asyncio.sleep(POLL_S)


async def start() -> None:
    global _poll_task
    if _poll_task is not None:
        return
    await refresh()
    _poll_task = asyncio.create_task(_poll_loop())


async def stop() -> None:
    global _poll_task
    if _poll_task is not None:
        _poll_task.cancel()
        _poll_task = None
