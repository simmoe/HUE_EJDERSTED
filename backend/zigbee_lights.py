"""Zigbee lights on the Sonoff dongle. Opens the existing PAN.

IKEA seng/toilet and a Hue loft bulb share the same coordinator. Hue lighting
clusters live on endpoint 11, IKEA on 1 — look the OnOff endpoint up.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import lights_log

DEFAULT_PORT = (
    "/dev/serial/by-id/"
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_"
    "9405800ca678f011b5fff4eba7772636-if00-port0"
)
def durable_db_path() -> Path:
    """Zigbee device table lives outside the repo so a deploy cannot wipe it."""
    path = Path.home() / ".local" / "share" / "hue" / "zigbee.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = Path(__file__).resolve().parent.parent / "zigbee.db"
    if not path.exists() and legacy.is_file() and legacy.stat().st_size > 0:
        path.write_bytes(legacy.read_bytes())
    return path


DB_PATH = durable_db_path()
KNOWN_SENG = "34:8d:13:ff:fe:7c:32:59"
KNOWN_TOILET = "94:34:69:ff:fe:64:2c:5b"
KNOWN_RODRET = "08:fd:52:ff:fe:d3:52:0f"
LAMP_NAMES = {"seng": "Seng", "toilet": "Toilet", "loft": "Loft"}
HUE_LAMP_PREFIXES = (
    "LCT", "LWA", "LWB", "LTW", "LCA", "LCD", "LCE", "LTA", "LTC", "LTD", "LST", "LLC", "LWO", "LWL",
)
HUE_NOT_LAMP_PREFIXES = ("RWL", "RDM", "SML", "ROM", "RDT", "LOM")

StateHook = Callable[[dict[str, Any]], None]


def is_motion(model: str) -> bool:
    text = (model or "").lower()
    return any(token in text for token in ("motion", "vallhorn", "occupancy"))


def is_hue_lamp(model: str, manufacturer: str = "") -> bool:
    text = (model or "").strip()
    upper = text.upper()
    if any(upper.startswith(prefix) for prefix in HUE_NOT_LAMP_PREFIXES):
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("dimmer", "motion", "tap", "switch", "outlet")):
        return False
    mfr = (manufacturer or "").lower()
    if "philips" in mfr or "signify" in mfr:
        return True
    return any(upper.startswith(prefix) for prefix in HUE_LAMP_PREFIXES)


def is_lamp(model: str, manufacturer: str = "") -> bool:
    if is_motion(model):
        return False
    if is_hue_lamp(model, manufacturer):
        return True
    text = (model or "").lower()
    if "dimmer" in text or "rodret" in text or "styrbar" in text:
        return False
    return any(token in text for token in ("bulb", "driver", "led", "lamp", "stoftmoln", "gu10", "e27", "e14"))


def adopt_lamp_id(
    model: str,
    manufacturer: str,
    ieee: str,
    devices: list[dict[str, Any]],
) -> str | None:
    """Map a newly interviewed device to seng, toilet or loft. Never steal a taken IEEE."""
    if ieee == KNOWN_SENG:
        return "seng"
    if ieee == KNOWN_TOILET:
        return "toilet"
    if ieee == KNOWN_RODRET:
        return None
    by_ieee = next((d for d in devices if d.get("ieee") == ieee), None)
    if by_ieee:
        return str(by_ieee.get("id") or "") or None
    if is_hue_lamp(model, manufacturer):
        loft = next((d for d in devices if d.get("id") == "loft"), None)
        if loft and loft.get("ieee") and loft["ieee"] != ieee:
            return None
        return "loft"
    if is_lamp(model, manufacturer):
        toilet = next((d for d in devices if d.get("id") == "toilet"), None)
        if toilet and toilet.get("ieee") and toilet["ieee"] != ieee:
            return None
        return "toilet"
    return None


def light_endpoint(device: Any) -> Any:
    from zigpy.zcl.clusters.general import OnOff

    endpoints = getattr(device, "endpoints", None) or {}
    found: list[tuple[int, Any]] = []
    for epid, ep in endpoints.items():
        if epid == 0:
            continue
        clusters = getattr(ep, "in_clusters", None) or {}
        if OnOff.cluster_id in clusters:
            found.append((int(epid), ep))
    if not found:
        raise RuntimeError("ingen on/off-endpoint")
    found.sort(key=lambda item: item[0])
    return found[0][1]


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


def attr_value(result: Any, name: str, default: Any = None) -> Any:
    success = result[0] if isinstance(result, tuple) else result
    if not isinstance(success, dict):
        return default
    if name in success:
        return success[name]
    aliases = {
        "on_off": {0, 0x0000, "on_off"},
        "current_level": {0, 0x0000, "current_level"},
        "occupancy": {0, 0x0000, "occupancy"},
        "measured_value": {0, 0x0000, "measured_value", "measuredValue"},
    }
    wanted = aliases.get(name, {name})
    for key, value in success.items():
        if key in wanted:
            return value
    return default


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
        self._on_state: StateHook | None = None
        self._listening: set[str] = set()

    async def start(self) -> None:
        from bellows.zigbee.application import ControllerApplication

        self.loop = asyncio.get_running_loop()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists() and self.db_path.stat().st_size == 0:
            self.db_path.unlink()
        app = ControllerApplication(
            {
                "device": {"path": self.port, "baudrate": 115200},
                "backup_enabled": False,
                "startup_energy_scan": False,
                "database_path": str(self.db_path),
                "use_thread": False,
            }
        )
        # ControllerApplication.new() loads the db. startup() alone never does,
        # so every restart forgot the PAN and waited for a device to speak.
        await app._load_db()
        await app.startup(auto_form=False)
        self._app = app
        await self._rediscover_from_coordinator()
        app.add_listener(_JoinWatch(self))
        import garden_lights

        garden_lights.upsert_device(id="loft", name="Loft", protocol="zigbee")
        for dev in garden_lights.configured_devices():
            if dev.get("id") == "toilet" and dev.get("ieee"):
                self._toilet_ieee = dev["ieee"]
            if dev.get("sensorIeee"):
                self._sensor_ieee = dev["sensorIeee"]
        if not self._toilet_ieee:
            self._toilet_ieee = KNOWN_TOILET
        for device in app.devices.values():
            model = str(getattr(device, "model", "") or "")
            if is_motion(model):
                self._sensor_ieee = str(device.ieee)
        lights_log.log(
            "zigbee.up",
            channel=int(app.state.network_info.channel),
            pan=f"0x{int(app.state.network_info.pan_id):04X}",
            toilet=self._toilet_ieee or "",
            sensor=self._sensor_ieee or "",
        )
        for dev in garden_lights.configured_devices():
            if str(dev.get("protocol") or "") not in ("zigbee", "ikea") or not dev.get("id"):
                continue
            if not dev.get("ieee"):
                self._publish(public_state(dev, online=False, error="ikke parret"))
                continue
            self.loop.create_task(self._refresh(dev, source="boot"))

    async def _rediscover_from_coordinator(self) -> None:
        """Ask the dongle who is still on the PAN, then interview anyone we forgot."""
        app = self._app
        coord = getattr(app, "_device", None)
        if coord is None:
            return
        try:
            neighbors = await app.topology._scan_neighbors(coord)
        except Exception as exc:
            lights_log.log("zigbee.neighbors", ok=False, detail=str(exc)[:160])
            return
        app.topology.neighbors[coord.ieee] = neighbors
        lights_log.log(
            "zigbee.neighbors",
            ok=True,
            n=len(neighbors),
            db=str(self.db_path),
            devices=len(app.devices),
        )
        await app.topology._find_unknown_devices(
            neighbors={coord.ieee: neighbors},
            routes={},
        )

    async def permit(self, seconds: int = 180) -> None:
        if self._app is None:
            raise RuntimeError("zigbee ikke startet")
        await self._app.permit(max(1, int(seconds)))
        lights_log.log("zigbee.permit", seconds=int(seconds))

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
        lights_log.log("zigbee.ready", nwk=record["nwk"], model=model, ieee=ieee)
        if is_motion(model):
            self._sensor_ieee = ieee
        import garden_lights

        light_id = adopt_lamp_id(model, record["manufacturer"], ieee, garden_lights.configured_devices())
        if not light_id:
            return
        fields = {
            "id": light_id,
            "name": LAMP_NAMES.get(light_id, light_id.title()),
            "protocol": "zigbee",
            "ieee": ieee,
            "nwk": record["nwk"],
        }
        if light_id == "toilet":
            self._toilet_ieee = ieee
            if self._sensor_ieee:
                fields["sensorIeee"] = self._sensor_ieee
        garden_lights.upsert_device(**fields)
        adopted = next(
            (d for d in garden_lights.configured_devices() if d.get("id") == light_id),
            fields,
        )
        await self._refresh(adopted, source="ready")

    def _publish(self, state: dict[str, Any]) -> None:
        light_id = str(state.get("id") or "")
        if light_id:
            self._cache[light_id] = dict(state)
        hook = self._on_state or _state_hook
        if hook is None:
            return
        try:
            hook(state)
        except Exception as exc:
            lights_log.log("zigbee.hook", ok=False, detail=str(exc)[:160])

    def _configured(self, light_id: str) -> dict[str, Any]:
        import garden_lights

        return next(
            (d for d in garden_lights.configured_devices() if d.get("id") == light_id),
            {"id": light_id, "name": light_id, "protocol": "zigbee"},
        )

    def _listen_lamp(self, device: Any, light_id: str) -> None:
        key = f"lamp:{light_id}"
        if key in self._listening:
            return
        try:
            from zigpy.zcl.clusters.general import LevelControl, OnOff

            ep = light_endpoint(device)
            ep.in_clusters[OnOff.cluster_id].add_listener(_LampWatch(self, light_id, "on_off"))
            level = ep.in_clusters.get(LevelControl.cluster_id)
            if level is not None:
                level.add_listener(_LampWatch(self, light_id, "level"))
            self._listening.add(key)
        except Exception as exc:
            lights_log.log("zigbee.listen", id=light_id, ok=False, detail=str(exc)[:160])

    async def _read_on_level(self, device: Any) -> tuple[bool, int]:
        from zigpy.zcl.clusters.general import LevelControl, OnOff

        ep = light_endpoint(device)
        onoff = ep.in_clusters[OnOff.cluster_id]
        result = await asyncio.wait_for(onoff.read_attributes(["on_off"], allow_cache=False), 8)
        on = bool(attr_value(result, "on_off", False))
        bri = 100 if on else 0
        level = ep.in_clusters.get(LevelControl.cluster_id)
        if level is not None:
            lres = await asyncio.wait_for(
                level.read_attributes(["current_level"], allow_cache=False), 8
            )
            raw = attr_value(lres, "current_level", None)
            if raw is not None:
                bri = max(0, min(100, int(round(int(raw) / 254 * 100))))
        return on, bri

    async def _bind_reports(self, device: Any, light_id: str) -> None:
        from zigpy.zcl.clusters.general import OnOff

        onoff = light_endpoint(device).in_clusters[OnOff.cluster_id]
        try:
            await asyncio.wait_for(onoff.bind(), 8)
            await asyncio.wait_for(onoff.configure_reporting("on_off", 1, 300, 1), 8)
            lights_log.log("zigbee.reporting", id=light_id, cluster="on_off", ok=True)
        except Exception as exc:
            lights_log.log(
                "zigbee.reporting",
                id=light_id,
                cluster="on_off",
                ok=False,
                detail=str(exc)[:160],
            )

    async def _refresh(self, dev: dict[str, Any], *, source: str) -> dict[str, Any]:
        if not str(dev.get("ieee") or "").strip():
            state = public_state(dev, online=False, error="ikke parret")
            self._publish(state)
            return state
        try:
            device = await self._device(dev)
            self._listen_lamp(device, str(dev.get("id") or ""))
            await self._bind_reports(device, str(dev.get("id") or ""))
            on, bri = await self._read_on_level(device)
            state = public_state(dev, on=on, brightness=bri, online=True)
            self._publish(state)
            lights_log.log(
                "zigbee.read",
                id=dev.get("id"),
                source=source,
                on=on,
                brightness=bri,
                nwk=f"0x{int(device.nwk):04X}",
            )
            return state
        except Exception as exc:
            state = public_state(dev, online=False, error=str(exc))
            self._publish(state)
            lights_log.log(
                "zigbee.read",
                id=dev.get("id"),
                source=source,
                ok=False,
                detail=str(exc)[:160],
            )
            return state

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
        return public_state(dev, online=False, error="ikke aflæst")

    async def apply(self, dev: dict[str, Any], command: Any) -> dict[str, Any]:
        from zigpy.zcl.clusters.general import LevelControl, OnOff

        if self._app is None:
            return public_state(dev, online=False, error="zigbee ikke startet")
        if not str(dev.get("ieee") or "").strip():
            return public_state(dev, online=False, error="ikke parret")
        light_id = str(dev.get("id") or "")
        try:
            device = await self._device(dev)
            ep = light_endpoint(device)
            onoff = ep.in_clusters[OnOff.cluster_id]
            want_on = command.on is not False and not (
                command.brightness is not None and command.brightness <= 0
            )
            if not want_on:
                lights_log.log("zigbee.zcl", id=light_id, cmd="off")
                await asyncio.wait_for(onoff.off(), 8)
                state = public_state(dev, on=False, brightness=0, online=True)
                self._publish(state)
                return state
            brightness = 100 if command.brightness is None else max(1, min(100, command.brightness))
            level = ep.in_clusters.get(LevelControl.cluster_id)
            lights_log.log("zigbee.zcl", id=light_id, cmd="on", brightness=brightness)
            await asyncio.wait_for(onoff.on(), 8)
            if level is not None:
                transition = 0
                if getattr(command, "transition_s", None):
                    transition = max(0, int(round(float(command.transition_s) * 10)))
                raw = max(1, min(254, int(round(brightness / 100 * 254))))
                await asyncio.wait_for(level.move_to_level(raw, transition), 8)
            state = public_state(dev, on=True, brightness=brightness, online=True)
            self._publish(state)
            return state
        except Exception as exc:
            state = public_state(dev, online=False, error=str(exc))
            lights_log.log("zigbee.zcl", id=light_id, ok=False, detail=str(exc)[:160])
            return state

    async def _device(self, dev: dict[str, Any]):
        from zigpy.types import EUI64

        ieee = EUI64.convert(str(dev.get("ieee") or ""))
        nwk = _parse_nwk(dev.get("nwk"))
        if ieee not in self._app.devices:
            self._app.add_device(nwk=nwk or 0xE6BF, ieee=ieee)
        device = self._app.get_device(ieee)
        if not device.is_initialized:
            await asyncio.wait_for(device.initialize(), 12)
        try:
            light_endpoint(device)
        except Exception:
            await asyncio.wait_for(device.initialize(), 12)
        return device

    def note_report(self, light_id: str, *, on: bool | None = None, brightness: int | None = None) -> None:
        dev = self._configured(light_id)
        prev = self._cache.get(light_id) or public_state(dev, online=True)
        next_on = prev.get("on") if on is None else on
        next_bri = prev.get("brightness") or 0 if brightness is None else brightness
        state = public_state(dev, on=bool(next_on), brightness=int(next_bri or 0), online=True)
        self._publish(state)


class _JoinWatch:
    def __init__(self, hub: ZigbeeHub) -> None:
        self.hub = hub

    def device_initialized(self, device: Any) -> None:
        loop = self.hub.loop
        if loop is not None:
            loop.create_task(self.hub._on_ready(device))


class _LampWatch:
    def __init__(self, hub: ZigbeeHub, light_id: str, kind: str) -> None:
        self.hub = hub
        self.light_id = light_id
        self.kind = kind

    def attribute_updated(self, attrid: Any, value: Any, *args: Any) -> None:
        lights_log.log(
            "zigbee.report",
            id=self.light_id,
            attr=self.kind,
            attrid=attrid,
            value=value,
        )
        if self.kind == "on_off":
            self.hub.note_report(self.light_id, on=bool(value))
            return
        if self.kind == "level" and value is not None:
            try:
                bri = max(0, min(100, int(round(int(value) / 254 * 100))))
            except (TypeError, ValueError):
                return
            self.hub.note_report(self.light_id, brightness=bri)


_hub: ZigbeeHub | None = None
_state_hook: StateHook | None = None


def hub() -> ZigbeeHub | None:
    return _hub


def set_state_hook(hook: StateHook | None) -> None:
    global _state_hook
    _state_hook = hook
    if _hub is not None:
        _hub._on_state = hook


async def permit(seconds: int = 180) -> None:
    if _hub is None:
        raise RuntimeError("zigbee ikke startet")
    await _hub.permit(seconds)


def recent_joins() -> list[dict[str, str]]:
    return list(_hub.joins) if _hub is not None else []


async def start(port: str = DEFAULT_PORT) -> None:
    global _hub
    _hub = ZigbeeHub(port=port)
    _hub._on_state = _state_hook
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
