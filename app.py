"""
B&O Volume Controller
Styrer volumen på Bang & Olufsen Mozart-platform højttalere (A9, M5, m.fl.)
via deres lokale REST API på port 8080.
"""

import json
import os
import socket
import threading
from flask import Flask, render_template, jsonify, request
import requests
from zeroconf import ServiceBrowser, Zeroconf

app = Flask(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "devices.json")

# mDNS service types B&O Mozart-enheder annoncerer sig som
BEO_SERVICE_TYPES = ["_beoremote._tcp.local.", "_hap._tcp.local."]


# ─── Enhedsstyring ────────────────────────────────────────────────────────────

def load_devices() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_devices(devices: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(devices, f, indent=2)


devices: dict = load_devices()
devices_lock = threading.Lock()


def device_id_from_ip(ip: str) -> str:
    return ip.replace(".", "_")


# ─── mDNS auto-opdagelse ──────────────────────────────────────────────────────

class BeoListener:
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        ip = socket.inet_ntoa(info.addresses[0])
        props = info.properties or {}
        friendly = (
            props.get(b"fn", b"").decode("utf-8", errors="ignore")
            or props.get(b"md", b"").decode("utf-8", errors="ignore")
            or name.split(".")[0]
        )
        dev_id = device_id_from_ip(ip)
        with devices_lock:
            if dev_id not in devices:
                devices[dev_id] = {
                    "id": dev_id,
                    "name": friendly,
                    "ip": ip,
                    "auto_discovered": True,
                }
                save_devices(devices)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)


_listener = BeoListener()
_zeroconf = Zeroconf()
_browsers = [ServiceBrowser(_zeroconf, st, _listener) for st in BEO_SERVICE_TYPES]


# ─── Hjælpefunktioner ─────────────────────────────────────────────────────────

def beo_get_volume(ip: str) -> dict:
    """Henter nuværende volumen fra enheden."""
    resp = requests.get(
        f"http://{ip}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level",
        timeout=3,
    )
    resp.raise_for_status()
    return resp.json()


def beo_set_volume(ip: str, level: int) -> None:
    """Sætter volumen på enheden (0-100)."""
    resp = requests.put(
        f"http://{ip}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level",
        json={"level": level},
        timeout=3,
    )
    resp.raise_for_status()


def resolve_to_ip(host: str) -> str:
    """Konverterer hostname til IP eller validerer eksisterende IP."""
    try:
        socket.inet_aton(host)
        return host  # allerede en IP
    except OSError:
        pass
    return socket.gethostbyname(host)  # kaster socket.gaierror ved fejl


# ─── API-ruter ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/devices", methods=["GET"])
def api_get_devices():
    with devices_lock:
        return jsonify(list(devices.values()))


@app.route("/api/devices", methods=["POST"])
def api_add_device():
    data = request.get_json(force=True) or {}
    host = (data.get("ip") or "").strip()
    name = (data.get("name") or "").strip()
    if not host:
        return jsonify({"error": "IP/hostname er påkrævet"}), 400
    try:
        ip = resolve_to_ip(host)
    except (OSError, socket.gaierror):
        return jsonify({"error": "Ugyldigt IP eller hostname"}), 400

    dev_id = device_id_from_ip(ip)
    device = {
        "id": dev_id,
        "name": name or f"Højttaler ({ip})",
        "ip": ip,
        "auto_discovered": False,
    }
    with devices_lock:
        devices[dev_id] = device
        save_devices(devices)
    return jsonify(device), 201


@app.route("/api/devices/<device_id>", methods=["DELETE"])
def api_delete_device(device_id: str):
    with devices_lock:
        if device_id not in devices:
            return jsonify({"error": "Enhed ikke fundet"}), 404
        del devices[device_id]
        save_devices(devices)
    return jsonify({"success": True})


@app.route("/api/volume/<device_id>", methods=["GET"])
def api_get_volume(device_id: str):
    with devices_lock:
        device = devices.get(device_id)
    if not device:
        return jsonify({"error": "Enhed ikke fundet"}), 404
    try:
        data = beo_get_volume(device["ip"])
        return jsonify({"level": data.get("level", data), "online": True})
    except requests.RequestException as e:
        return jsonify({"error": str(e), "online": False}), 503


@app.route("/api/volume/<device_id>", methods=["PUT"])
def api_set_volume(device_id: str):
    with devices_lock:
        device = devices.get(device_id)
    if not device:
        return jsonify({"error": "Enhed ikke fundet"}), 404
    data = request.get_json(force=True) or {}
    try:
        level = int(data["level"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "level (0-100) er påkrævet"}), 400
    if not 0 <= level <= 100:
        return jsonify({"error": "level skal være mellem 0 og 100"}), 400
    try:
        beo_set_volume(device["ip"], level)
        return jsonify({"success": True, "level": level})
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 503


if __name__ == "__main__":
    print("B&O Volume Controller kører på http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
