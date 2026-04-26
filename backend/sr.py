"""Sveriges Radio (SR) API-klient.

SR's offentlige API er pænt dokumenteret på sverigesradio.se/api/documentation/v2.
Vi henter:
  - Program-metadata (navn, cover) via /programs/{id}
  - Afsnitsliste via /episodes/index?programid=…
  - Specifikke afsnit via /episodes/get?id=…

Streamen er en direkte M4A (192 kbps AAC) fra `lyssna-cdn.sr.se` med rate-limiteret URL —
URL'en er kun gyldig i ~10 dage, så vi cacher metadata men slår streamen op igen lige
inden vi sender den til M5'en.
"""
import re
from datetime import datetime, timezone

import httpx

API = "https://api.sr.se/api/v2"

_http = httpx.AsyncClient(
    timeout=8.0,
    headers={"Accept": "application/json"},
)


def _parse_publishdate(blob: object) -> str:
    """SR returnerer datoer som '/Date(1777194000000)/'. Konverter til ISO yyyy-mm-dd."""
    if not blob:
        return ""
    m = re.search(r"\((\d+)", str(blob))
    if not m:
        return ""
    try:
        dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _media_url(ep: dict) -> str:
    """Find M4A streaming-URL i et afsnit-objekt. Foretrækker broadcast file
    (192 kbps AAC), falder tilbage til downloadpodfile hvis ikke til stede."""
    bf = ((ep.get("broadcast") or {}).get("broadcastfiles") or [])
    if bf:
        u = bf[0].get("url")
        if u:
            return u
    return (ep.get("downloadpodfile") or {}).get("url", "") or ""


def _duration_ms(ep: dict) -> int:
    """Returnér afsnittets varighed i ms (broadcastfile.duration er i sekunder)."""
    bf = ((ep.get("broadcast") or {}).get("broadcastfiles") or [])
    if bf:
        d = bf[0].get("duration") or 0
        try:
            return int(d) * 1000
        except Exception:
            return 0
    return 0


def normalize_episode(ep: dict) -> dict:
    """Pak et SR-afsnit til samme schema som vores Spotify-podcast-afsnit
    så frontend ikke behøver vide forskel."""
    sr_id = ep.get("id")
    return {
        "id": str(sr_id) if sr_id is not None else "",
        "uri": f"sr:episode:{sr_id}" if sr_id is not None else "",
        "name": ep.get("title") or "",
        "release_date": _parse_publishdate(ep.get("publishdateutc")),
        "duration_ms": _duration_ms(ep),
    }


async def get_program(program_id: int) -> dict | None:
    """Hent show-metadata. Returnerer dict med (mindst) name + image."""
    try:
        r = await _http.get(f"{API}/programs/{program_id}", params={"format": "json"})
        if r.status_code == 200:
            return (r.json() or {}).get("program")
        print(f"[SR] get_program HTTP {r.status_code}")
    except Exception as e:
        print(f"[SR] get_program error: {e}")
    return None


async def get_latest_episode(program_id: int) -> dict | None:
    """Hent seneste afsnit (rå API-objekt — kald normalize_episode for klient-shape)."""
    try:
        r = await _http.get(
            f"{API}/episodes/index",
            params={"programid": program_id, "format": "json", "page": 1, "size": 1},
        )
        if r.status_code == 200:
            items = (r.json() or {}).get("episodes") or []
            return items[0] if items else None
        print(f"[SR] get_latest_episode HTTP {r.status_code}")
    except Exception as e:
        print(f"[SR] get_latest_episode error: {e}")
    return None


async def get_episodes(
    program_id: int, page: int = 1, size: int = 20
) -> tuple[list[dict], bool]:
    """Pagineret afsnitsliste. Returnerer (rå_items, has_more)."""
    try:
        r = await _http.get(
            f"{API}/episodes/index",
            params={
                "programid": program_id,
                "format": "json",
                "page": max(1, page),
                "size": max(1, min(50, size)),
            },
        )
        if r.status_code == 200:
            d = r.json() or {}
            items = d.get("episodes") or []
            pag = d.get("pagination") or {}
            try:
                cur = int(pag.get("page", page) or page)
                total = int(pag.get("totalpages", 0) or 0)
            except Exception:
                cur, total = page, 0
            return items, cur < total
        print(f"[SR] get_episodes HTTP {r.status_code}")
    except Exception as e:
        print(f"[SR] get_episodes error: {e}")
    return [], False


async def get_episode(episode_id: int | str) -> dict | None:
    """Slå et specifikt afsnit op (bruges når vi skal afspille — vi vil have
    en frisk M4A-URL hver gang, da de TTL'er efter ~10 dage)."""
    try:
        r = await _http.get(
            f"{API}/episodes/get",
            params={"id": episode_id, "format": "json"},
        )
        if r.status_code == 200:
            return (r.json() or {}).get("episode")
        print(f"[SR] get_episode HTTP {r.status_code}")
    except Exception as e:
        print(f"[SR] get_episode error: {e}")
    return None


def episode_media_url(ep: dict) -> str:
    """Public helper: hent M4A-URL fra et rå SR-afsnitobjekt."""
    return _media_url(ep)
