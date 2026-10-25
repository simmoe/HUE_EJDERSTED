#!/usr/bin/env python3
"""POC: estimate real Fossibot load when BLE register 20 (outWatts) reads 0.

Run on the garden Pi (BLE range):
  .venv/bin/python backend/fossibot_load_poc.py [MAC]

Uses:
  - reg 59 time-to-empty (if non-zero)
  - SoC slope over ~60 s of polls
  - full register dump for candidates (e.g. reg 21)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import fossibot_ble

BATTERY_WH = 2048.0  # F2400 nameplate


def _estimate_from_tte(soc_percent: float, tte_min: int) -> float | None:
    if tte_min <= 0 or soc_percent <= 0:
        return None
    remaining_wh = (soc_percent / 100.0) * BATTERY_WH
    hours = tte_min / 60.0
    return remaining_wh / hours


def _estimate_from_soc_slope(samples: list[tuple[float, float]]) -> float | None:
    """(monotonic_time, soc_percent) — positive watts = discharge."""
    if len(samples) < 3:
        return None
    t0, s0 = samples[0]
    t1, s1 = samples[-1]
    dt_h = (t1 - t0) / 3600.0
    if dt_h <= 0:
        return None
    dsoc = s1 - s0
    if dsoc >= 0:
        return None  # not discharging over window
    wh_lost = (-dsoc / 100.0) * BATTERY_WH
    return wh_lost / dt_h


async def _full_registers(address: str | None, timeout: float) -> dict[int, int] | None:
    """One status read; return all registers 0..69 from the frame."""
    from bleak import BleakClient

    target = address
    if not target:
        target, _name = await fossibot_ble.discover_station(timeout=12.0)

    buf = bytearray()
    got: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()

    def on_notify(_handle: int, data: bytearray) -> None:
        buf.extend(data)
        if got.done():
            return
        payload = fossibot_ble._status_payload(bytes(buf), 6 + 70 * 2)
        if payload is not None:
            got.set_result(bytes(buf))

    async with BleakClient(target, timeout=timeout) as client:
        write_uuid, notify_uuid = fossibot_ble._pick_chars(client)
        await client.start_notify(notify_uuid, on_notify)
        await client.write_gatt_char(write_uuid, fossibot_ble.STATUS_POLL, response=False)
        try:
            frame = await asyncio.wait_for(got, timeout=timeout)
        finally:
            try:
                await client.stop_notify(notify_uuid)
            except Exception:
                pass
    return fossibot_ble.parse_registers(frame, 70)


async def run_poc(address: str | None, polls: int, interval: float, timeout: float) -> int:
    addr = address or os.getenv("HUB_FOSSIBOT_ADDRESS") or None
    print(f"address={addr or 'scan'} battery_wh={BATTERY_WH:.0f}")

    regs = await _full_registers(addr, timeout)
    if regs:
        interesting = {i: v for i, v in regs.items() if v and i not in (41, 48)}
        print("registers (non-zero, excl 41/48):", " ".join(f"{k}={v}" for k, v in sorted(interesting.items())))
    else:
        print("full register read failed")

    soc_samples: list[tuple[float, float]] = []
    readings: list[fossibot_ble.FossibotStatus] = []
    for i in range(polls):
        try:
            last = await fossibot_ble.read_status(addr, timeout=timeout)
        except Exception as exc:
            print(f"poll {i + 1}/{polls}: BLE failed ({exc}) — hub may hold the link; retry one-shot")
            break
        readings.append(last)
        now = time.time()
        soc_samples.append((now, last.soc_percent))
        pub = last.to_public(online=True)
        r21 = last.raw_registers.get(21)
        print(
            f"poll {i + 1}/{polls}: soc={last.soc_percent:.1f}% "
            f"in={pub['inWatts']}W out={pub['outWatts']}W reg21={r21} solar={pub['solarWatts']}W "
            f"usb={pub['usbOn']} dc={pub['dcOn']} "
            f"tte={last.time_to_empty_min}min ttf={last.time_to_full_min}min"
        )
        if i + 1 < polls:
            await asyncio.sleep(interval)

    if not readings:
        return 1
    last = readings[-1]
    pub = last.to_public(online=True)
    tte_w = _estimate_from_tte(last.soc_percent, last.time_to_empty_min)
    slope_w = _estimate_from_soc_slope(soc_samples)
    net_w = None
    if pub["inWatts"] is not None and pub["outWatts"] is not None:
        net_w = (pub["inWatts"] or 0) - (pub["outWatts"] or 0)

    print("--- estimates ---")
    print(f"BLE outWatts (reg 20): {pub['outWatts']} W")
    r21 = last.raw_registers.get(21)
    if r21:
        print(f"reg 21 (output candidate): {r21} W?")
    if tte_w is not None:
        print(f"from time-to-empty ({last.time_to_empty_min} min @ {last.soc_percent:.1f}%): ~{tte_w:.0f} W discharge")
    else:
        print("from time-to-empty: n/a (reg 59 is 0)")
    if slope_w is not None:
        dt = soc_samples[-1][0] - soc_samples[0][0]
        ds = soc_samples[-1][1] - soc_samples[0][1]
        print(f"from SoC slope ({dt:.0f}s, {ds:+.2f} pp): ~{slope_w:.0f} W discharge")
    else:
        print("from SoC slope: n/a (need falling SoC over window)")
    if net_w is not None:
        print(f"net in-out (reported): {net_w} W  (misleading if out stuck at 0)")

    candidates = [w for w in (slope_w, tte_w, float(r21) if r21 else None) if w is not None and w > 0]
    if candidates:
        print(f"POC load estimates: {', '.join(f'{w:.0f} W' for w in candidates)}")
        print(f"POC best guess (median of estimates): ~{sorted(candidates)[len(candidates) // 2]:.0f} W")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate Fossibot load when outWatts reads 0")
    parser.add_argument("address", nargs="?", help="BLE MAC (default HUB_FOSSIBOT_ADDRESS)")
    parser.add_argument("--polls", type=int, default=7, help="status polls (default 7)")
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between polls")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    return asyncio.run(run_poc(args.address, args.polls, args.interval, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
