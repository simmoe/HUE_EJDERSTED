"""Garden AC policy: mode, floor, hold, home, night.

Solar still follows its own sun window (charge relay). This module only decides
whether the Fossibot AC outlet should be on. It is always active on the garden
hub. Layers, top wins:

  mode    kiosk tænd / auto / sluk. on/off persist until Simon changes them.
          Nothing else — not the 15 % floor, not home, not night — overrides.
  floor   SoC <= off %  → AC off. Auto only. Burns a hold-on so the outlet
                          does not flap at the edge.
  hold    leftover timed hold (REST/WS). Auto only. Beats home/night.
  home    camera says someone is home → AC on. Only in auto.
  night   after sunset, unless the camera says home → AC off. Only in auto.
          Dark/blind/unknown is night, not an alibi to leave 230 V on.
  else    no opinion. Daylight and a high SoC do not turn 230 V on.

Night uses civil sunset, not the charge-relay cutoff (sunset − 90 min). The
kiosk still charges from 230 V until it moves to Fossibot USB.

Defaults for the floor are 15/25 (`Band.on` is unused). Raising the floor is
still a deploy setting.

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
VALID_MODES = ("auto", "on", "off")

SOURCE_FLOOR = "floor"
SOURCE_HOLD = "hold"
SOURCE_HOME = "home"
SOURCE_NIGHT = "night"
SOURCE_RULE = "rule"  # kept so old Firestore events still decode


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
class Band:
    """SoC hysteresis band: off at or below `off`, on at or above `on`."""

    off: float = OFF_PERCENT
    on: float = ON_PERCENT

    def __post_init__(self) -> None:
        if not 0 <= self.off < self.on <= 100:
            raise ValueError(f"power band must satisfy 0 <= off < on <= 100, got {self.off}/{self.on}")


DEFAULT_BAND = Band()


@dataclass(frozen=True)
class Decision:
    ac_on: bool
    source: str  # SOURCE_FLOOR | SOURCE_HOLD | SOURCE_HOME | SOURCE_NIGHT
    threshold: float | None = None  # the band edge that decided, for floor/rule


def desired_ac(
    soc: float | None,
    *,
    hold: Hold | None,
    wall: float,
    band: Band = DEFAULT_BAND,
    sun_up: bool = True,
    someone_home: bool | None = None,
    mode: str = "auto",
) -> Decision | None:
    """What the outlet should be right now. None = no opinion."""
    if mode == "on":
        return Decision(True, SOURCE_HOLD)
    if mode == "off":
        return Decision(False, SOURCE_HOLD)
    if soc is not None and soc <= band.off:
        return Decision(False, SOURCE_FLOOR, band.off)
    if hold is not None and hold.active(wall):
        return Decision(hold.ac_on, SOURCE_HOLD)
    if someone_home is True:
        return Decision(True, SOURCE_HOME)
    if not sun_up:
        return Decision(False, SOURCE_NIGHT)
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
    band: Band = DEFAULT_BAND,
    sun_up: bool = True,
    someone_home: bool | None = None,
    mode: str = "auto",
) -> Decision | None:
    """The press we should make now, or None to leave the finger alone."""
    if not online:
        return None
    want = desired_ac(
        soc,
        hold=hold,
        wall=wall,
        band=band,
        sun_up=sun_up,
        someone_home=someone_home,
        mode=mode,
    )
    if want is None or want.ac_on is ac_on:
        return None
    if last_press_at and now - last_press_at < cooldown_s:
        return None
    return want


def still_wants(original: Decision | None, again: Decision | None) -> bool:
    """Should we still execute `original` after a fresh decide()?

    A later tap on tænd/sluk must cancel a stale auto/night/floor press. Same
    on/off intent may proceed even if the source name changed.
    """
    if original is None or again is None:
        return False
    return original.ac_on is again.ac_on


class PowerPolicy:
    def __init__(self, state_path: Path, *, band: Band = DEFAULT_BAND):
        self._state_path = state_path
        self.band = band
        self._lock = threading.Lock()
        loaded_hold, loaded_mode = self._load()
        self.hold: Hold | None = loaded_hold
        self.mode: str = loaded_mode
        self.last_press_at = 0.0
        self.last_press_source: str | None = None

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load(self) -> tuple[Hold | None, str]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            mode = data.get("mode")
            if mode not in VALID_MODES:
                mode = "auto"
            return Hold.from_json(data.get("hold")), mode
        except Exception:
            return None, "auto"

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(
                    {
                        "hold": self.hold.to_json() if self.hold else None,
                        "mode": self.mode,
                    }
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[power] could not persist: {exc}")

    def set_mode(self, mode: str) -> str:
        if mode not in VALID_MODES:
            raise ValueError(f"invalid power mode: {mode}")
        with self._lock:
            self.mode = mode
            self.hold = None
            # Manual must be able to press even if auto just used the finger.
            if mode in ("on", "off"):
                self.last_press_at = 0.0
                self.last_press_source = None
            self._save()
            return self.mode

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

    def decide(
        self,
        status: dict[str, Any],
        *,
        now: float | None = None,
        wall: float | None = None,
        sun_up: bool = True,
        someone_home: bool | None = None,
    ) -> Decision | None:
        soc = _soc(status)
        wall = wall if wall is not None else time.time()
        hold = self.active_hold(wall)
        # Floor burns a hold-on in auto so we do not flap at the edge.
        # Manual tænd/sluk is never touched here.
        if self.mode == "auto" and soc is not None and soc <= self.band.off:
            if hold is not None and hold.ac_on:
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
            band=self.band,
            sun_up=sun_up,
            someone_home=someone_home,
            mode=self.mode,
        )

    def status(
        self,
        fossibot: dict[str, Any] | None = None,
        *,
        wall: float | None = None,
        sun_up: bool = True,
        someone_home: bool | None = None,
    ) -> dict[str, Any]:
        fb = fossibot or {}
        wall = wall if wall is not None else time.time()
        hold = self.active_hold(wall)
        want = desired_ac(
            _soc(fb),
            hold=hold,
            wall=wall,
            band=self.band,
            sun_up=sun_up,
            someone_home=someone_home,
            mode=self.mode,
        )
        return {
            "hold": hold.to_json() if hold else None,
            "mode": self.mode,
            "onPercent": self.band.on,
            "offPercent": self.band.off,
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
