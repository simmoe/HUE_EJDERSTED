"""IKEA / EmberZNet lights on the Sonoff dongle. Opens the existing PAN."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

import lights_log

DEFAULT_PORT = (
    "/dev/serial/by-id/"
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_"
    "9405800ca678f011b5fff4eba7772636-if00-port0"
)
DB_PATH = Path(__file__).parent.parent / "zigbee.db"
KNOWN_SENG = "34:8d:13:ff:fe:7c:32:59"
KNOWN_RODRET = "08:fd:52:ff:fe:d3:52:0f"
SENSOR_AWAKE_S = 90.0
BIND_RETRY_S = 30.0
BIND_TRIES = 40

StateHook = Callable[[dict[str, Any]], None]


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


def last_seen_age_s(device: Any, *, now: float | None = None) -> float | None:
    seen = getattr(device, "last_seen", None)
    if seen is None:
        return None
    if hasattr(seen, "timestamp"):
        seen = seen.timestamp()
    try:
        age = (now if now is not None else time.time()) - float(seen)
    except (TypeError, ValueError):
        return None
    return max(0.0, age)


def sensor_is_awake(device: Any, *, max_age_s: float = SENSOR_AWAKE_S, now: float | None = None) -> bool:
    age = last_seen_age_s(device, now=now)
    return age is not None and age <= max_age_s


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
        self._bind_ok = False
        self._bind_wait_logged = False
        self._bind_task: asyncio.Task | None = None
        self._bind_busy = False

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
                self._listen_sensor(device)
            if is_lamp(model):
                self._toilet_ieee = ieee
        lights_log.log(
            "zigbee.up",
            channel=int(app.state.network_info.channel),
            pan=f"0x{int(app.state.network_info.pan_id):04X}",
            toilet=self._toilet_ieee or "",
            sensor=self._sensor_ieee or "",
        )
        self._bind_task = self.loop.create_task(self._bind_when_awake())
        for dev in garden_lights.configured_devices():
            if str(dev.get("protocol") or "") in ("zigbee", "ikea") and dev.get("id"):
                self.loop.create_task(self._refresh(dev, source="boot"))

    async def permit(self, seconds: int = 180) -> None:
        if self._app is None:
            raise RuntimeError("zigbee ikke startet")
        await self._app.permit(max(1, int(seconds)))
        lights_log.log("zigbee.permit", seconds=int(seconds))

    def _sensor_device(self):
        from zigpy.types import EUI64

        if self._app is None or not self._sensor_ieee:
            return None
        ieee = EUI64.convert(self._sensor_ieee)
        if ieee not in self._app.devices:
            return None
        return self._app.get_device(ieee)

    def _toilet_device(self):
        from zigpy.types import EUI64

        if self._app is None or not self._toilet_ieee:
            return None
        ieee = EUI64.convert(self._toilet_ieee)
        if ieee not in self._app.devices:
            return None
        return self._app.get_device(ieee)

    async def _bind_when_awake(self) -> None:
        for _ in range(BIND_TRIES):
            if self._app is None or self._bind_ok:
                return
            await self._try_bind_toilet()
            if self._bind_ok:
                return
            await asyncio.sleep(BIND_RETRY_S)

    async def _try_bind_toilet(self) -> bool:
        if self._bind_ok or self._bind_busy:
            return self._bind_ok
        if not (self._sensor_ieee and self._toilet_ieee):
            return False
        sensor = self._sensor_device()
        toilet = self._toilet_device()
        if sensor is None or toilet is None:
            if not self._bind_wait_logged:
                lights_log.log(
                    "zigbee.bind",
                    ok=False,
                    detail="missing device",
                    sensor=self._sensor_ieee or "",
                    toilet=self._toilet_ieee or "",
                )
                self._bind_wait_logged = True
            return False
        if not sensor_is_awake(sensor):
            if not self._bind_wait_logged:
                age = last_seen_age_s(sensor)
                lights_log.log(
                    "zigbee.bind",
                    ok=False,
                    detail="sensor asleep",
                    age_s=None if age is None else round(age),
                )
                self._bind_wait_logged = True
            return False
        self._bind_wait_logged = False
        self._bind_busy = True
        try:
            result = await self.bind_on_off(self._sensor_ieee, self._toilet_ieee)
            ok = bool(result) and all("FAIL" not in part and "missing" not in part for part in result)
            self._bind_ok = ok
            lights_log.log("zigbee.bind", ok=ok, result=",".join(result))
            return ok
        except Exception as exc:
            lights_log.log("zigbee.bind", ok=False, detail=str(exc)[:160])
            return False
        finally:
            self._bind_busy = False

    async def bind_on_off(self, source_ieee: str, dest_ieee: str, source_nwk: int | None = None) -> list[str]:
        from zigpy.types import EUI64
        from zigpy.zdo.types import MultiAddress

        if self._app is None:
            raise RuntimeError("zigbee ikke startet")
        src_ieee = EUI64.convert(source_ieee)
        dst_ieee = EUI64.convert(dest_ieee)
        if src_ieee not in self._app.devices:
            return ["missing-source"]
        if dst_ieee not in self._app.devices:
            return ["missing-dest"]
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
        lights_log.log("zigbee.ready", nwk=record["nwk"], model=model, ieee=ieee)
        if ieee in {KNOWN_SENG, KNOWN_RODRET}:
            if ieee == KNOWN_SENG:
                import garden_lights

                seng = next(
                    (d for d in garden_lights.configured_devices() if d.get("id") == "seng"),
                    {"id": "seng", "name": "Seng", "protocol": "zigbee", "ieee": ieee},
                )
                await self._refresh(seng, source="ready")
            return
        if is_lamp(model):
            self._toilet_ieee = ieee
            _adopt_toilet(ieee, record["nwk"], self._sensor_ieee)
            import garden_lights

            toilet = next(
                (d for d in garden_lights.configured_devices() if d.get("id") == "toilet"),
                {"id": "toilet", "name": "Toilet", "protocol": "zigbee", "ieee": ieee},
            )
            await self._refresh(toilet, source="ready")
            return
        if is_motion(model):
            self._sensor_ieee = ieee
            self._listen_sensor(device)
            await self._try_bind_toilet()

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

            ep = device.endpoints[1]
            ep.in_clusters[OnOff.cluster_id].add_listener(_LampWatch(self, light_id, "on_off"))
            level = ep.in_clusters.get(LevelControl.cluster_id)
            if level is not None:
                level.add_listener(_LampWatch(self, light_id, "level"))
            self._listening.add(key)
        except Exception as exc:
            lights_log.log("zigbee.listen", id=light_id, ok=False, detail=str(exc)[:160])

    def _listen_sensor(self, device: Any) -> None:
        key = f"sensor:{device.ieee}"
        if key in self._listening:
            return
        try:
            ep = device.endpoints.get(1)
            if ep is None:
                return
            for cluster in list(getattr(ep, "in_clusters", {}).values()):
                cluster.add_listener(_SensorWatch(self))
            self._listening.add(key)
        except Exception as exc:
            lights_log.log("zigbee.listen", id="sensor", ok=False, detail=str(exc)[:160])

    async def _read_on_level(self, device: Any) -> tuple[bool, int]:
        from zigpy.zcl.clusters.general import LevelControl, OnOff

        onoff = device.endpoints[1].in_clusters[OnOff.cluster_id]
        result = await asyncio.wait_for(onoff.read_attributes(["on_off"], allow_cache=False), 8)
        on = bool(attr_value(result, "on_off", False))
        bri = 100 if on else 0
        level = device.endpoints[1].in_clusters.get(LevelControl.cluster_id)
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

        onoff = device.endpoints[1].in_clusters[OnOff.cluster_id]
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
        if self._bind_task is not None:
            self._bind_task.cancel()
            self._bind_task = None
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
        light_id = str(dev.get("id") or "")
        try:
            device = await self._device(dev)
            onoff = device.endpoints[1].in_clusters[OnOff.cluster_id]
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
            level = device.endpoints[1].in_clusters.get(LevelControl.cluster_id)
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
        if not device.is_initialized or 1 not in device.endpoints:
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


class _SensorWatch:
    def __init__(self, hub: ZigbeeHub) -> None:
        self.hub = hub

    def attribute_updated(self, attrid: Any, value: Any, *args: Any) -> None:
        lights_log.log("zigbee.report", id="sensor", attrid=attrid, value=value)
        loop = self.hub.loop
        if loop is None or self.hub._bind_ok:
            return
        loop.create_task(self.hub._try_bind_toilet())


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
