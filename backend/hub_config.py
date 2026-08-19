"""Runtime profile configuration for home/garden kiosk deployments."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent
CONFIG_FILE = Path(os.environ.get("HUB_CONFIG_PATH", REPO_ROOT / "hub_config.json"))


DEFAULT_CONFIG: dict[str, Any] = {
    "site": "home",
    "publicUrl": "https://192.168.86.16:8443",
    "features": {
        "camera": True,
        "audio": True,
        "hue": True,
        "spotify": True,
        "podcasts": True,
        "playlists": True,
        "adbKiosk": True,
        "solar": False,
    },
    "kiosk": {
        "phoneIp": "192.168.86.15",
        "adbSerial": "192.168.86.15:5555",
        "multiAppPackage": "com.velis.apartmentterminal",
    },
    "speakers": [
        {"ip": "192.168.86.20", "name": "BeoPlay A9"},
        {"ip": "192.168.86.21", "name": "Beoplay M5"},
    ],
    "audio": {
        "defaultTarget": "",
        "targets": {},
    },
    "camera": {
        "mode": "",
        "gardenHubUrl": "",
        "publisherHosts": [],
    },
    "solar": {
        "gpioPin": 17,
        "activeHigh": True,
        "lat": 55.6761,
        "lon": 12.5683,
        "sunriseOffsetMin": 30,
        "sunsetOffsetMin": 90,
        "tz": "Europe/Copenhagen",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_local_config() -> dict[str, Any]:
    if not CONFIG_FILE.is_file():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


CONFIG: dict[str, Any] = _deep_merge(DEFAULT_CONFIG, _load_local_config())


def _bool_env(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def _apply_env_overrides() -> None:
    site = os.environ.get("HUB_SITE")
    if site:
        CONFIG["site"] = site

    public_url = os.environ.get("HUB_PUBLIC_URL")
    if public_url:
        CONFIG["publicUrl"] = public_url

    features = CONFIG.setdefault("features", {})
    for feature in ("camera", "audio", "hue", "spotify", "podcasts", "playlists", "adbKiosk", "solar"):
        value = _bool_env(f"HUB_FEATURE_{feature.upper()}")
        if value is not None:
            features[feature] = value

    kiosk = CONFIG.setdefault("kiosk", {})
    for env_name, key in (
        ("HUB_KIOSK_PHONE_IP", "phoneIp"),
        ("HUB_KIOSK_ADB_SERIAL", "adbSerial"),
        ("HUB_KIOSK_MULTIAPP_PACKAGE", "multiAppPackage"),
    ):
        value = os.environ.get(env_name)
        if value is not None:
            kiosk[key] = value

    audio = CONFIG.setdefault("audio", {})
    if target := os.environ.get("HUB_AUDIO_DEFAULT_TARGET"):
        audio["defaultTarget"] = target
    if device := os.environ.get("HUB_AUDIO_SPOTIFY_DEVICE"):
        audio["spotifyDevice"] = device

    camera = CONFIG.setdefault("camera", {})
    if mode := os.environ.get("HUB_CAMERA_MODE"):
        camera["mode"] = mode.strip().lower()
    if upstream := os.environ.get("HUB_GARDEN_HUB_URL"):
        camera["gardenHubUrl"] = upstream.strip().rstrip("/")
    if publisher_hosts := os.environ.get("HUB_CAMERA_PUBLISHER_HOSTS"):
        camera["publisherHosts"] = [
            host.strip() for host in publisher_hosts.split(",") if host.strip()
        ]


_apply_env_overrides()


def validate_config(*, require_camera_upstream: bool = False) -> None:
    configured_site = str(CONFIG.get("site") or "").strip()
    if configured_site not in {"home", "garden"}:
        raise ValueError(f"HUB_SITE must be home or garden, got {configured_site!r}")

    mode = camera_mode()
    expected_mode = "viewer" if configured_site == "home" else "publisher"
    if mode != expected_mode:
        raise ValueError(
            f"{configured_site} profile requires HUB_CAMERA_MODE={expected_mode}, got {mode!r}"
        )

    upstream = garden_hub_url()
    if upstream:
        parsed = urlparse(upstream)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("HUB_GARDEN_HUB_URL must be an absolute HTTPS URL")
    if require_camera_upstream and configured_site == "home" and feature_enabled("camera") and not upstream:
        raise ValueError("home camera viewer requires HUB_GARDEN_HUB_URL")


def feature_enabled(name: str) -> bool:
    features = CONFIG.get("features", {})
    return bool(features.get(name))


def site() -> str:
    return str(CONFIG.get("site") or "home")


def camera_mode() -> str:
    camera = CONFIG.get("camera", {})
    if isinstance(camera, dict) and camera.get("mode"):
        return str(camera["mode"]).strip().lower()
    return "publisher" if site() == "garden" else "viewer"


def garden_hub_url() -> str:
    camera = CONFIG.get("camera", {})
    return str(camera.get("gardenHubUrl") or "").strip().rstrip("/") if isinstance(camera, dict) else ""


def camera_publisher_hosts() -> set[str]:
    camera = CONFIG.get("camera", {})
    configured = camera.get("publisherHosts", []) if isinstance(camera, dict) else []
    hosts = {
        str(host).strip()
        for host in configured
        if str(host).strip()
    } if isinstance(configured, list) else set()
    kiosk_host = kiosk_phone_ip().strip()
    adb_host = adb_serial().split(":", 1)[0].strip()
    if kiosk_host:
        hosts.add(kiosk_host)
    if adb_host:
        hosts.add(adb_host)
    return hosts


validate_config()


def bo_speakers_enabled() -> bool:
    """B&O Mozart/BeoLink speakers are a home-only concept (Vesterbro).

    Garden routes audio to configured output targets (BlueALSA etc.) instead, so
    it must not seed, discover, poll or BeoLink-expand the Vesterbro B&O units.
    """
    return feature_enabled("audio") and site() == "home"


def kiosk_phone_ip() -> str:
    return str(CONFIG.get("kiosk", {}).get("phoneIp") or "")


def adb_serial() -> str:
    kiosk = CONFIG.get("kiosk", {})
    return str(kiosk.get("adbSerial") or f"{kiosk_phone_ip()}:5555")


def multiapp_package() -> str:
    return str(CONFIG.get("kiosk", {}).get("multiAppPackage") or "")


def known_speakers() -> list[dict[str, str]]:
    speakers = CONFIG.get("speakers", [])
    if not isinstance(speakers, list):
        return []
    out: list[dict[str, str]] = []
    for speaker in speakers:
        if not isinstance(speaker, dict):
            continue
        ip = str(speaker.get("ip") or "").strip()
        if not ip:
            continue
        out.append({"ip": ip, "name": str(speaker.get("name") or f"B&O ({ip})")})
    return out


def audio_targets() -> list[dict[str, Any]]:
    """Configured output targets, normalized to include an `id` field.

    The garden profile can define these in hub_config.json without changing the
    browser contract. Example:
    {"audio": {"defaultTarget": "storm_lite", "targets": {"storm_lite": {...}}}}
    """
    audio = CONFIG.get("audio", {})
    raw_targets = audio.get("targets", {}) if isinstance(audio, dict) else {}
    items = raw_targets.items() if isinstance(raw_targets, dict) else enumerate(raw_targets if isinstance(raw_targets, list) else [])
    out: list[dict[str, Any]] = []
    for key, value in items:
        if not isinstance(value, dict):
            continue
        target = dict(value)
        target_id = str(target.get("id") or key).strip()
        if not target_id:
            continue
        target["id"] = target_id
        out.append(target)
    return out


def default_audio_target() -> str:
    audio = CONFIG.get("audio", {})
    return str(audio.get("defaultTarget") or "") if isinstance(audio, dict) else ""


def spotify_connect_device() -> str:
    """Preferred Spotify Connect device name to route playback to.

    Garden sets this to the on-Pi librespot device (e.g. "Ejdersted Garden") so
    playback lands on the Pi → BlueALSA → speaker instead of a browser/web player.
    """
    audio = CONFIG.get("audio", {})
    configured = str(audio.get("spotifyDevice") or "") if isinstance(audio, dict) else ""
    if configured:
        return configured
    return "Ejdersted Garden" if site() == "garden" else ""


def solar_config() -> dict[str, Any]:
    """Solar charge-relay settings (GPIO pin, polarity, location, sun offsets)."""
    solar = CONFIG.get("solar", {})
    return solar if isinstance(solar, dict) else {}


def public_config() -> dict[str, Any]:
    """Configuration safe for the browser."""
    targets = [
        {
            "id": t["id"],
            "name": str(t.get("name") or t["id"]),
            "type": str(t.get("type") or ""),
            "default": t["id"] == default_audio_target(),
        }
        for t in audio_targets()
    ]
    return {
        "site": CONFIG.get("site", "home"),
        "publicUrl": CONFIG.get("publicUrl", ""),
        "features": CONFIG.get("features", {}),
        "camera": {
            "mode": camera_mode(),
        },
        "audio": {
            "defaultTarget": default_audio_target(),
            "targets": targets,
        },
    }
