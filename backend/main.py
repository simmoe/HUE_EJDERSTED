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
import email.utils
import json
import os
import signal
import shutil
import socket
import contextlib
import wave
import time
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import httpx
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from zeroconf import ServiceBrowser, Zeroconf

import bo_dlna
import bo_link
import sr
import audio_targets
import camera_presence
import hub_config
import solar
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
CAMERA_PRESENCE_FILE = CAMERA_DIR / "presence.json"
CAMERA_PRESENCE_BASELINE_FILE = CAMERA_DIR / "presence_baseline.raw"
CAMERA_PERSON_MODEL_FILE = REPO_ROOT / "runtime" / "models" / "yolov8n.onnx"
MAX_CAMERA_SNAPSHOT_BYTES = 2_500_000
CAMERA_PRESENCE_WINDOW_SECONDS = 10 * 60
SILENCE_WAV_FILE = REPO_ROOT / "runtime" / "audio" / "silence_2s.wav"
GARDEN_KEEPALIVE_SECONDS = 15 * 60
GARDEN_KEEPALIVE_PULSE_SECONDS = 2
GARDEN_KEEPALIVE_INTERVAL_SECONDS = 60

# ─── HTTP client ──────────────────────────────────────────────────────────────
_http = httpx.AsyncClient(timeout=2.5)
_camera_http = httpx.AsyncClient(
    timeout=httpx.Timeout(5.0),
    verify=True,
    follow_redirects=False,
)

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
    if mDNS is silent at boot. mDNS may overwrite name/IP later when it sees them.

    Home-only: garden uses audio output targets, not the Vesterbro B&O units."""
    if not hub_config.bo_speakers_enabled():
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

# ─── Solar charge relay ───────────────────────────────────────────────────────
SOLAR_STATE_FILE = REPO_ROOT / "solar_state.json"
solar_ctrl: solar.SolarController | None = None
solar_status_cache: dict = {}


def _solar_status() -> dict:
    return solar_ctrl.status() if solar_ctrl else {"enabled": False}

# ─── Hue ───────────────────────────────────────────────────────────────────────
hue_bridge: HueBridge                    # initialised in lifespan
hue_rooms_cache: list[dict] = []
hue_status_cache: dict = {}
# ─── Spotify ───────────────────────────────────────────────────────────────
spotify = Spotify()

# ─── Garden camera security/presence pipeline ─────────────────────────────
camera_security = camera_presence.CameraPresenceService(
    camera_dir=CAMERA_DIR,
    snapshot_file=CAMERA_SNAPSHOT_FILE,
    state_file=CAMERA_PRESENCE_FILE,
    baseline_file=CAMERA_PRESENCE_BASELINE_FILE,
    model_path=CAMERA_PERSON_MODEL_FILE,
)

# ─── Garden audio keepalive ───────────────────────────────────────────────────
garden_keepalive_until = 0.0
garden_keepalive_last = 0.0
garden_keepalive_no_pulse_until = 0.0
garden_rss_player: asyncio.subprocess.Process | None = None
garden_rss_player_task: asyncio.Task | None = None
garden_sr_player: asyncio.subprocess.Process | None = None
garden_sr_player_task: asyncio.Task | None = None
garden_sr_stream_url = ""
garden_sr_stream_title = ""
podcast_player_state: dict = {
    "active": False,
    "source": "",
    "showId": "",
    "showTitle": "",
    "episodeId": "",
    "episodeUri": "",
    "episodeTitle": "",
    "episodeIndex": 0,
    "queue": [],
    "playing": False,
    "positionMs": 0,
    "durationMs": 0,
    "updatedAt": 0.0,
    "error": "",
}

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


def _mark_garden_audio_active(extra_seconds: int = GARDEN_KEEPALIVE_SECONDS) -> None:
    """Keep Bluetooth output warm after playback so the speaker does not sleep."""
    global garden_keepalive_until, garden_keepalive_no_pulse_until
    if hub_config.site() != "garden" or not hub_config.feature_enabled("audio"):
        return
    now = time.time()
    extra_seconds = max(0, extra_seconds)
    garden_keepalive_until = max(garden_keepalive_until, now + extra_seconds)
    if extra_seconds > GARDEN_KEEPALIVE_SECONDS:
        # The caller knows real audio should be playing for the first part of
        # the window. Do not inject silent keepalive audio until that is over.
        garden_keepalive_no_pulse_until = max(
            garden_keepalive_no_pulse_until,
            now + extra_seconds - GARDEN_KEEPALIVE_SECONDS,
        )


def _allow_garden_idle_keepalive_pulses() -> None:
    """Playback has stopped/paused, so silent pulses may maintain the idle window."""
    global garden_keepalive_no_pulse_until
    garden_keepalive_no_pulse_until = min(garden_keepalive_no_pulse_until, time.time())


def _ensure_silence_wav() -> Path:
    SILENCE_WAV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SILENCE_WAV_FILE.exists():
        return SILENCE_WAV_FILE
    sample_rate = 44100
    frames = b"\x00\x00" * sample_rate * GARDEN_KEEPALIVE_PULSE_SECONDS
    with wave.open(str(SILENCE_WAV_FILE), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return SILENCE_WAV_FILE


async def _spotify_is_playing() -> bool:
    try:
        np = await spotify.now_playing()
        return bool(np and np.get("is_playing"))
    except Exception:
        return False


async def _pulse_garden_audio_keepalive() -> None:
    """Play a short silent PCM buffer to keep the A2DP link awake while idle."""
    targets = _configured_audio_targets()
    target = next((t for t in targets if t.default), targets[0] if targets else None)
    if not target or target.type != "bluealsa" or not target.mac:
        return
    status = await audio_targets.target_status(target)
    if not status.get("online"):
        return
    wav = _ensure_silence_wav()
    device = f"bluealsa:DEV={target.mac},PROFILE=a2dp"
    proc = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-D", device, str(wav),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=GARDEN_KEEPALIVE_PULSE_SECONDS + 3)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()


async def _garden_audio_keepalive_tick() -> None:
    global garden_keepalive_last
    if time.time() >= garden_keepalive_until:
        return
    if time.time() - garden_keepalive_last < GARDEN_KEEPALIVE_INTERVAL_SECONDS:
        return
    if time.time() < garden_keepalive_no_pulse_until:
        garden_keepalive_last = time.time()
        return
    if podcast_player_state.get("active") and podcast_player_state.get("playing"):
        garden_keepalive_last = time.time()
        _mark_garden_audio_active()
        return
    if garden_rss_player and garden_rss_player.returncode is None:
        garden_keepalive_last = time.time()
        _mark_garden_audio_active()
        return
    if await _spotify_is_playing():
        garden_keepalive_last = time.time()
        _mark_garden_audio_active()
        return
    garden_keepalive_last = time.time()
    try:
        await _pulse_garden_audio_keepalive()
    except Exception as exc:
        print(f"[audio] keepalive failed: {exc}")


async def _podcast_player_tick() -> None:
    state = _public_podcast_state()
    if not state.get("active") or not state.get("playing"):
        return
    duration = int(state.get("durationMs") or 0)
    position = int(state.get("positionMs") or 0)
    if duration <= 0 or position < max(0, duration - 1500):
        return
    if state.get("source") == "rss":
        idx = int(state.get("episodeIndex") or 0)
        queue = state.get("queue") if isinstance(state.get("queue"), list) else []
        show_id = str(state.get("showId") or "")
        sh = _find_show(show_id)
        if sh and idx + 1 < len(queue):
            await _play_rss_index(sh, idx + 1)
        else:
            await _stop_rss_player()
            _allow_garden_idle_keepalive_pulses()
            _set_podcast_state(active=False, playing=False, positionMs=duration)
    else:
        _allow_garden_idle_keepalive_pulses()
        _set_podcast_state(playing=False, positionMs=duration)


# ─── Background volume polling ────────────────────────────────────────────────
async def poll_loop():
    """Poll B&O og Hue hvert 2. sekund og push ændringer via WebSocket."""
    while True:
        await asyncio.sleep(2)

        # ── B&O (home-only; garden uses audio output targets) ─────────────────
        if hub_config.bo_speakers_enabled():
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

        # ── Solar charge relay (sun-cycle auto schedule) ─────────────────────
        global solar_status_cache
        if solar_ctrl is not None:
            solar_ctrl.apply()
            status = solar_ctrl.status()
            # Broadcast when the relevant state changes (ignore the ticking clock).
            compare = {k: v for k, v in status.items() if k != "now"}
            if compare != solar_status_cache:
                solar_status_cache = compare
                await manager.broadcast({"type": "solar_status", **status})

        if hub_config.site() == "garden":
            await _garden_audio_keepalive_tick()
            await _podcast_player_tick()

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

    global solar_ctrl
    if hub_config.feature_enabled("solar"):
        try:
            solar_ctrl = solar.SolarController(hub_config.solar_config(), SOLAR_STATE_FILE)
            print(f"[solar] controller ready (mode={solar_ctrl.mode}, simulated={solar_ctrl.simulated})")
        except Exception as exc:
            print(f"[solar] failed to start: {exc}")
            solar_ctrl = None

    hue_bridge = HueBridge()
    poll_task = asyncio.create_task(poll_loop())

    zc = Zeroconf()
    if hub_config.bo_speakers_enabled():
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
    if solar_ctrl is not None:
        solar_ctrl.close()
    zc.close()
    await _http.aclose()
    await _camera_http.aclose()
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
            "solar": _solar_status(),
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
            elif msg.get("type") == "set_solar_mode":
                if solar_ctrl is None:
                    continue
                mode = str(msg.get("mode", ""))
                if mode not in solar.VALID_MODES:
                    continue
                global solar_status_cache
                solar_ctrl.set_mode(mode)
                status = solar_ctrl.status()
                solar_status_cache = {k: v for k, v in status.items() if k != "now"}
                await manager.broadcast({"type": "solar_status", **status})
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
def _configured_audio_targets() -> list[audio_targets.AudioTarget]:
    return audio_targets.configured_targets(
        hub_config.audio_targets(),
        hub_config.default_audio_target(),
    )


def _audio_target_by_id(target_id: str) -> audio_targets.AudioTarget | None:
    for target in _configured_audio_targets():
        if target.id == target_id:
            return target
    return None


@app.get("/api/audio/targets")
async def get_audio_targets():
    if not hub_config.feature_enabled("audio"):
        return []
    return [await audio_targets.target_status(target) for target in _configured_audio_targets()]


@app.post("/api/audio/targets/{target_id}/connect")
async def connect_audio_target(target_id: str):
    if not hub_config.feature_enabled("audio"):
        return JSONResponse({"ok": False, "error": "Audio er deaktiveret for denne profil"}, status_code=404)
    target = _audio_target_by_id(target_id)
    if not target:
        return JSONResponse({"ok": False, "error": "Audio target ikke fundet"}, status_code=404)
    return await audio_targets.connect_target(target)


@app.post("/api/audio/targets/{target_id}/volume")
async def set_audio_target_volume(target_id: str, data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("audio"):
        return JSONResponse({"ok": False, "error": "Audio er deaktiveret for denne profil"}, status_code=404)
    target = _audio_target_by_id(target_id)
    if not target:
        return JSONResponse({"ok": False, "error": "Audio target ikke fundet"}, status_code=404)
    try:
        level = int(data.get("level"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Ugyldigt level"}, status_code=400)
    return await audio_targets.set_target_volume(target, level)


@app.get("/api/solar/status")
async def get_solar_status():
    return _solar_status()


@app.post("/api/solar/mode")
async def set_solar_mode(data: dict = Body(default_factory=dict)):
    global solar_status_cache
    if solar_ctrl is None:
        return JSONResponse({"ok": False, "error": "Solar er deaktiveret for denne profil"}, status_code=404)
    mode = str(data.get("mode", ""))
    if mode not in solar.VALID_MODES:
        return JSONResponse({"ok": False, "error": "Ugyldig mode"}, status_code=400)
    solar_ctrl.set_mode(mode)
    status = solar_ctrl.status()
    solar_status_cache = {k: v for k, v in status.items() if k != "now"}
    await manager.broadcast({"type": "solar_status", **status})
    return {"ok": True, **status}


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
        f"adb -s {serial} shell cmd window set-user-rotation lock 1",
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


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "site": hub_config.site(),
        "cameraMode": hub_config.camera_mode(),
        "gardenUpstreamConfigured": bool(hub_config.garden_hub_url()),
        "release": os.environ.get("HUB_RELEASE", "development"),
    }


def _load_firebase_config() -> dict:
    if not HUB_GLOBALS_FILE.is_file():
        return {}
    try:
        blob = json.loads(HUB_GLOBALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    fb = blob.get("firebase")
    return fb if isinstance(fb, dict) else {}


@app.get("/api/config/firebase")
async def hub_firebase_config():
    """Returnerer Firebase web client config til frontend (tom objekt hvis fil mangler)."""
    return _load_firebase_config()


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

@app.post("/api/spotify/voice/transcribe")
async def spotify_voice_transcribe(request: Request):
    if not hub_config.feature_enabled("spotify"):
        return JSONResponse({"error": "Spotify disabled"}, status_code=404)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/x-wav"}:
        return JSONResponse({"error": "Unsupported audio format"}, status_code=415)
    audio = await request.body()
    if not audio:
        return JSONResponse({"error": "Tom lydoptagelse"}, status_code=400)
    if len(audio) > 2_000_000:
        return JSONResponse({"error": "Lydoptagelsen er for stor"}, status_code=413)
    language = request.headers.get("x-voice-language", "en-US")
    ok, transcript = await spotify.transcribe_audio(audio, content_type, language)
    if not ok:
        return JSONResponse({"error": transcript}, status_code=502)
    return {"ok": True, "transcript": transcript}

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
    ok = await spotify.pause()
    if ok:
        _allow_garden_idle_keepalive_pulses()
        _mark_garden_audio_active()
    return {"ok": ok}

@app.post("/api/spotify/resume")
async def spotify_resume():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    detail = await _prepare_garden_spotify_audio()
    if detail:
        return {"ok": False, "detail": detail}
    ok = await spotify.resume()
    if ok:
        _mark_garden_audio_active()
    return {
        "ok": ok,
        **(
            {"detail": "Spotify Connect på have-Pi'en er offline"}
            if not ok and hub_config.site() == "garden"
            else {}
        ),
    }

@app.post("/api/spotify/play-uris")
async def spotify_play_uris(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    uris = data.get("uris") or []
    offset = int(data.get("offset") or 0)
    position_ms = int(data.get("position_ms") or 0)
    detail = await _prepare_garden_spotify_audio()
    if detail:
        return {"ok": False, "detail": detail}
    ok, detail, duration_ms = await spotify.play_uris_queue(uris, offset, position_ms)
    resp: dict = {"ok": ok}
    if ok and duration_ms:
        remaining_ms = max(0, duration_ms - position_ms)
        resp["duration_ms"] = remaining_ms
        _mark_garden_audio_active((remaining_ms // 1000) + GARDEN_KEEPALIVE_SECONDS)
    elif ok:
        _mark_garden_audio_active()
    if not ok and detail:
        resp["detail"] = detail
    return resp

@app.post("/api/spotify/skip")
async def spotify_skip():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    ok = await spotify.skip()
    if ok:
        _mark_garden_audio_active()
    return {"ok": ok}


@app.post("/api/spotify/previous")
async def spotify_previous():
    if not hub_config.feature_enabled("spotify"):
        return {"ok": False, "error": "Spotify disabled"}
    ok = await spotify.previous()
    if ok:
        _mark_garden_audio_active()
    return {"ok": ok}

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
#   - "rss":     Standard RSS/Omny MP3 → mpg123 → Pi/BlueALSA (garden)
# show_id ud mod frontend prefixes med source for ikke-Spotify shows så det er entydigt.
PODCAST_SHOWS: list[dict] = [
    {
        "source": "rss",
        "id": "fodboldlisten",
        "fallback_name": "Fodboldlisten",
        "order": "latest",
        "feed": "https://api.dr.dk/podcasts/v1/feeds/fodboldlisten.xml?format=podcast",
    },
    {"source": "spotify", "id": "5d4yba4KbcBTtwZ8glscZZ", "fallback_name": "Det næste kapitel"},
    {"source": "sr",      "id": "4914", "fallback_name": "Text och musik med Eric Schüldt"},
    {"source": "sr",      "id": "2488", "fallback_name": "Rendezvous med Kristjan Saag"},
    {
        "source": "rss",
        "id": "magtfuld",
        "fallback_name": "Magtfuld",
        "feed": "https://www.omnycontent.com/d/playlist/414edbb4-4b91-4960-8650-ad4000dbc027/da2c3abe-ae37-4c83-bae0-b29500896504/bf710267-fe14-48e8-82f5-b29500896988/podcast.rss",
    },
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
    if sh["source"] == "rss":
        return f"rss:{sh['id']}"
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


def _rss_text(el: ET.Element | None, path: str) -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None else ""


def _rss_duration_ms(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if raw.isdigit():
            return int(raw) * 1000
        parts = [int(p) for p in raw.split(":")]
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds * 1000
    except ValueError:
        return None


def _rss_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return email.utils.parsedate_to_datetime(raw).astimezone(timezone.utc).date().isoformat()
    except Exception:
        return raw[:10]


async def _rss_feed(sh: dict) -> tuple[dict, list[dict]]:
    feed = sh.get("feed") or ""
    if not feed:
        return {}, []
    r = await _http.get(feed, timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.content.decode("utf-8-sig", errors="replace"))
    channel = root.find("channel")
    ns_itunes = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
    meta = {
        "title": _rss_text(channel, "title") or sh["fallback_name"],
        "image": "",
    }
    img = channel.find(f"{ns_itunes}image") if channel is not None else None
    if img is not None:
        meta["image"] = img.attrib.get("href", "")
    if not meta["image"]:
        meta["image"] = _rss_text(channel, "image/url")

    items: list[dict] = []
    for idx, item in enumerate(channel.findall("item") if channel is not None else []):
        guid = _rss_text(item, "guid") or _rss_text(item, "link") or _rss_text(item, "title")
        enclosure = item.find("enclosure")
        media_url = enclosure.attrib.get("url", "") if enclosure is not None else ""
        if not media_url:
            continue
        items.append({
            "id": guid,
            "uri": f"rss:{sh['id']}:{idx}",
            "name": _rss_text(item, "title") or sh["fallback_name"],
            "release_date": _rss_date(_rss_text(item, "pubDate")),
            "duration_ms": _rss_duration_ms(_rss_text(item, f"{ns_itunes}duration")),
            "media_url": media_url,
        })
    return meta, items


def _public_podcast_state() -> dict:
    state = dict(podcast_player_state)
    if state.get("active") and state.get("playing"):
        elapsed = int((time.time() - float(state.get("updatedAt") or time.time())) * 1000)
        position = max(0, int(state.get("positionMs") or 0) + elapsed)
        duration = max(0, int(state.get("durationMs") or 0))
        state["positionMs"] = min(
            position,
            duration or 24 * 60 * 60 * 1000,
        )
    return state


def _set_podcast_state(**updates) -> dict:
    podcast_player_state.update(updates)
    podcast_player_state["updatedAt"] = time.time()
    return _public_podcast_state()


def _episode_public(ep: dict) -> dict:
    return {
        "id": ep.get("id") or "",
        "uri": ep.get("uri") or "",
        "name": ep.get("name") or "",
        "release_date": ep.get("release_date") or "",
        "duration_ms": ep.get("duration_ms") or 0,
    }


def _chronological_queue(episodes: list[dict]) -> list[dict]:
    # RSS/Omny feeds are normally newest-first; playback should follow the story
    # order, so we keep a canonical oldest->newest queue internally.
    return list(reversed(episodes))


async def _rss_meta_and_queue(sh: dict) -> tuple[dict, list[dict]]:
    meta, episodes = await _rss_feed(sh)
    queue = list(episodes) if sh.get("order") == "latest" else _chronological_queue(episodes)
    return meta, [
        {**ep, "uri": f"rss:{sh['id']}:{idx}"}
        for idx, ep in enumerate(queue)
    ]


async def _garden_bluealsa_device() -> str:
    """Return the garden speaker PCM with a precise operator-facing error."""
    if hub_config.site() != "garden":
        raise RuntimeError("Garden audio is unavailable on this kiosk")
    targets = _configured_audio_targets()
    target = next((t for t in targets if t.default), targets[0] if targets else None)
    if not target or target.type != "bluealsa" or not target.mac:
        raise RuntimeError("Havehøjttaleren er ikke konfigureret")
    status = await audio_targets.target_status(target)
    if not status.get("online"):
        status = await audio_targets.connect_target(target)
    if not status.get("online"):
        detail = str(status.get("error") or "").strip()
        if detail:
            raise RuntimeError(detail)
        if status.get("connected") and not status.get("playback"):
            raise RuntimeError("Højttaleren er forbundet, men A2DP-lydprofilen mangler")
        raise RuntimeError("Gå hen og tænd højttaleren")
    return f"bluealsa:DEV={target.mac},PROFILE=a2dp"


async def _prepare_garden_spotify_audio() -> str:
    if hub_config.site() != "garden":
        return ""
    await _stop_rss_player()
    await _stop_garden_sr_player()
    try:
        await _garden_bluealsa_device()
    except RuntimeError as exc:
        return str(exc)
    return ""


async def _stop_rss_player() -> None:
    global garden_rss_player, garden_rss_player_task
    proc = garden_rss_player
    garden_rss_player = None
    if proc and proc.returncode is None:
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.write(b"STOP\nQUIT\n")
                await proc.stdin.drain()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    if garden_rss_player_task:
        garden_rss_player_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await garden_rss_player_task
        garden_rss_player_task = None


async def _ensure_rss_player() -> asyncio.subprocess.Process:
    global garden_rss_player, garden_rss_player_task
    if garden_rss_player and garden_rss_player.returncode is None:
        return garden_rss_player

    device = await _garden_bluealsa_device()
    # Keep remote-control chatter on stdout, which is discarded below. Sending
    # it to an unread stderr pipe eventually fills the pipe and stalls audio.
    garden_rss_player = await asyncio.create_subprocess_exec(
        "mpg123",
        "-R",
        "--stereo",
        "--timeout",
        "0",
        "-a",
        device,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    garden_rss_player_task = asyncio.create_task(_watch_rss_player(garden_rss_player))
    return garden_rss_player


async def _send_rss_command(command: str) -> None:
    proc = await _ensure_rss_player()
    if not proc.stdin:
        raise RuntimeError("rss player stdin unavailable")
    proc.stdin.write((command.rstrip() + "\n").encode())
    await proc.stdin.drain()


async def _watch_rss_player(proc: asyncio.subprocess.Process) -> None:
    global garden_rss_player
    try:
        code = await proc.wait()
    except asyncio.CancelledError:
        return
    err = ""
    with contextlib.suppress(Exception):
        if proc.stderr:
            err = (await proc.stderr.read()).decode(errors="ignore").strip()
    if garden_rss_player is proc:
        garden_rss_player = None
    state = _public_podcast_state()
    if state.get("active") and state.get("source") == "rss" and state.get("playing"):
        duration = int(state.get("durationMs") or 0)
        position = int(state.get("positionMs") or 0)
        if duration <= 0 or position < duration - 10_000:
            show_id = str(state.get("showId") or "")
            sh = _find_show(show_id)
            if sh:
                print(f"[Podcast] mpg123 stopped early ({code}); restarting at {position}ms. {err}")
                ok, detail, _ = await _play_rss_index(sh, int(state.get("episodeIndex") or 0), position)
                if ok:
                    return
                _set_podcast_state(playing=False, positionMs=position, error=f"mpg123 restart fejlede: {detail}")
                return
        _set_podcast_state(playing=False, positionMs=position, error=f"mpg123 stoppede ({code}) {err}".strip())


async def _stop_garden_sr_player() -> None:
    global garden_sr_player, garden_sr_player_task
    proc = garden_sr_player
    garden_sr_player = None
    if proc and proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGCONT)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    if garden_sr_player_task and garden_sr_player_task is not asyncio.current_task():
        garden_sr_player_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await garden_sr_player_task
    garden_sr_player_task = None


async def _watch_garden_sr_player(proc: asyncio.subprocess.Process) -> None:
    global garden_sr_player
    try:
        code = await proc.wait()
    except asyncio.CancelledError:
        return
    err = ""
    with contextlib.suppress(Exception):
        if proc.stderr:
            err = (await proc.stderr.read()).decode(errors="ignore").strip()
    if garden_sr_player is not proc:
        return
    garden_sr_player = None
    state = _public_podcast_state()
    if state.get("active") and state.get("source") == "sr" and state.get("playing"):
        duration = int(state.get("durationMs") or 0)
        position = int(state.get("positionMs") or 0)
        finished = duration > 0 and position >= duration - 10_000
        _set_podcast_state(
            playing=False,
            positionMs=duration if finished else position,
            error="" if finished else f"SR-afspilleren stoppede ({code}) {err}".strip(),
        )


async def _play_garden_sr_url(
    url: str, title: str, position_ms: int = 0
) -> tuple[bool, str]:
    global garden_sr_player, garden_sr_player_task
    global garden_sr_stream_url, garden_sr_stream_title
    if hub_config.site() != "garden":
        return False, "Garden audio is unavailable on this kiosk"
    if not shutil.which("ffmpeg"):
        return False, "Garden Pi mangler ffmpeg"
    try:
        device = await _garden_bluealsa_device()
    except RuntimeError as exc:
        return False, str(exc)
    await _stop_rss_player()
    await _stop_garden_sr_player()
    garden_sr_player = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0, position_ms) / 1000:.3f}",
        "-i",
        url,
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-f",
        "alsa",
        device,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(1.2)
    if garden_sr_player.returncode is not None:
        err = ""
        if garden_sr_player.stderr:
            err = (await garden_sr_player.stderr.read()).decode(errors="ignore").strip()
        garden_sr_player = None
        return False, err or "SR-afspilleren kunne ikke starte"
    garden_sr_stream_url = url
    garden_sr_stream_title = title
    garden_sr_player_task = asyncio.create_task(_watch_garden_sr_player(garden_sr_player))
    _mark_garden_audio_active()
    return True, f"spiller {title}"


async def _play_rss_index(sh: dict, index: int, position_ms: int = 0) -> tuple[bool, str, dict]:
    global garden_rss_player, garden_rss_player_task
    meta, queue = await _rss_meta_and_queue(sh)
    if not queue:
        return False, "no episodes", {}
    index = max(0, min(index, len(queue) - 1))
    ep = queue[index]
    if hub_config.site() != "garden":
        ok, detail = await bo_dlna.play_url(ep.get("media_url") or "", title=ep.get("name") or sh["fallback_name"])
        if ok:
            await bo_link.expand_to_a9("dlna")
            public_queue = [_episode_public(e) for e in queue]
            _set_podcast_state(
                active=True,
                source="rss",
                showId=_show_key(sh),
                showTitle=meta.get("title") or sh["fallback_name"],
                episodeId=ep.get("id") or "",
                episodeUri=ep.get("uri") or "",
                episodeTitle=ep.get("name") or sh["fallback_name"],
                episodeIndex=index,
                queue=public_queue,
                playing=True,
                positionMs=0,
                durationMs=ep.get("duration_ms") or 0,
                error="",
            )
        return ok, detail, _episode_public(ep)
    await _stop_garden_sr_player()
    if garden_rss_player and garden_rss_player.returncode is None:
        proc = garden_rss_player
        garden_rss_player = None
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.write(b"STOP\nQUIT\n")
                await proc.stdin.drain()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        if garden_rss_player_task and garden_rss_player_task is not asyncio.current_task():
            garden_rss_player_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await garden_rss_player_task
        garden_rss_player_task = None
    try:
        await _send_rss_command(f"LOAD {ep['media_url']}")
        if position_ms > 0:
            await asyncio.sleep(0.3)
            await _send_rss_command(f"JUMP {max(0, position_ms // 1000)}s")
    except Exception as exc:
        return False, str(exc), {}

    public_queue = [_episode_public(e) for e in queue]
    _set_podcast_state(
        active=True,
        source="rss",
        showId=_show_key(sh),
        showTitle=meta.get("title") or sh["fallback_name"],
        episodeId=ep.get("id") or "",
        episodeUri=ep.get("uri") or "",
        episodeTitle=ep.get("name") or sh["fallback_name"],
        episodeIndex=index,
        queue=public_queue,
        playing=True,
        positionMs=max(0, position_ms),
        durationMs=ep.get("duration_ms") or 0,
        error="",
    )
    _mark_garden_audio_active(((ep.get("duration_ms") or 0) // 1000) + GARDEN_KEEPALIVE_SECONDS)
    return True, f"spiller {ep.get('name') or sh['fallback_name']}", _episode_public(ep)


async def _play_rss_url(url: str, title: str = "Podcast") -> tuple[bool, str]:
    """Play an RSS MP3 episode on the garden Pi's default BlueALSA target."""
    global garden_rss_player
    if garden_rss_player and garden_rss_player.returncode is None:
        garden_rss_player.terminate()
        try:
            await asyncio.wait_for(garden_rss_player.wait(), timeout=2)
        except asyncio.TimeoutError:
            garden_rss_player.kill()
            await garden_rss_player.wait()

    try:
        device = await _garden_bluealsa_device()
    except RuntimeError as exc:
        return False, str(exc)
    garden_rss_player = await asyncio.create_subprocess_exec(
        "mpg123", "-q", "-a", device, url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(1.0)
    if garden_rss_player.returncode is not None:
        err = ""
        if garden_rss_player.stderr:
            err = (await garden_rss_player.stderr.read()).decode(errors="ignore")
        return False, (err or "mpg123 stoppede").strip()
    _mark_garden_audio_active()
    return True, f"spiller {title}"


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
        elif sh["source"] == "rss":
            try:
                meta, episodes = await _rss_feed(sh)
            except Exception as exc:
                print(f"[Podcast] skip rss {sh['id']} — {exc}")
                continue
            if not episodes:
                print(f"[Podcast] skip rss {sh['id']} — no episodes")
                continue
            ep = episodes[0]
            out.append({
                "show_id": _show_key(sh),
                "source": "rss",
                "show_name": meta.get("title") or sh["fallback_name"],
                "show_image": meta.get("image") or "",
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
    if hub_config.site() == "garden":
        detail = await _prepare_garden_spotify_audio()
        if detail:
            return False, detail, _episode_public(ep)
    ok, detail = await spotify.play_episode(uri)
    ep_public = {
        "id": ep.get("id"),
        "name": ep.get("name"),
        "uri": uri,
        "release_date": ep.get("release_date"),
        "duration_ms": ep.get("duration_ms"),
    }
    if ok:
        _set_podcast_state(
            active=True,
            source="spotify",
            showId=_show_key(sh),
            showTitle=sh["fallback_name"],
            episodeId=ep_public["id"] or "",
            episodeUri=uri,
            episodeTitle=ep_public["name"] or sh["fallback_name"],
            episodeIndex=0,
            queue=[ep_public],
            playing=True,
            positionMs=0,
            durationMs=ep_public["duration_ms"] or 0,
            error="",
        )
    return ok, detail, ep_public


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
    if hub_config.site() == "garden":
        ok, detail = await _play_garden_sr_url(url, title)
    else:
        ok, detail = await bo_dlna.play_url(url, title=title)
        if ok:
            await bo_link.expand_to_a9("dlna")
    if ok:
        _set_podcast_state(
            active=True,
            source="sr",
            showId=_show_key(sh),
            showTitle=sh["fallback_name"],
            episodeId=ep.get("id") or "",
            episodeUri=ep.get("uri") or "",
            episodeTitle=title,
            episodeIndex=0,
            queue=[ep],
            playing=True,
            positionMs=0,
            durationMs=ep.get("duration_ms") or 0,
            error="",
        )
    return ok, detail, ep


async def _play_latest_rss(sh: dict) -> tuple[bool, str, dict]:
    _, queue = await _rss_meta_and_queue(sh)
    if not queue:
        return False, "no latest episode", {}
    index = 0 if sh.get("order") == "latest" else len(queue) - 1
    return await _play_rss_index(sh, index)


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
        return {"ok": ok, "detail": detail, "episode": ep, "player": _public_podcast_state()}

    if sh["source"] == "sr":
        ok, detail, ep = await _play_latest_sr(sh)
        if ok:
            _active_podcast_engine = "garden_sr" if hub_config.site() == "garden" else "dlna"
        return {"ok": ok, "detail": detail, "episode": ep, "player": _public_podcast_state()}

    if sh["source"] == "rss":
        ok, detail, ep = await _play_latest_rss(sh)
        if ok:
            _active_podcast_engine = "rss"
        return {"ok": ok, "detail": detail, "episode": ep, "player": _public_podcast_state()}

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
                if isinstance(ep, dict)
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

    if src == "rss":
        try:
            _, episodes = await _rss_meta_and_queue(sh)
        except Exception:
            return {"episodes": [], "has_more": False, "offset": offset}
        start = max(0, int(offset))
        end = start + max(1, min(50, int(limit)))
        public = [
            {k: ep.get(k) for k in ("id", "uri", "name", "release_date", "duration_ms")}
            for ep in episodes[start:end]
        ]
        return {
            "episodes": public,
            "has_more": end < len(episodes),
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
        if hub_config.site() == "garden":
            detail = await _prepare_garden_spotify_audio()
            if detail:
                return {"ok": False, "detail": detail}
        ok, detail = await spotify.play_episode(uri)
        if ok:
            _active_podcast_engine = "spotify"
            ep_id = uri.rsplit(":", 1)[-1]
            ep_meta = await spotify.get_episode(ep_id)
            title = (ep_meta or {}).get("name") or "Podcast"
            show_title = ((ep_meta or {}).get("show") or {}).get("name") or "Podcast"
            duration = int((ep_meta or {}).get("duration_ms") or 0)
            _set_podcast_state(
                active=True,
                source="spotify",
                showId="",
                showTitle=show_title,
                episodeId=ep_id,
                episodeUri=uri,
                episodeTitle=title,
                episodeIndex=0,
                queue=[{"id": ep_id, "uri": uri, "name": title, "duration_ms": duration}],
                playing=True,
                positionMs=0,
                durationMs=duration,
                error="",
            )
        return {"ok": ok, "detail": detail, "player": _public_podcast_state()}

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
        if hub_config.site() == "garden":
            ok, detail = await _play_garden_sr_url(url, title)
        else:
            ok, detail = await bo_dlna.play_url(url, title=title)
        if ok:
            _active_podcast_engine = "garden_sr" if hub_config.site() == "garden" else "dlna"
            if hub_config.site() != "garden":
                await bo_link.expand_to_a9("dlna")
            ep = sr.normalize_episode(ep_raw)
            _set_podcast_state(
                active=True,
                source="sr",
                showId="",
                showTitle="Sveriges Radio",
                episodeId=ep.get("id") or "",
                episodeUri=ep.get("uri") or uri,
                episodeTitle=title,
                episodeIndex=0,
                queue=[ep],
                playing=True,
                positionMs=0,
                durationMs=ep.get("duration_ms") or 0,
                error="",
            )
        return {"ok": ok, "detail": detail}

    if uri.startswith("rss:"):
        try:
            _, show_id, idx_raw = uri.split(":", 2)
            idx = int(idx_raw)
        except ValueError:
            return JSONResponse({"ok": False, "error": "bad rss episode uri"}, status_code=400)
        sh = _find_show(f"rss:{show_id}") or _find_show(show_id)
        if not sh or sh.get("source") != "rss":
            return JSONResponse({"ok": False, "error": f"rss show {show_id} not found"}, status_code=404)
        _, queue = await _rss_meta_and_queue(sh)
        queue_idx = next((i for i, ep in enumerate(queue) if ep.get("uri") == uri), -1)
        if queue_idx < 0:
            return JSONResponse({"ok": False, "error": f"rss episode {idx} not found"}, status_code=404)
        ok, detail, ep = await _play_rss_index(sh, queue_idx)
        if ok:
            _active_podcast_engine = "rss"
        return {"ok": ok, "detail": detail, "episode": ep, "player": _public_podcast_state()}

    return JSONResponse(
        {"ok": False, "error": f"unknown uri scheme: {uri[:32]}"}, status_code=400
    )


@app.post("/api/podcasts/pause")
async def pause_podcast():
    """Pauser den senest startede podcast — dispatcher efter aktiv engine."""
    if not hub_config.feature_enabled("podcasts"):
        return {"ok": False, "error": "Podcasts disabled"}
    if _active_podcast_engine == "rss":
        return await podcast_player_pause()
    if _active_podcast_engine == "garden_sr":
        return await podcast_player_pause()
    if _active_podcast_engine == "dlna":
        ok, detail = await bo_dlna.pause()
        if ok:
            _allow_garden_idle_keepalive_pulses()
            _set_podcast_state(playing=False, positionMs=_public_podcast_state().get("positionMs") or 0)
        return {"ok": ok, "detail": detail, "engine": "dlna"}
    ok = await spotify.pause()
    if ok:
        _allow_garden_idle_keepalive_pulses()
        _set_podcast_state(playing=False, positionMs=_public_podcast_state().get("positionMs") or 0)
    return {"ok": ok, "engine": "spotify"}


@app.get("/api/podcasts/player")
async def get_podcast_player():
    if not hub_config.feature_enabled("podcasts"):
        return JSONResponse({"ok": False, "error": "Podcasts disabled"}, status_code=404)
    return {"ok": True, "player": _public_podcast_state()}


@app.post("/api/podcasts/player/pause")
async def podcast_player_pause():
    state = _public_podcast_state()
    if not state.get("active"):
        return {"ok": True, "player": state}
    if state.get("source") == "rss" and state.get("playing"):
        if hub_config.site() == "garden":
            await _send_rss_command("PAUSE")
        else:
            await bo_dlna.pause()
    elif state.get("source") == "spotify":
        await spotify.pause()
    elif state.get("source") == "sr":
        if hub_config.site() == "garden":
            if garden_sr_player and garden_sr_player.returncode is None:
                garden_sr_player.send_signal(signal.SIGSTOP)
        else:
            await bo_dlna.pause()
    _allow_garden_idle_keepalive_pulses()
    updated = _set_podcast_state(playing=False, positionMs=state.get("positionMs") or 0)
    _mark_garden_audio_active()
    return {"ok": True, "player": updated}


@app.post("/api/podcasts/player/resume")
async def podcast_player_resume():
    state = _public_podcast_state()
    if not state.get("active"):
        return {"ok": False, "error": "no active podcast", "player": state}
    if state.get("source") == "rss" and not state.get("playing"):
        if hub_config.site() == "garden":
            if not garden_rss_player or garden_rss_player.returncode is not None:
                sh = _find_show(str(state.get("showId") or ""))
                if not sh:
                    return {"ok": False, "error": "unknown RSS show", "player": state}
                ok, detail, _ = await _play_rss_index(
                    sh,
                    int(state.get("episodeIndex") or 0),
                    int(state.get("positionMs") or 0),
                )
                if not ok:
                    return {"ok": False, "error": detail, "player": state}
            else:
                await _send_rss_command("PAUSE")
        else:
            await bo_dlna.resume()
    elif state.get("source") == "spotify":
        await spotify.resume()
    elif state.get("source") == "sr":
        if hub_config.site() == "garden":
            if not garden_sr_player or garden_sr_player.returncode is not None:
                return {
                    "ok": False,
                    "error": "SR-afspilleren er ikke aktiv",
                    "player": state,
                }
            garden_sr_player.send_signal(signal.SIGCONT)
        else:
            await bo_dlna.resume()
    updated = _set_podcast_state(playing=True, positionMs=state.get("positionMs") or 0)
    _mark_garden_audio_active()
    return {"ok": True, "player": updated}


@app.post("/api/podcasts/player/seek")
async def podcast_player_seek(data: dict = Body(default_factory=dict)):
    state = _public_podcast_state()
    if not state.get("active"):
        return {"ok": False, "error": "no active podcast", "player": state}
    duration = int(state.get("durationMs") or 0)
    current = int(state.get("positionMs") or 0)
    if "positionSeconds" in data:
        target_ms = max(0, int(float(data.get("positionSeconds") or 0) * 1000))
    else:
        target_ms = current + int(float(data.get("offsetSeconds") or 0) * 1000)
    if duration > 0:
        target_ms = min(duration, target_ms)
    target_ms = max(0, target_ms)
    if state.get("source") == "rss":
        if hub_config.site() == "garden":
            await _send_rss_command(f"JUMP {target_ms // 1000}s")
    elif state.get("source") == "sr" and hub_config.site() == "garden":
        if not garden_sr_stream_url:
            return {"ok": False, "error": "SR-stream mangler", "player": state}
        ok, detail = await _play_garden_sr_url(
            garden_sr_stream_url, garden_sr_stream_title or state.get("episodeTitle") or "Sveriges Radio", target_ms
        )
        if not ok:
            return {"ok": False, "error": detail, "player": state}
    updated = _set_podcast_state(positionMs=target_ms)
    _mark_garden_audio_active()
    return {"ok": True, "player": updated}


@app.post("/api/podcasts/player/next")
async def podcast_player_next():
    state = _public_podcast_state()
    if state.get("source") != "rss":
        return {"ok": False, "error": "next only supported for RSS podcasts", "player": state}
    sh = _find_show(str(state.get("showId") or ""))
    queue = state.get("queue") if isinstance(state.get("queue"), list) else []
    idx = int(state.get("episodeIndex") or 0) + 1
    if not sh or idx >= len(queue):
        return {"ok": False, "error": "ingen næste episode", "player": state}
    ok, detail, ep = await _play_rss_index(sh, idx)
    return {"ok": ok, "detail": detail, "episode": ep, "player": _public_podcast_state()}


@app.post("/api/podcasts/player/previous")
async def podcast_player_previous():
    state = _public_podcast_state()
    if state.get("source") != "rss":
        return {"ok": False, "error": "previous only supported for RSS podcasts", "player": state}
    sh = _find_show(str(state.get("showId") or ""))
    idx = max(0, int(state.get("episodeIndex") or 0) - 1)
    if not sh:
        return {"ok": False, "error": "unknown show", "player": state}
    ok, detail, ep = await _play_rss_index(sh, idx)
    return {"ok": ok, "detail": detail, "episode": ep, "player": _public_podcast_state()}


@app.post("/api/podcasts/player/clear")
async def podcast_player_clear():
    state = _public_podcast_state()
    await _stop_rss_player()
    await _stop_garden_sr_player()
    if hub_config.site() != "garden" and state.get("source") in ("rss", "sr"):
        await bo_dlna.stop()
    elif state.get("source") == "spotify":
        await spotify.pause()
    _allow_garden_idle_keepalive_pulses()
    _set_podcast_state(
        active=False,
        source="",
        showId="",
        showTitle="",
        episodeId="",
        episodeUri="",
        episodeTitle="",
        episodeIndex=0,
        queue=[],
        playing=False,
        positionMs=0,
        durationMs=0,
        error="",
    )
    return {"ok": True, "player": _public_podcast_state()}


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
def _request_client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def _camera_publisher_allowed(request: Request) -> bool:
    """Only the configured garden Android kiosk may publish camera snapshots."""
    if hub_config.camera_mode() != "publisher" or hub_config.site() != "garden":
        return False
    return _request_client_host(request) in hub_config.camera_publisher_hosts()


async def _garden_camera_get(path: str) -> httpx.Response | JSONResponse:
    """Fetch a read-only camera resource from the garden over verified HTTPS."""
    upstream = hub_config.garden_hub_url()
    if not upstream:
        return JSONResponse(
            {
                "ok": False,
                "available": False,
                "error": "Garden camera upstream is not configured",
                "source": "garden-proxy",
            },
            status_code=503,
        )
    try:
        response = await _camera_http.get(f"{upstream}{path}")
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        return JSONResponse(
            {
                "ok": False,
                "available": False,
                "error": f"Garden camera unavailable: {type(exc).__name__}",
                "source": "garden-proxy",
            },
            status_code=502,
        )
    if response.status_code != 200:
        return JSONResponse(
            {
                "ok": False,
                "available": False,
                "error": f"Garden camera returned HTTP {response.status_code}",
                "source": "garden-proxy",
            },
            status_code=502,
        )
    return response


async def _garden_camera_json(path: str) -> dict | JSONResponse:
    response = await _garden_camera_get(path)
    if isinstance(response, JSONResponse):
        return response
    try:
        payload = response.json()
    except ValueError:
        return JSONResponse(
            {
                "ok": False,
                "available": False,
                "error": "Garden camera returned invalid JSON",
                "source": "garden-proxy",
            },
            status_code=502,
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {
                "ok": False,
                "available": False,
                "error": "Garden camera returned an invalid payload",
                "source": "garden-proxy",
            },
            status_code=502,
        )
    payload["source"] = "garden-proxy"
    return payload


@app.get("/api/camera/publisher")
async def camera_publisher_status(request: Request):
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    return {
        "ok": True,
        "canPublish": _camera_publisher_allowed(request),
        "client": _request_client_host(request),
    }


@app.post("/api/camera/snapshot")
async def upload_camera_snapshot(request: Request):
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if not _camera_publisher_allowed(request):
        return JSONResponse({"ok": False, "error": "Only the garden kiosk may publish snapshots"}, status_code=403)

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
    presence = await camera_security.process_snapshot(
        dict(request.headers),
        body,
        firebase_config=_load_firebase_config(),
        http_client=_http,
    )
    await manager.broadcast({"type": "security_status", **presence})
    return {"ok": True, "bytes": len(body), "ts": CAMERA_SNAPSHOT_FILE.stat().st_mtime, "presence": presence}


@app.get("/api/camera/latest.jpg")
async def latest_camera_snapshot():
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if hub_config.camera_mode() == "viewer":
        response = await _garden_camera_get("/api/camera/latest.jpg")
        if isinstance(response, JSONResponse):
            return response
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "no-store, max-age=0", "X-Camera-Source": "garden-proxy"},
        )
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
    if hub_config.camera_mode() == "viewer":
        return await _garden_camera_json("/api/camera/status")
    if not CAMERA_SNAPSHOT_FILE.is_file():
        return {"ok": True, "available": False, "ts": None, "age": None, "bytes": 0, "presence": camera_security.public_state()}
    stat = CAMERA_SNAPSHOT_FILE.stat()
    return {
        "ok": True,
        "available": True,
        "ts": stat.st_mtime,
        "age": max(0, time.time() - stat.st_mtime),
        "bytes": stat.st_size,
        "presence": camera_security.public_state(),
    }


@app.get("/api/security/garden")
async def garden_security_status():
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if hub_config.camera_mode() == "viewer":
        return await _garden_camera_json("/api/security/garden")
    return {"ok": True, "security": camera_security.public_state()}


@app.post("/api/security/garden/armed")
async def set_garden_security_armed(data: dict = Body(default_factory=dict)):
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if hub_config.camera_mode() != "publisher":
        return JSONResponse(
            {"ok": False, "error": "Camera viewers cannot change garden security state"},
            status_code=403,
        )
    armed = bool(data.get("armed"))
    security = await camera_security.set_armed(
        armed,
        firebase_config=_load_firebase_config(),
        http_client=_http,
    )
    await manager.broadcast({"type": "security_status", **security})
    return {"ok": True, "security": security}


@app.get("/api/security/evidence/{event_id}.jpg")
async def garden_security_evidence(event_id: str):
    if not hub_config.feature_enabled("camera"):
        return JSONResponse({"ok": False, "error": "Camera disabled"}, status_code=404)
    if hub_config.camera_mode() == "viewer":
        response = await _garden_camera_get(f"/api/security/evidence/{event_id}.jpg")
        if isinstance(response, JSONResponse):
            return response
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "no-store, max-age=0", "X-Camera-Source": "garden-proxy"},
        )
    evidence = camera_security.evidence_file(event_id)
    if evidence is None:
        return JSONResponse({"ok": False, "error": "Evidence not found"}, status_code=404)
    return FileResponse(
        evidence,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/dashboard")
async def dashboard_page():
    dashboard_file = STATIC_DIR / "dashboard.html"
    if dashboard_file.is_file():
        return FileResponse(dashboard_file, media_type="text/html")
    return JSONResponse({"ok": False, "error": "Dashboard not built"}, status_code=404)


# ─── Static files (SvelteKit build) — mount last ──────────────────────────────
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

def _tls_cert_paths() -> tuple[Path, Path]:
    """Resolve hub TLS material. Prefer env overrides, then repo certs/."""
    cert = Path(os.environ.get("HUB_TLS_CERT", BASE_DIR.parent / "certs" / "cert.pem"))
    key = Path(os.environ.get("HUB_TLS_KEY", BASE_DIR.parent / "certs" / "key.pem"))
    return cert, key


def _describe_tls_cert(cert: Path) -> str:
    """Best-effort subject/issuer summary so operators can spot self-signed leftovers."""
    if not shutil.which("openssl") or not cert.is_file():
        return cert.name
    try:
        proc = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", "-subject", "-issuer", "-enddate"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        text = (proc.stdout or "").strip().replace("\n", " | ")
        return text or cert.name
    except Exception:
        return cert.name


if __name__ == "__main__":
    import uvicorn

    cert, key = _tls_cert_paths()
    use_tls = cert.exists() and key.exists()
    port = 8443 if use_tls else 8000

    try:
        if use_tls:
            print(f"{hub_config.CONFIG.get('site', 'home')} Hub → https://0.0.0.0:8443")
            print(f"TLS: {_describe_tls_cert(cert)}")
            hint = BASE_DIR.parent / "certs" / "public-url.txt"
            if hint.is_file():
                print(f"Public URL hint: {hint.read_text(encoding='utf-8').strip()}")
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
