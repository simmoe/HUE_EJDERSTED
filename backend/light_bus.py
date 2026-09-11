"""Generic garden light commands. Protocols stay in adapters.

The kiosk and the AC-restore policy both speak LightCommand. Flare is Tuya
today; a Hue/Zigbee lamp later is another adapter, not a second handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import garden_lights

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
    by_rule: bool,
    ac_was_on: bool | None,
    ac_on: bool,
    online: bool,
) -> bool:
    """Rising edge on Fossibot AC that the power rule caused (SoC back at 25 %)."""
    return bool(by_rule and online and ac_on and ac_was_on is False)


def protocol_of(dev: dict[str, Any]) -> str:
    return str(dev.get("protocol") or "tuya").strip().lower() or "tuya"


def apply(dev: dict[str, Any], command: LightCommand, *, stop_scene: bool = True) -> dict[str, Any]:
    if stop_scene:
        import light_scenes

        light_scenes.stop(str(dev.get("id") or ""))
    handler = _ADAPTERS.get(protocol_of(dev))
    if handler is None:
        return garden_lights.public_light(
            dev, online=False, error=f"ukendt protokol: {protocol_of(dev)}"
        )
    try:
        return handler(dev, command)
    except Exception as exc:
        return garden_lights.public_light(dev, online=False, error=str(exc))


def apply_all(command: LightCommand) -> list[dict[str, Any]]:
    return [apply(dev, command) for dev in garden_lights.configured_devices() if dev.get("id")]


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
