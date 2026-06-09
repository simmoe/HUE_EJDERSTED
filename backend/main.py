"""
Home Automation Hub — FastAPI backend

• B&O Mozart REST API (volumen)
• Philips Hue bridge (lysstyrke pr. rum)
• WebSocket push til alle tilsluttede klienter
• mDNS auto-opdagelse
• Serverer SvelteKit static build
"""

import asyncio
import errno
import json
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from zeroconf import ServiceBrowser, Zeroconf

import bo_dlna
import bo_link
import sr
import hub_config
from hue import HueBridge, start_hue_mdns
from spotify import Spotify, BEO_A9_IP, BEO_M5_IP

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent
DEVICES_FILE = REPO_ROOT / "devices.json"
STATIC_DIR = BASE_DIR / "static"
HUB_GLOBALS_FILE = REPO_ROOT / "hub_globals.json"
MULTIAPP_PACKAGE = hub_config.multiapp_package()
CAMERA_DIR = REPO_ROOT / "runtime" / "camera"
CAMERA_SNAPSHOT_FILE = CAMERA_DIR / "latest.jpg"
MAX_CAMERA_SNAPSHOT_BYTES = 2_500_000

# ─── HTTP client ──────────────────────────────────────────────────────────────
_http = httpx.AsyncClient(timeout=2.5)

# ─── Device storage ───────────────────────────────────────────────────────────
def load_devices() -> dict:
    if DEVICES_FILE.exists():
        try:
            return json.loads(DEVICES_FILE.read_text())
        except Exception:
            pass
    return {}

def save_devices(devices: dict) -> None:
    DEVICES_FILE.write_text(json.dumps(devices, indent=2))

devices: dict = load_devices()
devices_lock = asyncio.Lock()


def _ensure_known_speakers() -> None:
    """Pre-seed devices for the fixed B&O speakers, so UI is never empty even
    if mDNS is silent at boot. mDNS may overwrite name/IP later when it sees them."""
    if not hub_config.feature_enabled("audio"):
        return
    configured = hub_config.known_speakers()
    known = configured or [
        {"ip": BEO_A9_IP, "name": "BeoPlay A9"},
        {"ip": BEO_M5_IP, "name": "Beoplay M5"},
    ]
    changed = False
    for speaker in known:
        ip = speaker["ip"]
        name = speaker["name"]
        dev_id = ip.replace(".", "_")
        if dev_id not in devices:
            devices[dev_id] = {
                "id": dev_id,
                "name": name,
                "ip": ip,
                "auto_discovered": True,
            }
            changed = True
    if changed:
        save_devices(devices)


_ensure_known_speakers()

# ─── Volume cache ─────────────────────────────────────────────────────────────
volume_cache: dict[str, dict] = {}       # device_id → {level, online}

# ─── Now-playing cache ────────────────────────────────────────────────────────
now_playing_cache: dict[str, dict] = {}  # device_id → {name, artist, album}
_notify_tasks: dict[str, asyncio.Task] = {}

# ─── Hue ───────────────────────────────────────────────────────────────────────
hue_bridge: HueBridge                    # initialised in lifespan
hue_rooms_cache: list[dict] = []
hue_status_cache: dict = {}
# ─── Spotify ───────────────────────────────────────────────────────────────
spotify = Spotify()
# ─── WebSocket connection manager ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        try:
            self._connections.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, msg: dict):
        data = json.dumps(msg)
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = ConnectionManager()

# ─── B&O Mozart API ───────────────────────────────────────────────────────────
async def beo_get_volume(ip: str) -> int:
    r = await _http.get(
        f"http://{ip}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level"
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        return int(data.get("level", 0))
    return int(data)

async def beo_set_volume(ip: str, level: int) -> None:
    r = await _http.put(
        f"http://{ip}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level",
        json={"level": level},
    )
    r.raise_for_status()

# ─── BeoNotify stream ─────────────────────────────────────────────────────────
_stream_http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))

async def beo_notify_listener(dev_id: str, ip: str):
    """Stream BeoNotify/Notifications og broadcast NOW_PLAYING_STORED_MUSIC."""
    url = f"http://{ip}:8080/BeoNotify/Notifications"
    while True:
        try:
            async with _stream_http.stream("GET", url) as r:
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    n = data.get("notification", {})
                    if n.get("type") == "NOW_PLAYING_STORED_MUSIC":
                        nd = n.get("data", {})
                        state = {
                            "name": nd.get("name", ""),
                            "artist": nd.get("artist", ""),
                            "album": nd.get("album", ""),
                        }
                        if now_playing_cache.get(dev_id) != state:
                            now_playing_cache[dev_id] = state
                            await manager.broadcast({
                                "type": "now_playing",
                                "device_id": dev_id,
                                **state,
                            })
                    elif n.get("type") == "NOW_PLAYING_ENDED":
                        if dev_id in now_playing_cache:
                            del now_playing_cache[dev_id]
                            await manager.broadcast({
                                "type": "now_playing",
                                "device_id": dev_id,
                                "name": "", "artist": "", "album": "",
                            })
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(5)

# ─── Background volume polling ────────────────────────────────────────────────
async def poll_loop():
    """Poll B&O og Hue hvert 2. sekund og push ændringer via WebSocket."""
    while True:
        await asyncio.sleep(2)

        # ── B&O ──────────────────────────────────────────────────────────────
        if hub_config.feature_enabled("audio"):
            async with devices_lock:
                devs = list(devices.values())

            for dev in devs:
                dev_id = dev["id"]
                # Start notify stream task if not already running
                task = _notify_tasks.get(dev_id)
                if task is None or task.done():
                    _notify_tasks[dev_id] = asyncio.create_task(
                        beo_notify_listener(dev_id, dev["ip"])
                    )
                try:
                    level = await beo_get_volume(dev["ip"])
                    state = {"level": level, "online": True}
                except Exception:
                    cached_level = volume_cache.get(dev_id, {}).get("level", 0)
                    state = {"level": cached_level, "online": False}

                if volume_cache.get(dev_id) != state:
                    volume_cache[dev_id] = state
                    await manager.broadcast({
                        "type": "volume_update",
                        "device_id": dev_id,
                        **state,
                    })

        # ── Hue ──────────────────────────────────────────────────────────────
        global hue_rooms_cache, hue_status_cache
        if hub_config.feature_enabled("hue"):
            new_status = hue_bridge.status()
            if new_status != hue_status_cache:
                hue_status_cache = new_status
                await manager.broadcast({"type": "hue_status", **new_status})
            if hue_bridge.paired:
                rooms = await hue_bridge.get_rooms()
                if rooms is not None and rooms != hue_rooms_cache:
                    hue_rooms_cache = rooms
                    await manager.broadcast({"type": "hue_rooms", "rooms": rooms})

# ─── mDNS discovery ───────────────────────────────────────────────────────────
def _device_id(ip: str) -> str:
    return ip.replace(".", "_")

class BeoListener:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def _register(self, ip: str, name: str):
        dev_id = _device_id(ip)

        async def _add():
            async with devices_lock:
                if dev_id not in devices:
                    device = {
                        "id": dev_id,
                        "name": name,
                        "ip": ip,
                        "auto_discovered": True,
                    }
                    devices[dev_id] = device
                    save_devices(devices)
                    await manager.broadcast({"type": "device_added", "device": device})

        asyncio.run_coroutine_threadsafe(_add(), self._loop)

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        ip = socket.inet_ntoa(info.addresses[0])
        props = info.properties or {}
        friendly = (
            (props.get(b"fn") or b"").decode("utf-8", errors="ignore")
            or (props.get(b"md") or b"").decode("utf-8", errors="ignore")
            or name.split(".")[0]
        )
        self._register(ip, friendly or f"B&O ({ip})")

    def remove_service(self, zc, type_, name):
        pass

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

# ─── App lifespan ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global hue_bridge
    loop = asyncio.get_event_loop()

    # Force Android kiosk settings on startup (reuses /api/kiosk logic)
    if hub_config.feature_enabled("adbKiosk"):
        serial = await _get_adb_serial()
        if serial:
            print(f"[ADB] Kiosk phone connected: {serial}")
            # Trigger full kiosk lockdown via the endpoint handler
            await trigger_kiosk()

    hue_bridge = HueBridge()
    poll_task = asyncio.create_task(poll_loop())

    zc = Zeroconf()
    if hub_config.feature_enabled("audio"):
        beo_listener = BeoListener(loop)
        ServiceBrowser(zc, "_beoremote._tcp.local.", beo_listener)

    async def on_hue_found(ip: str):
        await manager.broadcast({"type": "hue_status", **hue_bridge.status()})

    if hub_config.feature_enabled("hue"):
        start_hue_mdns(hue_bridge, loop, zc, on_found=on_hue_found)

    yield

    poll_task.cancel()
    for t in _notify_tasks.values():
        t.cancel()
    zc.close()
    await _http.aclose()
    await _stream_http.aclose()

# ─── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)

# ─── WebSocket ────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global hue_rooms_cache
    await manager.connect(ws)
    print(f"[WS] Client connected ({len(manager._connections)} total)")
    try:
        # Send initial state on connect
        async with devices_lock:
            devs = list(devices.values())
        await ws.send_text(json.dumps({
            "type": "init",
            "devices": devs,
            "volumes": volume_cache,
            "hue_status": hue_bridge.status(),
            "hue_rooms": hue_rooms_cache,
            "now_playing": now_playing_cache,
            "config": hub_config.public_config(),
        }))

        async for text in ws.iter_text():
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "ping":
                try:
                    await ws.send_text(json.dumps({"type": "pong", "t": msg.get("t")}))
                except Exception:
                    pass
                continue

            if msg.get("type") == "set_volume":
                if not hub_config.feature_enabled("audio"):
                    continue
                dev_id = str(msg.get("device_id", ""))
                try:
                    level = max(0, min(100, int(msg["level"])))
                except (KeyError, ValueError, TypeError):
                    continue

                async with devices_lock:
                    dev = devices.get(dev_id)

                if dev:
                    try:
                        await beo_set_volume(dev["ip"], level)
                        volume_cache[dev_id] = {"level": level, "online": True}
                        await manager.broadcast({
                            "type": "volume_update",
                            "device_id": dev_id,
                            "level": level,
                            "online": True,
                        })
                    except Exception as e:
                        await ws.send_text(json.dumps({
                            "type": "error",
                            "device_id": dev_id,
                            "message": str(e),
                        }))
            elif msg.get("type") == "set_hue_brightness":
                if not hub_config.feature_enabled("hue"):
                    continue
                room_id = str(msg.get("room_id", ""))
                try:
                    brightness = max(0, min(100, int(msg["brightness"])))
                except (KeyError, ValueError, TypeError):
                    continue
                ok = await hue_bridge.set_brightness(room_id, brightness)
                if ok:
                    hue_rooms_cache = [
                        {**r, "brightness": brightness, "on": brightness > 0}
                        if r["id"] == room_id else r
                        for r in hue_rooms_cache
                    ]
                    await manager.broadcast({
                        "type": "hue_rooms",
                        "rooms": hue_rooms_cache,
                    })
    except WebSocketDisconnect:
        print(f"[WS] Client disconnected ({len(manager._connections)-1} remain)")
        manager.disconnect(ws)
    except Exception as e:
        print(f"[WS] Client error: {e}")
        manager.disconnect(ws)

# ─── REST: device management ──────────────────────────────────────────────────
@app.get("/api/devices")
async def get_devices():
    if not hub_config.feature_enabled("audio"):
        return []
    async with devices_lock:
        return list(devices.values())

@app.post("/api/devices")
async def add_device(data: dict):
    if not hub_config.feature_enabled("audio"):
        return JSONResponse({"error": "Audio er deaktiveret for denne profil"}, status_code=404)
    host = (data.get("ip") or "").strip()
    name = (data.get("name") or "").strip()
    if not host:
        return JSONResponse({"error": "ip er påkrævet"}, status_code=400)
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return JSONResponse({"error": "Ugyldigt IP eller hostname"}, status_code=400)

    dev_id = _device_id(ip)
    device = {
        "id": dev_id,
        "name": name or f"Enhed ({ip})",
        "ip": ip,
        "auto_discovered": False,
    }
    async with devices_lock:
        devices[dev_id] = device
        save_devices(devices)
    await manager.broadcast({"type": "device_added", "device": device})
    return JSONResponse(device, status_code=201)

@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str):
    if not hub_config.feature_enabled("audio"):
        return JSONResponse({"error": "Audio er deaktiveret for denne profil"}, status_code=404)
    async with devices_lock:
        if device_id not in devices:
            return JSONResponse({"error": "Enhed ikke fundet"}, status_code=404)
        del devices[device_id]
        save_devices(devices)
    volume_cache.pop(device_id, None)
    await manager.broadcast({"type": "device_removed", "device_id": device_id})
    return {"success": True}

# ─── REST: Hue bridge ────────────────────────────────────────────────────────
@app.get("/api/hue/status")
async def hue_status():
    if not hub_config.feature_enabled("hue"):
        return {"ip": None, "paired": False, "disabled": True}
    return hue_bridge.status()

@app.post("/api/hue/pair")
async def hue_pair(data: dict = {}):
    if not hub_config.feature_enabled("hue"):
        return {"ok": False, "error": "Hue er deaktiveret for denne profil"}
    # Tillad manuel IP-override
    if ip := (data.get("ip") or "").strip():
        hue_bridge.set_ip(ip)
    result = await hue_bridge.pair()
    if result["ok"]:
        rooms = await hue_bridge.get_rooms()
        global hue_rooms_cache
        hue_rooms_cache = rooms
        await manager.broadcast({"type": "hue_status", **hue_bridge.status()})
        await manager.broadcast({"type": "hue_rooms", "rooms": rooms})
    return result

# ─── ADB constants (kiosk: Samsung Galaxy A12, se KIOSK.md) ───────────────────
KIOSK_PHONE_IP = hub_config.kiosk_phone_ip()
ADB_SERIAL = hub_config.adb_serial()

# ─── REST: Screen brightness (ADB) ───────────────────────────────────────────

async def _get_adb_serial() -> str | None:
    """Return ADB serial for the kiosk phone (Galaxy A12), auto-reconnecting if needed."""
    if not hub_config.feature_enabled("adbKiosk") or not ADB_SERIAL:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "devices",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        for line in out.decode().splitlines():
            if KIOSK_PHONE_IP in line and "device" in line:
                return line.split()[0]
        proc = await asyncio.create_subprocess_exec(
            "adb", "connect", ADB_SERIAL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if b"connected" in out:
            return ADB_SERIAL
    except Exception:
        pass
    return None


@app.put("/api/brightness/{level}")
async def set_brightness(level: int):
    if not hub_config.feature_enabled("adbKiosk"):
        return {"ok": False, "error": "adbKiosk disabled"}
    level = max(0, min(255, level))
    serial = await _get_adb_serial()
    if not serial:
        return {"ok": False, "error": "no ADB device"}
    proc = await asyncio.create_subprocess_exec(
        "adb", "-s", serial, "shell",
        f"settings put system screen_brightness {level}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return {"ok": True, "brightness": level}

@app.post("/api/kiosk")
async def trigger_kiosk():
    if not hub_config.feature_enabled("adbKiosk"):
        return {"ok": False, "error": "adbKiosk disabled"}
    serial = await _get_adb_serial()
    if not serial:
        return {"ok": False, "error": "no ADB device"}
    cmds = [
        # Display & orientation
        f"adb -s {serial} shell settings put system accelerometer_rotation 0",
        f"adb -s {serial} shell settings put system user_rotation 1",
        f"adb -s {serial} shell settings put system screen_brightness_mode 0",
        f"adb -s {serial} shell settings put system screen_brightness 255",
        # Never turn the screen off (int32 max ms ≈ 24.8 days; Android wraps internally)
        f"adb -s {serial} shell settings put system screen_off_timeout 2147483647",
        f"adb -s {serial} shell settings put global policy_control immersive.full=com.android.chrome,{MULTIAPP_PACKAGE}",
        # Skjul toast/overlay fra SystemUI (volume-HUD m.m. — se KIOSK.md §5/§11)
        f"adb -s {serial} shell cmd appops set com.android.systemui SYSTEM_ALERT_WINDOW deny",
        f"adb -s {serial} shell cmd appops set com.android.systemui TOAST_WINDOW deny",
        f"adb -s {serial} shell cmd statusbar collapse",
        # Keep doorphone calls audible, while media stays muted.
        f"adb -s {serial} shell media volume --stream 1 --set 0",
        f"adb -s {serial} shell media volume --stream 2 --set 7",
        f"adb -s {serial} shell media volume --stream 3 --set 0",
        f"adb -s {serial} shell media volume --stream 4 --set 0",
        f"adb -s {serial} shell media volume --stream 5 --set 7",
        f"adb -s {serial} shell settings put global zen_mode 0",
        f"adb -s {serial} shell cmd notification set_dnd off",
        # Prevent updates & restarts
        f"adb -s {serial} shell settings put global stay_on_while_plugged_in 3",
        f"adb -s {serial} shell settings put global heads_up_notifications_enabled 1",
        # Exempt Chrome and MultiApp from Doze / App Standby optimizations.
        f"adb -s {serial} shell dumpsys deviceidle whitelist +com.android.chrome",
        f"adb -s {serial} shell dumpsys deviceidle whitelist +{MULTIAPP_PACKAGE}",
        f"adb -s {serial} shell cmd appops set com.android.chrome RUN_IN_BACKGROUND allow",
        f"adb -s {serial} shell cmd appops set com.android.chrome RUN_ANY_IN_BACKGROUND allow",
        f"adb -s {serial} shell cmd appops set {MULTIAPP_PACKAGE} RUN_IN_BACKGROUND allow",
        f"adb -s {serial} shell cmd appops set {MULTIAPP_PACKAGE} RUN_ANY_IN_BACKGROUND allow",
        f"adb -s {serial} shell cmd appops set {MULTIAPP_PACKAGE} SYSTEM_ALERT_WINDOW allow",
        f"adb -s {serial} shell cmd appops set {MULTIAPP_PACKAGE} WAKE_LOCK allow",
        f"adb -s {serial} shell cmd appops set {MULTIAPP_PACKAGE} RECORD_AUDIO allow",
        f"adb -s {serial} shell cmd appops set {MULTIAPP_PACKAGE} CAMERA allow",
    ]
    for cmd in cmds:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
    return {"ok": True}

# ─── Hub globals (Firebase m.m. — hub_globals.json, ikke i git) ───────────────
@app.get("/api/config")
async def app_config():
    return hub_config.public_config()


@app.get("/api/config/firebase")
async def hub_firebase_config():
    """Returnerer Firebase web client config til frontend (tom objekt hvis fil mangler)."""
    if not HUB_GLOBALS_FILE.is_file():
        return {}
    try:
        blob = json.loads(HUB_GLOBALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    fb = blob.get("firebase")
    return fb if isinstance(fb, dict) else {}


# ─── REST: Spotify ────────────────────────────────────────────────────────────
@app.get("/api/spotify/status")
async def spotify_status():
    if not hub_config.feature_enabled("spotify"):
        return {"configured": False, "authenticated": False, "disabled": True}
    return {"configured": spotify.configured, "authenticated": spotify.authenticated}

@app.get("/api/spotify/login")
async def spotify_login():
    if not hub_config.feature_enabled("spotify"):
        return JSONResponse({"error": "Spotify disabled"}, status_code=404)
    if not spotify.configured:
        return JSONResponse({"error": "Spotify not configured"}, status_code=500)
    return RedirectResponse(spotify.login_url())

@app.get("/api/spotify/callback")
async def spotify_callback(code: str = ""):
    if not hub_config.feature_enabled("spotify"):
        return JSONResponse({"error": "Spotify disabled"}, status_code=404)
    if not code:
        return JSONResponse({"error": "No code"}, status_code=400)
    ok = await spotify.handle_callback(code)
    if ok:
        return RedirectResponse("/")
    return JSONResponse({"error": "Auth failed"}, status_code=401)

@app.post("/api/spotify/voice")
async def spotify_voice(data: dict):
    if not hub_config.feature_enabled("spotify"):
        return JSONResponse({"error": "Spotify disabled"}, status_code=404)
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        return JSONResponse({"error": "No transcript"}, status_code=400)
    result = await spotify.voice_command(transcript)
    return result

@app.get("/api/spotify/now-playing")
async def spotify_now_playing():
    if not hub_config.feature_enabled("spotify"):
        return {}
    return await spotify.now_playing() or {}

@app.get("/api/spotify/devices")
async def spotify_devices():
    if not hub_config.feature_enabled("spotify"):
        return []
    return await spotify.devices()

@app.post("/api/spotify/pause")
async def spotify_pause():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return {"ok": await spotify.pause()}

@app.post("/api/spotify/resume")
async def spotify_resume():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return {"ok": await spotify.resume()}

@app.post("/api/spotify/play-uris")
async def spotify_play_uris(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    uris = data.get("uris") or []
    offset = int(data.get("offset") or 0)
    position_ms = int(data.get("position_ms") or 0)
    ok, detail, duration_ms = await spotify.play_uris_queue(uris, offset, position_ms)
    resp: dict = {"ok": ok}
    if ok and duration_ms:
        resp["duration_ms"] = duration_ms - position_ms
    if not ok and detail:
        resp["detail"] = detail
    return resp

@app.post("/api/spotify/skip")
async def spotify_skip():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return {"ok": await spotify.skip()}


@app.post("/api/spotify/previous")
async def spotify_previous():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return {"ok": await spotify.previous()}

@app.post("/api/spotify/radio/build")
async def spotify_radio_build(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.build_radio_queue(
        (data.get("seed_uri") or "").strip(),
        (data.get("seed_name") or "").strip(),
        (data.get("seed_artist") or "").strip(),
    )

@app.post("/api/spotify/radio")
async def spotify_radio(data: dict = Body(default_factory=dict)):
    """Byg radio-kø (kræver JSON-body med seed_uri)."""
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.build_radio_queue(
        (data.get("seed_uri") or "").strip(),
        (data.get("seed_name") or "").strip(),
        (data.get("seed_artist") or "").strip(),
    )

@app.delete("/api/spotify/radio")
async def spotify_radio_stop():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.stop_radio()

@app.post("/api/spotify/radio/save")
async def spotify_radio_save(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    tracks = data.get("tracks") or []
    if not isinstance(tracks, list):
        tracks = []
    import logging
    logging.warning(f"[radio/save] seed_name={data.get('seed_name')!r} tracks_len={len(tracks)} sample={tracks[:2]!r}")
    return await spotify.save_radio_playlist(
        (data.get("seed_name") or "").strip(),
        (data.get("seed_artist") or "").strip(),
        tracks,
    )

@app.delete("/api/spotify/playlist/{playlist_id}")
async def spotify_delete_playlist(playlist_id: str):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.unfollow_playlist(playlist_id)

@app.delete("/api/spotify/playlist/{playlist_id}/track")
async def spotify_delete_playlist_track(playlist_id: str, data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    position = data.get("position")
    if not isinstance(position, int):
        position = None
    return await spotify.remove_playlist_track(
        playlist_id,
        (data.get("track_uri") or "").strip(),
        position,
    )

@app.post("/api/spotify/album/build")
async def spotify_album_build(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.build_album_queue_from_track_uri((data.get("track_uri") or "").strip())

@app.post("/api/spotify/album")
async def spotify_album(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.build_album_queue_from_track_uri((data.get("track_uri") or "").strip())

@app.post("/api/spotify/save")
async def spotify_save(data: dict | None = Body(default=None)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    uri = (data or {}).get("uri") if data else None
    if isinstance(uri, str):
        uri = uri.strip() or None
    return await spotify.save_track(uri)

@app.get("/api/spotify/is-saved")
async def spotify_is_saved(uri: str | None = None):
    if not hub_config.feature_enabled("spotify"):
        return {"saved": False}
    u = (uri or "").strip() or None
    return {"saved": await spotify.is_track_saved(u)}

@app.get("/api/spotify/playlists")
async def spotify_playlists(limit: int = 50, offset: int = 0):
    if not hub_config.feature_enabled("spotify"):
        return {"items": []}
    return await spotify.list_playlists(limit=limit, offset=offset)

@app.post("/api/spotify/playlist/build")
async def spotify_playlist_build(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    return await spotify.build_playlist_queue((data.get("playlist_uri") or "").strip())

# ─── REST: Podcasts ───────────────────────────────────────────────────────────
# Hardkodet liste. Hver podcast har en `source` der bestemmer afspilningsvej:
#   - "spotify": Spotify Web API + Spotify Connect på M5 (samme rute som musik)
#   - "sr":      Sveriges Radio API → direkte M4A-stream → DLNA AVTransport på M5
# show_id ud mod frontend prefixes med "sr:" for SR-shows så det er entydigt.
PODCAST_SHOWS: list[dict] = [
    {"source": "spotify", "id": "6FVyoDMn4GKxveMegJ2Yih", "fallback_name": "Fodboldlisten"},
    {"source": "spotify", "id": "5d4yba4KbcBTtwZ8glscZZ", "fallback_name": "Det næste kapitel"},
    {"source": "sr",      "id": "4914", "fallback_name": "Text och musik med Eric Schüldt"},
    {"source": "sr",      "id": "2488", "fallback_name": "Rendezvous med Kristjan Saag"},
]
PODCAST_CACHE_TTL = 30 * 60  # 30 min — afsnit udkommer typisk én gang om ugen
_podcast_cache: list[dict] = []
_podcast_cache_at: float = 0.0

# Track senest startet podcast-engine så vi kan dispatche pause/resume korrekt.
_active_podcast_engine: str = ""  # "spotify" | "dlna" | ""


def _show_key(sh: dict) -> str:
    """Stabil show-id ud mod frontend (entydig på tværs af kilder)."""
    if sh["source"] == "sr":
        return f"sr:{sh['id']}"
    return sh["id"]


def _find_show(show_id: str) -> dict | None:
    for sh in PODCAST_SHOWS:
        if _show_key(sh) == show_id:
            return sh
    # Tolerant fallback: rå ID uden prefix
    for sh in PODCAST_SHOWS:
        if sh["id"] == show_id:
            return sh
    return None


async def _build_podcast_list() -> list[dict]:
    out: list[dict] = []
    for sh in PODCAST_SHOWS:
        if sh["source"] == "spotify":
            meta = await spotify.get_show(sh["id"])
            ep = await spotify.get_show_latest_episode(sh["id"])
            if not meta or not ep:
                print(f"[Podcast] skip spotify {sh['id']} — meta={bool(meta)} ep={bool(ep)}")
                continue
            images = meta.get("images") or []
            cover = images[0].get("url", "") if images else ""
            out.append({
                "show_id": _show_key(sh),
                "source": "spotify",
                "show_name": meta.get("name") or sh["fallback_name"],
                "show_image": cover,
                "episode_id": ep.get("id"),
                "episode_uri": ep.get("uri"),
                "episode_name": ep.get("name"),
                "episode_release_date": ep.get("release_date"),
                "episode_duration_ms": ep.get("duration_ms"),
            })
        elif sh["source"] == "sr":
            try:
                pid = int(sh["id"])
            except ValueError:
                continue
            prog = await sr.get_program(pid)
            ep_raw = await sr.get_latest_episode(pid)
            if not prog or not ep_raw:
                print(f"[Podcast] skip sr {sh['id']} — prog={bool(prog)} ep={bool(ep_raw)}")
                continue
            ep = sr.normalize_episode(ep_raw)
            out.append({
                "show_id": _show_key(sh),
                "source": "sr",
                "show_name": prog.get("name") or sh["fallback_name"],
                "show_image": prog.get("programimage") or "",
                "episode_id": ep["id"],
                "episode_uri": ep["uri"],
                "episode_name": ep["name"],
                "episode_release_date": ep["release_date"],
                "episode_duration_ms": ep["duration_ms"],
            })
    return out


@app.get("/api/podcasts")
async def list_podcasts(refresh: int = 0):
    """Hardkodet liste af podcasts, beriget med cover + seneste afsnit. Cache 30 min."""
    if not hub_config.feature_enabled("podcasts"):
        return []
    global _podcast_cache, _podcast_cache_at
    now = time.time()
    if refresh or not _podcast_cache or (now - _podcast_cache_at) >= PODCAST_CACHE_TTL:
        try:
            _podcast_cache = await _build_podcast_list()
            _podcast_cache_at = now
        except Exception as e:
            print(f"[Podcast] list error: {e}")
            if not _podcast_cache:
                return JSONResponse({"error": str(e)}, status_code=502)
    return _podcast_cache


async def _play_latest_spotify(sh: dict) -> tuple[bool, str, dict]:
    ep = await spotify.get_show_latest_episode(sh["id"])
    if not ep:
        return False, "no latest episode", {}
    uri = ep.get("uri") or ""
    ok, detail = await spotify.play_episode(uri)
    return ok, detail, {
        "id": ep.get("id"),
        "name": ep.get("name"),
        "uri": uri,
        "release_date": ep.get("release_date"),
        "duration_ms": ep.get("duration_ms"),
    }


async def _play_latest_sr(sh: dict) -> tuple[bool, str, dict]:
    try:
        pid = int(sh["id"])
    except ValueError:
        return False, "bad sr id", {}
    ep_raw = await sr.get_latest_episode(pid)
    if not ep_raw:
        return False, "no latest episode", {}
    url = sr.episode_media_url(ep_raw)
    if not url:
        return False, "no media url", {}
    ep = sr.normalize_episode(ep_raw)
    title = ep.get("name") or sh["fallback_name"]
    ok, detail = await bo_dlna.play_url(url, title=title)
    if ok:
        # A9 skal følge M5 også når kilden er DLNA, ikke kun Spotify
        await bo_link.expand_to_a9("dlna")
    return ok, detail, ep


@app.post("/api/podcasts/play-latest")
async def play_latest_podcast(data: dict = Body(default_factory=dict)):
    """Spil seneste afsnit af et show på B&O M5. Dispatcher per source."""
    if not hub_config.feature_enabled("podcasts"):
        return JSONResponse({"ok": False, "error": "Podcasts disabled"}, status_code=404)
    global _active_podcast_engine
    show_id = (data.get("show_id") or "").strip()
    if not show_id:
        return JSONResponse({"ok": False, "error": "no show_id"}, status_code=400)
    sh = _find_show(show_id)
    if not sh:
        return JSONResponse({"ok": False, "error": f"unknown show: {show_id}"}, status_code=404)

    if sh["source"] == "spotify":
        ok, detail, ep = await _play_latest_spotify(sh)
        if ok:
            _active_podcast_engine = "spotify"
        return {"ok": ok, "detail": detail, "episode": ep}

    if sh["source"] == "sr":
        ok, detail, ep = await _play_latest_sr(sh)
        if ok:
            _active_podcast_engine = "dlna"
        return {"ok": ok, "detail": detail, "episode": ep}

    return JSONResponse(
        {"ok": False, "error": f"unknown source: {sh['source']}"}, status_code=500
    )


@app.get("/api/podcasts/{show_id}/episodes")
async def list_show_episodes(show_id: str, limit: int = 20, offset: int = 0):
    """Hent en side af afsnit (drill-in). Dispatcher per source."""
    if not hub_config.feature_enabled("podcasts"):
        return {"episodes": [], "has_more": False, "offset": offset}
    sh = _find_show(show_id)
    # Default til Spotify hvis show_id er ukendt (bagudkompatibilitet)
    src = sh["source"] if sh else "spotify"
    real_id = sh["id"] if sh else show_id

    if src == "spotify":
        items, has_more = await spotify.get_show_episodes(real_id, limit=limit, offset=offset)
        return {
            "episodes": [
                {
                    "id": ep.get("id"),
                    "uri": ep.get("uri"),
                    "name": ep.get("name"),
                    "release_date": ep.get("release_date"),
                    "duration_ms": ep.get("duration_ms"),
                }
                for ep in items
            ],
            "has_more": has_more,
            "offset": offset,
        }

    if src == "sr":
        try:
            pid = int(real_id)
        except ValueError:
            return {"episodes": [], "has_more": False, "offset": offset}
        # SR API er 1-indexed pages; oversæt offset/limit til (page, size)
        size = max(1, min(50, int(limit)))
        page = (max(0, int(offset)) // size) + 1
        raw, has_more = await sr.get_episodes(pid, page=page, size=size)
        return {
            "episodes": [sr.normalize_episode(ep) for ep in raw],
            "has_more": has_more,
            "offset": offset,
        }

    return {"episodes": [], "has_more": False, "offset": offset}


@app.post("/api/podcasts/play")
async def play_specific_episode(data: dict = Body(default_factory=dict)):
    """Spil et specifikt afsnit. Dispatcher per URI-prefix."""
    if not hub_config.feature_enabled("podcasts"):
        return JSONResponse({"ok": False, "error": "Podcasts disabled"}, status_code=404)
    global _active_podcast_engine
    uri = (data.get("episode_uri") or "").strip()
    if not uri:
        return JSONResponse({"ok": False, "error": "no episode_uri"}, status_code=400)

    if uri.startswith("spotify:episode:"):
        ok, detail = await spotify.play_episode(uri)
        if ok:
            _active_podcast_engine = "spotify"
        return {"ok": ok, "detail": detail}

    if uri.startswith("sr:episode:"):
        sr_id = uri.split(":", 2)[2]
        ep_raw = await sr.get_episode(sr_id)
        if not ep_raw:
            return JSONResponse(
                {"ok": False, "error": f"sr episode {sr_id} not found"}, status_code=404
            )
        url = sr.episode_media_url(ep_raw)
        if not url:
            return JSONResponse({"ok": False, "error": "no media url"}, status_code=502)
        title = ep_raw.get("title") or "Sveriges Radio"
        ok, detail = await bo_dlna.play_url(url, title=title)
        if ok:
            _active_podcast_engine = "dlna"
            await bo_link.expand_to_a9("dlna")
        return {"ok": ok, "detail": detail}

    return JSONResponse(
        {"ok": False, "error": f"unknown uri scheme: {uri[:32]}"}, status_code=400
    )


@app.post("/api/podcasts/pause")
async def pause_podcast():
    """Pauser den senest startede podcast — dispatcher efter aktiv engine."""
    if not hub_config.feature_enabled("podcasts"):
        return {"ok": False, "error": "Podcasts disabled"}
    if _active_podcast_engine == "dlna":
        ok, detail = await bo_dlna.pause()
        return {"ok": ok, "detail": detail, "engine": "dlna"}
    ok = await spotify.pause()
    return {"ok": ok, "engine": "spotify"}


@app.get("/api/spotify/token")
async def spotify_token():
    """Return access token for Web Playback SDK (kræver `streaming` i seneste OAuth-scope)."""
    if not hub_config.feature_enabled("spotify"):
        return JSONResponse({"error": "Spotify disabled"}, status_code=404)
    token = await spotify.access_token_for_web_playback()
    if not token:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    gs = spotify.granted_scope
    if gs and "streaming" not in gs.split():
        return JSONResponse(
            {
                "error": "missing_streaming_scope",
                "granted_scope": gs,
                "fix": "Åbn /api/spotify/login på denne hub og godkend igen (alle scopes).",
            },
            status_code=403,
        )
    return {"token": token, "scope": gs or None}


# ─── REST: Garden camera snapshot POC ─────────────────────────────────────────
@app.post("/api/camera/snapshot")
async def upload_camera_snapshot(request: Request):
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)

    content_type = request.headers.get("content-type", "")
    if "image/jpeg" not in content_type:
        return JSONResponse({"ok": False, "error": "Expected image/jpeg"}, status_code=415)

    body = await request.body()
    if not body:
        return JSONResponse({"ok": False, "error": "Empty snapshot"}, status_code=400)
    if len(body) > MAX_CAMERA_SNAPSHOT_BYTES:
        return JSONResponse({"ok": False, "error": "Snapshot too large"}, status_code=413)

    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = CAMERA_SNAPSHOT_FILE.with_suffix(".tmp")
    tmp_file.write_bytes(body)
    tmp_file.replace(CAMERA_SNAPSHOT_FILE)
    return {"ok": True, "bytes": len(body), "ts": CAMERA_SNAPSHOT_FILE.stat().st_mtime}


@app.get("/api/camera/latest.jpg")
async def latest_camera_snapshot():
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if not CAMERA_SNAPSHOT_FILE.is_file():
        return JSONResponse({"ok": False, "error": "No snapshot yet"}, status_code=404)
    return FileResponse(
        CAMERA_SNAPSHOT_FILE,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/camera/status")
async def camera_snapshot_status():
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if not CAMERA_SNAPSHOT_FILE.is_file():
        return {"ok": True, "available": False, "ts": None, "age": None, "bytes": 0}
    stat = CAMERA_SNAPSHOT_FILE.stat()
    return {
        "ok": True,
        "available": True,
        "ts": stat.st_mtime,
        "age": max(0, time.time() - stat.st_mtime),
        "bytes": stat.st_size,
    }


@app.get("/dashboard")
async def dashboard_page():
    dashboard_file = STATIC_DIR / "dashboard.html"
    if dashboard_file.is_file():
        return FileResponse(dashboard_file, media_type="text/html")
    return JSONResponse({"ok": False, "error": "Dashboard not built"}, status_code=404)


# ─── Static files (SvelteKit build) — mount last ──────────────────────────────
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    cert = BASE_DIR.parent / "certs" / "cert.pem"
    key = BASE_DIR.parent / "certs" / "key.pem"
    use_tls = cert.exists() and key.exists()
    port = 8443 if use_tls else 8000

    try:
        if use_tls:
            print(f"{hub_config.CONFIG.get('site', 'home')} Hub → https://localhost:8443")
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=8443,
                log_level="warning",
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
            )
        else:
            print(f"{hub_config.CONFIG.get('site', 'home')} Hub → http://localhost:8000")
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(
                f"\nPort {port} is already in use — another Home Hub (or process) holds it.\n"
                f"Free the port, then start again:\n"
                f"  lsof -ti tcp:{port} | xargs kill -9\n"
            )
            raise SystemExit(1) from e
        raise
