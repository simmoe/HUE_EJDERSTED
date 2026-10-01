"""Tell the gate window which scene to show. Topic have_laage, payload 1..SCENES."""

from __future__ import annotations

import socket

BROKER = "mqtt.nextservices.dk"
PORT = 1883
TOPIC = "have_laage"
SCENES = 3


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
