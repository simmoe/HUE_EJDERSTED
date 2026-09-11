"""Garden AC policy: floor, resume, hold.

Solar still follows its own sun window. This module only decides whether the
Fossibot AC outlet should be on. It is always active on the garden hub — there
is no "autonomous" flag any more. Three layers, top wins:

  floor   SoC <= 15 %  → AC off. Beats everything, and burns a hold-on so the
                          outlet does not flap at the threshold.
  hold    a tap on the kiosk: AC on/off until a wall-clock deadline
                          (1 t · 2 t · 5 t · i morgen). Beats the rules below.
  resume  SoC >= 25 %  → AC on.
  band    15–25 %      → no opinion.

The night rule (AC off outside the sun window unless someone is home) waits
until the router is off the inverter; otherwise we cut our own uplink.

The SwitchBot finger is a momentary press; Fossibot `acOn` is the source of
truth.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ON_PERCENT = 25.0
OFF_PERCENT = 15.0
PRESS_COOLDOWN_S = 90.0

# Fixed-length holds in seconds. "tomorrow" is resolved by the caller from the
# solar window (next day's on-time), so it is not listed here.
HOLD_DURATIONS: dict[str, float] = {"1h": 3600.0, "2h": 7200.0, "5h": 18000.0}
HOLD_TOMORROW = "tomorrow"
VALID_DURATIONS = frozenset({*HOLD_DURATIONS, HOLD_TOMORROW})

SOURCE_FLOOR = "floor"
SOURCE_HOLD = "hold"
SOURCE_RULE = "rule"


@dataclass(frozen=True)
class Hold:
    ac_on: bool
    until: float  # wall clock, seconds since epoch
    duration: str  # the wheel choice that made it, for the UI

    def active(self, wall: float) -> bool:
        return wall < self.until

    def to_json(self) -> dict[str, Any]:
        return {"acOn": self.ac_on, "until": self.until, "duration": self.duration}

    @classmethod
    def from_json(cls, raw: Any) -> "Hold | None":
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                ac_on=bool(raw["acOn"]),
                until=float(raw["until"]),
                duration=str(raw.get("duration") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Decision:
    ac_on: bool
    source: str  # SOURCE_FLOOR | SOURCE_HOLD | SOURCE_RULE

    @property
    def threshold(self) -> float | None:
        if self.source == SOURCE_FLOOR:
            return OFF_PERCENT
        if self.source == SOURCE_RULE:
            return ON_PERCENT
        return None


def desired_ac(soc: float | None, *, hold: Hold | None, wall: float) -> Decision | None:
    """What the outlet should be right now. None = no opinion."""
    if soc is not None and soc <= OFF_PERCENT:
        return Decision(False, SOURCE_FLOOR)
    if hold is not None and hold.active(wall):
        return Decision(hold.ac_on, SOURCE_HOLD)
    if soc is None:
        return None
    if soc >= ON_PERCENT:
        return Decision(True, SOURCE_RULE)
    return None


def decide_press(
    *,
    online: bool,
    soc: float | None,
    ac_on: bool,
    hold: Hold | None,
    now: float,
    wall: float,
    last_press_at: float,
    cooldown_s: float = PRESS_COOLDOWN_S,
) -> Decision | None:
    """The press we should make now, or None to leave the finger alone."""
    if not online:
        return None
    want = desired_ac(soc, hold=hold, wall=wall)
    if want is None or want.ac_on is ac_on:
        return None
    if last_press_at and now - last_press_at < cooldown_s:
        return None
    return want


class PowerPolicy:
    def __init__(self, state_path: Path):
        self._state_path = state_path
        self._lock = threading.Lock()
        self.hold: Hold | None = self._load()
        self.last_press_at = 0.0
        self.last_press_source: str | None = None

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load(self) -> Hold | None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return Hold.from_json(data.get("hold"))
        except Exception:
            return None

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"hold": self.hold.to_json() if self.hold else None}),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[power] could not persist hold: {exc}")

    # ── Hold ─────────────────────────────────────────────────────────────────
    def set_hold(self, ac_on: bool, *, until: float, duration: str) -> Hold:
        with self._lock:
            self.hold = Hold(ac_on=bool(ac_on), until=float(until), duration=duration)
            self._save()
            return self.hold

    def clear_hold(self) -> None:
        with self._lock:
            if self.hold is None:
                return
            self.hold = None
            self._save()

    def active_hold(self, wall: float | None = None) -> Hold | None:
        """The current hold, dropping it if it has run out."""
        wall = wall if wall is not None else time.time()
        hold = self.hold
        if hold is None:
            return None
        if hold.active(wall):
            return hold
        self.clear_hold()
        return None

    # ── Decisions ────────────────────────────────────────────────────────────
    def note_press(self, source: str, now: float | None = None) -> None:
        with self._lock:
            self.last_press_at = now if now is not None else time.monotonic()
            self.last_press_source = source

    def pressed_recently(self, source: str, *, now: float | None = None, within_s: float = 180.0) -> bool:
        """Did we press for `source` within the last `within_s`? Used to tell a
        rule-driven AC edge from Simon pressing the physical button."""
        if self.last_press_source != source or not self.last_press_at:
            return False
        now = now if now is not None else time.monotonic()
        return now - self.last_press_at < within_s

    def decide(self, status: dict[str, Any], *, now: float | None = None, wall: float | None = None) -> Decision | None:
        soc = _soc(status)
        wall = wall if wall is not None else time.time()
        hold = self.active_hold(wall)
        # The floor burns a hold-on: otherwise we would turn on again at 15.1 %,
        # drain to 15 %, turn off, and repeat every cooldown.
        if hold is not None and hold.ac_on and soc is not None and soc <= OFF_PERCENT:
            self.clear_hold()
            hold = None
        return decide_press(
            online=bool(status.get("online")),
            soc=soc,
            ac_on=bool(status.get("acOn")),
            hold=hold,
            now=now if now is not None else time.monotonic(),
            wall=wall,
            last_press_at=self.last_press_at,
        )

    def status(self, fossibot: dict[str, Any] | None = None, *, wall: float | None = None) -> dict[str, Any]:
        fb = fossibot or {}
        wall = wall if wall is not None else time.time()
        hold = self.active_hold(wall)
        want = desired_ac(_soc(fb), hold=hold, wall=wall)
        return {
            "hold": hold.to_json() if hold else None,
            "onPercent": ON_PERCENT,
            "offPercent": OFF_PERCENT,
            "wantAc": want.ac_on if want else None,
            "wantSource": want.source if want else None,
        }


def _soc(status: dict[str, Any]) -> float | None:
    raw = status.get("socPercent")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
