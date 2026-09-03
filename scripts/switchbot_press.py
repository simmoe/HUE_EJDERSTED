#!/usr/bin/env python3
"""Scan for a SwitchBot Bot and press it from the garden Pi.

Usage (on the Pi, Bot within a few metres, SwitchBot app closed):

    .venv/bin/python scripts/switchbot_press.py
    .venv/bin/python scripts/switchbot_press.py --scan
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import switchbot_bot  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Press the garden SwitchBot Bot over BLE")
    parser.add_argument("--scan", action="store_true", help="only scan, do not press")
    parser.add_argument("--mac", default="", help="Bot MAC, otherwise scan/remembered")
    args = parser.parse_args()

    ctrl = switchbot_bot.SwitchbotController(mac=args.mac)
    if args.scan:
        bots = await ctrl.scan()
        if not bots:
            print("Ingen SwitchBot fundet. Luk appen på telefonen og hold Bot'en tæt på Pi'en.")
            return 1
        for bot in bots:
            print(f"{bot.mac}  {bot.name}  rssi={bot.rssi}")
        return 0

    try:
        status = await ctrl.press()
    except Exception as exc:
        print(f"FEJL: {exc}")
        return 1
    print(f"OK  mac={status.get('mac')}  at={status.get('lastPressAt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
