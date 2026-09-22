"""Garden AC policy: manual tænd / sluk.

Solar still follows its own sun window (charge relay). This module only decides
whether the Fossibot AC outlet should be on. Pi, router and kiosk are on the
12 V DC group, so 230 V is for lamps and other mains loads. Simon turns it on
and off. There is no auto mode: SoC, camera presence and sunset do not press
the button.

A missing mode, or an old file that only said "auto", leaves the finger alone.
A legacy forever-hold named ``manual-on`` / ``manual-off`` is read once as
tænd / sluk.

The SwitchBot finger is a momentary press; Fossibot ``acOn`` is the source of
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
VALID_MODES = ("on", "off")

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
    mode: str = "",
) -> Decision | None:
    """What the outlet should be right now. None = leave it until Simon taps."""
    del soc, hold, wall, band, sun_up, someone_home
    if mode == "on":
        return Decision(True, SOURCE_HOLD)
    if mode == "off":
        return Decision(False, SOURCE_HOLD)
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
    mode: str = "",
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
        loaded_hold, loaded_mode, migrated = self._load()
        self.hold: Hold | None = loaded_hold
        self.mode: str = loaded_mode
        if migrated:
            self._save()
        self.last_press_at = 0.0
        self.last_press_source: str | None = None

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load(self) -> tuple[Hold | None, str, bool]:
        """Return hold, mode, and whether a legacy file was translated.

        ``auto``, a missing mode, and the old ``autonomous`` flag do not press.
        A forever hold named ``manual-on`` / ``manual-off`` becomes tænd / sluk.
        """
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return None, "", False
        if not isinstance(data, dict):
            return None, "", False
        mode = data.get("mode")
        hold = Hold.from_json(data.get("hold"))
        if mode in VALID_MODES:
            return hold, mode, False
        if hold is not None and str(hold.duration).startswith("manual-"):
            return None, "on" if hold.ac_on else "off", True
        if mode == "auto" or "autonomous" in data:
            return None, "", True
        return hold, "", False

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
