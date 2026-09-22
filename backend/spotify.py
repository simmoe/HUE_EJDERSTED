"""Spotify Web API — OAuth2 PKCE-fri Authorization Code flow + afspilningsstyring."""

import asyncio
import base64
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import httpx

import hub_config

CONFIG_FILE = Path(__file__).parent.parent / "spotify_config.json"
GEMINI_KEY_FILE = Path(__file__).parent.parent / "gemini_api_key.txt"
# Web Playback SDK kræver bl.a. `streaming` + `user-read-private` (melody/check_scope web-playback).
# Efter scope-ændring: besøg /api/spotify/login én gang så refresh_token får de nye rettigheder.
SCOPES = (
    "streaming user-read-email user-read-private user-read-playback-state "
    "user-modify-playback-state user-read-currently-playing user-library-read "
    "user-library-modify playlist-read-private playlist-modify-private"
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
BEO_M5_IP = "192.168.86.21"
BEO_A9_IP = "192.168.86.20"
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


def _spotify_error(r: httpx.Response) -> str:
    detail = ""
    try:
        body = r.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or "")
            elif isinstance(err, str):
                detail = err
    except Exception:
        detail = r.text[:120]
    retry_after = r.headers.get("retry-after")
    if r.status_code == 429 and retry_after:
        detail = f"{detail} - prøv igen om {retry_after}s" if detail else f"prøv igen om {retry_after}s"
    suffix = f": {detail}" if detail else ""
    return f"{r.status_code}{suffix}"


def _usable_spotify_episodes(items: list) -> list[dict]:
    """Spotify often pads /shows/{id}/episodes with null entries for unavailable markets."""
    usable: list[dict] = []
    for item in items:
        if isinstance(item, dict) and item.get("id") and item.get("uri"):
            usable.append(item)
    return usable


def _looks_like_m5(device: dict) -> bool:
    name = str(device.get("name") or "").lower()
    return "m5" in name or "beoplay m5" in name


_TITLE_ARTIST_SPLIT = re.compile(r"^(?P<title>.+?)\s+(?:by|af|med|with)\s+(?P<artist>.+)$", re.IGNORECASE)


def pick_best_track(query: str, tracks: list[dict]) -> dict | None:
    """Prefer a track whose title/artist actually appear in the spoken query."""
    q = re.sub(r"\s+", " ", (query or "").lower()).strip()
    valid = [t for t in tracks if isinstance(t, dict) and t.get("uri")]
    if not valid:
        return None

    def score(track: dict) -> int:
        name = str(track.get("name") or "").lower()
        artists = " ".join(
            str(a.get("name") or "") for a in track.get("artists") or [] if isinstance(a, dict)
        ).lower()
        points = 0
        if name and name in q:
            points += 3
        if artists and artists in q:
            points += 3
        for token in re.findall(r"[a-z0-9']{3,}", artists):
            if token in q:
                points += 1
        return points

    ranked = sorted(valid, key=score, reverse=True)
    return ranked[0] if score(ranked[0]) > 0 else valid[0]


def split_title_artist(query: str) -> tuple[str, str] | None:
    """'keep going by this is the kit' → ('keep going', 'this is the kit').
    First separator wins, so a title containing 'by' still splits on the
    earliest one; the caller falls back to the raw query if nothing matches."""
    m = _TITLE_ARTIST_SPLIT.match(query.strip())
    if not m:
        return None
    title = m.group("title").strip().strip('"')
    artist = m.group("artist").strip().strip('"')
    if len(title) < 2 or len(artist) < 2:
        return None
    return title, artist


class Spotify:
    def __init__(self):
        self._cfg = _load()
        self._http = httpx.AsyncClient(timeout=5)
        self._gemini_http = httpx.AsyncClient(timeout=60)
        self._last_connect_restart = 0.0
        self._m5_device_id = ""
        self._m5_device_at = 0.0
        self._spotify_user_id_cache = ""
        self._queue_task: asyncio.Task[None] | None = None

    @property
    def configured(self) -> bool:
        return bool(self._cfg.get("client_id"))

    @property
    def authenticated(self) -> bool:
        return bool(self._cfg.get("refresh_token"))

    @property
    def granted_scope(self) -> str:
        """Seneste scope-streng fra Spotify (code eller refresh). Tom indtil næste token-svindel."""
        return str(self._cfg.get("granted_scope") or "").strip()

    async def access_token_for_web_playback(self) -> str | None:
        """Sikrer access_token; hvis `granted_scope` aldrig er gemt, tvinger én refresh så Spotify returnerer `scope`."""
        if self._cfg.get("refresh_token") and not (self._cfg.get("granted_scope") or "").strip():
            self._cfg["expires_at"] = 0
            _save(self._cfg)
        return await self._ensure_token()

    # ── OAuth flow ────────────────────────────────────────────────────────────

    def login_url(self) -> str:
        params = {
            "client_id": self._cfg["client_id"],
            "response_type": "code",
            "redirect_uri": self._cfg["redirect_uri"],
            "scope": SCOPES,
            # Tving samtykke igen så nye scopes (fx streaming) skrives på refresh_token.
            "show_dialog": "true",
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
        if isinstance(data.get("scope"), str) and data["scope"].strip():
            self._cfg["granted_scope"] = data["scope"].strip()
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
        if isinstance(data.get("scope"), str) and data["scope"].strip():
            self._cfg["granted_scope"] = data["scope"].strip()
        _save(self._cfg)
        return self._cfg["access_token"]

    async def _headers(self) -> dict | None:
        token = await self._ensure_token()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    def _garden_player_base(self) -> str:
        return hub_config.garden_player_url()

    async def _garden_player_ready(self) -> bool:
        try:
            response = await self._http.get(f"{self._garden_player_base()}/")
            return response.status_code == 200 and bool((response.json() or {}).get("playback_ready"))
        except Exception:
            return False

    async def _garden_player_post(self, path: str, body: dict | None = None) -> bool:
        try:
            response = await self._http.post(f"{self._garden_player_base()}{path}", json=body)
            return response.status_code == 200
        except Exception as exc:
            print(f"[Spotify] garden player {path} failed: {exc}")
            return False

    async def _ensure_garden_player(self) -> bool:
        if await self._garden_player_ready():
            return True
        if not await self._systemctl_librespot("start"):
            return await self._garden_player_ready()
        for _ in range(15):
            await asyncio.sleep(1)
            if await self._garden_player_ready():
                return True
        return False

    async def _play_garden_uris(
        self,
        uris: list[str],
        offset: int,
        position_ms: int,
    ) -> tuple[bool, str, int]:
        playable = [
            uri
            for uri in uris
            if uri.startswith("spotify:track:") or uri.startswith("spotify:episode:")
        ]
        if not playable:
            return False, "no valid uris", 0
        if not await self._ensure_garden_player():
            return False, "", 0
        index = max(0, min(offset, len(playable) - 1))
        first = playable[index]
        started = await self._garden_player_post(
            "/player/play",
            {"uri": first, "position": max(0, int(position_ms))},
        )
        if not started:
            return False, "", 0
        for uri in playable[index + 1 :]:
            await self._garden_player_post("/player/add_to_queue", {"uri": uri})
        headers = await self._headers()
        duration_ms = await self._track_duration(first, headers) if headers else 0
        return True, "", duration_ms

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
        if hub_config.site() == "garden":
            return await self._garden_player_post("/player/pause")
        h = await self._headers()
        if not h:
            return False
        # Stop whoever is currently making sound (phone leftover, old Connect
        # session), then the house speaker. 404 = already idle.
        codes: list[int] = []
        active = await self._http.put(f"{API}/me/player/pause", headers=h)
        codes.append(active.status_code)
        device_id = await self._target_device_id()
        if device_id:
            house = await self._http.put(
                f"{API}/me/player/pause",
                headers=h,
                params={"device_id": device_id},
            )
            codes.append(house.status_code)
        return any(code in (200, 204, 404) for code in codes)

    async def _post_player_next(self) -> bool:
        if hub_config.site() == "garden":
            return await self._garden_player_post("/player/next")
        h = await self._headers()
        if not h:
            return False
        device_id = await self._target_device_id()
        if not device_id:
            return False
        r = await self._http.post(
            f"{API}/me/player/next",
            headers=h,
            params={"device_id": device_id},
        )
        return r.status_code in (200, 204)

    async def _post_player_previous(self) -> bool:
        if hub_config.site() == "garden":
            return await self._garden_player_post("/player/prev")
        h = await self._headers()
        if not h:
            return False
        device_id = await self._target_device_id()
        if not device_id:
            return False
        r = await self._http.post(
            f"{API}/me/player/previous",
            headers=h,
            params={"device_id": device_id},
        )
        if r.status_code not in (200, 204):
            print(f"[Spotify] previous HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code in (200, 204)

    async def _seek_track(self, position_ms: int = 0) -> bool:
        if hub_config.site() == "garden":
            return await self._garden_player_post(
                "/player/seek",
                {"position": max(0, int(position_ms))},
            )
        h = await self._headers()
        if not h:
            return False
        device_id = await self._target_device_id()
        if not device_id:
            return False
        r = await self._http.put(
            f"{API}/me/player/seek",
            headers=h,
            params={"position_ms": position_ms, "device_id": device_id},
        )
        return r.status_code in (200, 204)

    async def play_uris_queue(
        self,
        uris: list[str],
        offset: int = 0,
        position_ms: int = 0,
        preferred_device_id: str | None = None,
    ) -> tuple[bool, str, int]:
        """Start afspilning på B&O M5. Returnerer (ok, detail, duration_ms)."""
        uris = [u for u in uris if u and u.startswith("spotify:track:")][:50]
        if not uris:
            return False, "no valid uris", 0
        off = max(0, min(offset, len(uris) - 1))
        if hub_config.site() == "garden":
            return await self._play_garden_uris(uris, off, position_ms)
        h = await self._headers()
        if not h:
            return False, "no auth headers", 0
        speaker = preferred_device_id or await self._find_speaker_device_id()
        if not speaker:
            return False, "Højttaleren er ikke på Spotify", 0

        body = {"uris": uris, "offset": {"position": off}, "position_ms": position_ms}

        async def put_play(device_id: str) -> tuple[int, str]:
            r = await self._http.put(
                f"{API}/me/player/play",
                headers=h,
                params={"device_id": device_id},
                json=body,
            )
            return r.status_code, f"device_id={device_id!r} HTTP {r.status_code}: {r.text[:350]}"

        status, last_snip = await put_play(speaker)
        if status == 404:
            self._forget_m5()
            print("[Spotify] play-uris 404, rebinding M5")
            rebound = await self._find_speaker_device_id(force_wake=True)
            if rebound:
                speaker = rebound
                status, last_snip = await put_play(speaker)
        if status not in (200, 204):
            print(f"[Spotify] play-uris {last_snip}")
            return False, last_snip, 0
        self._remember_m5(speaker)

        await asyncio.sleep(1)

        if not await self._playback_matches(h, speaker, uris[off]):
            print(f"[Spotify] play-uris accepted but {uris[off]} is not on {speaker}")
            return False, "Højttaleren startede ikke sangen", 0

        await self._beolink_expand()
        duration_ms = await self._track_duration(uris[off], h)
        # B&O often plays only the first URI from PUT /play. Queue the rest
        # after M5 is actually on the track, same pattern as the garden player.
        self._start_enqueue(h, speaker, uris[off + 1 :])
        return True, "", duration_ms

    def _cancel_enqueue(self) -> None:
        task = getattr(self, "_queue_task", None)
        if task and not task.done():
            task.cancel()
        self._queue_task = None

    def _start_enqueue(self, headers: dict, device_id: str, uris: list[str]) -> None:
        self._cancel_enqueue()
        if not uris or not device_id:
            return
        self._queue_task = asyncio.create_task(self._enqueue_remaining(headers, device_id, uris))

    async def _enqueue_remaining(self, headers: dict, device_id: str, uris: list[str]) -> None:
        for uri in uris:
            try:
                r = await self._http.post(
                    f"{API}/me/player/queue",
                    headers=headers,
                    params={"uri": uri, "device_id": device_id},
                )
                if r.status_code in (401, 403, 404):
                    print(f"[Spotify] queue {uri} HTTP {r.status_code}")
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[Spotify] queue failed: {exc}")
                break

    async def _playback_matches(self, headers: dict, device_id: str, uri: str) -> bool:
        try:
            response = await self._http.get(f"{API}/me/player", headers=headers)
            if response.status_code != 200:
                return False
            player = response.json()
            return (
                bool(player.get("is_playing"))
                and ((player.get("device") or {}).get("id") == device_id)
                and ((player.get("item") or {}).get("uri") == uri)
            )
        except Exception:
            return False

    async def _garden_connect_device_id(self) -> str | None:
        preferred = hub_config.spotify_connect_device().strip().lower()
        if not preferred:
            return None
        for device in await self.devices():
            if device["name"].strip().lower() == preferred:
                self._remember_m5(device["id"])
                return device["id"]
        return None

    async def _librespot_is_active(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "is-active",
                "librespot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return out.decode().strip() == "active"
        except Exception:
            return False

    async def _systemctl_librespot(self, action: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo",
                "-n",
                "systemctl",
                action,
                "librespot",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except Exception as exc:
            print(f"[Spotify] librespot {action} failed: {exc}")
            return False

    async def _wait_garden_connect_device(self, seconds: int = 12) -> str | None:
        for _ in range(seconds):
            found = await self._garden_connect_device_id()
            if found:
                return found
            await asyncio.sleep(1)
        return await self._garden_connect_device_id()

    async def _ensure_garden_connect_device(self) -> str | None:
        found = await self._garden_connect_device_id()
        if found:
            return found
        if not await self._librespot_is_active():
            if not await self._systemctl_librespot("start"):
                return None
        return await self._wait_garden_connect_device()

    async def _restart_garden_connect_device(self) -> str | None:
        self._last_connect_restart = time.monotonic()
        if not await self._systemctl_librespot("restart"):
            return None
        return await self._wait_garden_connect_device()

    async def _track_duration(self, uri: str, headers: dict) -> int:
        """Hent duration_ms for et track-URI fra Spotify."""
        try:
            tid = uri.split(":")[-1]
            r = await self._http.get(f"{API}/tracks/{tid}", headers=headers)
            if r.status_code == 200:
                return r.json().get("duration_ms", 0)
        except Exception:
            pass
        return 0

    # ── Podcast / show ────────────────────────────────────────────────────
    async def get_show(self, show_id: str) -> dict | None:
        """Hent show-metadata (navn, cover, beskrivelse) for et podcast-show."""
        h = await self._headers()
        if not h:
            return None
        try:
            r = await self._http.get(
                f"{API}/shows/{show_id}",
                headers=h,
                params={"market": "DK"},
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[Spotify] get_show error: {e}")
        return None

    async def get_show_latest_episode(self, show_id: str) -> dict | None:
        """Hent seneste afsnit af et show. Spotify returnerer episodes sorteret nyeste først."""
        items, _ = await self.get_show_episodes(show_id, limit=5, offset=0)
        return items[0] if items else None

    async def get_episode(self, episode_id: str) -> dict | None:
        """Hent ét afsnit (titel, show, duration) til player-state."""
        h = await self._headers()
        if not h or not episode_id:
            return None
        try:
            r = await self._http.get(
                f"{API}/episodes/{episode_id}",
                headers=h,
                params={"market": "DK"},
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, dict) else None
        except Exception as e:
            print(f"[Spotify] get_episode error: {e}")
        return None

    async def get_show_episodes(
        self, show_id: str, limit: int = 20, offset: int = 0
    ) -> tuple[list[dict], bool]:
        """Hent en side af afsnit fra et show. Returnerer (items, has_more)."""
        h = await self._headers()
        if not h:
            return [], False
        try:
            r = await self._http.get(
                f"{API}/shows/{show_id}/episodes",
                headers=h,
                params={
                    "market": "DK",
                    "limit": max(1, min(50, limit)),
                    "offset": max(0, offset),
                },
            )
            if r.status_code == 200:
                data = r.json()
                items = _usable_spotify_episodes(data.get("items") or [])
                has_more = bool(data.get("next"))
                return items, has_more
            print(f"[Spotify] get_show_episodes HTTP {r.status_code} for {show_id}")
        except Exception as e:
            print(f"[Spotify] get_show_episodes error: {e}")
        return [], False

    async def play_episode(self, episode_uri: str) -> tuple[bool, str]:
        """Spil ét enkelt podcast-afsnit på B&O M5 (samme rute som tracks)."""
        if not episode_uri or not episode_uri.startswith("spotify:episode:"):
            return False, "not an episode uri"
        if hub_config.site() == "garden":
            ok, _, _ = await self._play_garden_uris([episode_uri], 0, 0)
            return ok, ""
        h = await self._headers()
        if not h:
            return False, "no auth headers"
        speaker = await self._find_speaker_device_id()
        if speaker:
            target = next((d for d in await self.devices() if d.get("id") == speaker), None)
            if target and str(target.get("type") or "").lower() == "computer" and hub_config.site() != "garden":
                speaker = None
        if hub_config.site() != "garden" and not speaker:
            return False, "Afspilning fejlede"

        body = {"uris": [episode_uri], "position_ms": 0}
        last_snip = ""
        r = await self._http.put(
            f"{API}/me/player/play",
            headers=h,
            params={"device_id": speaker} if speaker else {},
            json=body,
        )
        if r.status_code in (200, 204):
            await self._beolink_expand()
            return True, ""
        last_snip = f"device_id={speaker!r} HTTP {r.status_code}: {r.text[:350]}"
        print(f"[Spotify] play-episode {last_snip}")
        return False, last_snip

    async def skip(self) -> bool:
        return await self._post_player_next()

    async def previous(self) -> bool:
        return await self._post_player_previous()

    async def now_playing(self) -> dict | None:
        h = await self._headers()
        if not h:
            return None
        r = await self._http.get(
            f"{API}/me/player/currently-playing",
            headers=h,
            params={"additional_types": "episode"},
        )
        if r.status_code == 200:
            data = r.json() if r.content else {}
            item = data.get("item") if isinstance(data, dict) else None
            if not isinstance(item, dict):
                item = {}
            artists = item.get("artists") if isinstance(item.get("artists"), list) else []
            show = item.get("show") if isinstance(item.get("show"), dict) else {}
            album = item.get("album") if isinstance(item.get("album"), dict) else {}
            images = album.get("images") or item.get("images") or show.get("images") or []
            artist = ", ".join(
                a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")
            ) or str(show.get("name") or "")
            result = {
                "name": item.get("name", "") or "",
                "artist": artist,
                "album": album.get("name", "") or show.get("name", "") or "",
                "image": (images[0].get("url", "") if images and isinstance(images[0], dict) else ""),
                "is_playing": bool(data.get("is_playing")),
                "uri": item.get("uri", "") or "",
                "progress_ms": data.get("progress_ms", 0) or 0,
                "duration_ms": item.get("duration_ms", 0) or 0,
                "next_name": "",
                "next_artist": "",
            }
            try:
                qr = await self._http.get(f"{API}/me/player/queue", headers=h)
                if qr.status_code == 200:
                    queue = qr.json().get("queue", [])
                    if queue:
                        nxt = queue[0]
                        if isinstance(nxt, dict):
                            result["next_name"] = nxt.get("name", "") or ""
                            nxt_artists = nxt.get("artists") if isinstance(nxt.get("artists"), list) else []
                            result["next_artist"] = ", ".join(
                                a.get("name", "") for a in nxt_artists if isinstance(a, dict) and a.get("name")
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
        """A9 follows M5's Spotify source. Home-only."""
        if not hub_config.bo_speakers_enabled():
            return
        import bo_link
        await bo_link.expand_to_a9("spotify")

    @staticmethod
    def _is_phone_or_computer(device: dict) -> bool:
        kind = str(device.get("type") or "").strip().lower()
        return kind in ("smartphone", "tablet", "computer")

    def _remember_m5(self, device_id: str) -> None:
        if not device_id:
            return
        self._m5_device_id = device_id
        self._m5_device_at = time.monotonic()

    def _forget_m5(self) -> None:
        self._m5_device_id = ""
        self._m5_device_at = 0.0

    def _cached_m5(self) -> str | None:
        if self._m5_device_id and (time.monotonic() - self._m5_device_at) < 3600:
            return self._m5_device_id
        return None

    async def _spotify_user_id(self) -> str:
        if self._spotify_user_id_cache:
            return self._spotify_user_id_cache
        h = await self._headers()
        if not h:
            return ""
        r = await self._http.get(f"{API}/me", headers=h)
        if r.status_code != 200:
            return ""
        uid = str((r.json() or {}).get("id") or "").strip()
        self._spotify_user_id_cache = uid
        return uid

    async def _bind_m5(self) -> str | None:
        import bo_link
        token = await self._ensure_token() or ""
        user_id = await self._spotify_user_id() if token else ""
        woke = await bo_link.ensure_m5_spotify(user_id, token)
        if woke:
            self._remember_m5(woke)
        return woke

    async def _find_speaker_device_id(self, force_wake: bool = False) -> str | None:
        """House speaker only. Never phone, tablet, or kiosk browser."""
        if not force_wake:
            cached = self._cached_m5()
            if cached:
                return cached
        devs = await self.devices()
        preferred = hub_config.spotify_connect_device().strip().lower()
        if preferred:
            for d in devs:
                if d["name"].strip().lower() == preferred:
                    if hub_config.site() != "garden" and self._is_phone_or_computer(d):
                        continue
                    return d["id"]
            if hub_config.site() == "garden":
                now = time.monotonic()
                if now - self._last_connect_restart >= 60:
                    self._last_connect_restart = now
                    return await self._ensure_garden_connect_device()
                return None
        if hub_config.site() == "garden":
            return None
        if not force_wake:
            for d in devs:
                if _looks_like_m5(d):
                    self._remember_m5(d["id"])
                    return d["id"]
        if not hub_config.bo_speakers_enabled():
            return None
        woke = await self._bind_m5()
        if not woke:
            return None
        for d in await self.devices():
            if d.get("id") == woke or _looks_like_m5(d):
                self._remember_m5(d["id"])
                return d["id"]
        return woke

    async def _target_device_id(self) -> str | None:
        """Pause/skip/seek rammer samme hus-højttaler som play. Aldrig /me/player."""
        return await self._find_speaker_device_id()

    async def resume(self) -> bool:
        """Resume playback on M5 + ensure BeoLink multiroom."""
        if hub_config.site() == "garden":
            return await self._garden_player_post("/player/resume")
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

    async def transcribe_audio(self, audio: bytes, mime_type: str, language: str = "en-US") -> tuple[bool, str]:
        """Transcribe a short kiosk voice command with the existing Gemini integration."""
        key = self._gemini_api_key()
        if not key:
            return False, "Talegenkendelse mangler Gemini-nøgle"
        if not audio:
            return False, "Tom lydoptagelse"
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={key}"
        )
        language_name = "Danish" if language.lower().startswith("da") else "English"
        payload = {
            "contents": [{
                "parts": [
                    {
                        "text": (
                            f"Transcribe this short {language_name} music voice command verbatim. "
                            "Artist names and song titles may be in any language. "
                            "Return only the transcript, with no quotes or explanation."
                        )
                    },
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(audio).decode("ascii"),
                        }
                    },
                ]
            }],
            "generationConfig": {"temperature": 0},
        }
        try:
            response = await self._gemini_http.post(url, json=payload)
            if response.status_code != 200:
                print(f"[Gemini transcription] error: {response.status_code} {response.text[:300]}")
                return False, "Tale-API fejlede"
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.strip("`\"' \n")
            return (True, text) if text else (False, "Ingen tale genkendt")
        except Exception as exc:
            print(f"[Gemini transcription] error: {exc}")
            return False, "Tale-API fejlede"

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

    async def save_radio_playlist(
        self,
        seed_name: str,
        seed_artist: str,
        tracks: list[dict],
    ) -> dict:
        """Gem den aktuelle radiokø som privat Spotify-playliste."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        uris: list[str] = []
        for t in tracks:
            uri = str((t or {}).get("uri") or "")
            if uri.startswith("spotify:track:") and uri not in uris:
                uris.append(uri)
        if not uris:
            return {"ok": False, "error": "Ingen tracks at gemme"}

        title_seed = seed_name.strip() or "Radio"
        artist_suffix = f" - {seed_artist.strip()}" if seed_artist.strip() else ""
        name = f"{title_seed}{artist_suffix}"
        try:
            cr = await self._http.post(
                f"{API}/me/playlists",
                headers=h,
                json={
                    "name": name,
                    "public": False,
                    "description": "Ejdersted radio gemt fra kiosken.",
                },
            )
        except httpx.TimeoutException:
            return {"ok": False, "error": "Spotify svarede ikke i tide"}
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"Spotify-forbindelse fejlede: {type(e).__name__}"}
        if cr.status_code != 201:
            return {"ok": False, "error": f"Kunne ikke oprette playliste ({_spotify_error(cr)})"}
        playlist = cr.json()
        pid = playlist.get("id")
        if not pid:
            return {"ok": False, "error": "Spotify returnerede ikke playlist-id"}

        # Spotify accepterer max 100 URIs per kald.
        import logging
        for i in range(0, len(uris), 100):
            batch = uris[i:i + 100]
            logging.warning(f"[save_radio] Adding {len(batch)} tracks to {pid}: {batch[:3]}")
            try:
                r = await self._http.post(
                    f"{API}/playlists/{pid}/items",
                    headers=h,
                    json={"uris": batch},
                )
            except httpx.TimeoutException:
                return {"ok": False, "error": "Playliste oprettet, men Spotify timeoutede på tracks"}
            except httpx.HTTPError as e:
                return {"ok": False, "error": f"Playliste oprettet, men Spotify-forbindelse fejlede: {type(e).__name__}"}
            logging.warning(f"[save_radio] Spotify responded: {r.status_code} {r.text[:300]}")
            if r.status_code not in (200, 201):
                return {"ok": False, "error": f"Playliste oprettet, men tracks fejlede ({_spotify_error(r)})"}

        return {
            "ok": True,
            "id": pid,
            "uri": playlist.get("uri") or f"spotify:playlist:{pid}",
            "name": playlist.get("name") or name,
            "tracks": len(uris),
        }

    async def unfollow_playlist(self, playlist_id: str) -> dict:
        """Slet (unfollow) en playliste."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        r = await self._http.delete(
            f"{API}/playlists/{playlist_id}/followers",
            headers=h,
        )
        if r.status_code == 200:
            return {"ok": True}
        return {"ok": False, "error": f"Kunne ikke slette ({r.status_code})"}

    async def remove_playlist_track(self, playlist_id: str, track_uri: str, position: int | None = None) -> dict:
        """Fjern et track fra en playliste."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind"}
        if not playlist_id:
            return {"ok": False, "error": "Mangler playliste"}
        if not track_uri.startswith("spotify:track:"):
            return {"ok": False, "error": "Mangler sang"}
        track: dict = {"uri": track_uri}
        if position is not None and position >= 0:
            track["positions"] = [position]
        r = await self._http.request(
            "DELETE",
            f"{API}/playlists/{playlist_id}/tracks",
            headers=h,
            json={"tracks": [track]},
        )
        if r.status_code == 200:
            return {"ok": True}
        return {"ok": False, "error": f"Kunne ikke slette sang ({r.status_code})"}

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
                f"{API}/playlists/{pid}/items",
                headers=h,
                params={"limit": 50, "offset": offset},
            )
            if r.status_code != 200:
                break
            data = r.json()
            items = data.get("items", [])
            for entry_index, entry in enumerate(items):
                tr = entry.get("item") or entry.get("track") or {}
                if not tr or tr.get("is_local"):
                    continue
                uri = tr.get("uri") or ""
                if not uri.startswith("spotify:track:"):
                    continue
                rows.append({
                    "uri": uri,
                    "name": tr.get("name", ""),
                    "artist": ", ".join(a["name"] for a in tr.get("artists", [])),
                    "position": offset + entry_index,
                })
                if len(rows) >= limit:
                    break
            if len(items) < 50:
                break
            offset += 50
        if not rows:
            return {"ok": False, "error": "Tom playlist"}
        return {"ok": True, "queue": rows}

    async def list_playlists(self, limit: int = 50, offset: int = 0) -> dict:
        """Hent alle brugerens Ejdersted-radio playlister (paginerer automatisk)."""
        h = await self._headers()
        if not h:
            return {"ok": False, "error": "Ikke logget ind", "playlists": []}
        rows: list[dict] = []
        url: str | None = f"{API}/me/playlists"
        page_limit = max(1, min(50, int(limit or 50)))
        start_offset = max(0, int(offset or 0))
        params: dict = {"limit": page_limit, "offset": start_offset}
        while url:
            try:
                r = await self._http.get(url, headers=h, params=params)
            except httpx.TimeoutException:
                return {"ok": False, "error": "Spotify svarede ikke i tide", "playlists": rows}
            except httpx.HTTPError as e:
                return {"ok": False, "error": f"Spotify-forbindelse fejlede: {type(e).__name__}", "playlists": rows}
            if r.status_code != 200:
                return {"ok": False, "error": f"Kunne ikke hente playlister ({_spotify_error(r)})", "playlists": rows}
            data = r.json()
            for p in data.get("items", []) or []:
                if not p:
                    continue
                desc = p.get("description") or ""
                if "ejdersted radio" not in desc.lower():
                    continue
                tracks_total = (p.get("items") or p.get("tracks") or {}).get("total", 0)
                images = p.get("images") or []
                rows.append({
                    "id": p.get("id", ""),
                    "uri": p.get("uri", ""),
                    "name": p.get("name", ""),
                    "description": desc,
                    "image": images[0].get("url") if images else "",
                    "tracks_total": tracks_total,
                    "owner": (p.get("owner") or {}).get("display_name", ""),
                })
            url = data.get("next")
            params = {}
        return {"ok": True, "playlists": rows}

    async def voice_command(self, transcript: str) -> dict:
        """Stemme: kun kø-metadata til klienten — ingen afspilning herfra."""
        raw = transcript.strip()
        t = re.sub(r"\s+", " ", raw.lower()).strip()

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
        for prefix in (
            "spil en sang med ",
            "spil en sang af ",
            "spil noget med ",
            "spil musik med ",
            "play a song by ",
            "play a song with ",
            "play a song from ",
            "play something by ",
            "play something with ",
            "play something from ",
            "a song by ",
            "a song with ",
            "a song from ",
            "en sang med ",
            "en sang af ",
            "noget med ",
            "musik med ",
        ):
            if t.startswith(prefix):
                query = t[len(prefix):]
                force_artist = True
                break
        else:
            force_artist = False
            for prefix in ("spil ", "play ", "afspil ", "sæt ", "put on "):
                if t.startswith(prefix):
                    query = t[len(prefix):]
                    break
        query = query.removesuffix(" på").removesuffix(" on").strip()
        query = re.sub(r"^(by|with|from|med|af)\s+", "", query).strip()
        if not query:
            return {"action": "search", "ok": False, "error": "Jeg hørte ikke hvad der skulle spilles"}

        force_album = False
        for album_prefix in ("album ", "albummet ", "albumet "):
            if query.startswith(album_prefix):
                query = query[len(album_prefix):]
                force_album = True
                break

        # "keep going by this is the kit" → track:"keep going" artist:"this is the kit".
        # Spotify's free-text search guesses badly at the artist otherwise.
        results = None
        if not force_artist and not force_album:
            split = split_title_artist(query)
            if split:
                title, artist = split
                results = await self.search(
                    f'track:"{title}" artist:"{artist}"', types="track", limit=3
                )
                if not ((results or {}).get("tracks") or {}).get("items"):
                    results = None
        if results is None:
            track_first = await self.search(query, types="track", limit=5)
            tracks_only = [
                x
                for x in ((track_first or {}).get("tracks") or {}).get("items") or []
                if isinstance(x, dict) and x.get("uri")
            ]
            if tracks_only and pick_best_track(query, tracks_only):
                results = track_first
            else:
                results = await self.search(query, types="track,artist,album,playlist", limit=3)
        if not results:
            return {"action": "search", "ok": False, "query": query, "error": f"Fandt ikke {query}"}

        tracks = [x for x in (results.get("tracks") or {}).get("items") or [] if isinstance(x, dict) and x.get("uri")]
        artists = [x for x in (results.get("artists") or {}).get("items") or [] if isinstance(x, dict) and x.get("uri")]
        albums = [x for x in (results.get("albums") or {}).get("items") or [] if isinstance(x, dict) and x.get("uri")]
        playlists = [x for x in (results.get("playlists") or {}).get("items") or [] if isinstance(x, dict) and x.get("uri")]

        if force_artist:
            if artists:
                inner = await self.build_artist_top_queue(artists[0]["uri"])
                if not inner.get("ok"):
                    return {**inner, "action": "enqueue_queue"}
                return {
                    "action": "enqueue_queue",
                    "ok": True,
                    "queue": inner["queue"],
                    "label": artists[0].get("name", query),
                }
            if tracks:
                track = tracks[0]
                return {
                    "action": "enqueue",
                    "ok": True,
                    "uri": track["uri"],
                    "name": track["name"],
                    "artist": ", ".join(a["name"] for a in track.get("artists", [])),
                }
            return {"action": "search", "ok": False, "query": query, "error": f"Fandt ikke kunstneren {query}"}

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
            track = pick_best_track(query, tracks) or tracks[0]
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

        return {"action": "search", "ok": False, "query": query, "error": f"Fandt ikke {query}"}
