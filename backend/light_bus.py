"""Generic garden light commands. Protocols stay in adapters.

The kiosk and the AC-restore policy both speak LightCommand. Flare is Tuya
today; a Hue/Zigbee lamp later is another adapter, not a second handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import garden_lights
import lights_log

AFTER_AC_FIRST_WAIT_S = 20.0
AFTER_AC_RETRY_S = 20.0
# Router boot + LTE + Flare rejoining Wi-Fi can take several minutes.
AFTER_AC_GIVE_UP_S = 900.0


@dataclass(frozen=True)
class LightCommand:
    on: bool | None = None
    brightness: int | None = None
    hue: int | None = None
    sat: int | None = None
    white: bool = False
    transition_s: float | None = None


OFF = LightCommand(on=False, brightness=0)


def should_force_off_after_ac(
    *,
    ac_was_on: bool | None,
    ac_on: bool,
    online: bool,
) -> bool:
    """Rising edge on Fossibot AC. The toilet boots on; the motion sensor should own it."""
    return bool(online and ac_on and ac_was_on is False)


def protocol_of(dev: dict[str, Any]) -> str:
    return str(dev.get("protocol") or "tuya").strip().lower() or "tuya"


def apply(
    dev: dict[str, Any],
    command: LightCommand,
    *,
    stop_scene: bool = True,
    source: str = "unknown",
) -> dict[str, Any]:
    light_id = str(dev.get("id") or "")
    lights_log.log(
        "apply",
        id=light_id,
        protocol=protocol_of(dev),
        source=source,
        on=command.on,
        brightness=command.brightness,
        hue=command.hue,
        white=command.white or None,
    )
    if stop_scene:
        import light_scenes

        light_scenes.stop(light_id)
    handler = _ADAPTERS.get(protocol_of(dev))
    if handler is None:
        state = garden_lights.public_light(
            dev, online=False, error=f"ukendt protokol: {protocol_of(dev)}"
        )
        lights_log.log("apply.result", id=light_id, source=source, ok=False, error=state.get("error"))
        return state
    try:
        state = handler(dev, command)
        lights_log.log(
            "apply.result",
            id=light_id,
            source=source,
            online=state.get("online"),
            on=state.get("on"),
            brightness=state.get("brightness"),
            error=state.get("error"),
        )
        return state
    except Exception as exc:
        lights_log.log("apply.result", id=light_id, source=source, ok=False, error=str(exc)[:160])
        return garden_lights.public_light(dev, online=False, error=str(exc))


def apply_all(command: LightCommand, *, source: str = "ac.sweep") -> list[dict[str, Any]]:
    return [
        apply(dev, command, source=source)
        for dev in garden_lights.configured_devices()
        if dev.get("id")
    ]


def apply_toilet_off() -> list[dict[str, Any]]:
    """Only the toilet. Seng and gårdlys stay when Simon turns 230 V on."""
    return [
        apply(dev, OFF, source="ac.sweep")
        for dev in garden_lights.configured_devices()
        if dev.get("id") == "toilet"
    ]


def refresh_for_apply() -> None:
    """Give Tuya a chance to reappear after mains return."""
    garden_lights.adopt_scan(garden_lights.scan_lan(maxretry=1))


def _apply_zigbee(dev: dict[str, Any], command: LightCommand) -> dict[str, Any]:
    import zigbee_lights

    return zigbee_lights.apply_sync(dev, command)


def _apply_tuya(dev: dict[str, Any], command: LightCommand) -> dict[str, Any]:
    if command.on is False or (command.brightness is not None and command.brightness <= 0):
        return garden_lights.set_brightness(dev, 0)
    brightness = command.brightness
    if command.white:
        return garden_lights.set_white(dev, brightness)
    if command.hue is not None:
        sat = 80 if command.sat is None else command.sat
        return garden_lights.set_color(dev, command.hue, sat, brightness)
    if brightness is None:
        brightness = 100
    return garden_lights.set_brightness(dev, brightness)


_ADAPTERS: dict[str, Callable[[dict[str, Any], LightCommand], dict[str, Any]]] = {
    "tuya": _apply_tuya,
    "flare": _apply_tuya,
    "zigbee": _apply_zigbee,
    "ikea": _apply_zigbee,
}


def all_off_and_online(states: list[dict[str, Any]]) -> bool:
    real = [s for s in states if s.get("id")]
    return bool(real) and all(s.get("online") and not s.get("on") and not s.get("any_on") for s in real)
