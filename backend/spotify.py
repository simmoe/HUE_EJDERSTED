"""Spotify Web API — OAuth2 PKCE-fri Authorization Code flow + afspilningsstyring."""

import json
import time
import urllib.parse
from pathlib import Path

import httpx

CONFIG_FILE = Path(__file__).parent.parent / "spotify_config.json"
SCOPES = "streaming user-read-email user-read-playback-state user-modify-playback-state user-read-currently-playing"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

# ─── BeoLink multiroom ────────────────────────────────────────────────────────
# M5 = Spotify Connect master, A9 = BeoLink listener
BEO_M5_IP = "192.168.86.188"
BEO_A9_IP = "192.168.86.153"
BEO_A9_JID = "3034.1200366.32115907@products.bang-olufsen.com"
BEO_M5_JID = "2714.1200298.33798625@products.bang-olufsen.com"


def _load() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _save(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


class Spotify:
    def __init__(self):
        self._cfg = _load()
        self._http = httpx.AsyncClient(timeout=5)

    @property
    def configured(self) -> bool:
        return bool(self._cfg.get("client_id"))

    @property
    def authenticated(self) -> bool:
        return bool(self._cfg.get("refresh_token"))

    # ── OAuth flow ────────────────────────────────────────────────────────────

    def login_url(self) -> str:
        params = {
            "client_id": self._cfg["client_id"],
            "response_type": "code",
            "redirect_uri": self._cfg["redirect_uri"],
            "scope": SCOPES,
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def handle_callback(self, code: str) -> bool:
        r = await self._http.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._cfg["redirect_uri"],
            "client_id": self._cfg["client_id"],
            "client_secret": self._cfg["client_secret"],
        })
        if r.status_code != 200:
            return False
        data = r.json()
        self._cfg["access_token"] = data["access_token"]
        self._cfg["refresh_token"] = data["refresh_token"]
        self._cfg["expires_at"] = time.time() + data["expires_in"] - 60
        _save(self._cfg)
        return True

    async def _ensure_token(self) -> str | None:
        if not self._cfg.get("refresh_token"):
            return None
        if time.time() < self._cfg.get("expires_at", 0):
            return self._cfg["access_token"]
        r = await self._http.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self._cfg["refresh_token"],
            "client_id": self._cfg["client_id"],
            "client_secret": self._cfg["client_secret"],
        })
        if r.status_code != 200:
            return None
        data = r.json()
        self._cfg["access_token"] = data["access_token"]
        if "refresh_token" in data:
            self._cfg["refresh_token"] = data["refresh_token"]
        self._cfg["expires_at"] = time.time() + data["expires_in"] - 60
        _save(self._cfg)
        return self._cfg["access_token"]

    async def _headers(self) -> dict | None:
        token = await self._ensure_token()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    # ── API calls ─────────────────────────────────────────────────────────────

    async def search(self, query: str, types: str = "track,artist,album,playlist", limit: int = 5) -> dict | None:
        h = await self._headers()
        if not h:
            return None
        r = await self._http.get(f"{API}/search", headers=h, params={
            "q": query, "type": types, "limit": limit, "market": "DK",
        })
        return r.json() if r.status_code == 200 else None

    async def play(self, uri: str | None = None, context_uri: str | None = None, device_id: str | None = None) -> bool:
        h = await self._headers()
        if not h:
            return False
        params = {}
        if device_id:
            params["device_id"] = device_id
        body: dict = {}
        if context_uri:
            body["context_uri"] = context_uri
        elif uri:
            body["uris"] = [uri]
        r = await self._http.put(f"{API}/me/player/play", headers=h, params=params, json=body if body else None)
        return r.status_code in (200, 204)

    async def pause(self) -> bool:
        h = await self._headers()
        if not h:
            return False
        r = await self._http.put(f"{API}/me/player/pause", headers=h)
        return r.status_code in (200, 204)

    async def skip(self) -> bool:
        h = await self._headers()
        if not h:
            return False
        r = await self._http.post(f"{API}/me/player/next", headers=h)
        return r.status_code in (200, 204)

    async def previous(self) -> bool:
        h = await self._headers()
        if not h:
            return False
        r = await self._http.post(f"{API}/me/player/previous", headers=h)
        return r.status_code in (200, 204)

    async def now_playing(self) -> dict | None:
        h = await self._headers()
        if not h:
            return None
        r = await self._http.get(f"{API}/me/player/currently-playing", headers=h)
        if r.status_code == 200:
            data = r.json()
            item = data.get("item", {})
            result = {
                "name": item.get("name", ""),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                "album": item.get("album", {}).get("name", ""),
                "image": (item.get("album", {}).get("images", [{}])[0].get("url", "") if item.get("album", {}).get("images") else ""),
                "is_playing": data.get("is_playing", False),
                "uri": item.get("uri", ""),
                "progress_ms": data.get("progress_ms", 0),
                "next_name": "",
                "next_artist": "",
            }
            try:
                qr = await self._http.get(f"{API}/me/player/queue", headers=h)
                if qr.status_code == 200:
                    queue = qr.json().get("queue", [])
                    if queue:
                        nxt = queue[0]
                        result["next_name"] = nxt.get("name", "")
                        result["next_artist"] = ", ".join(
                            a["name"] for a in nxt.get("artists", [])
                        )
            except Exception:
                pass
            return result
        return None

    async def devices(self) -> list:
        h = await self._headers()
        if not h:
            return []
        r = await self._http.get(f"{API}/me/player/devices", headers=h)
        if r.status_code == 200:
            return [
                {"id": d["id"], "name": d["name"], "type": d["type"], "is_active": d["is_active"]}
                for d in r.json().get("devices", [])
            ]
        return []

    async def _beolink_expand(self) -> None:
        """Tell A9 to stream from M5's Spotify source + nudge M5 volume to wake audio."""
        import asyncio
        try:
            M5_SPOTIFY = f"spotify:{BEO_M5_JID}"
            r = await self._http.post(
                f"http://{BEO_A9_IP}:8080/BeoZone/Zone/ActiveSources",
                json={"primaryExperience": {"source": {
                    "id": M5_SPOTIFY,
                    "product": {"jid": BEO_M5_JID, "friendlyName": "Beoplay M5"},
                }}},
            )
            if r.status_code < 300:
                print("[BeoLink] A9 joined M5 Spotify source")
            else:
                print(f"[BeoLink] A9 join failed: {r.status_code} {r.text}")

            # Nudge M5 volume to wake audio stream to A9
            await asyncio.sleep(0.5)
            vol = await self._http.get(f"http://{BEO_M5_IP}:8080/BeoZone/Zone/Sound/Volume")
            if vol.status_code == 200:
                level = vol.json().get("volume", {}).get("speaker", {}).get("level", 45)
                await self._http.put(
                    f"http://{BEO_M5_IP}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level",
                    json={"level": level},
                )
        except Exception as e:
            print(f"[BeoLink] expand error: {e}")

    async def _find_speaker_device_id(self) -> str | None:
        """Find the BeoPlay M5's Spotify Connect device ID (A9 has no Spotify Connect)."""
        devs = await self.devices()
        for d in devs:
            if "m5" in d["name"].lower() or "beoplay m5" in d["name"].lower():
                return d["id"]
        # Fallback: pick first Speaker
        for d in devs:
            if d["type"] == "Speaker":
                return d["id"]
        return devs[0]["id"] if devs else None

    async def resume(self) -> bool:
        """Resume playback on M5 + ensure BeoLink multiroom."""
        device_id = await self._find_speaker_device_id()
        ok = await self.play(device_id=device_id)
        await self._beolink_expand()
        return ok

    async def start_radio(self) -> dict:
        """Build a radio queue via /search — the only reliable method post-2024."""
        import random
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        np = await self.now_playing()
        if not np or not np.get("uri"):
            return {"ok": False, "error": "Intet spiller"}

        artist_name = np.get("artist", "")
        track_name = np.get("name", "")
        current_uri = np["uri"]
        progress_ms = np.get("progress_ms", 0)

        uris: list[str] = []

        async def _search(q: str, pages: int = 5) -> list[str]:
            """Paginate /search (limit=10 per page for dev-mode apps)."""
            found: list[str] = []
            for off in range(0, pages * 10, 10):
                r = await self._http.get(
                    f"{API}/search", headers=h,
                    params={"q": q, "type": "track", "limit": 10,
                            "offset": off, "market": "DK"},
                )
                if r.status_code != 200:
                    break
                for t in r.json().get("tracks", {}).get("items", []):
                    found.append(t["uri"])
            return found

        # 1) Search: "artist track" — best similarity signal
        if artist_name and track_name:
            uris.extend(await _search(f"{artist_name} {track_name}", pages=3))

        # 2) Search: just artist name — fills with discography
        if artist_name:
            uris.extend(await _search(f"{artist_name}", pages=3))

        # Deduplicate, remove current track, shuffle
        seen: set[str] = set()
        unique: list[str] = []
        for u in uris:
            if u not in seen and u != current_uri:
                seen.add(u)
                unique.append(u)
        random.shuffle(unique)
        # Prepend current track so playback continues seamlessly
        unique.insert(0, current_uri)

        if len(unique) < 2:
            return {"ok": False, "error": "Ingen resultater for denne artist"}

        device_id = await self._find_speaker_device_id()
        r = await self._http.put(
            f"{API}/me/player/play",
            headers=h,
            params={"device_id": device_id} if device_id else {},
            json={"uris": unique[:50], "offset": {"position": 0}, "position_ms": progress_ms},
        )
        ok = r.status_code in (200, 204)
        if ok:
            await self._beolink_expand()
        return {"ok": ok, "name": f"Radio: {artist_name}", "tracks": len(unique[:50])}

    async def stop_radio(self) -> dict:
        """Clear radio queue — play only the current track from its current position."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        np = await self.now_playing()
        if not np or not np.get("uri"):
            return {"ok": True}
        device_id = await self._find_speaker_device_id()
        r = await self._http.put(
            f"{API}/me/player/play",
            headers=h,
            params={"device_id": device_id} if device_id else {},
            json={"uris": [np["uri"]], "position_ms": np.get("progress_ms", 0)},
        )
        return {"ok": r.status_code in (200, 204)}

    async def voice_command(self, transcript: str) -> dict:
        """Parse a voice transcript and execute the most likely intent."""
        t = transcript.lower().strip()

        # Stop / pause
        if t in ("stop", "pause", "stil", "stop musik", "pause musik"):
            ok = await self.pause()
            return {"action": "pause", "ok": ok}

        # Skip
        if t in ("skip", "næste", "next", "skip sang"):
            ok = await self.skip()
            return {"action": "skip", "ok": ok}

        # Previous
        if t in ("tilbage", "previous", "forrige"):
            ok = await self.previous()
            return {"action": "previous", "ok": ok}

        # Resume
        if t in ("play", "spil", "afspil", "fortsæt"):
            device_id = await self._find_speaker_device_id()
            await self._beolink_expand()
            ok = await self.play(device_id=device_id)
            return {"action": "resume", "ok": ok}

        # Search & play — strip leading "spil"/"play"/"sæt .. på"
        query = t
        for prefix in ("spil ", "play ", "afspil ", "sæt ", "put on "):
            if t.startswith(prefix):
                query = t[len(prefix):]
                break
        query = query.removesuffix(" på").removesuffix(" on")

        # Album keyword — "album X" forces album search
        force_album = False
        for album_prefix in ("album ", "albummet ", "albumet "):
            if query.startswith(album_prefix):
                query = query[len(album_prefix):]
                force_album = True
                break

        results = await self.search(query, types="track,artist,album,playlist", limit=3)
        if not results:
            return {"action": "search", "ok": False, "query": query}

        # Prioritise: track → artist → album → playlist
        tracks = results.get("tracks", {}).get("items", [])
        artists = results.get("artists", {}).get("items", [])
        albums = results.get("albums", {}).get("items", [])
        playlists = results.get("playlists", {}).get("items", [])

        # Always prefer M5 as target — BeoLink expand BEFORE Spotify play
        device_id = await self._find_speaker_device_id()
        await self._beolink_expand()

        result: dict | None = None

        if force_album and albums:
            album = albums[0]
            ok = await self.play(context_uri=album["uri"], device_id=device_id)
            result = {"action": "play_album", "ok": ok, "name": album["name"],
                      "artist": ", ".join(a["name"] for a in album.get("artists", []))}
        elif tracks and not force_album:
            track = tracks[0]
            ok = await self.play(uri=track["uri"], device_id=device_id)
            result = {"action": "play_track", "ok": ok,
                      "name": track["name"],
                      "artist": ", ".join(a["name"] for a in track.get("artists", []))}
        elif artists and not force_album:
            artist = artists[0]
            ok = await self.play(context_uri=artist["uri"], device_id=device_id)
            result = {"action": "play_artist", "ok": ok, "name": artist["name"]}
        elif albums:
            album = albums[0]
            ok = await self.play(context_uri=album["uri"], device_id=device_id)
            result = {"action": "play_album", "ok": ok, "name": album["name"],
                      "artist": ", ".join(a["name"] for a in album.get("artists", []))}
        elif playlists:
            pl = playlists[0]
            ok = await self.play(context_uri=pl["uri"], device_id=device_id)
            result = {"action": "play_playlist", "ok": ok, "name": pl["name"]}
        else:
            return {"action": "search", "ok": False, "query": query}

        return result
