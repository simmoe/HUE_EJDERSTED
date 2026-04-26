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
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from zeroconf import ServiceBrowser, Zeroconf

from hue import HueBridge, start_hue_mdns
from spotify import Spotify, BEO_A9_IP, BEO_M5_IP

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent
DEVICES_FILE = REPO_ROOT / "devices.json"
STATIC_DIR = BASE_DIR / "static"
HUB_GLOBALS_FILE = REPO_ROOT / "hub_globals.json"

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
    known = (
        (BEO_A9_IP, "BeoPlay A9"),
        (BEO_M5_IP, "Beoplay M5"),
    )
    changed = False
    for ip, name in known:
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
    serial = await _get_adb_serial()
    if serial:
        print(f"[ADB] Kiosk phone (Galaxy A12) connected: {serial}")
        # Trigger full kiosk lockdown via the endpoint handler
        await trigger_kiosk()

    hue_bridge = HueBridge()
    poll_task = asyncio.create_task(poll_loop())

    zc = Zeroconf()
    beo_listener = BeoListener(loop)
    ServiceBrowser(zc, "_beoremote._tcp.local.", beo_listener)

    async def on_hue_found(ip: str):
        await manager.broadcast({"type": "hue_status", **hue_bridge.status()})

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
    async with devices_lock:
        return list(devices.values())

@app.post("/api/devices")
async def add_device(data: dict):
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
    return hue_bridge.status()

@app.post("/api/hue/pair")
async def hue_pair(data: dict = {}):
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
KIOSK_PHONE_IP = "192.168.86.15"
ADB_SERIAL = f"{KIOSK_PHONE_IP}:5555"  # fast port — sat via `adb tcpip 5555`

# ─── REST: Screen brightness (ADB) ───────────────────────────────────────────

async def _get_adb_serial() -> str | None:
    """Return ADB serial for the kiosk phone (Galaxy A12), auto-reconnecting if needed."""
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
        f"adb -s {serial} shell settings put global policy_control immersive.full=com.android.chrome",
        f"adb -s {serial} shell cmd statusbar collapse",
        # Mute everything
        f"adb -s {serial} shell media volume --stream 1 --set 0",
        f"adb -s {serial} shell media volume --stream 2 --set 0",
        f"adb -s {serial} shell media volume --stream 3 --set 0",
        f"adb -s {serial} shell media volume --stream 4 --set 0",
        f"adb -s {serial} shell media volume --stream 5 --set 0",
        f"adb -s {serial} shell settings put global zen_mode 2",
        # Prevent updates & restarts
        f"adb -s {serial} shell settings put global stay_on_while_plugged_in 3",
        f"adb -s {serial} shell settings put global heads_up_notifications_enabled 0",
        # Exempt Chrome from Doze / App Standby / battery optimizations
        # so background timers and WebSockets don't get suspended.
        f"adb -s {serial} shell dumpsys deviceidle whitelist +com.android.chrome",
        f"adb -s {serial} shell cmd appops set com.android.chrome RUN_IN_BACKGROUND allow",
        f"adb -s {serial} shell cmd appops set com.android.chrome RUN_ANY_IN_BACKGROUND allow",
    ]
    for cmd in cmds:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
    return {"ok": True}

# ─── Hub globals (Firebase m.m. — hub_globals.json, ikke i git) ───────────────
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
    return {"configured": spotify.configured, "authenticated": spotify.authenticated}

@app.get("/api/spotify/login")
async def spotify_login():
    if not spotify.configured:
        return JSONResponse({"error": "Spotify not configured"}, status_code=500)
    return RedirectResponse(spotify.login_url())

@app.get("/api/spotify/callback")
async def spotify_callback(code: str = ""):
    if not code:
        return JSONResponse({"error": "No code"}, status_code=400)
    ok = await spotify.handle_callback(code)
    if ok:
        return RedirectResponse("/")
    return JSONResponse({"error": "Auth failed"}, status_code=401)

@app.post("/api/spotify/voice")
async def spotify_voice(data: dict):
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        return JSONResponse({"error": "No transcript"}, status_code=400)
    result = await spotify.voice_command(transcript)
    return result

@app.get("/api/spotify/now-playing")
async def spotify_now_playing():
    return await spotify.now_playing() or {}

@app.get("/api/spotify/devices")
async def spotify_devices():
    return await spotify.devices()

@app.post("/api/spotify/pause")
async def spotify_pause():
    return {"ok": await spotify.pause()}

@app.post("/api/spotify/resume")
async def spotify_resume():
    return {"ok": await spotify.resume()}

@app.post("/api/spotify/play-uris")
async def spotify_play_uris(data: dict = Body(default_factory=dict)):
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
    return {"ok": await spotify.skip()}


@app.post("/api/spotify/previous")
async def spotify_previous():
    return {"ok": await spotify.previous()}

@app.post("/api/spotify/radio/build")
async def spotify_radio_build(data: dict = Body(default_factory=dict)):
    return await spotify.build_radio_queue(
        (data.get("seed_uri") or "").strip(),
        (data.get("seed_name") or "").strip(),
        (data.get("seed_artist") or "").strip(),
    )

@app.post("/api/spotify/radio")
async def spotify_radio(data: dict = Body(default_factory=dict)):
    """Byg radio-kø (kræver JSON-body med seed_uri)."""
    return await spotify.build_radio_queue(
        (data.get("seed_uri") or "").strip(),
        (data.get("seed_name") or "").strip(),
        (data.get("seed_artist") or "").strip(),
    )

@app.delete("/api/spotify/radio")
async def spotify_radio_stop():
    return await spotify.stop_radio()

@app.post("/api/spotify/album/build")
async def spotify_album_build(data: dict = Body(default_factory=dict)):
    return await spotify.build_album_queue_from_track_uri((data.get("track_uri") or "").strip())

@app.post("/api/spotify/album")
async def spotify_album(data: dict = Body(default_factory=dict)):
    return await spotify.build_album_queue_from_track_uri((data.get("track_uri") or "").strip())

@app.post("/api/spotify/save")
async def spotify_save(data: dict | None = Body(default=None)):
    uri = (data or {}).get("uri") if data else None
    if isinstance(uri, str):
        uri = uri.strip() or None
    return await spotify.save_track(uri)

@app.get("/api/spotify/is-saved")
async def spotify_is_saved(uri: str | None = None):
    u = (uri or "").strip() or None
    return {"saved": await spotify.is_track_saved(u)}

# ─── REST: Podcasts ───────────────────────────────────────────────────────────
# Hardkodet liste — udvides senere (SR-programmer kommer i fase 2 via deres egen API).
PODCAST_SHOWS: list[dict] = [
    {"id": "6FVyoDMn4GKxveMegJ2Yih", "fallback_name": "Fodboldlisten"},
    {"id": "5d4yba4KbcBTtwZ8glscZZ", "fallback_name": "Det næste kapitel"},
]
PODCAST_CACHE_TTL = 30 * 60  # 30 min — afsnit udkommer typisk én gang om ugen
_podcast_cache: list[dict] = []
_podcast_cache_at: float = 0.0


async def _build_podcast_list() -> list[dict]:
    out: list[dict] = []
    for sh in PODCAST_SHOWS:
        meta = await spotify.get_show(sh["id"])
        ep = await spotify.get_show_latest_episode(sh["id"])
        if not meta or not ep:
            print(f"[Podcast] skip {sh['id']} — meta={bool(meta)} ep={bool(ep)}")
            continue
        images = meta.get("images") or []
        cover = images[0].get("url", "") if images else ""
        out.append({
            "show_id": sh["id"],
            "show_name": meta.get("name") or sh["fallback_name"],
            "show_image": cover,
            "episode_id": ep.get("id"),
            "episode_uri": ep.get("uri"),
            "episode_name": ep.get("name"),
            "episode_release_date": ep.get("release_date"),
            "episode_duration_ms": ep.get("duration_ms"),
        })
    return out


@app.get("/api/podcasts")
async def list_podcasts(refresh: int = 0):
    """Hardkodet liste af podcasts, beriget med cover + seneste afsnit. Cache 30 min."""
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


@app.post("/api/podcasts/play-latest")
async def play_latest_podcast(data: dict = Body(default_factory=dict)):
    """Spil seneste afsnit af et show på B&O M5."""
    show_id = (data.get("show_id") or "").strip()
    if not show_id:
        return JSONResponse({"ok": False, "error": "no show_id"}, status_code=400)
    ep = await spotify.get_show_latest_episode(show_id)
    if not ep:
        return JSONResponse({"ok": False, "error": "no latest episode"}, status_code=404)
    uri = ep.get("uri") or ""
    ok, detail = await spotify.play_episode(uri)
    return {
        "ok": ok,
        "detail": detail,
        "episode": {
            "id": ep.get("id"),
            "name": ep.get("name"),
            "uri": uri,
            "release_date": ep.get("release_date"),
            "duration_ms": ep.get("duration_ms"),
        },
    }


@app.get("/api/podcasts/{show_id}/episodes")
async def list_show_episodes(show_id: str, limit: int = 20, offset: int = 0):
    """Hent en side af afsnit (drill-in)."""
    items, has_more = await spotify.get_show_episodes(show_id, limit=limit, offset=offset)
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


@app.post("/api/podcasts/play")
async def play_specific_episode(data: dict = Body(default_factory=dict)):
    """Spil et specifikt afsnit (givet ved episode_uri)."""
    uri = (data.get("episode_uri") or "").strip()
    if not uri:
        return JSONResponse({"ok": False, "error": "no episode_uri"}, status_code=400)
    ok, detail = await spotify.play_episode(uri)
    return {"ok": ok, "detail": detail}


@app.get("/api/spotify/token")
async def spotify_token():
    """Return access token for Web Playback SDK (kræver `streaming` i seneste OAuth-scope)."""
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
            print("Home Hub → https://localhost:8443")
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=8443,
                log_level="warning",
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
            )
        else:
            print("Home Hub → http://localhost:8000")
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
