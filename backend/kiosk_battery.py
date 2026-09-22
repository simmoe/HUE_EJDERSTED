"""Read the garden Android kiosk battery over ADB.

Dumpsys text is parsed here so Firestore samples can carry
``kioskBatteryPercent`` / ``kioskCharging`` without the poll loop knowing
Samsung's dump format.
"""

from __future__ import annotations

import subprocess
from typing import Any


def parse_dumpsys(text: str) -> dict[str, Any] | None:
    level: int | None = None
    ac = False
    usb = False
    wireless = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("level:"):
            try:
                level = int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
        elif line.startswith("AC powered:"):
            ac = line.split(":", 1)[1].strip().lower() == "true"
        elif line.startswith("USB powered:"):
            usb = line.split(":", 1)[1].strip().lower() == "true"
        elif line.startswith("Wireless powered:"):
            wireless = line.split(":", 1)[1].strip().lower() == "true"
    if level is None:
        return None
    return {"percent": max(0, min(100, level)), "charging": bool(ac or usb or wireless)}


def read_via_adb(serial: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "battery"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return parse_dumpsys(proc.stdout or "")
