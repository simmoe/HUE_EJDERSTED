"""B&O multiroom helper — får A9'en til at følge med M5'en uanset hvilken kilde
M5'en spiller (Spotify, DLNA, …)."""
import asyncio

import httpx

BEO_M5_IP = "192.168.86.21"
BEO_A9_IP = "192.168.86.20"
BEO_M5_JID = "2714.1200298.33798625@products.bang-olufsen.com"
M5_SPOTIFY_SOURCE = f"spotify:{BEO_M5_JID}"

_http = httpx.AsyncClient(timeout=4.0)


async def add_spotify_user(user_name: str, access_token: str) -> bool:
    """Bind this Spotify account to M5 so Web API play can see the speaker."""
    if not user_name or not access_token:
        return False
    try:
        r = await _http.post(
            f"http://{BEO_M5_IP}/api/stream/spotify:zeroconf",
            data={
                "action": "addUser",
                "userName": user_name,
                "blob": access_token,
                "clientKey": "",
                "tokenType": "accesstoken",
                "loginId": "ejdersted",
                "deviceName": "Ejdersted",
                "deviceId": "ejdersted-hub",
                "version": "2.9.0",
            },
            timeout=15.0,
        )
        data = r.json() if r.content else {}
        ok = r.status_code == 200 and int(data.get("status") or 0) == 101 and int(data.get("spotifyError") or 0) == 0
        print(f"[BeoLink] M5 addUser status={data.get('status')} err={data.get('spotifyError')} ok={ok}")
        return ok
    except Exception as e:
        print(f"[BeoLink] M5 addUser failed: {e}")
        return False


async def ensure_m5_spotify(user_name: str = "", access_token: str = "") -> str | None:
    """M5 is always on. Select Spotify, bind the account, return Connect id."""
    try:
        await _http.put(
            f"http://{BEO_M5_IP}:8080/BeoDevice/powerManagement/standby",
            json={"standby": {"powerState": "on"}},
        )
        src = await _http.post(
            f"http://{BEO_M5_IP}:8080/BeoZone/Zone/ActiveSourceType",
            json={"sourceType": {"type": "SPOTIFY"}},
        )
        if src.status_code >= 300:
            print(f"[BeoLink] M5 Spotify source failed: {src.status_code} {src.text}")
        await _nudge_m5_volume()
        if user_name and access_token:
            await add_spotify_user(user_name, access_token)
            await asyncio.sleep(1)
        for _ in range(6):
            info = await _http.get(
                f"http://{BEO_M5_IP}/api/stream/spotify:zeroconf",
                params={"action": "getInfo"},
            )
            if info.status_code == 200:
                data = info.json() if info.content else {}
                device_id = str(data.get("deviceID") or "").strip()
                if device_id and int(data.get("spotifyError") or 0) == 0:
                    print(f"[BeoLink] M5 Connect {data.get('remoteName') or 'M5'} id={device_id}")
                    return device_id
            await asyncio.sleep(0.8)
    except Exception as e:
        print(f"[BeoLink] M5 Spotify wake failed: {e}")
    return None


async def _nudge_m5_volume() -> None:
    vol = await _http.get(f"http://{BEO_M5_IP}:8080/BeoZone/Zone/Sound/Volume")
    if vol.status_code != 200:
        return
    level = (vol.json().get("volume") or {}).get("speaker", {}).get("level", 45)
    await _http.put(
        f"http://{BEO_M5_IP}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level",
        json={"level": level},
    )


async def expand_to_a9(source_prefix: str) -> None:
    """A9 skal joine M5'en på en specifik kilde (fx 'spotify' eller 'dlna').

    Vi sender ActiveSources-kommando til A9, og giver M5'ens volumen et lille
    nudge bagefter for at vække audio-streamen til A9'en (gammel B&O-quirk —
    uden volumen-skubbet falder A9'en undertiden tilbage til lokal kilde).
    """
    full_source_id = f"{source_prefix}:{BEO_M5_JID}"
    try:
        r = await _http.post(
            f"http://{BEO_A9_IP}:8080/BeoZone/Zone/ActiveSources",
            json={
                "primaryExperience": {
                    "source": {
                        "id": full_source_id,
                        "product": {
                            "jid": BEO_M5_JID,
                            "friendlyName": "Beoplay M5",
                        },
                    }
                }
            },
        )
        if r.status_code < 300:
            print(f"[BeoLink] A9 joined M5 source: {source_prefix}")
        else:
            print(f"[BeoLink] A9 join failed ({source_prefix}): {r.status_code} {r.text}")

        await asyncio.sleep(0.5)
        await _nudge_m5_volume()
    except Exception as e:
        print(f"[BeoLink] expand error ({source_prefix}): {e}")
