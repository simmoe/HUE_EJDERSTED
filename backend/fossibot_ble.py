"""Read-only Fossibot / BrightEMS BLE client (garden PoC).

Polls the F2400 for main battery SoC. Never writes a register — firmware does
not validate writes, and register 68 = 0 has bricked units in the field.

Protocol: community reverse-engineering of the Sydpower / BrightEMS GATT
(service a002, write c304, notify c305). Status poll is a fixed 8-byte frame.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

STATUS_POLL = bytes.fromhex("110400000050a6f2")
SOC_REGISTER = 56
# Highest register we print from a status dump (time-to-empty).
LAST_STATUS_REGISTER = 59
MIN_FRAME_FOR_SOC = 6 + (SOC_REGISTER + 1) * 2
MIN_FRAME_FOR_STATUS = 6 + (LAST_STATUS_REGISTER + 1) * 2

SERVICE_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000c304-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000c305-0000-1000-8000-00805f9b34fb"

# Older / alternate GATT map seen in some notes. Tried only if a002 is missing.
LEGACY_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
LEGACY_WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
LEGACY_NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"

NAME_PREFIXES = ("POWER", "FOSSIBOT", "AFERIY", "SYDPOWER")


@dataclass(frozen=True)
class FossibotStatus:
    address: str
    name: str
    soc_percent: float
    raw_soc: int
    solar_watts: int
    ac_in_watts: int
    total_in_watts: int
    total_out_watts: int
    usb_on: bool
    dc_on: bool
    ac_on: bool
    charging: bool
    time_to_full_min: int
    time_to_empty_min: int
    raw_registers: dict[int, int]

    def to_public(self, *, online: bool = True, error: str | None = None) -> dict[str, Any]:
        return {
            "enabled": True,
            "online": online,
            "socPercent": round(self.soc_percent, 1),
            "solarWatts": self.solar_watts,
            "outWatts": self.total_out_watts,
            "usbOn": self.usb_on,
            "acOn": self.ac_on,
            "charging": self.charging,
            "error": error,
        }


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001) if crc & 1 else (crc >> 1)
    return crc & 0xFFFF


def looks_like_station(name: str | None) -> bool:
    if not name:
        return False
    upper = name.upper()
    return any(upper.startswith(prefix) for prefix in NAME_PREFIXES)


def _status_payload(frame: bytes, min_len: int) -> bytes | None:
    start = frame.find(b"\x11\x04")
    if start < 0:
        return None
    payload = frame[start:]
    if len(payload) < min_len:
        return None
    return payload


def _reg(payload: bytes, index: int) -> int:
    offset = 6 + index * 2
    return int.from_bytes(payload[offset : offset + 2], "big")


def parse_soc(frame: bytes) -> int | None:
    """Return raw register 56 from a 0x1104 status frame, or None."""
    payload = _status_payload(frame, MIN_FRAME_FOR_SOC)
    if payload is None:
        return None
    return _reg(payload, SOC_REGISTER)


def parse_registers(frame: bytes, count: int = 70) -> dict[int, int] | None:
    payload = _status_payload(frame, 6 + count * 2)
    if payload is None:
        return None
    return {index: _reg(payload, index) for index in range(count)}


def parse_status(frame: bytes, address: str = "", name: str = "") -> FossibotStatus | None:
    payload = _status_payload(frame, MIN_FRAME_FOR_STATUS)
    if payload is None:
        return None
    raw_soc = _reg(payload, SOC_REGISTER)
    flags = _reg(payload, 48)
    caps = _reg(payload, 41)
    raw = {index: _reg(payload, index) for index in (3, 4, 6, 20, 21, 24, 25, 26, 27, 41, 48, 56, 58, 59)}
    return FossibotStatus(
        address=address,
        name=name or address,
        soc_percent=raw_soc / 10.0,
        raw_soc=raw_soc,
        solar_watts=_reg(payload, 4),
        ac_in_watts=_reg(payload, 3),
        total_in_watts=_reg(payload, 6),
        total_out_watts=_reg(payload, 20),
        usb_on=_reg(payload, 24) != 0 or bool(caps & (1 << 9)),
        dc_on=_reg(payload, 25) != 0 or bool(caps & (1 << 10)),
        ac_on=_reg(payload, 26) != 0 or bool(caps & (1 << 11)),
        charging=bool(flags & 0x8000) or _reg(payload, 4) > 0 or _reg(payload, 6) > 0,
        time_to_full_min=_reg(payload, 58),
        time_to_empty_min=_reg(payload, 59),
        raw_registers=raw,
    )


def idle_status() -> dict[str, Any]:
    return {
        "enabled": True,
        "online": False,
        "socPercent": None,
        "solarWatts": None,
        "outWatts": None,
        "usbOn": False,
        "acOn": False,
        "charging": False,
        "error": None,
    }


class FossibotMonitor:
    """Caches the last successful read-only poll. Never writes."""

    def __init__(self, address: str, poll_sec: float = 20.0) -> None:
        self.address = address
        self.poll_sec = poll_sec
        self._lock = threading.Lock()
        self._last = idle_status()
        self._last_poll = 0.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last)

    def poll_once(self) -> dict[str, Any]:
        try:
            result = asyncio.run(read_status(self.address, timeout=20.0))
            public = result.to_public(online=True)
        except Exception as exc:
            with self._lock:
                public = {**self._last, "online": False, "error": str(exc)}
        with self._lock:
            self._last = public
            self._last_poll = time.monotonic()
        return dict(public)


def _pick_chars(client) -> tuple[str, str]:
    services = getattr(client, "services", None)
    if services is None:
        return WRITE_UUID, NOTIFY_UUID
    uuids = {str(s.uuid).lower() for s in services}
    if SERVICE_UUID in uuids:
        return WRITE_UUID, NOTIFY_UUID
    if LEGACY_SERVICE_UUID in uuids:
        return LEGACY_WRITE_UUID, LEGACY_NOTIFY_UUID
    return WRITE_UUID, NOTIFY_UUID


async def discover_station(timeout: float = 12.0) -> tuple[str, str]:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout)
    matches = [
        d
        for d in devices
        if looks_like_station(d.name) or looks_like_station(getattr(d, "local_name", None))
    ]
    if not matches:
        seen = ", ".join(sorted({(d.name or d.address) for d in devices})) or "(none)"
        raise RuntimeError(f"No Fossibot-like BLE name found. Seen: {seen}")
    device = matches[0]
    return device.address, device.name or device.address


async def read_status(
    address: str | None = None,
    timeout: float = 20.0,
    scan_timeout: float = 12.0,
) -> FossibotStatus:
    from bleak import BleakClient

    name = address or ""
    if not address:
        address, name = await discover_station(timeout=scan_timeout)
    elif looks_like_station(address):
        found_address, name = await discover_station(timeout=scan_timeout)
        address = found_address

    got: asyncio.Future[FossibotStatus] = asyncio.get_running_loop().create_future()
    buf = bytearray()

    def on_notify(_handle: int, data: bytearray) -> None:
        if got.done():
            return
        buf.extend(data)
        status = parse_status(bytes(buf), address=address, name=name)
        if status is not None:
            got.set_result(status)

    async with BleakClient(address, timeout=timeout) as client:
        write_uuid, notify_uuid = _pick_chars(client)
        await client.start_notify(notify_uuid, on_notify)
        await client.write_gatt_char(write_uuid, STATUS_POLL, response=False)
        try:
            return await asyncio.wait_for(got, timeout=timeout)
        finally:
            try:
                await client.stop_notify(notify_uuid)
            except Exception:
                pass


async def read_soc(
    address: str | None = None,
    timeout: float = 20.0,
    scan_timeout: float = 12.0,
) -> FossibotStatus:
    return await read_status(address, timeout=timeout, scan_timeout=scan_timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read Fossibot battery SoC over BLE (read-only).")
    parser.add_argument("address", nargs="?", help="BLE MAC, or omit to scan for POWER/FOSSIBOT")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--raw", action="store_true", help="print key raw status registers")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(read_status(args.address, timeout=args.timeout))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{result.name} {result.address}")
    print(f"soc {result.soc_percent:.1f}% (reg56={result.raw_soc})")
    print(f"solar {result.solar_watts} W  ac_in {result.ac_in_watts} W  in {result.total_in_watts} W  out {result.total_out_watts} W")
    print(
        f"usb {'on' if result.usb_on else 'off'}  "
        f"dc {'on' if result.dc_on else 'off'}  "
        f"ac {'on' if result.ac_on else 'off'}  "
        f"{'charging' if result.charging else 'not-charging'}"
    )
    print(f"ttf {result.time_to_full_min} min  tte {result.time_to_empty_min} min")
    if args.raw:
        caps = result.raw_registers.get(41, 0)
        print(
            "raw "
            + " ".join(f"{index}={value}" for index, value in result.raw_registers.items())
        )
        print(
            f"caps usb={bool(caps & (1 << 9))} dc={bool(caps & (1 << 10))} "
            f"ac={bool(caps & (1 << 11))} led={bool(caps & (1 << 12))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
