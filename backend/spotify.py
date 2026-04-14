"""Spotify Web API — OAuth2 PKCE-fri Authorization Code flow + afspilningsstyring."""

import json
import os
import time
import urllib.parse
from pathlib import Path

import httpx

CONFIG_FILE = Path(__file__).parent.parent / "spotify_config.json"
GEMINI_KEY_FILE = Path(__file__).parent.parent / "gemini_api_key.txt"
SCOPES = (
    "streaming user-read-email user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing user-library-read user-library-modify "
    "playlist-read-private playlist-modify-private"
)
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

# ─── Gemini (LLM-powered radio) ──────────────────────────────────────────────
# Key: env GEMINI_API_KEY or gemini_api_key in spotify_config.json (never commit keys).
GEMINI_MODEL = "gemini-2.5-flash"

LISTENER_PROFILE = """\
A musician and deep listener. Taste is rooted in folk, soul, reggae and the \
singer-songwriter tradition (Bonnie Prince Billy, Joan Armatrading, Bob Dylan, \
This Is The Kit) but stretches from Arvo Pärt to aggressive metal without \
contradiction. The common thread is melodic intelligence — the blue note, \
minor/major tension, bittersweet hooks. Never bright, never obvious.
Core loves: Jeff Buckley, D'Angelo, Bob Marley, Taj Mahal, H.E.R., Boards of \
Canada, Cigarettes After Sex, Above & Beyond Acoustic (Zoë Johnston), Nusrat \
Fateh Ali Khan, This Is The Kit. Recently discovered Burna Boy.
Open to any language and any continent — qawwali, fado, French rap, Middle \
Eastern folk, Afrobeats. Values mood continuity above all — a radio playlist \
should drift naturally, never jolt. Discovery is welcome but should feel like \
the next logical step.
Dislikes: generic college rock, try-hard energy, anything normatively loud and \
empty. Not a Queen or Prince person. No Danish "poetic" rap.
Emotional register: depth, warmth, darkness, devotion, yearning. The sad/deep \
song always wins over the happy/sharp one."""

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
        self._gemini_http = httpx.AsyncClient(timeout=60)

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
        device_id = await self._target_device_id()
        params = {"device_id": device_id} if device_id else {}
        r = await self._http.put(f"{API}/me/player/pause", headers=h, params=params)
        return r.status_code in (200, 204)

    async def _post_player_next(self) -> bool:
        h = await self._headers()
        if not h:
            return False
        device_id = await self._target_device_id()
        params = {"device_id": device_id} if device_id else {}
        r = await self._http.post(f"{API}/me/player/next", headers=h, params=params)
        return r.status_code in (200, 204)

    async def _post_player_previous(self) -> bool:
        h = await self._headers()
        if not h:
            return False
        device_id = await self._target_device_id()
        params = {"device_id": device_id} if device_id else {}
        r = await self._http.post(f"{API}/me/player/previous", headers=h, params=params)
        if r.status_code not in (200, 204):
            print(f"[Spotify] previous HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code in (200, 204)

    async def _seek_track(self, position_ms: int = 0) -> bool:
        h = await self._headers()
        if not h:
            return False
        device_id = await self._target_device_id()
        params: dict = {"position_ms": position_ms}
        if device_id:
            params["device_id"] = device_id
        r = await self._http.put(f"{API}/me/player/seek", headers=h, params=params)
        return r.status_code in (200, 204)

    async def play_uris_queue(self, uris: list[str], offset: int = 0, position_ms: int = 0) -> bool:
        """Start afspilning udelukkende fra en eksplicit uri-liste (kioskens lokale kø)."""
        uris = [u for u in uris if u and u.startswith("spotify:track:")][:50]
        if not uris:
            return False
        h = await self._headers()
        if not h:
            return False
        off = max(0, min(offset, len(uris) - 1))
        device_id = await self._find_speaker_device_id()
        r = await self._http.put(
            f"{API}/me/player/play",
            headers=h,
            params={"device_id": device_id} if device_id else {},
            json={"uris": uris, "offset": {"position": off}, "position_ms": position_ms},
        )
        ok = r.status_code in (200, 204)
        if ok:
            await self._beolink_expand()
        return ok

    async def skip(self) -> bool:
        return await self._post_player_next()

    async def previous(self) -> bool:
        return await self._post_player_previous()

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
                {
                    "id": d["id"],
                    "name": d["name"],
                    "type": d["type"],
                    "is_active": d["is_active"],
                    "is_restricted": d.get("is_restricted", False),
                }
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

    async def _target_device_id(self) -> str | None:
        """Device for skip/pause/previous: samme som GET /me/player, ellers aktiv/M5."""
        h = await self._headers()
        if not h:
            return None
        player_data = None
        pr = await self._http.get(f"{API}/me/player", headers=h)
        if pr.status_code == 200:
            player_data = pr.json()
            dev = (player_data or {}).get("device") or {}
            did = dev.get("id")
            if did and not dev.get("is_restricted"):
                return did
        devs = await self.devices()
        for d in devs:
            if d.get("is_active") and not d.get("is_restricted"):
                return d["id"]
        for d in devs:
            if d.get("is_active"):
                return d["id"]
        did_fb = await self._find_speaker_device_id()
        if did_fb:
            return did_fb
        if player_data:
            did = (player_data.get("device") or {}).get("id")
            if did:
                return did
        return None

    async def resume(self) -> bool:
        """Resume playback on M5 + ensure BeoLink multiroom."""
        device_id = await self._find_speaker_device_id()
        ok = await self.play(device_id=device_id)
        await self._beolink_expand()
        return ok

    def _gemini_api_key(self) -> str:
        """Env, så filen gemini_api_key.txt (én linje, gitignored), så spotify_config.json."""
        env = (
            os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GEMINI_KEY", "")
        ).strip()
        if env:
            return env
        if GEMINI_KEY_FILE.is_file():
            try:
                line = GEMINI_KEY_FILE.read_text(encoding="utf-8").strip().splitlines()
                if line and not line[0].startswith("#"):
                    return line[0].strip()
            except OSError:
                pass
        disk = _load()
        return str(disk.get("gemini_api_key") or "").strip()

    async def _ask_gemini(self, artist: str, track: str, n: int = 10) -> list[dict]:
        """Ask Gemini for track recommendations. Returns list of {artist, track}."""
        key = self._gemini_api_key()
        if not key:
            print(
                "[Gemini] mangler nøgle: sæt GEMINI_API_KEY eller "
                "gemini_api_key i spotify_config.json"
            )
            return []
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={key}"
        )
        prompt = (
            f'I\'m listening to "{track}" by {artist}.\n'
            f'Suggest {n} songs that match the mood, style and vibe of this specific recording.\n'
            f'Think about: tempo, instrumentation, vocal style, era, genre, emotional tone.\n'
            f'Don\'t just pick songs by the same artist — cast a wide net across artists who share this feel.\n\n'
            f'LISTENER PROFILE — tailor recommendations to this person:\n'
            f'{LISTENER_PROFILE}\n\n'
            f'Return ONLY a JSON array, no markdown, no explanation. '
            f'Each element: {{"artist": "...", "track": "..."}}'
        )
        try:
            r = await self._gemini_http.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7},
            })
            if r.status_code != 200:
                print(f"[Gemini] error: {r.status_code} {r.text[:200]}")
                return []
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            return json.loads(text.strip())
        except Exception as e:
            print(f"[Gemini] error: {e}")
            return []

    async def _find_track_meta(self, artist: str, track: str) -> dict | None:
        """Søg ét track; returner uri + visningsfelter til radio-kø."""
        h = await self._headers()
        if not h:
            return None
        r = await self._http.get(f"{API}/search", headers=h, params={
            "q": f"track:{track} artist:{artist}",
            "type": "track", "limit": 1, "market": "DK",
        })
        if r.status_code != 200:
            return None
        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            return None
        t = items[0]
        return {
            "uri": t["uri"],
            "name": t.get("name") or track,
            "artist": ", ".join(a["name"] for a in t.get("artists", [])),
        }

    async def _find_track_uri(self, artist: str, track: str) -> str | None:
        m = await self._find_track_meta(artist, track)
        return m["uri"] if m else None

    async def build_radio_queue(
        self, seed_uri: str, seed_name: str = "", seed_artist: str = ""
    ) -> dict:
        """Byg Song Radio-kø ud fra et seed-track (ingen afspilning — kun metadata)."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        if not seed_uri or not seed_uri.startswith("spotify:track:"):
            return {"ok": False, "error": "Mangler seed (vælg et track i mikrofon-køen)"}

        artist_name = seed_artist
        track_name = seed_name
        current_uri = seed_uri
        if not track_name or not artist_name:
            tid = seed_uri.split(":")[-1]
            tr = await self._http.get(f"{API}/tracks/{tid}", headers=h, params={"market": "DK"})
            if tr.status_code == 200:
                tj = tr.json()
                track_name = track_name or tj.get("name", "")
                artist_name = artist_name or ", ".join(
                    a["name"] for a in tj.get("artists", [])
                )

        if not self._gemini_api_key():
            return {
                "ok": False,
                "error": "Radio: mangler Gemini-nøgle på serveren (gemini_api_key i spotify_config.json eller GEMINI_API_KEY)",
            }
        suggestions = await self._ask_gemini(artist_name, track_name)
        if not suggestions:
            return {
                "ok": False,
                "error": "Radio: Gemini svarede ikke (nøgle/udgående net på Pi?)",
            }

        tracks_meta: list[dict] = [
            {"uri": current_uri, "name": track_name, "artist": artist_name},
        ]
        for s in suggestions:
            meta = await self._find_track_meta(s["artist"], s["track"])
            if meta and meta["uri"] != current_uri:
                tracks_meta.append(meta)
                print(f"[Radio] ✓ {s['track']} — {s['artist']}")
            else:
                print(f"[Radio] ✗ {s['track']} — {s['artist']}")

        if len(tracks_meta) < 2:
            return {"ok": False, "error": "Kunne ikke finde nok sange"}

        uris = [t["uri"] for t in tracks_meta]
        return {
            "ok": True,
            "name": f"Radio: {artist_name}",
            "tracks": len(uris),
            "queue": tracks_meta,
        }

    async def stop_radio(self) -> dict:
        """Kun klient-state; ingen Spotify-kald."""
        return {"ok": True}

    async def _album_track_rows(self, album_id: str, h: dict) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while offset < 500:
            r = await self._http.get(
                f"{API}/albums/{album_id}/tracks",
                headers=h,
                params={"limit": 50, "offset": offset, "market": "DK"},
            )
            if r.status_code != 200:
                break
            data = r.json()
            items = data.get("items", [])
            for item in items:
                tid = item.get("id")
                uri = item.get("uri") or (f"spotify:track:{tid}" if tid else "")
                if not uri:
                    continue
                rows.append({
                    "uri": uri,
                    "name": item.get("name", ""),
                    "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                })
            if len(items) < 50:
                break
            offset += 50
        return rows

    async def build_album_queue_from_track_uri(self, track_uri: str) -> dict:
        """Alle spor på albummet for et track-uri (ingen afspilning)."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        if not track_uri or "track" not in track_uri:
            return {"ok": False, "error": "Mangler track"}
        track_id = track_uri.split(":")[-1]
        tr = await self._http.get(f"{API}/tracks/{track_id}", headers=h, params={"market": "DK"})
        if tr.status_code != 200:
            return {"ok": False, "error": "Kunne ikke hente track"}
        album = tr.json().get("album") or {}
        album_uri = album.get("uri") or ""
        album_name = album.get("name", "")
        album_id = album_uri.split(":")[-1] if album_uri else ""
        if not album_id:
            return {"ok": False, "error": "Intet album fundet"}
        rows = await self._album_track_rows(album_id, h)
        if not rows:
            return {"ok": False, "error": "Tomt album"}
        return {"ok": True, "album": album_name, "queue": rows}

    # Default kiosk playlist (POST /me/playlists). Override in spotify_config.json only if needed.
    _FAVORITES_PLAYLIST_ID_DEFAULT = "0lQTaldhDsvMlLAEctDX81"
    _FAVORITES_PLAYLIST_NAME = "Ejdersted Favorites"
    _FAVORITES_PLAYLIST_DESC = "Tracks gemt fra kiosken (plus Liked Songs)."

    def _favorites_playlist_id_resolved(self) -> str:
        pid = self._cfg.get("favorites_playlist_id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
        return self._FAVORITES_PLAYLIST_ID_DEFAULT

    async def _append_favorites_playlist(self, h: dict, track_uri: str) -> None:
        """Append track; if default playlist is missing (404), create one and persist id."""
        pl_id = self._favorites_playlist_id_resolved()
        r = await self._http.post(
            f"{API}/playlists/{pl_id}/items",
            headers=h,
            json={"uris": [track_uri]},
        )
        if r.status_code in (200, 201):
            return
        if r.status_code != 404:
            return
        cr = await self._http.post(
            f"{API}/me/playlists",
            headers=h,
            json={
                "name": self._FAVORITES_PLAYLIST_NAME,
                "public": False,
                "description": self._FAVORITES_PLAYLIST_DESC,
            },
        )
        if cr.status_code != 201:
            return
        new_id = cr.json().get("id")
        if not new_id:
            return
        self._cfg["favorites_playlist_id"] = new_id
        _save(self._cfg)
        await self._http.post(
            f"{API}/playlists/{new_id}/items",
            headers=h,
            json={"uris": [track_uri]},
        )

    async def is_track_saved(self, track_uri: str | None = None) -> bool:
        """Check via GET /me/library/contains whether a track is in Liked Songs."""
        h = await self._headers()
        if not h:
            return False
        if not track_uri:
            np = await self.now_playing()
            if not np or not np.get("uri"):
                return False
            track_uri = np["uri"]
        r = await self._http.get(
            f"{API}/me/library/contains", headers=h, params={"uris": track_uri})
        if r.status_code == 200:
            vals = r.json()
            return vals[0] if vals else False
        return False

    async def save_track(self, track_uri: str | None = None) -> dict:
        """Toggle save for et track-uri (kiosk sender uri) eller nuværende afspilning."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        if not track_uri:
            np = await self.now_playing()
            if not np or not np.get("uri"):
                return {"ok": False, "error": "Intet track"}
            track_uri = np["uri"]
        saved = await self.is_track_saved(track_uri)
        if saved:
            r = await self._http.delete(
                f"{API}/me/library", headers=h, params={"uris": track_uri})
            ok = r.status_code == 200
            return {"ok": ok, "saved": not ok}
        else:
            r = await self._http.put(
                f"{API}/me/library", headers=h, params={"uris": track_uri})
            ok = r.status_code == 200
            if ok:
                await self._append_favorites_playlist(h, track_uri)
            return {"ok": ok, "saved": ok}

    async def build_album_queue_from_album_uri(self, album_uri: str) -> dict:
        """Alle spor på et album-uri (spotify:album:…)."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        if not album_uri or "album" not in album_uri:
            return {"ok": False, "error": "Mangler album-uri"}
        album_id = album_uri.split(":")[-1]
        ar = await self._http.get(f"{API}/albums/{album_id}", headers=h, params={"market": "DK"})
        album_name = ""
        if ar.status_code == 200:
            album_name = ar.json().get("name", "")
        rows = await self._album_track_rows(album_id, h)
        if not rows:
            return {"ok": False, "error": "Tomt album"}
        return {"ok": True, "album": album_name, "queue": rows}

    async def build_artist_top_queue(self, artist_uri: str) -> dict:
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        aid = artist_uri.split(":")[-1]
        r = await self._http.get(
            f"{API}/artists/{aid}/top-tracks", headers=h, params={"market": "DK"},
        )
        if r.status_code != 200:
            return {"ok": False, "error": "Kunne ikke hente artist"}
        rows: list[dict] = []
        for tr in r.json().get("tracks", []) or []:
            rows.append({
                "uri": tr.get("uri", ""),
                "name": tr.get("name", ""),
                "artist": ", ".join(a["name"] for a in tr.get("artists", [])),
            })
        rows = [x for x in rows if x["uri"]]
        if not rows:
            return {"ok": False, "error": "Ingen top-sange"}
        return {"ok": True, "queue": rows}

    async def build_playlist_queue(self, playlist_uri: str, limit: int = 80) -> dict:
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        pid = playlist_uri.split(":")[-1]
        rows: list[dict] = []
        offset = 0
        while len(rows) < limit:
            r = await self._http.get(
                f"{API}/playlists/{pid}/tracks",
                headers=h,
                params={"limit": 50, "offset": offset, "market": "DK"},
            )
            if r.status_code != 200:
                break
            for item in r.json().get("items", []):
                tr = item.get("track") or {}
                if not tr or tr.get("is_local"):
                    continue
                uri = tr.get("uri") or ""
                if not uri.startswith("spotify:track:"):
                    continue
                rows.append({
                    "uri": uri,
                    "name": tr.get("name", ""),
                    "artist": ", ".join(a["name"] for a in tr.get("artists", [])),
                })
                if len(rows) >= limit:
                    break
            if len(r.json().get("items", [])) < 50:
                break
            offset += 50
        if not rows:
            return {"ok": False, "error": "Tom playlist"}
        return {"ok": True, "queue": rows}

    async def voice_command(self, transcript: str) -> dict:
        """Stemme: kun kø-metadata til klienten — ingen afspilning herfra."""
        t = transcript.lower().strip()

        if t in ("stop", "pause", "stil", "stop musik", "pause musik"):
            ok = await self.pause()
            return {"action": "pause", "ok": ok}

        if t in ("skip", "næste", "next", "skip sang"):
            return {"action": "local_nav", "ok": True, "delta": 1}

        if t in ("tilbage", "previous", "forrige"):
            return {"action": "local_nav", "ok": True, "delta": -1}

        if t in ("play", "spil", "afspil", "fortsæt") and len(t.split()) <= 1:
            return {"action": "use_play_button", "ok": True}

        query = t
        for prefix in ("spil ", "play ", "afspil ", "sæt ", "put on "):
            if t.startswith(prefix):
                query = t[len(prefix):]
                break
        query = query.removesuffix(" på").removesuffix(" on")

        force_album = False
        for album_prefix in ("album ", "albummet ", "albumet "):
            if query.startswith(album_prefix):
                query = query[len(album_prefix):]
                force_album = True
                break

        results = await self.search(query, types="track,artist,album,playlist", limit=3)
        if not results:
            return {"action": "search", "ok": False, "query": query}

        tracks = results.get("tracks", {}).get("items", [])
        artists = results.get("artists", {}).get("items", [])
        albums = results.get("albums", {}).get("items", [])
        playlists = results.get("playlists", {}).get("items", [])

        if force_album and albums:
            inner = await self.build_album_queue_from_album_uri(albums[0]["uri"])
            if not inner.get("ok"):
                return {**inner, "action": "enqueue_queue"}
            return {
                "action": "enqueue_queue",
                "ok": True,
                "queue": inner["queue"],
                "label": inner.get("album", albums[0].get("name", "")),
            }

        if tracks and not force_album:
            track = tracks[0]
            return {
                "action": "enqueue",
                "ok": True,
                "uri": track["uri"],
                "name": track["name"],
                "artist": ", ".join(a["name"] for a in track.get("artists", [])),
            }

        if artists and not force_album:
            inner = await self.build_artist_top_queue(artists[0]["uri"])
            if not inner.get("ok"):
                return {**inner, "action": "enqueue_queue"}
            return {
                "action": "enqueue_queue",
                "ok": True,
                "queue": inner["queue"],
                "label": artists[0].get("name", ""),
            }

        if albums:
            inner = await self.build_album_queue_from_album_uri(albums[0]["uri"])
            if not inner.get("ok"):
                return {**inner, "action": "enqueue_queue"}
            return {
                "action": "enqueue_queue",
                "ok": True,
                "queue": inner["queue"],
                "label": inner.get("album", albums[0].get("name", "")),
            }

        if playlists:
            inner = await self.build_playlist_queue(playlists[0]["uri"])
            if not inner.get("ok"):
                return {**inner, "action": "enqueue_queue"}
            return {
                "action": "enqueue_queue",
                "ok": True,
                "queue": inner["queue"],
                "label": playlists[0].get("name", ""),
            }

        return {"action": "search", "ok": False, "query": query}
