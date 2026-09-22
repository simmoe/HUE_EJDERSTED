"""Player runtime in Firestore (`ejdersted/player_home` / `player_garden`).

Podcast transport lives here — the same document the kiosk already treats as
source of truth. The hub hydrates from it after a restart and writes it back
on play/pause/seek/clear.
"""

from __future__ import annotations

import os
from typing import Any

PLAYER_COLLECTION = "ejdersted"


def player_doc_id(site: str) -> str:
    return "player_garden" if site == "garden" else "player_home"


def infer_source(uri: str, source: str = "") -> str:
    if source:
        return source
    if uri.startswith("rss:"):
        return "rss"
    if uri.startswith("sr:"):
        return "sr"
    if uri.startswith("spotify:"):
        return "spotify"
    return ""


def infer_engine(uri: str, source: str, site: str, engine: str = "") -> str:
    if engine:
        return engine
    resolved = infer_source(uri, source)
    if resolved == "rss":
        return "rss"
    if resolved == "spotify":
        return "spotify"
    if resolved == "sr":
        return "garden_sr" if site == "garden" else "dlna"
    if site != "garden":
        return "dlna"
    return ""


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
    if "mapValue" in value:
        fields = (value.get("mapValue") or {}).get("fields") or {}
        return {key: decode_value(item) for key, item in fields.items()}
    if "arrayValue" in value:
        items = (value.get("arrayValue") or {}).get("values") or []
        return [decode_value(item) for item in items]
    return None


def decode_document(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {key: decode_value(value) for key, value in fields.items()}


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
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {key: encode_value(item) for key, item in value.items()}}}
    return {"stringValue": str(value)}


def updated_at_ms(raw: Any) -> int:
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    if value < 1e12:
        return int(value * 1000)
    return int(value)


def updated_at_sec(raw: Any) -> float:
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if value > 1e12:
        return value / 1000.0
    return value


def podcasts_from_state(state: dict[str, Any], engine: str) -> dict[str, Any]:
    queue = state.get("queue") if isinstance(state.get("queue"), list) else []
    return {
        "queue": queue,
        "index": int(state.get("episodeIndex") or 0),
        "showTitle": state.get("showTitle") or "",
        "episodeTitle": state.get("episodeTitle") or "",
        "playing": bool(state.get("playing")),
        "positionMs": int(state.get("positionMs") or 0),
        "durationMs": int(state.get("durationMs") or 0),
        "updatedAt": updated_at_ms(state.get("updatedAt")),
        "source": state.get("source") or "",
        "showId": state.get("showId") or "",
        "engine": engine or "",
        "episodeId": state.get("episodeId") or "",
        "episodeUri": state.get("episodeUri") or "",
    }


def player_patch(state: dict[str, Any], engine: str) -> dict[str, Any]:
    active = bool(state.get("active"))
    return {
        "transport": {"active": "podcast" if active else ""},
        "podcasts": podcasts_from_state(state, engine) if active else {
            "queue": [],
            "index": 0,
            "showTitle": "",
            "episodeTitle": "",
            "playing": False,
            "positionMs": 0,
            "durationMs": 0,
            "updatedAt": updated_at_ms(state.get("updatedAt")),
            "source": "",
            "showId": "",
            "engine": "",
            "episodeId": "",
            "episodeUri": "",
        },
    }


def encode_patch(patch: dict[str, Any]) -> dict[str, Any]:
    return {"fields": {key: encode_value(value) for key, value in patch.items()}}


def state_from_doc(doc: dict[str, Any], site: str) -> tuple[dict[str, Any], str] | None:
    transport = doc.get("transport") if isinstance(doc.get("transport"), dict) else {}
    podcasts = doc.get("podcasts") if isinstance(doc.get("podcasts"), dict) else {}
    if transport.get("active") != "podcast":
        return None
    queue = podcasts.get("queue") if isinstance(podcasts.get("queue"), list) else []
    try:
        idx = int(podcasts.get("index") or 0)
    except (TypeError, ValueError):
        idx = 0
    current = queue[idx] if 0 <= idx < len(queue) and isinstance(queue[idx], dict) else {}
    uri = str(podcasts.get("episodeUri") or current.get("uri") or "")
    source = infer_source(uri, str(podcasts.get("source") or ""))
    engine = infer_engine(uri, source, site, str(podcasts.get("engine") or ""))
    return {
        "active": True,
        "source": source,
        "showId": str(podcasts.get("showId") or ""),
        "showTitle": str(podcasts.get("showTitle") or ""),
        "episodeId": str(podcasts.get("episodeId") or current.get("id") or ""),
        "episodeUri": uri,
        "episodeTitle": str(podcasts.get("episodeTitle") or current.get("name") or ""),
        "episodeIndex": idx,
        "queue": queue,
        "playing": bool(podcasts.get("playing")),
        "positionMs": int(podcasts.get("positionMs") or 0),
        "durationMs": int(podcasts.get("durationMs") or 0),
        "updatedAt": updated_at_sec(podcasts.get("updatedAt")),
        "error": "",
    }, engine


def _rest_target(firebase_config: dict[str, Any], site: str) -> tuple[str, dict[str, str], list[tuple[str, str]]] | None:
    project_id = str(firebase_config.get("projectId") or os.getenv("FIREBASE_PROJECT_ID") or "")
    if not project_id or not site:
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
        f"/documents/{PLAYER_COLLECTION}/{player_doc_id(site)}"
    )
    return url, headers, params


async def fetch_player_doc(
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    site: str,
) -> dict[str, Any]:
    target = _rest_target(firebase_config, site)
    if not target or http_client is None:
        return {}
    url, headers, params = target
    try:
        response = await http_client.get(url, params=params, headers=headers, timeout=8)
    except Exception as exc:
        print(f"[player] firestore read failed: {exc}", flush=True)
        return {}
    if response.status_code >= 400:
        print(f"[player] firestore read HTTP {response.status_code}", flush=True)
        return {}
    try:
        payload = response.json()
    except Exception:
        return {}
    return decode_document(payload if isinstance(payload, dict) else {})


async def persist_player_patch(
    patch: dict[str, Any],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    site: str,
) -> bool:
    target = _rest_target(firebase_config, site)
    if not target or http_client is None:
        return False
    url, headers, params = target
    query = list(params)
    for path in patch:
        query.append(("updateMask.fieldPaths", path))
    try:
        response = await http_client.patch(
            url,
            params=query,
            headers=headers,
            json=encode_patch(patch),
            timeout=8,
        )
    except Exception as exc:
        print(f"[player] firestore write failed: {exc}", flush=True)
        return False
    if response.status_code >= 400:
        print(f"[player] firestore write HTTP {response.status_code}", flush=True)
        return False
    return True
