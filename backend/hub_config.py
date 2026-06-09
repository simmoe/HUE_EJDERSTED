"""Runtime profile configuration for home/garden kiosk deployments."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


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
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides() -> None:
    site = os.environ.get("HUB_SITE")
    if site:
        CONFIG["site"] = site

    public_url = os.environ.get("HUB_PUBLIC_URL")
    if public_url:
        CONFIG["publicUrl"] = public_url

    features = CONFIG.setdefault("features", {})
    for feature in ("camera", "audio", "hue", "spotify", "podcasts", "playlists", "adbKiosk"):
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


_apply_env_overrides()


def feature_enabled(name: str) -> bool:
    features = CONFIG.get("features", {})
    return bool(features.get(name))


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


def public_config() -> dict[str, Any]:
    """Configuration safe for the browser."""
    return {
        "site": CONFIG.get("site", "home"),
        "publicUrl": CONFIG.get("publicUrl", ""),
        "features": CONFIG.get("features", {}),
    }
