"""Philips Hue bridge — mDNS-opdagelse, pairing og gruppe-styring."""

import asyncio
import json
import socket
from pathlib import Path

import httpx
from zeroconf import ServiceBrowser, Zeroconf

CONFIG_FILE = Path(__file__).parent.parent / "hue_config.json"


def _load() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _save(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


class HueBridge:
    def __init__(self):
        self._http = httpx.AsyncClient(verify=False)
        self._cfg = _load()

    @property
    def ip(self) -> str | None:
        return self._cfg.get("ip")

    @property
    def username(self) -> str | None:
        return self._cfg.get("username")

    @property
    def paired(self) -> bool:
        return bool(self.ip and self.username)

    def set_ip(self, ip: str) -> None:
        self._cfg["ip"] = ip
        _save(self._cfg)

    def status(self) -> dict:
        return {"ip": self.ip, "paired": self.paired}

    async def pair(self) -> dict:
        """Brugeren trykker på fysisk knap på bridge, kalder derefter dette."""
        if not self.ip:
            return {"ok": False, "error": "Ingen bridge fundet endnu"}
        try:
            r = await self._http.post(
                f"https://{self.ip}/api",
                json={"devicetype": "hue_ejdersted#app"},
                timeout=8,
            )
            data = r.json()
            if not data:
                return {"ok": False, "error": "Tomt svar fra bridge"}
            if "success" in data[0]:
                self._cfg["username"] = data[0]["success"]["username"]
                _save(self._cfg)
                return {"ok": True}
            if "error" in data[0]:
                desc = data[0]["error"].get("description", "Ukendt fejl")
                return {"ok": False, "error": desc}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "Uventet svar"}

    async def get_rooms(self) -> list[dict]:
        if not self.paired:
            return []
        try:
            r = await self._http.get(
                f"https://{self.ip}/api/{self.username}/groups",
                timeout=3,
            )
            r.raise_for_status()
            rooms = []
            for gid, g in r.json().items():
                if g.get("type") not in ("Room", "Zone"):
                    continue
                action = g.get("action", {})
                state = g.get("state", {})
                bri = action.get("bri", 0)
                rooms.append({
                    "id": gid,
                    "name": g.get("name", f"Rum {gid}"),
                    "brightness": round(bri / 254 * 100),
                    "on": action.get("on", False),
                    "any_on": state.get("any_on", False),
                })
            def _sort_key(r):
                n = r["name"].lower()
                if n == "stue":               return (0, n)
                if "soveværelse" in n:        return (1, n)
                return (2, n)
            return sorted(rooms, key=_sort_key)
        except Exception:
            return []

    async def set_brightness(self, group_id: str, brightness: int) -> bool:
        """brightness 0–100"""
        if not self.paired:
            return False
        bri_hue = round(brightness / 100 * 254)
        payload = (
            {"on": True, "bri": max(1, bri_hue)}
            if brightness > 0
            else {"on": False}
        )
        try:
            r = await self._http.put(
                f"https://{self.ip}/api/{self.username}/groups/{group_id}/action",
                json=payload,
                timeout=3,
            )
            return r.status_code == 200
        except Exception:
            return False


class HueMdnsListener:
    def __init__(
        self,
        bridge: HueBridge,
        loop: asyncio.AbstractEventLoop,
        on_found=None,
    ):
        self._bridge = bridge
        self._loop = loop
        self._on_found = on_found

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        ip = socket.inet_ntoa(info.addresses[0])
        if self._bridge.ip != ip:
            self._bridge.set_ip(ip)
            if self._on_found:
                asyncio.run_coroutine_threadsafe(
                    self._on_found(ip), self._loop
                )

    def remove_service(self, zc, type_, name) -> None:
        pass

    def update_service(self, zc, type_, name) -> None:
        self.add_service(zc, type_, name)


def start_hue_mdns(
    bridge: HueBridge,
    loop: asyncio.AbstractEventLoop,
    zc: Zeroconf,
    on_found=None,
) -> ServiceBrowser:
    listener = HueMdnsListener(bridge, loop, on_found)
    return ServiceBrowser(zc, "_hue._tcp.local.", listener)
