"""Local control of garden LEDVANCE SMART+ WiFi lights (Tuya OEM).

The 2020 Flare Wall RGBW is not Hue/Matter. After it has joined garden Wi-Fi,
the hub talks to it on the LAN with tinytuya (device id + local key).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).parent.parent / "garden_lights.json"

# Tuya local TCP (6668) dies if we reconnect every poll tick. tinytuya defaults
# to 5 retries with 5s delay — that plus a 2s hub poll wedges the lamp while
# UDP discovery and the cloud app keep working.
POLL_OK_S = 15.0
POLL_FAIL_S = 30.0
OFFLINE_GRACE_S = 45.0

_last_good: dict[str, dict[str, Any]] = {}
_last_good_at: dict[str, float] = {}
_last_poll_at = 0.0
_last_poll_ok = True


def reset_poll_state() -> None:
    """Test helper."""
    global _last_poll_at, _last_poll_ok
    _last_good.clear()
    _last_good_at.clear()
    _last_poll_at = 0.0
    _last_poll_ok = True


def _load() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"devices": []}


def _save(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def configured_devices() -> list[dict[str, str]]:
    raw = _load().get("devices")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Flare").strip() or "Flare"
        out.append({
            "id": str(item.get("id") or "").strip(),
            "name": name,
            "ip": str(item.get("ip") or "").strip(),
            "localKey": str(item.get("localKey") or item.get("local_key") or "").strip(),
            "version": str(item.get("version") or "3.3").strip() or "3.3",
        })
    return out


def upsert_device(**fields: str) -> dict[str, str]:
    cfg = _load()
    devices = cfg.get("devices")
    if not isinstance(devices, list):
        devices = []
    updated = {k: str(v) for k, v in fields.items() if v is not None}
    match_id = updated.get("id") or ""
    idx = next((i for i, d in enumerate(devices) if isinstance(d, dict) and str(d.get("id") or "") == match_id and match_id), None)
    if idx is None:
        idx = next((i for i, d in enumerate(devices) if isinstance(d, dict) and not str(d.get("id") or "")), None)
    if idx is None:
        devices.append(updated)
    else:
        merged = dict(devices[idx])
        merged.update(updated)
        devices[idx] = merged
    cfg["devices"] = devices
    _save(cfg)
    return {k: str(v) for k, v in (devices[idx if idx is not None else -1] or {}).items()}


def _dps_map(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    dps = payload.get("dps") if isinstance(payload.get("dps"), dict) else payload
    return {str(k): v for k, v in dps.items()} if isinstance(dps, dict) else {}


def dps_to_light(dps: dict[str, Any]) -> tuple[bool, int]:
    """Map Tuya DPS to (on, brightness 0–100)."""
    on_raw = dps.get("20", dps.get("1"))
    on = bool(on_raw) if not isinstance(on_raw, str) else on_raw.lower() in {"true", "1", "on"}
    if "22" in dps:
        try:
            bri = int(round(int(dps["22"]) / 10))
        except (TypeError, ValueError):
            bri = 0
    elif "3" in dps:
        try:
            bri = int(round(int(dps["3"]) / 2.55))
        except (TypeError, ValueError):
            bri = 0
    else:
        bri = 100 if on else 0
    return on, max(0, min(100, bri))


def hsv_to_hex(hue: int, sat: int, value: int = 100) -> str:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(
        max(0, min(360, hue)) / 360.0,
        max(0, min(100, sat)) / 100.0,
        max(0, min(100, value)) / 100.0,
    )
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def encode_colour_dps(hue: int, sat: int, brightness: int) -> str:
    hue = max(0, min(360, int(hue)))
    sat1000 = max(0, min(1000, int(sat) * 10))
    val1000 = max(10, min(1000, int(brightness) * 10))
    return f"{hue:04x}{sat1000:04x}{val1000:04x}"


def dps_to_color(dps: dict[str, Any]) -> dict[str, Any]:
    """Parse Tuya colour DPS 21/24 into public hue/sat/mode."""
    has_color = "21" in dps or "24" in dps
    mode_raw = str(dps.get("21") or "").strip().lower()
    mode = "colour" if mode_raw == "colour" else "white" if mode_raw else ""
    hue, sat = 30, 80
    raw = dps.get("24")
    if isinstance(raw, str) and len(raw) >= 12:
        try:
            hue = int(raw[0:4], 16)
            sat = int(round(int(raw[4:8], 16) / 10))
        except ValueError:
            pass
    hue = max(0, min(360, hue))
    sat = max(0, min(100, sat))
    return {
        "has_color": has_color,
        "mode": mode,
        "hue": hue,
        "sat": sat,
        "hex": hsv_to_hex(hue, sat),
    }


def public_light(dev: dict[str, str], *, dps: dict[str, Any] | None = None, online: bool = False, error: str = "") -> dict[str, Any]:
    on, bri = dps_to_light(dps or {})
    has_key = bool(dev.get("localKey"))
    has_id = bool(dev.get("id"))
    if not has_id:
        error = error or "ikke parret"
        online = False
    elif not has_key:
        error = error or "mangler nøgle"
        online = False
    color = dps_to_color(dps or {})
    return {
        "id": dev.get("id") or "flare",
        "name": dev.get("name") or "Flare",
        "brightness": bri if on else 0,
        "on": on and online,
        "any_on": on and online,
        "online": online,
        "lights": 1,
        "error": error,
        "has_color": color["has_color"],
        "mode": color["mode"],
        "hue": color["hue"],
        "sat": color["sat"],
        "hex": color["hex"],
    }


def _bulb(dev: dict[str, str]):
    import tinytuya
    bulb = tinytuya.BulbDevice(
        dev["id"],
        dev.get("ip") or None,
        dev.get("localKey") or "",
        version=float(dev.get("version") or 3.3),
    )
    bulb.set_socketTimeout(5)
    bulb.set_socketRetryLimit(1)
    bulb.set_socketRetryDelay(0.2)
    bulb.set_socketPersistent(False)
    return bulb


def scan_lan(maxretry: int = 1) -> list[dict[str, str]]:
    try:
        import tinytuya
    except ImportError:
        return []
    found = tinytuya.deviceScan(False, maxretry) or {}
    out: list[dict[str, str]] = []
    for ip, info in found.items():
        if not isinstance(info, dict):
            continue
        gw = str(info.get("gwId") or info.get("gwID") or info.get("id") or "").strip()
        if not gw:
            continue
        out.append({
            "id": gw,
            "ip": str(info.get("ip") or ip),
            "version": str(info.get("version") or "3.3"),
        })
    return out


def adopt_scan(found: list[dict[str, str]]) -> None:
    """Fill missing id/ip on the first unnamed Flare, and refresh IPs by id."""
    devices = configured_devices()
    by_id = {d["id"]: d for d in found if d.get("id")}
    for scan in found:
        existing = next((d for d in devices if d.get("id") == scan["id"]), None)
        if existing:
            upsert_device(
                id=scan["id"],
                name=existing.get("name") or "Flare",
                ip=scan["ip"],
                localKey=existing.get("localKey") or "",
                version=scan.get("version") or existing.get("version") or "3.3",
            )
    if len(found) == 1:
        vacant = next((d for d in devices if not d.get("id")), None)
        if vacant:
            scan = found[0]
            upsert_device(
                id=scan["id"],
                name=vacant.get("name") or "Flare",
                ip=scan["ip"],
                localKey=vacant.get("localKey") or "",
                version=scan.get("version") or "3.3",
            )


def _remember(state: dict[str, Any]) -> dict[str, Any]:
    light_id = str(state.get("id") or "")
    if light_id and state.get("online"):
        _last_good[light_id] = dict(state)
        _last_good_at[light_id] = time.monotonic()
    return state


def _or_last_good(dev: dict[str, str], failed: dict[str, Any]) -> dict[str, Any]:
    light_id = str(dev.get("id") or "")
    prev = _last_good.get(light_id)
    seen = _last_good_at.get(light_id)
    if prev and seen is not None and time.monotonic() - seen < OFFLINE_GRACE_S:
        return dict(prev)
    return failed


def read_device(dev: dict[str, str], *, use_grace: bool = True) -> dict[str, Any]:
    if not dev.get("id") or not dev.get("localKey"):
        return public_light(dev, online=False)
    try:
        raw = _bulb(dev).status()
        if not isinstance(raw, dict) or raw.get("Error"):
            failed = public_light(dev, online=False, error=str((raw or {}).get("Error") or "offline"))
            return _or_last_good(dev, failed) if use_grace else failed
        on, bri = dps_to_light(_dps_map(raw))
        state = public_light(dev, dps=_dps_map(raw), online=True)
        state["on"] = on
        state["any_on"] = on
        state["brightness"] = bri if on else 0
        return _remember(state)
    except Exception as exc:
        failed = public_light(dev, online=False, error=str(exc))
        return _or_last_good(dev, failed) if use_grace else failed


def reconnect(light_id: str = "") -> dict[str, Any]:
    """One status read, then one LAN scan if still down. Skips last-good grace."""
    reset_poll_state()
    devices = configured_devices()
    wanted = light_id.strip()
    dev = next((d for d in devices if d.get("id") == wanted), None) if wanted else None
    if not dev:
        dev = next((d for d in devices if d.get("id")), None)
    if not dev:
        return public_light({"id": "", "name": "Flare"})
    state = read_device(dev, use_grace=False)
    if state.get("online"):
        return state
    adopt_scan(scan_lan(maxretry=1))
    refreshed = next(
        (d for d in configured_devices() if d.get("id") == (wanted or dev.get("id"))),
        None,
    ) or dev
    return read_device(refreshed, use_grace=False)


def set_brightness(dev: dict[str, str], brightness: int) -> dict[str, Any]:
    brightness = max(0, min(100, int(brightness)))
    if not dev.get("id") or not dev.get("localKey"):
        return public_light(dev, online=False)
    bulb = _bulb(dev)
    if brightness <= 0:
        bulb.turn_off()
        return public_light(dev, dps={"20": False, "22": 0}, online=True)
    bulb.turn_on()
    try:
        bulb.set_brightness_percentage(brightness)
    except Exception:
        bulb.set_value(22, max(10, brightness * 10))
    return public_light(dev, dps={"20": True, "22": max(10, brightness * 10)}, online=True)


def set_color(dev: dict[str, str], hue: int, sat: int, brightness: int | None = None) -> dict[str, Any]:
    hue = max(0, min(360, int(hue)))
    sat = max(0, min(100, int(sat)))
    if brightness is None:
        brightness = 100
    brightness = max(1, min(100, int(brightness)))
    if not dev.get("id") or not dev.get("localKey"):
        return public_light(dev, online=False)
    colour = encode_colour_dps(hue, sat, brightness)
    bulb = _bulb(dev)
    bulb.turn_on()
    try:
        bulb.set_value(21, "colour")
    except Exception:
        pass
    try:
        bulb.set_value(24, colour)
    except Exception:
        try:
            bulb.set_hsv(hue / 360.0, sat / 100.0, brightness / 100.0)
        except Exception:
            pass
    return public_light(
        dev,
        dps={"20": True, "21": "colour", "22": max(10, brightness * 10), "24": colour},
        online=True,
    )


def set_white(dev: dict[str, str], brightness: int | None = None) -> dict[str, Any]:
    if brightness is None:
        brightness = 100
    brightness = max(0, min(100, int(brightness)))
    if brightness <= 0:
        return set_brightness(dev, 0)
    if not dev.get("id") or not dev.get("localKey"):
        return public_light(dev, online=False)
    bulb = _bulb(dev)
    bulb.turn_on()
    try:
        bulb.set_mode("white")
    except Exception:
        try:
            bulb.set_value(21, "white")
        except Exception:
            pass
    try:
        bulb.set_brightness_percentage(brightness)
    except Exception:
        bulb.set_value(22, max(10, brightness * 10))
    return public_light(dev, dps={"20": True, "21": "white", "22": max(10, brightness * 10)}, online=True)


def snapshot() -> list[dict[str, Any]]:
    devices = configured_devices()
    real = [dev for dev in devices if dev.get("id")]
    return [read_device(dev) for dev in (real or devices)]


def poll_snapshot() -> list[dict[str, Any]] | None:
    """Poll Tuya at most every 15s (30s after a failure). None = too soon."""
    global _last_poll_at, _last_poll_ok
    now = time.monotonic()
    interval = POLL_OK_S if _last_poll_ok else POLL_FAIL_S
    if _last_poll_at and now - _last_poll_at < interval:
        return None
    _last_poll_at = now
    lights = snapshot()
    _last_poll_ok = bool(lights) and all(item.get("online") for item in lights)
    return lights
