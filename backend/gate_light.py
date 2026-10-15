"""Garden gate window: cycle in Firestore, on/off from TÆND·AUTO·SLUK.

MQTT `have_laage` carries the live command (1–3, or 0 for off). Scene 4 is
gone — sluk is state. Cycle is never invented at boot; only a kiosk tap
writes it. Presence is MQTT last-will plus a retained heartbeat on
`have_laage/status`.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import solar

BROKER = "mqtt.nextservices.dk"
PORT = 1883
TOPIC = "have_laage"
STATUS_TOPIC = "have_laage/status"
DOC = "ejdersted/gate_garden"
SCENES = 3
CYCLES = frozenset({1, 2, 3})
VALID_MODES = ("on", "auto", "off")
OFF_HOUR = 2
STALE_S = 75.0
GATE_IP = "192.168.8.146"

PublishFn = Callable[[int], None]
PersistFn = Callable[[dict[str, Any]], None]


def next_scene(current: int, scenes: int = SCENES) -> int:
    try:
        n = int(current)
    except (TypeError, ValueError):
        n = 0
    if scenes < 1:
        return 1
    if n < 0:
        n = 0
    return (n % scenes) + 1


def parse_cycle(raw: Any) -> int | None:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n in CYCLES else None


def parse_mode(raw: Any) -> str | None:
    mode = str(raw or "").strip().lower()
    return mode if mode in VALID_MODES else None


def auto_should_be_on(
    now: datetime,
    sunset_today: datetime | None,
    sunset_yesterday: datetime | None,
) -> bool:
    """On from sunset until 02:00. Off from 02:00 until the next sunset."""
    two_am = now.replace(hour=OFF_HOUR, minute=0, second=0, microsecond=0)
    if now < two_am:
        return sunset_yesterday is not None and now >= sunset_yesterday
    return sunset_today is not None and now >= sunset_today


def desired_on(
    mode: str,
    now: datetime,
    sunset_today: datetime | None,
    sunset_yesterday: datetime | None,
    *,
    clock_trusted: bool = True,
) -> bool:
    if mode == "on":
        return True
    if mode == "off":
        return False
    if not clock_trusted:
        return False
    return auto_should_be_on(now, sunset_today, sunset_yesterday)


def command_for(want_on: bool, cycle: int | None) -> int:
    if want_on and cycle in CYCLES:
        return int(cycle)
    return 0


def desired_command(
    mode: str,
    cycle: int | None,
    now: datetime,
    sunset_today: datetime | None,
    sunset_yesterday: datetime | None,
    *,
    clock_trusted: bool = True,
) -> int:
    """MQTT payload. 0 = off. Never invents a cycle."""
    want = desired_on(
        mode,
        now,
        sunset_today,
        sunset_yesterday,
        clock_trusted=clock_trusted,
    )
    return command_for(want, cycle)


def _packet(header: int, body: bytes) -> bytes:
    n = len(body)
    if n < 128:
        return bytes([header, n]) + body
    return bytes([header, 0x80 | (n & 0x7F), n >> 7]) + body


def mqtt_publish(
    scene: int,
    broker: str = BROKER,
    port: int = PORT,
    topic: str = TOPIC,
    *,
    retain: bool = True,
) -> None:
    """Publish one command (0 off, or cycle 1–3). Raises OSError on refusal."""
    number = int(scene)
    if number < 0 or number > SCENES:
        raise ValueError("scene")
    client = b"hue-laage"
    connect = b"\x00\x04MQTT\x04\x02\x00\x0f" + len(client).to_bytes(2, "big") + client
    topic_b = topic.encode()
    payload = str(number).encode()
    flags = 0x31 if retain else 0x30
    publish = len(topic_b).to_bytes(2, "big") + topic_b + payload
    sock = socket.create_connection((broker, port), timeout=4)
    try:
        sock.settimeout(4)
        sock.sendall(_packet(0x10, connect))
        ack = sock.recv(4)
        if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
            raise OSError("mqtt refused the connection")
        sock.sendall(_packet(flags, publish))
        sock.sendall(b"\xe0\x00")
    finally:
        sock.close()


def parse_status_payload(raw: bytes | str) -> bool | None:
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    word = text.strip().lower()
    if word in {"online", "1", "up"}:
        return True
    if word in {"offline", "0", "down"}:
        return False
    return None


class GateController:
    def __init__(
        self,
        *,
        lat: float = 55.6761,
        lon: float = 12.5683,
        tz: str | ZoneInfo = "Europe/Copenhagen",
        publish: PublishFn | None = None,
        persist: PersistFn | None = None,
        clock_trusted: Callable[[], bool] | None = None,
    ):
        self.lat = float(lat)
        self.lon = float(lon)
        self.tz = tz if isinstance(tz, ZoneInfo) else ZoneInfo(str(tz))
        self._publish = publish or (lambda scene: mqtt_publish(scene))
        self._persist = persist
        self._clock_trusted = clock_trusted or solar.clock_is_trusted
        self.mode = "auto"
        self.cycle: int | None = None
        self.on = False
        self.online = False
        self.last_status_at = 0.0
        self._last_command: int | None = None
        self._lock = threading.Lock()

    def hydrate(self, doc: dict[str, Any] | None) -> None:
        """Load mode/cycle from Firestore. Does not write, does not invent a cycle."""
        data = doc if isinstance(doc, dict) else {}
        mode = parse_mode(data.get("mode"))
        cycle = parse_cycle(data.get("cycle"))
        with self._lock:
            if mode:
                self.mode = mode
            self.cycle = cycle
            if "online" in data:
                self.online = bool(data.get("online"))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            cycle = self.cycle
            online = self.online
            on = self.on
            mode = self.mode
        return {
            "scene": cycle or 0,
            "scenes": SCENES,
            "mode": mode,
            "on": on,
            "online": online,
            "status": "" if online else "offline",
            "ip": GATE_IP,
        }

    def sunsets(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        today = now.date()
        _, sunset_today = solar.sun_times(today, self.lat, self.lon, self.tz)
        _, sunset_yesterday = solar.sun_times(
            today - timedelta(days=1), self.lat, self.lon, self.tz
        )
        return sunset_today, sunset_yesterday

    def set_mode(self, mode: str, now: datetime | None = None) -> dict[str, Any]:
        parsed = parse_mode(mode)
        if not parsed:
            raise ValueError("mode")
        with self._lock:
            self.mode = parsed
        self._write({"mode": parsed})
        self.apply(now)
        return self.snapshot()

    def set_cycle(self, cycle: int, now: datetime | None = None) -> dict[str, Any]:
        parsed = parse_cycle(cycle)
        if parsed is None:
            raise ValueError("cycle")
        with self._lock:
            self.cycle = parsed
        self._write({"cycle": parsed})
        self.apply(now)
        return self.snapshot()

    def next_cycle(self, now: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            current = self.cycle or 0
        return self.set_cycle(next_scene(current), now)

    def set_online(self, online: bool, *, at: float | None = None) -> bool:
        stamp = at if at is not None else time.monotonic()
        with self._lock:
            changed = self.online != bool(online)
            self.online = bool(online)
            self.last_status_at = stamp
        if changed:
            self._write({"online": bool(online)})
        return changed

    def note_status(self, payload: bytes | str, *, at: float | None = None) -> bool | None:
        online = parse_status_payload(payload)
        if online is None:
            return None
        self.set_online(online, at=at)
        return online

    def mark_stale_if_needed(self, *, now: float | None = None, stale_s: float = STALE_S) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            if not self.online or not self.last_status_at:
                return False
            if now - self.last_status_at < stale_s:
                return False
            self.online = False
        self._write({"online": False})
        return True

    def apply(self, now: datetime | None = None, *, force: bool = False) -> bool:
        now = now or datetime.now(self.tz)
        sunset_today, sunset_yesterday = self.sunsets(now)
        with self._lock:
            cmd = desired_command(
                self.mode,
                self.cycle,
                now,
                sunset_today,
                sunset_yesterday,
                clock_trusted=self._clock_trusted(),
            )
            if not force and cmd == self._last_command:
                self.on = cmd in CYCLES
                return False
        try:
            self._publish(cmd)
        except OSError:
            return False
        with self._lock:
            self._last_command = cmd
            self.on = cmd in CYCLES
        self._write({"on": cmd in CYCLES})
        return True

    def _write(self, fields: dict[str, Any]) -> None:
        if self._persist is None:
            return
        try:
            self._persist(fields)
        except Exception as exc:
            print(f"[gate] persist failed: {exc}", flush=True)


def start_status_watch(
    on_payload: Callable[[bytes], None],
    *,
    broker: str = BROKER,
    port: int = PORT,
    topic: str = STATUS_TOPIC,
    stop: threading.Event | None = None,
) -> threading.Thread:
    """Daemon thread: subscribe to the retained status topic."""

    halt = stop or threading.Event()

    def loop() -> None:
        while not halt.is_set():
            sock = None
            try:
                sock = _open_watch(broker, port, topic)
                _drain_watch(sock, on_payload, halt)
            except Exception as exc:
                print(f"[gate] mqtt watch: {exc}", flush=True)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
            halt.wait(5)

    thread = threading.Thread(target=loop, name="gate-mqtt", daemon=True)
    thread.start()
    return thread


def _open_watch(broker: str, port: int, topic: str) -> socket.socket:
    client = b"hue-laage-watch"
    connect = b"\x00\x04MQTT\x04\x02\x00\x1e" + len(client).to_bytes(2, "big") + client
    sock = socket.create_connection((broker, port), timeout=8)
    sock.settimeout(8)
    sock.sendall(_packet(0x10, connect))
    ack = sock.recv(4)
    if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
        raise OSError("mqtt refused the watch")
    topic_b = topic.encode()
    subscribe = b"\x00\x01" + len(topic_b).to_bytes(2, "big") + topic_b + b"\x00"
    sock.sendall(_packet(0x82, subscribe))
    sock.settimeout(45)
    return sock


def _read_remaining(sock: socket.socket) -> int:
    multiplier = 1
    value = 0
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise OSError("mqtt closed")
        value += (chunk[0] & 0x7F) * multiplier
        if chunk[0] & 0x80 == 0:
            return value
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise OSError("mqtt remaining length")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("mqtt closed")
        buf += chunk
    return buf


def _drain_watch(
    sock: socket.socket,
    on_payload: Callable[[bytes], None],
    halt: threading.Event,
) -> None:
    last_ping = time.monotonic()
    while not halt.is_set():
        try:
            header = sock.recv(1)
        except TimeoutError:
            if time.monotonic() - last_ping > 20:
                sock.sendall(b"\xc0\x00")
                last_ping = time.monotonic()
            continue
        if not header:
            raise OSError("mqtt closed")
        kind = header[0] & 0xF0
        remaining = _read_remaining(sock)
        body = _recv_exact(sock, remaining) if remaining else b""
        if kind == 0x30:
            if len(body) < 2:
                continue
            tlen = int.from_bytes(body[:2], "big")
            payload = body[2 + tlen :]
            on_payload(payload)
        elif kind == 0xD0:
            last_ping = time.monotonic()
        if time.monotonic() - last_ping > 20:
            sock.sendall(b"\xc0\x00")
            last_ping = time.monotonic()


def encode_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    return {"stringValue": str(value)}


def decode_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "nullValue" in value:
        return None
    return None


def decode_document(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {key: decode_value(value) for key, value in fields.items()}


def _rest_target(firebase_config: dict[str, Any]) -> tuple[str, dict[str, str], list[tuple[str, str]]] | None:
    project_id = str(firebase_config.get("projectId") or os.getenv("FIREBASE_PROJECT_ID") or "")
    if not project_id:
        return None
    api_key = str(firebase_config.get("apiKey") or os.getenv("FIREBASE_API_KEY") or "")
    token = os.getenv("FIREBASE_FIRESTORE_BEARER_TOKEN") or os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or ""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: list[tuple[str, str]] = []
    if api_key:
        params.append(("key", api_key))
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
        f"/documents/{DOC}"
    )
    return url, headers, params


async def fetch_doc(*, firebase_config: dict[str, Any], http_client: Any) -> dict[str, Any]:
    target = _rest_target(firebase_config)
    if not target or http_client is None:
        return {}
    url, headers, params = target
    try:
        response = await http_client.get(url, params=params, headers=headers, timeout=8)
    except Exception as exc:
        print(f"[gate] firestore read failed: {exc}", flush=True)
        return {}
    if response.status_code >= 400:
        print(f"[gate] firestore read HTTP {response.status_code}", flush=True)
        return {}
    try:
        payload = response.json()
    except Exception:
        return {}
    return decode_document(payload if isinstance(payload, dict) else {})


async def persist_fields(
    fields: dict[str, Any],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
) -> bool:
    target = _rest_target(firebase_config)
    if not target or http_client is None or not fields:
        return False
    url, headers, params = target
    query = list(params)
    for path in fields:
        query.append(("updateMask.fieldPaths", path))
    body = {"fields": {key: encode_value(value) for key, value in fields.items()}}
    try:
        response = await http_client.patch(
            url,
            params=query,
            headers=headers,
            json=body,
            timeout=8,
        )
    except Exception as exc:
        print(f"[gate] firestore write failed: {exc}", flush=True)
        return False
    if response.status_code >= 400:
        print(f"[gate] firestore write HTTP {response.status_code}", flush=True)
        return False
    return True
