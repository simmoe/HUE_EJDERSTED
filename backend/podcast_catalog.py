"""Podcast show catalog in Firestore (`ejdersted/podcasts`).

The kiosk reads whatever `shows` is in that document. Defaults seed an empty
doc so a restart still has a list if Firebase is down.
"""

from __future__ import annotations

import os
import time
from typing import Any

import player_state

DOC = "ejdersted/podcasts"
REFRESH_S = 60.0

DEFAULT_SHOWS: list[dict[str, str]] = [
    {
        "source": "rss",
        "id": "fodboldlisten",
        "fallback_name": "Fodboldlisten",
        "order": "latest",
        "spotify_id": "6FVyoDMn4GKxveMegJ2Yih",
        "feed": "https://api.dr.dk/podcasts/v1/feeds/fodboldlisten.xml?format=podcast",
    },
    {
        "source": "rss",
        "id": "det-naeste-kapitel",
        "fallback_name": "Det næste kapitel",
        "spotify_id": "5d4yba4KbcBTtwZ8glscZZ",
        "feed": "https://www.omnycontent.com/d/playlist/1283f5f4-2508-4981-a99f-acb500e64dcf/3a33d3f9-b4e2-4e62-96cf-ad0800b1138a/cb87422a-2300-40a2-8fc0-ad0800b11393/podcast.rss",
    },
    {"source": "sr", "id": "4914", "fallback_name": "Text och musik med Eric Schüldt"},
    {"source": "sr", "id": "2488", "fallback_name": "Rendezvous med Kristjan Saag"},
    {
        "source": "rss",
        "id": "magtfuld",
        "fallback_name": "Magtfuld",
        "feed": "https://www.omnycontent.com/d/playlist/414edbb4-4b91-4960-8650-ad4000dbc027/da2c3abe-ae37-4c83-bae0-b29500896504/bf710267-fe14-48e8-82f5-b29500896988/podcast.rss",
    },
    {
        "source": "rss",
        "id": "prompt",
        "fallback_name": "Prompt",
        "order": "latest",
        "feed": "https://api.dr.dk/podcasts/v1/feeds/prompt.xml?format=podcast",
    },
    {
        "source": "rss",
        "id": "song-exploder",
        "fallback_name": "Song Exploder",
        "order": "latest",
        "feed": "https://feed.songexploder.net/SongExploder",
    },
    {
        "source": "rss",
        "id": "splittet-til-atomer",
        "fallback_name": "Splittet til atomer",
        "order": "latest",
        "feed": "https://api.dr.dk/podcasts/v1/feeds/splittet-til-atomer.xml?format=podcast",
    },
    {
        "source": "rss",
        "id": "portraetalbum",
        "fallback_name": "Portrætalbum",
        "order": "latest",
        "feed": "https://www.omnycontent.com/d/playlist/414edbb4-4b91-4960-8650-ad4000dbc027/d32c8e84-d488-459e-982c-ae1900df1a62/19111071-6b3e-469e-a67f-ae1900dfd1f2/podcast.rss",
    },
]

_cache: list[dict[str, str]] = []
_cache_at: float = 0.0
_fingerprint = ""


def reset() -> None:
    global _cache, _cache_at, _fingerprint
    _cache = []
    _cache_at = 0.0
    _fingerprint = ""


def current() -> list[dict[str, str]]:
    return list(_cache or DEFAULT_SHOWS)


def fingerprint(shows: list[dict[str, str]] | None = None) -> str:
    rows = shows if shows is not None else current()
    return "|".join(f"{row.get('source')}:{row.get('id')}" for row in rows)


def parse_show(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "").strip().lower()
    show_id = str(raw.get("id") or "").strip()
    name = str(raw.get("fallback_name") or raw.get("name") or "").strip()
    if source not in {"rss", "sr"} or not show_id or not name:
        return None
    row: dict[str, str] = {"source": source, "id": show_id, "fallback_name": name}
    feed = str(raw.get("feed") or "").strip()
    if source == "rss":
        if not feed.startswith("http"):
            return None
        row["feed"] = feed
    order = str(raw.get("order") or "").strip()
    if order:
        row["order"] = order
    spotify_id = str(raw.get("spotify_id") or "").strip()
    if spotify_id:
        row["spotify_id"] = spotify_id
    return row


def parse_shows(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        row = parse_show(item)
        if row is None:
            continue
        key = f"{row['source']}:{row['id']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


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


async def fetch_shows(
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
) -> list[dict[str, str]] | None:
    target = _rest_target(firebase_config)
    if target is None or http_client is None:
        return None
    url, headers, params = target
    try:
        response = await http_client.get(url, params=params, headers=headers, timeout=8)
    except Exception as exc:
        print(f"[podcasts] catalog read failed: {exc}", flush=True)
        return None
    if response.status_code == 404:
        return []
    if response.status_code >= 400:
        print(f"[podcasts] catalog read HTTP {response.status_code}", flush=True)
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    doc = player_state.decode_document(payload if isinstance(payload, dict) else {})
    return parse_shows(doc.get("shows"))


async def persist_shows(
    shows: list[dict[str, str]],
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
) -> bool:
    target = _rest_target(firebase_config)
    if target is None or http_client is None:
        return False
    url, headers, params = target
    query = list(params)
    query.append(("updateMask.fieldPaths", "shows"))
    try:
        response = await http_client.patch(
            url,
            params=query,
            headers=headers,
            json=player_state.encode_patch({"shows": shows}),
            timeout=8,
        )
    except Exception as exc:
        print(f"[podcasts] catalog write failed: {exc}", flush=True)
        return False
    if response.status_code >= 400:
        print(f"[podcasts] catalog write HTTP {response.status_code}", flush=True)
        return False
    return True


def _remember(shows: list[dict[str, str]]) -> list[dict[str, str]]:
    global _cache, _cache_at, _fingerprint
    _cache = list(shows)
    _cache_at = time.time()
    _fingerprint = fingerprint(_cache)
    return current()


async def hydrate(
    *,
    firebase_config: dict[str, Any],
    http_client: Any,
    force: bool = False,
) -> list[dict[str, str]]:
    if not force and _cache and (time.time() - _cache_at) < REFRESH_S:
        return current()
    remote = await fetch_shows(firebase_config=firebase_config, http_client=http_client)
    if remote is None:
        if not _cache:
            _remember(DEFAULT_SHOWS)
        return current()
    if not remote:
        seeded = list(DEFAULT_SHOWS)
        await persist_shows(seeded, firebase_config=firebase_config, http_client=http_client)
        return _remember(seeded)
    return _remember(remote)
