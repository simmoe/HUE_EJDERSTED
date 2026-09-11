"""Named garden-light scenes. Fest is a slow colour wash, not a strobe."""

from __future__ import annotations

import asyncio
import math
from typing import Awaitable, Callable

from light_bus import LightCommand

ApplyFn = Callable[[dict, LightCommand], Awaitable[dict]]

_fest_tasks: dict[str, asyncio.Task] = {}

HYGGE = LightCommand(on=True, white=True, brightness=100)
DAEMPET = LightCommand(on=True, white=True, brightness=22)
FEST_TICK_S = 0.55

# Tequila sunrise: grenadine → orange → gold, one lazy bachata bar.
_HUE_LO = 8
_HUE_HI = 38
_BAR_S = 7.4
_SWAY_S = 11.0
_BREATH_S = 9.2


def fest_frame(elapsed_s: float) -> LightCommand:
    t = max(0.0, float(elapsed_s))
    wave = 0.5 + 0.5 * math.sin(t * 2 * math.pi / _BAR_S)
    sway = 0.5 + 0.5 * math.sin(t * 2 * math.pi / _SWAY_S + 0.8)
    breath = 0.5 + 0.5 * math.sin(t * 2 * math.pi / _BREATH_S)
    hue = int(round(_HUE_LO + (_HUE_HI - _HUE_LO) * wave))
    sat = int(round(74 + 16 * sway))
    brightness = int(round(40 + 26 * breath))
    return LightCommand(on=True, hue=hue, sat=sat, brightness=brightness)


def command_for(scene: str) -> LightCommand | None:
    name = scene.strip().lower()
    if name in ("hygge", "kraftig"):
        return HYGGE
    if name in ("daempet", "dæmpet"):
        return DAEMPET
    if name == "off":
        return LightCommand(on=False, brightness=0)
    return None


def stop(light_id: str) -> None:
    task = _fest_tasks.pop(light_id, None)
    if task is not None:
        task.cancel()


def stop_all() -> None:
    for light_id in list(_fest_tasks):
        stop(light_id)


async def start_fest(
    dev: dict,
    apply_fn: ApplyFn,
    on_state: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    light_id = str(dev.get("id") or "")
    stop(light_id)

    async def _run() -> None:
        t0 = asyncio.get_running_loop().time()
        try:
            while True:
                state = await apply_fn(dev, fest_frame(asyncio.get_running_loop().time() - t0))
                state["scene"] = "fest"
                if on_state is not None:
                    await on_state(state)
                await asyncio.sleep(FEST_TICK_S)
        except asyncio.CancelledError:
            return

    _fest_tasks[light_id] = asyncio.create_task(_run())
