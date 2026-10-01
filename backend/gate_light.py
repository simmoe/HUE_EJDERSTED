"""Tell the gate window which scene to show. Topic have_laage, payload 1..SCENES."""

from __future__ import annotations

import socket
import time
import urllib.request

BROKER = "mqtt.nextservices.dk"
PORT = 1883
TOPIC = "have_laage"
SCENES = 4
GATE_HOST = "192.168.8.146"
GATE_PORT = 9011


def next_scene(current: int, scenes: int = SCENES) -> int:
    try:
        n = int(current)
    except (TypeError, ValueError):
        n = 0
    if scenes < 1:
        return 1
    return (n % scenes) + 1


def _packet(header: int, body: bytes) -> bytes:
    n = len(body)
    if n < 128:
        return bytes([header, n]) + body
    return bytes([header, 0x80 | (n & 0x7F), n >> 7]) + body


class GateDown(OSError):
    """The M5 did not answer, so it has no address to send to."""


class GateMqtt(OSError):
    """The broker or the M5 did not take the scene."""


def parse_status(text: str) -> tuple[str, int, bool]:
    bits = text.split()
    if len(bits) < 6 or bits[0] != "laage" or bits[2] != "mode" or bits[4] != "mqtt":
        raise GateDown("status")
    ip = bits[1]
    if not ip or ip == "0.0.0.0":
        raise GateDown("ip")
    return ip, int(bits[3]), bits[5].startswith("1")


def read_gate(host: str = GATE_HOST, port: int = GATE_PORT, timeout: float = 1.5) -> tuple[str, int, bool]:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=timeout) as res:
            text = res.read().decode()
    except OSError as exc:
        raise GateDown("down") from exc
    return parse_status(text)


def commit_scene(scene: int) -> dict:
    """Publish only after the M5 has an address, and return once it shows the scene."""
    read_gate()
    try:
        mqtt_publish(scene)
    except OSError as exc:
        raise GateMqtt("handshake") from exc
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        ip, mode, mqtt = read_gate()
        if mqtt and mode == int(scene):
            return {"ip": ip, "scene": mode, "mqtt": True, "status": ""}
        time.sleep(0.3)
    raise GateMqtt("unconfirmed")


def mqtt_publish(scene: int, broker: str = BROKER, port: int = PORT, topic: str = TOPIC) -> None:
    """Publish one scene number. Raises OSError if the broker does not accept it."""
    number = int(scene)
    if number < 1 or number > SCENES:
        raise ValueError("scene")
    client = b"hue-laage"
    connect = b"\x00\x04MQTT\x04\x02\x00\x0f" + len(client).to_bytes(2, "big") + client
    topic_b = topic.encode()
    payload = str(number).encode()
    publish = len(topic_b).to_bytes(2, "big") + topic_b + payload
    sock = socket.create_connection((broker, port), timeout=4)
    try:
        sock.settimeout(4)
        sock.sendall(_packet(0x10, connect))
        ack = sock.recv(4)
        if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
            raise OSError("mqtt refused the connection")
        sock.sendall(_packet(0x30, publish))
        sock.sendall(b"\xe0\x00")
    finally:
        sock.close()
