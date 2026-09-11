"""IKEA / EmberZNet lights on the Sonoff dongle. Opens the existing PAN."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

DEFAULT_PORT = (
    "/dev/serial/by-id/"
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_"
    "9405800ca678f011b5fff4eba7772636-if00-port0"
)
DB_PATH = Path(__file__).parent.parent / "zigbee.db"
KNOWN_SENG = "34:8d:13:ff:fe:7c:32:59"
KNOWN_RODRET = "08:fd:52:ff:fe:d3:52:0f"


def is_motion(model: str) -> bool:
    text = (model or "").lower()
    return any(token in text for token in ("motion", "vallhorn", "occupancy"))


def is_lamp(model: str) -> bool:
    if is_motion(model):
        return False
    text = (model or "").lower()
    if "dimmer" in text or "rodret" in text or "styrbar" in text:
        return False
    return any(token in text for token in ("bulb", "driver", "led", "lamp", "stoftmoln", "gu10", "e27", "e14"))


def public_state(
    dev: dict[str, Any],
    *,
    on: bool = False,
    brightness: int = 0,
    online: bool = False,
    error: str = "",
) -> dict[str, Any]:
    bri = max(0, min(100, int(brightness)))
    return {
        "id": dev.get("id") or "seng",
        "name": dev.get("name") or "Seng",
        "protocol": "zigbee",
        "brightness": bri if on else 0,
        "on": on and online,
        "any_on": on and online,
        "online": online,
        "lights": 1,
        "error": error,
        "has_color": False,
        "mode": "white",
        "hue": None,
        "sat": None,
        "hex": "",
    }


class ZigbeeHub:
    def __init__(self, port: str = DEFAULT_PORT, db_path: Path = DB_PATH) -> None:
        self.port = port
        self.db_path = db_path
        self.loop: asyncio.AbstractEventLoop | None = None
        self._app = None
        self._cache: dict[str, dict[str, Any]] = {}
        self.joins: list[dict[str, str]] = []
        self._toilet_ieee: str | None = None
        self._sensor_ieee: str | None = None

    async def start(self) -> None:
        from bellows.zigbee.application import ControllerApplication

        self.loop = asyncio.get_running_loop()
        app = ControllerApplication(
            {
                "device": {"path": self.port, "baudrate": 115200},
                "backup_enabled": False,
                "startup_energy_scan": False,
                "database_path": str(self.db_path),
                "use_thread": False,
            }
        )
        await app.startup(auto_form=False)
        self._app = app
        app.add_listener(_JoinWatch(self))
        import garden_lights

        for dev in garden_lights.configured_devices():
            if dev.get("id") == "toilet" and dev.get("ieee"):
                self._toilet_ieee = dev["ieee"]
            if dev.get("sensorIeee"):
                self._sensor_ieee = dev["sensorIeee"]
        for device in app.devices.values():
            model = str(getattr(device, "model", "") or "")
            ieee = str(device.ieee)
            if ieee in {KNOWN_SENG, KNOWN_RODRET}:
                continue
            if is_motion(model):
                self._sensor_ieee = ieee
            if is_lamp(model):
                self._toilet_ieee = ieee
        if self._sensor_ieee and self._toilet_ieee:
            self.loop.create_task(self._bind_toilet())
        print(
            f"[zigbee] up channel={app.state.network_info.channel} "
            f"pan=0x{int(app.state.network_info.pan_id):04X}"
        )

    async def permit(self, seconds: int = 180) -> None:
        if self._app is None:
            raise RuntimeError("zigbee ikke startet")
        await self._app.permit(max(1, int(seconds)))
        print(f"[zigbee] join open {seconds}s")

    async def _bind_toilet(self) -> None:
        if not (self._sensor_ieee and self._toilet_ieee):
            return
        try:
            result = await self.bind_on_off(self._sensor_ieee, self._toilet_ieee)
            print(f"[zigbee] sensor bind {result}")
        except Exception as exc:
            print(f"[zigbee] sensor bind FAIL {type(exc).__name__}: {exc}")

    async def bind_on_off(self, source_ieee: str, dest_ieee: str, source_nwk: int | None = None) -> list[str]:
        from zigpy.types import EUI64
        from zigpy.zdo.types import MultiAddress

        if self._app is None:
            raise RuntimeError("zigbee ikke startet")
        src_ieee = EUI64.convert(source_ieee)
        dst_ieee = EUI64.convert(dest_ieee)
        if src_ieee not in self._app.devices:
            self._app.add_device(nwk=source_nwk or 0x8A55, ieee=src_ieee)
        if dst_ieee not in self._app.devices:
            self._app.add_device(nwk=0xCEC7, ieee=dst_ieee)
        src = self._app.get_device(src_ieee)
        dst_dev = self._app.get_device(dst_ieee)
        dst = MultiAddress()
        dst.addrmode = 0x03
        dst.ieee = dst_dev.ieee
        dst.endpoint = 1
        out: list[str] = []
        for cluster_id, name in ((6, "on_off"), (8, "level")):
            try:
                status = await asyncio.wait_for(
                    src.zdo.Bind_req(src.ieee, 1, cluster_id, dst), 8
                )
                out.append(f"{name}:{status}")
            except Exception as exc:
                out.append(f"{name}:FAIL:{type(exc).__name__}")
        return out

    async def _on_ready(self, device: Any) -> None:
        model = str(getattr(device, "model", "") or "")
        ieee = str(device.ieee)
        record = {
            "ieee": ieee,
            "nwk": f"0x{int(device.nwk):04X}",
            "model": model,
            "manufacturer": str(getattr(device, "manufacturer", "") or ""),
        }
        self.joins.append(record)
        print(f"[zigbee] ready {record['nwk']} {model}")
        if ieee in {KNOWN_SENG, KNOWN_RODRET}:
            return
        if is_lamp(model):
            self._toilet_ieee = ieee
            _adopt_toilet(ieee, record["nwk"], self._sensor_ieee)
            if self._sensor_ieee:
                result = await self.bind_on_off(self._sensor_ieee, ieee)
                print(f"[zigbee] sensor bind {result}")
            return
        if is_motion(model):
            self._sensor_ieee = ieee
            if self._toilet_ieee:
                result = await self.bind_on_off(ieee, self._toilet_ieee)
                print(f"[zigbee] sensor bind {result}")

    async def stop(self) -> None:
        app = self._app
        self._app = None
        if app is not None:
            await app.shutdown()

    def cached(self, dev: dict[str, Any]) -> dict[str, Any]:
        light_id = str(dev.get("id") or "")
        prev = self._cache.get(light_id)
        if prev:
            return dict(prev)
        if self._app is None:
            return public_state(dev, online=False, error="zigbee ikke startet")
        return public_state(dev, online=True)

    async def apply(self, dev: dict[str, Any], command: Any) -> dict[str, Any]:
        from zigpy.zcl.clusters.general import LevelControl, OnOff

        if self._app is None:
            return public_state(dev, online=False, error="zigbee ikke startet")
        try:
            device = await self._device(dev)
            onoff = device.endpoints[1].in_clusters[OnOff.cluster_id]
            want_on = command.on is not False and not (
                command.brightness is not None and command.brightness <= 0
            )
            if not want_on:
                await asyncio.wait_for(onoff.off(), 8)
                state = public_state(dev, on=False, brightness=0, online=True)
                self._cache[str(dev.get("id") or "")] = state
                return state
            brightness = 100 if command.brightness is None else max(1, min(100, command.brightness))
            level = device.endpoints[1].in_clusters.get(LevelControl.cluster_id)
            await asyncio.wait_for(onoff.on(), 8)
            if level is not None:
                transition = 0
                if getattr(command, "transition_s", None):
                    transition = max(0, int(round(float(command.transition_s) * 10)))
                raw = max(1, min(254, int(round(brightness / 100 * 254))))
                await asyncio.wait_for(level.move_to_level(raw, transition), 8)
            state = public_state(dev, on=True, brightness=brightness, online=True)
            self._cache[str(dev.get("id") or "")] = state
            return state
        except Exception as exc:
            state = public_state(dev, online=False, error=str(exc))
            return state

    async def _device(self, dev: dict[str, Any]):
        from zigpy.types import EUI64
        from zigpy.zcl.clusters.general import LevelControl, OnOff

        ieee = EUI64.convert(str(dev.get("ieee") or ""))
        nwk = _parse_nwk(dev.get("nwk"))
        if ieee not in self._app.devices:
            self._app.add_device(nwk=nwk or 0xE6BF, ieee=ieee)
        device = self._app.get_device(ieee)
        if not device.is_initialized or 1 not in device.endpoints:
            await asyncio.wait_for(device.initialize(), 12)
        return device


class _JoinWatch:
    def __init__(self, hub: ZigbeeHub) -> None:
        self.hub = hub

    def device_initialized(self, device: Any) -> None:
        loop = self.hub.loop
        if loop is not None:
            loop.create_task(self.hub._on_ready(device))


def _adopt_toilet(ieee: str, nwk: str, sensor_ieee: str | None = None) -> None:
    import garden_lights

    fields = {
        "id": "toilet",
        "name": "Toilet",
        "protocol": "zigbee",
        "ieee": ieee,
        "nwk": nwk,
    }
    if sensor_ieee:
        fields["sensorIeee"] = sensor_ieee
    garden_lights.upsert_device(**fields)


_hub: ZigbeeHub | None = None


def hub() -> ZigbeeHub | None:
    return _hub


async def permit(seconds: int = 180) -> None:
    if _hub is None:
        raise RuntimeError("zigbee ikke startet")
    await _hub.permit(seconds)


def recent_joins() -> list[dict[str, str]]:
    return list(_hub.joins) if _hub is not None else []


async def start(port: str = DEFAULT_PORT) -> None:
    global _hub
    _hub = ZigbeeHub(port=port)
    await _hub.start()


async def stop() -> None:
    global _hub
    if _hub is not None:
        await _hub.stop()
        _hub = None


def public(dev: dict[str, Any]) -> dict[str, Any]:
    if _hub is None:
        return public_state(dev, online=False, error="zigbee ikke startet")
    return _hub.cached(dev)


async def apply_async(dev: dict[str, Any], command: Any) -> dict[str, Any]:
    if _hub is None:
        return public_state(dev, online=False, error="zigbee ikke startet")
    return await _hub.apply(dev, command)


def apply_sync(dev: dict[str, Any], command: Any) -> dict[str, Any]:
    if _hub is None or _hub.loop is None:
        return public_state(dev, online=False, error="zigbee ikke startet")
    fut = asyncio.run_coroutine_threadsafe(_hub.apply(dev, command), _hub.loop)
    return fut.result(timeout=15)


def _parse_nwk(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw), 0)
    except ValueError:
        return None
