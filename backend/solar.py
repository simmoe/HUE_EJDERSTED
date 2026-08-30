"""Solar charge relay control with sun-cycle scheduling (garden).

A relay (GPIO17 by default) connects the solar panel to the battery. During the
sporadic light at dawn/dusk the battery (Fossibot 2400) chatters on/off for ~30
min, which is annoying and pointless. So the panel is only connected when the sun
is *safely* up (sunrise + offset) and is disconnected well before sunset
(sunset - offset), where yield is negligible anyway.

Modes:
  auto  – follow the computed sun-cycle window (default)
  on    – force connected (manual override)
  off   – force disconnected (manual override)

The relay line is held by this process via lgpio for as long as the backend
runs. On a machine without lgpio/GPIO (e.g. local dev) it degrades to a
simulated controller so the rest of the app keeps working.

If NTP is not synchronized (Pi reboot with no Wi-Fi, no RTC), auto mode
fail-opens: the panel stays connected so a dead Fossibot can still wake.
"""

from __future__ import annotations

import json
import math
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:  # Hardware GPIO is only present on the Pi.
    import lgpio  # type: ignore

    _HAS_LGPIO = True
except Exception:  # pragma: no cover - dev machines
    lgpio = None  # type: ignore
    _HAS_LGPIO = False


VALID_MODES = ("auto", "on", "off")
_CLOCK_CHECK_S = 30.0
_clock_trusted_at = 0.0
_clock_trusted = True


def reset_clock_cache() -> None:
    """Test helper."""
    global _clock_trusted_at, _clock_trusted
    _clock_trusted_at = 0.0
    _clock_trusted = True


def clock_is_trusted() -> bool:
    """True when systemd-timesyncd has NTP, or the wall clock is not epoch-era."""
    global _clock_trusted_at, _clock_trusted
    now = time.monotonic()
    if _clock_trusted_at and now - _clock_trusted_at < _CLOCK_CHECK_S:
        return _clock_trusted
    _clock_trusted = _read_ntp_synchronized()
    _clock_trusted_at = now
    return _clock_trusted


def _read_ntp_synchronized() -> bool:
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lower() in {"yes", "1", "true"}
    except Exception:
        pass
    return datetime.now(timezone.utc).year >= 2026


def _norm360(x: float) -> float:
    return x % 360.0


def sun_times(
    d: date, lat: float, lon: float, tz: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """Sunrise/sunset for a date as tz-aware datetimes (NOAA/Almanac algorithm).

    Returns (sunrise, sunset); a value is None on a polar day/night when the sun
    never crosses the horizon (not relevant at Danish latitudes, but guarded).
    Accurate to ~1 minute, which is plenty for a charge-relay schedule.
    """

    def event(rising: bool) -> datetime | None:
        zenith = 90.833  # official sunrise/sunset, includes atmospheric refraction
        n = d.timetuple().tm_yday
        lng_hour = lon / 15.0
        t = n + ((6 if rising else 18) - lng_hour) / 24.0
        m = 0.9856 * t - 3.289
        lsun = _norm360(
            m
            + 1.916 * math.sin(math.radians(m))
            + 0.020 * math.sin(math.radians(2 * m))
            + 282.634
        )
        ra = _norm360(math.degrees(math.atan(0.91764 * math.tan(math.radians(lsun)))))
        # Put RA in the same quadrant as the sun's true longitude.
        l_quad = math.floor(lsun / 90.0) * 90.0
        ra_quad = math.floor(ra / 90.0) * 90.0
        ra = (ra + (l_quad - ra_quad)) / 15.0
        sin_dec = 0.39782 * math.sin(math.radians(lsun))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = (
            math.cos(math.radians(zenith)) - sin_dec * math.sin(math.radians(lat))
        ) / (cos_dec * math.cos(math.radians(lat)))
        if cos_h > 1 or cos_h < -1:
            return None
        if rising:
            h = (360.0 - math.degrees(math.acos(cos_h))) / 15.0
        else:
            h = math.degrees(math.acos(cos_h)) / 15.0
        local_t = h + ra - 0.06571 * t - 6.622
        ut = local_t - lng_hour
        ut %= 24.0
        base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return (base + timedelta(hours=ut)).astimezone(tz)

    return event(True), event(False)


class SolarController:
    def __init__(self, cfg: dict[str, Any], state_path: Path):
        self.pin = int(cfg.get("gpioPin", 17))
        self.active_high = bool(cfg.get("activeHigh", True))
        self.lat = float(cfg.get("lat", 55.6761))
        self.lon = float(cfg.get("lon", 12.5683))
        self.sunrise_offset = int(cfg.get("sunriseOffsetMin", 30))
        self.sunset_offset = int(cfg.get("sunsetOffsetMin", 90))
        self.tz = ZoneInfo(str(cfg.get("tz", "Europe/Copenhagen")))
        self._state_path = state_path
        self._lock = threading.Lock()
        self.mode: str = self._load_mode()
        self._relay_on: bool = False
        self._chip = None
        self._claimed = False
        self.simulated = not _HAS_LGPIO
        self._init_gpio()
        self.apply()

    # ── GPIO ────────────────────────────────────────────────────────────────
    def _level_for(self, on: bool) -> int:
        if self.active_high:
            return 1 if on else 0
        return 0 if on else 1

    def _init_gpio(self) -> None:
        if not _HAS_LGPIO:
            print("[solar] lgpio unavailable — running in simulated mode")
            return
        try:
            self._chip = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self._chip, self.pin, self._level_for(False))
            self._claimed = True
            self._relay_on = False
        except Exception as exc:  # pragma: no cover
            self.simulated = True
            self._chip = None
            print(f"[solar] GPIO init failed ({exc}) — simulated mode")

    def _write(self, on: bool) -> None:
        if self._claimed and self._chip is not None:
            try:
                lgpio.gpio_write(self._chip, self.pin, self._level_for(on))
            except Exception as exc:  # pragma: no cover
                print(f"[solar] gpio_write failed: {exc}")
        self._relay_on = on

    # ── State persistence ────────────────────────────────────────────────────
    def _load_mode(self) -> str:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            mode = data.get("mode")
            if mode in VALID_MODES:
                return mode
        except Exception:
            pass
        return "auto"

    def _save_mode(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps({"mode": self.mode}), encoding="utf-8"
            )
        except Exception as exc:
            print(f"[solar] could not persist mode: {exc}")

    # ── Schedule ──────────────────────────────────────────────────────────────
    def window(self, d: date) -> tuple[datetime | None, datetime | None]:
        """Connect/disconnect datetimes for date d (sunrise+off, sunset-off)."""
        sunrise, sunset = sun_times(d, self.lat, self.lon, self.tz)
        on_dt = sunrise + timedelta(minutes=self.sunrise_offset) if sunrise else None
        off_dt = sunset - timedelta(minutes=self.sunset_offset) if sunset else None
        return on_dt, off_dt

    def desired_on(self, now: datetime, *, clock_trusted: bool | None = None) -> bool:
        if self.mode == "on":
            return True
        if self.mode == "off":
            return False
        trusted = clock_is_trusted() if clock_trusted is None else clock_trusted
        if not trusted:
            return True
        on_dt, off_dt = self.window(now.date())
        if on_dt is None or off_dt is None:
            return False
        return on_dt <= now < off_dt

    def apply(self, now: datetime | None = None) -> bool:
        """Drive the relay to the desired state. Returns True if it changed."""
        now = now or datetime.now(self.tz)
        with self._lock:
            want = self.desired_on(now)
            if self._relay_on != want:
                self._write(want)
                return True
            return False

    def set_mode(self, mode: str) -> bool:
        if mode not in VALID_MODES:
            raise ValueError(f"invalid mode: {mode}")
        with self._lock:
            self.mode = mode
            self._save_mode()
        self.apply()
        return True

    # ── Status ────────────────────────────────────────────────────────────────
    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(self.tz)
        sunrise, sunset = sun_times(now.date(), self.lat, self.lon, self.tz)
        on_dt, off_dt = self.window(now.date())

        def hm(dt: datetime | None) -> str | None:
            return dt.strftime("%H:%M") if dt else None

        within = (
            on_dt is not None and off_dt is not None and on_dt <= now < off_dt
        )
        return {
            "enabled": True,
            "relayOn": bool(self._relay_on),
            "mode": self.mode,
            "onTime": hm(on_dt),
            "offTime": hm(off_dt),
            "sunrise": hm(sunrise),
            "sunset": hm(sunset),
            "withinWindow": within,
            "clockTrusted": clock_is_trusted(),
            "simulated": self.simulated,
            "now": now.strftime("%H:%M"),
        }

    def close(self) -> None:
        # Leave the panel disconnected on shutdown (safe default for sporadic light).
        try:
            if self._claimed and self._chip is not None:
                self._write(False)
                lgpio.gpio_free(self._chip, self.pin)
                lgpio.gpiochip_close(self._chip)
        except Exception:
            pass
