"""Audio output targets for site-specific speaker routing.

This module intentionally handles only configured targets. Discovery/pairing new
speakers is an operator task, not kiosk UI.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any


# A2DP carries an absolute volume in the range 0..127. The kiosk slider is 0..100.
BLUEALSA_A2DP_MAX = 127


@dataclass(frozen=True)
class AudioTarget:
    id: str
    name: str
    type: str
    mac: str = ""
    default: bool = False


async def _run(*args: str, timeout: float = 8.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, err = await proc.communicate()
        return 124, out.decode(errors="ignore"), err.decode(errors="ignore")
    return proc.returncode or 0, out.decode(errors="ignore"), err.decode(errors="ignore")


def _target_from_config(raw: dict[str, Any], default_id: str = "") -> AudioTarget | None:
    target_id = str(raw.get("id") or "").strip()
    target_type = str(raw.get("type") or "").strip()
    if not target_id or not target_type:
        return None
    output = raw.get("output") if isinstance(raw.get("output"), dict) else {}
    mac = str(raw.get("mac") or output.get("mac") or "").strip().upper()
    return AudioTarget(
        id=target_id,
        name=str(raw.get("name") or output.get("name") or target_id),
        type=target_type,
        mac=mac,
        default=target_id == default_id,
    )


def configured_targets(raw_targets: list[dict[str, Any]], default_id: str = "") -> list[AudioTarget]:
    return [t for raw in raw_targets if (t := _target_from_config(raw, default_id))]


def _parse_bool(info: str, field: str) -> bool:
    match = re.search(rf"^\s*{re.escape(field)}:\s*(yes|no)\s*$", info, re.MULTILINE)
    return bool(match and match.group(1) == "yes")


async def _bluealsa_playback_available(mac: str) -> bool:
    code, out, _ = await _run("bluealsa-aplay", "-L", timeout=5)
    return code == 0 and mac.upper() in out.upper() and "playback" in out.lower()


async def _bluealsa_pcm_path(mac: str) -> str | None:
    """Resolve the live BlueALSA A2DP PCM D-Bus path for a connected speaker.

    The path only exists while the speaker is connected, so we look it up at
    runtime instead of constructing it (robust across BlueALSA versions)."""
    code, out, _ = await _run("bluealsa-cli", "list-pcms", timeout=5)
    if code != 0:
        return None
    needle = "DEV_" + mac.upper().replace(":", "_")
    for line in out.splitlines():
        path = line.strip().split()[0] if line.strip() else ""
        if not path:
            continue
        if needle in path.upper() and "a2dp" in path.lower():
            return path
    return None


async def _prefer_native_volume(mac: str) -> None:
    """Use the speaker's native Bluetooth volume instead of BlueALSA soft-volume.

    Some speakers remember a low hardware volume for the Pi. With SoftVolume on,
    100% in the UI only scales audio up to that low remembered level. Turning it
    off lets BlueALSA's volume command drive AVRCP/native volume when available.
    """
    path = await _bluealsa_pcm_path(mac)
    if path:
        await _run("bluealsa-cli", "soft-volume", path, "off", timeout=5)


async def get_target_volume(target: AudioTarget) -> int | None:
    """Current speaker volume as 0..100, or None if unavailable/disconnected."""
    if target.type != "bluealsa" or not target.mac:
        return None
    path = await _bluealsa_pcm_path(target.mac)
    if not path:
        return None
    await _run("bluealsa-cli", "soft-volume", path, "off", timeout=5)
    code, out, _ = await _run("bluealsa-cli", "volume", path, timeout=5)
    if code != 0:
        return None
    nums = [int(n) for n in re.findall(r"\d+", out)]
    if not nums:
        return None
    return round(max(nums) * 100 / BLUEALSA_A2DP_MAX)


async def set_target_volume(target: AudioTarget, level: int) -> dict[str, Any]:
    """Set the connected speaker volume from a 0..100 kiosk slider value."""
    if target.type != "bluealsa":
        return {"ok": False, "error": f"Unsupported target type: {target.type}"}
    if not target.mac:
        return {"ok": False, "error": "Missing Bluetooth MAC"}
    path = await _bluealsa_pcm_path(target.mac)
    if not path:
        return {"ok": False, "error": "Højttaler ikke forbundet"}
    await _run("bluealsa-cli", "soft-volume", path, "off", timeout=5)
    level = max(0, min(100, int(level)))
    raw = round(level * BLUEALSA_A2DP_MAX / 100)
    code, out, err = await _run("bluealsa-cli", "volume", path, str(raw), timeout=5)
    if code != 0:
        return {"ok": False, "error": (err or out or "Kunne ikke sætte volumen").strip()}
    actual = await get_target_volume(target)
    return {"ok": True, "volume": actual if actual is not None else level}


async def target_status(target: AudioTarget) -> dict[str, Any]:
    if target.type != "bluealsa":
        return _public_target(target, online=False, error=f"Unsupported target type: {target.type}")
    if not target.mac:
        return _public_target(target, online=False, error="Missing Bluetooth MAC")
    _, info, err = await _run("bluetoothctl", "info", target.mac, timeout=6)
    text = info or err
    paired = _parse_bool(text, "Paired")
    trusted = _parse_bool(text, "Trusted")
    connected = _parse_bool(text, "Connected")
    playback = await _bluealsa_playback_available(target.mac) if connected else False
    if playback:
        await _prefer_native_volume(target.mac)
    volume = await get_target_volume(target) if playback else None
    return _public_target(
        target,
        online=connected and playback,
        paired=paired,
        trusted=trusted,
        connected=connected,
        playback=playback,
        volume=volume,
    )


async def _wait_for_playback(target: AudioTarget, timeout: float = 8.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    status = await target_status(target)
    while not status.get("online") and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.8)
        status = await target_status(target)
    return status


async def connect_target(target: AudioTarget) -> dict[str, Any]:
    if target.type != "bluealsa":
        return _public_target(target, online=False, error=f"Unsupported target type: {target.type}")
    if not target.mac:
        return _public_target(target, online=False, error="Missing Bluetooth MAC")

    await _run("bluetoothctl", "power", "on", timeout=5)
    await _run("bluetoothctl", "trust", target.mac, timeout=5)

    # BlueZ can report Connected while BlueALSA has not exposed an A2DP playback
    # PCM. Force a clean profile negotiation instead of accepting that limbo state.
    current = await target_status(target)
    if current.get("connected") and not current.get("playback"):
        await _run("bluetoothctl", "disconnect", target.mac, timeout=8)
        await asyncio.sleep(1.0)

    code, out, err = await _run("bluetoothctl", "connect", target.mac, timeout=20)
    status = await _wait_for_playback(target, timeout=8)
    if status.get("connected") and not status.get("playback"):
        await _run("sudo", "systemctl", "restart", "bluealsa", timeout=10)
        await asyncio.sleep(2.0)
        await _run("bluetoothctl", "disconnect", target.mac, timeout=8)
        await asyncio.sleep(1.0)
        code, out, err = await _run("bluetoothctl", "connect", target.mac, timeout=20)
        status = await _wait_for_playback(target, timeout=10)

    status["ok"] = bool(status.get("online"))
    if not status["ok"]:
        if status.get("connected") and not status.get("playback"):
            status["error"] = "Tilsluttet, men A2DP-lydprofilen mangler"
        else:
            status["error"] = "Højttaler kunne ikke forbindes"
        status["connectExitCode"] = code
        detail = (err or out or "").strip()
        if detail:
            status["detail"] = detail
    return status


def _public_target(
    target: AudioTarget,
    *,
    online: bool,
    paired: bool = False,
    trusted: bool = False,
    connected: bool = False,
    playback: bool = False,
    volume: int | None = None,
    error: str = "",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": target.id,
        "name": target.name,
        "type": target.type,
        "default": target.default,
        "online": online,
        "paired": paired,
        "trusted": trusted,
        "connected": connected,
        "playback": playback,
    }
    if volume is not None:
        data["volume"] = volume
    if error:
        data["error"] = error
    return data
