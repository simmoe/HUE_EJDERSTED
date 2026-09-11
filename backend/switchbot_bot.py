"""One-shot BLE press for a SwitchBot Bot. The AC watchdog lives in power.py."""

from __future__ import annotations

import argparse
import asyncio
import sys

WRITE_CHAR = "cba20002-224d-11e6-9fb8-0002a5d5c51b"
COMMANDS = {
    "press": bytes.fromhex("570100"),
    "on": bytes.fromhex("570101"),
    "off": bytes.fromhex("570102"),
}
BOT_TYPE = 0x48


def _is_bot(device, adv) -> bool:
    name = (adv.local_name or device.name or "").lower()
    if "wohand" in name or name.startswith("switchbot"):
        return True
    for uuid, data in (adv.service_data or {}).items():
        text = str(uuid).lower()
        if ("fd3d" in text or text.endswith("000d")) and data and (data[0] & 0x7F) == BOT_TYPE:
            return True
    return False


async def scan(seconds: float) -> list[tuple[str, str, int]]:
    from bleak import BleakScanner

    found: dict[str, tuple[str, int]] = {}

    def _on(device, adv):
        if not _is_bot(device, adv):
            return
        found[device.address] = (adv.local_name or device.name or "SwitchBot", adv.rssi)

    scanner = BleakScanner(detection_callback=_on)
    await scanner.start()
    await asyncio.sleep(seconds)
    await scanner.stop()
    return [(addr, name, rssi) for addr, (name, rssi) in found.items()]


async def send_command(address: str, action: str) -> bool:
    from bleak import BleakClient

    payload = COMMANDS.get(action)
    if not payload:
        raise ValueError(f"unknown SwitchBot action: {action}")
    async with BleakClient(address, timeout=20) as client:
        await client.write_gatt_char(WRITE_CHAR, payload, response=True)
    return True


async def press(address: str) -> bool:
    return await send_command(address, "press")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Scan or press a SwitchBot Bot over BLE")
    parser.add_argument("command", choices=("scan", "press", "on", "off"))
    parser.add_argument("--address", default="")
    parser.add_argument("--seconds", type=float, default=12)
    args = parser.parse_args()

    if args.command == "scan":
        bots = await scan(args.seconds)
        if not bots:
            print("Ingen SwitchBot Bot i nærheden")
            return 1
        for addr, name, rssi in bots:
            print(f"{addr}\t{name}\t{rssi} dBm")
        return 0

    address = args.address.strip()
    if not address:
        bots = await scan(args.seconds)
        if len(bots) != 1:
            print("Sig --address, eller sørg for at præcis én Bot advertiser")
            for addr, name, rssi in bots:
                print(f"{addr}\t{name}\t{rssi} dBm")
            return 1
        address = bots[0][0]
        print(f"found {address} {bots[0][1]}")
    action = "press" if args.command == "press" else args.command
    print(f"{action} {address}")
    await send_command(address, action)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
