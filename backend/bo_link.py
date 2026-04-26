"""B&O multiroom helper — får A9'en til at følge med M5'en uanset hvilken kilde
M5'en spiller (Spotify, DLNA, …)."""
import asyncio

import httpx

BEO_M5_IP = "192.168.86.21"
BEO_A9_IP = "192.168.86.20"
BEO_M5_JID = "2714.1200298.33798625@products.bang-olufsen.com"

_http = httpx.AsyncClient(timeout=4.0)


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
        vol = await _http.get(f"http://{BEO_M5_IP}:8080/BeoZone/Zone/Sound/Volume")
        if vol.status_code == 200:
            level = (vol.json().get("volume", {}) or {}).get("speaker", {}).get("level", 45)
            await _http.put(
                f"http://{BEO_M5_IP}:8080/BeoZone/Zone/Sound/Volume/Speaker/Level",
                json={"level": level},
            )
    except Exception as e:
        print(f"[BeoLink] expand error ({source_prefix}): {e}")
