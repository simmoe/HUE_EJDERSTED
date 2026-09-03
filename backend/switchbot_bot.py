"""Local BLE control of a SwitchBot Bot (WoHand) for the garden Fossibot.

The Bot is Bluetooth-only. The garden Pi talks to it directly — no SwitchBot
Hub and no cloud API. Pairing in the phone app is only for the first physical
test; close the app (or turn phone Bluetooth off) before the Pi connects, or
the Bot stays busy.

Press mode (`570100`) is a single arm swing, which is what a Fossibot button
needs. The MAC is remembered in switchbot.json after the first successful scan.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / "switchbot.json"

SWITCHBOT_SERVICE = "cba20d00-224d-11e6-9fb8-0002a5d5c51b"
CHAR_TX = "cba20002-224d-11e6-9fb8-0002a5d5c51b"
CHAR_NOTIFY = "cba20003-224d-11e6-9fb8-0002a5d5c51b"
PRESS_CMD = bytes.fromhex("570100")
BOT_MODEL = 0x48  # 'H' / WoHand
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

try:
    from bleak import BleakClient, BleakScanner  # type: ignore

    _HAS_BLEAK = True
except Exception:  # pragma: no cover - dev machines without bleak
    BleakClient = None  # type: ignore
    BleakScanner = None  # type: ignore
    _HAS_BLEAK = False


def normalize_mac(value: str | None) -> str:
    raw = (value or "").strip().replace("-", ":").upper()
    if not raw:
        return ""
    if MAC_RE.match(raw):
        return raw
    hex_only = re.sub(r"[^0-9A-F]", "", raw)
    if len(hex_only) == 12:
        return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))
    return ""


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_bot_advert(name: str, service_data: dict[str, bytes], service_uuids: list[str]) -> bool:
    if "wohand" in (name or "").lower() or (name or "").lower() == "bot":
        return True
    for payload in service_data.values():
        if payload and payload[0] == BOT_MODEL:
            return True
    return False


@dataclass
class DiscoveredBot:
    mac: str
    name: str
    rssi: int | None = None


class BleBackend(Protocol):
    async def discover(self, timeout: float) -> list[DiscoveredBot]: ...
    async def press(self, address: str) -> None: ...


class SimulatedBackend:
    """Used in tests and on machines without Bluetooth."""

    def __init__(self, bots: list[DiscoveredBot] | None = None):
        self.bots = list(bots or [DiscoveredBot(mac="AA:BB:CC:DD:EE:FF", name="WoHand", rssi=-50)])
        self.pressed: list[str] = []

    async def discover(self, timeout: float) -> list[DiscoveredBot]:
        return list(self.bots)

    async def press(self, address: str) -> None:
        mac = normalize_mac(address)
        if mac not in {bot.mac for bot in self.bots}:
            raise RuntimeError(f"Ingen SwitchBot på {address}")
        self.pressed.append(mac)


class BleakBackend:
    async def discover(self, timeout: float) -> list[DiscoveredBot]:
        if not _HAS_BLEAK:
            raise RuntimeError("bleak er ikke installeret på denne Pi")
        found: dict[str, DiscoveredBot] = {}

        def _cb(device: Any, adv: Any) -> None:
            name = str(getattr(device, "name", None) or getattr(adv, "local_name", None) or "")
            service_data = {
                str(k): bytes(v) for k, v in (getattr(adv, "service_data", None) or {}).items()
            }
            service_uuids = [str(u) for u in (getattr(adv, "service_uuids", None) or [])]
            if not _is_bot_advert(name, service_data, service_uuids):
                return
            mac = normalize_mac(getattr(device, "address", ""))
            if not mac:
                return
            found[mac] = DiscoveredBot(
                mac=mac,
                name=name or "SwitchBot",
                rssi=getattr(adv, "rssi", None),
            )

        async with BleakScanner(detection_callback=_cb) as _scanner:
            await asyncio.sleep(max(1.0, timeout))
        return list(found.values())

    async def press(self, address: str) -> None:
        if not _HAS_BLEAK:
            raise RuntimeError("bleak er ikke installeret på denne Pi")
        got = asyncio.Event()
        payload: dict[str, bytes] = {}

        def _notify(_sender: Any, data: bytearray) -> None:
            payload["data"] = bytes(data)
            got.set()

        async with BleakClient(address, timeout=15.0) as client:
            await client.start_notify(CHAR_NOTIFY, _notify)
            await client.write_gatt_char(CHAR_TX, PRESS_CMD, response=True)
            try:
                await asyncio.wait_for(got.wait(), timeout=5.0)
            except TimeoutError as exc:
                raise RuntimeError("Bot svarede ikke på tryk") from exc
        data = payload.get("data") or b""
        if data and data[0] not in {1, 5}:
            raise RuntimeError(
                "Bot afviste trykket. Luk SwitchBot-appen på telefonen og prøv igen."
            )


class SwitchbotController:
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        backend: BleBackend | None = None,
        mac: str | None = None,
        name: str = "Fossibot",
        scan_timeout: float = 8.0,
    ):
        self._state_path = state_path or STATE_FILE
        self._lock = asyncio.Lock()
        stored = self._load()
        env_mac = normalize_mac(os.environ.get("HUB_SWITCHBOT_BOT_MAC") or mac or "")
        self.mac = env_mac or normalize_mac(str(stored.get("mac") or ""))
        self.name = str(os.environ.get("HUB_SWITCHBOT_BOT_NAME") or stored.get("name") or name).strip() or "Fossibot"
        self.scan_timeout = scan_timeout
        self.simulated = backend is not None or _bool_env("HUB_SWITCHBOT_SIMULATE") or not _HAS_BLEAK
        self._backend: BleBackend = backend or (SimulatedBackend() if self.simulated else BleakBackend())
        self.last_error: str | None = None
        self.last_press_at: str | None = stored.get("lastPressAt") if isinstance(stored.get("lastPressAt"), str) else None
        self.pressing = False
        self.last_seen: list[DiscoveredBot] = []

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps(
                    {
                        "mac": self.mac,
                        "name": self.name,
                        "lastPressAt": self.last_press_at,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[switchbot] could not persist state: {exc}")

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "simulated": self.simulated,
            "name": self.name,
            "mac": self.mac or None,
            "macShort": self.mac[-5:].replace(":", "") if self.mac else None,
            "ready": bool(self.mac),
            "pressing": self.pressing,
            "lastPressAt": self.last_press_at,
            "lastError": self.last_error,
            "bots": [
                {"mac": bot.mac, "name": bot.name, "rssi": bot.rssi}
                for bot in self.last_seen
            ],
        }

    async def scan(self) -> list[DiscoveredBot]:
        bots = await self._backend.discover(self.scan_timeout)
        async with self._lock:
            self.last_seen = bots
            if not self.mac and len(bots) == 1:
                self.mac = bots[0].mac
                if bots[0].name:
                    self.name = bots[0].name if bots[0].name.lower() != "wohand" else self.name
                self._save()
            elif self.mac and any(bot.mac == self.mac for bot in bots):
                self.last_error = None
        return bots

    async def _resolve_mac(self) -> str:
        if self.mac:
            return self.mac
        bots = await self.scan()
        if len(bots) == 1:
            return bots[0].mac
        if not bots:
            raise RuntimeError(
                "Ingen SwitchBot i nærheden. Hold den tæt på Pi'en og luk appen på telefonen."
            )
        raise RuntimeError("Flere SwitchBots fundet — sæt HUB_SWITCHBOT_BOT_MAC")

    async def press(self) -> dict[str, Any]:
        async with self._lock:
            if self.pressing:
                raise RuntimeError("Bot er allerede i gang med et tryk")
            self.pressing = True
            self.last_error = None
        try:
            mac = await self._resolve_mac()
            await self._backend.press(mac)
            async with self._lock:
                self.mac = mac
                self.last_press_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self.last_error = None
                self.pressing = False
                self._save()
            return self.status()
        except Exception as exc:
            async with self._lock:
                self.last_error = str(exc)
                self.pressing = False
            raise
        finally:
            async with self._lock:
                self.pressing = False


def disabled_status() -> dict[str, Any]:
    return {
        "enabled": False,
        "simulated": False,
        "name": "Fossibot",
        "mac": None,
        "macShort": None,
        "ready": False,
        "pressing": False,
        "lastPressAt": None,
        "lastError": None,
        "bots": [],
    }
