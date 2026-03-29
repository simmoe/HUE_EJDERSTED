# KIOSK — Komplet opslagsdokument til sprogmodeller

> Denne fil indeholder alt, en AI-assistent behøver for at starte, genstarte,
> deploye og fejlfinde kiosk-setuppet i **HUE_EJDERSTED**-projektet.

---

## 1. Overblik

En Samsung Android-tablet kører Chrome i fuldskærmskiosk-mode og viser en
touch-baseret home automation UI (lysstyring via Philips Hue + volumenkontrol
for B&O-højttalere + Spotify voice control). Backend er en FastAPI Python-server
der kører på en Raspberry Pi 5 (produktion) eller en Mac (udvikling).

---

## 2. Netværk

Alle enheder har faste IP'er via Google Home DHCP-reservationer.

| Enhed | IP | Port | Bemærkning |
|---|---|---|---|
| Raspberry Pi 5 (prod server) | `192.168.86.16` | `8443` (HTTPS) | SSH: simmoe / k18Medh18 |
| Mac (dev) | `192.168.86.13` | `8443` (HTTPS) | Kun til udvikling |
| Android-tablet (Samsung) | `192.168.86.15` | ADB: variabel | Trådløs ADB port skifter ved genstart |
| Philips Hue Bridge | `192.168.86.25` | HTTPS (clipv2) | |
| B&O BeoPlay A9 | `192.168.86.153` | `8080` (Mozart API) | |
| B&O BeoSound M5 | `192.168.86.188` | `8080` (Mozart API) | BeoLink multiroom med A9 |

**ADB**: Tabletens trådløse debugging-port skifter ved hver genstart.
Find ny port i tabletens Developer Options → Wireless debugging.
Eksempel: `adb connect 192.168.86.15:36873`

**HTTPS**: Serveren kører HTTPS med self-signed certifikat (port 8443).
Pi bruger certs i `certs/`, Mac bruger mkcert-genererede certs.

---

## 3. Projektstruktur

```
/Users/simon/Documents/Git/HUE_EJDERSTED/
├── backend/
│   ├── main.py          ← FastAPI server (start her)
│   ├── hue.py           ← Hue bridge integration
│   ├── spotify.py       ← Spotify Web API + BeoLink multiroom
│   ├── static/          ← SvelteKit build-output (serveres af FastAPI, IKKE i git)
│   └── requirements.txt
├── frontend/
│   ├── src/             ← SvelteKit 5 kildekode
│   ├── static/
│   │   └── sw.js        ← Service worker (cache-version!)
│   ├── package.json
│   └── svelte.config.js
├── certs/               ← TLS-certifikater (IKKE i git)
├── deploy.sh            ← Deploy-script: Mac → Pi
├── devices.json         ← Persisteret B&O-enhedsliste
├── hue_config.json      ← Hue bridge IP + username (IKKE i git)
├── spotify_config.json  ← Spotify OAuth tokens (IKKE i git)
├── KIOSK.md             ← Denne fil
└── requirements.txt
```

---

## 4. Produktion — Raspberry Pi

Serveren kører som systemd-service på Pi'en og starter automatisk ved boot.

```bash
# Tjek status
sshpass -p 'k18Medh18' ssh simmoe@192.168.86.16 "sudo systemctl status hue --no-pager"

# Genstart
sshpass -p 'k18Medh18' ssh simmoe@192.168.86.16 "sudo systemctl restart hue"

# Se logs
sshpass -p 'k18Medh18' ssh simmoe@192.168.86.16 "sudo journalctl -u hue -f"
```

**Kiosk URL (tablet)**: `https://192.168.86.16:8443`

---

## 5. Hurtigstart — "genstart kiosk"

Kør disse trin fra projektets rodmappe på Mac'en:

```bash
cd /Users/simon/Documents/Git/HUE_EJDERSTED

# 1. Forbind ADB (porten skifter — tjek tablet Developer Options)
adb connect 192.168.86.15:<PORT>

# 2. Sæt landscape + immersive + åbn Chrome
ADB="192.168.86.15:<PORT>"
adb -s $ADB shell settings put system accelerometer_rotation 0
adb -s $ADB shell settings put system user_rotation 1
adb -s $ADB shell settings put global policy_control "immersive.full=com.android.chrome"
adb -s $ADB shell am force-stop com.android.chrome
adb -s $ADB shell am start -a android.intent.action.VIEW -d "https://192.168.86.16:8443"
```

Første gang skal det self-signed certifikat accepteres i Chrome (Avanceret → Fortsæt).

---

## 6. Deploy (Mac → Pi)

> **Regel**: Deploy kun til Pi når en feature er klar til test på tablet.  
> Under fejlretning og iterativ udvikling: brug `npm run dev` lokalt (Vite på :5173).

```bash
# Alt-i-én deploy (git push, pull på Pi, sync static, restart service)
./deploy.sh

# Med frontend rebuild først
./deploy.sh --build
```

Manuelt:
```bash
cd frontend
sed -i '' 's/hue-v17/hue-v18/g' static/sw.js   # Bump SW-cache!
npm run build
cd ..
git add -A && git commit -m "deploy" && git push
sshpass -p 'k18Medh18' ssh simmoe@192.168.86.16 "cd ~/HUE_EJDERSTED && git pull"
sshpass -p 'k18Medh18' scp -r backend/static simmoe@192.168.86.16:~/HUE_EJDERSTED/backend/
sshpass -p 'k18Medh18' ssh simmoe@192.168.86.16 "sudo systemctl restart hue"
```

**Cache-version**: Filen `frontend/static/sw.js` har `const CACHE = 'hue-vNN'`.
Bump ALTID dette tal inden build — ellers ser tabletten den gamle version.

---

## 7. ADB kiosk-kommandoer (reference)

Disse kan trigges via `POST /api/kiosk`. ADB er installeret på Pi'en og parret med tabletten.
Backend finder dynamisk tabletens ADB-serial via `_get_adb_serial()` (kører `adb devices`).

```bash
ADB="adb -s 192.168.86.15:<PORT>"

# Lås landskab
$ADB shell settings put system accelerometer_rotation 0
$ADB shell settings put system user_rotation 1

# Manuel lysstyrke, maks
$ADB shell settings put system screen_brightness_mode 0
$ADB shell settings put system screen_brightness 255

# Fjern volume-HUD overlay
$ADB shell appops set com.android.systemui SYSTEM_ALERT_WINDOW deny

# Immersive mode (fjern system bars)
$ADB shell settings put global policy_control "immersive.full=com.android.chrome"
```

For at genaktivere volume-HUD: `... SYSTEM_ALERT_WINDOW allow`.

**Bemærk**: ADB-port på tabletten skifter ved genstart af trådløs debugging.

---

## 8. API-endpoints (vigtigste)

| Metode | Endpoint | Beskrivelse |
|---|---|---|
| `WS` | `/ws` | WebSocket — init state + push updates |
| `GET` | `/api/devices` | Liste over B&O-enheder |
| `POST` | `/api/devices` | Tilføj B&O-enhed manuelt |
| `GET` | `/api/hue/status` | Hue bridge status (paired/ip) |
| `POST` | `/api/hue/pair` | Par Hue bridge (tryk fysisk knap først) |
| `PUT` | `/api/brightness/{level}` | Sæt tablet-skærmlysstyrke via ADB (0–255) |
| `POST` | `/api/kiosk` | Kør alle ADB kiosk-kommandoer |
| `GET` | `/api/spotify/status` | Spotify auth status |
| `POST` | `/api/spotify/voice` | Stemmesøgning (EN/DA) → afspil på M5 + BeoLink A9 |
| `POST` | `/api/spotify/resume` | Genoptag afspilning |
| `POST` | `/api/spotify/pause` | Pause afspilning |
| `POST` | `/api/spotify/radio` | Start radio (anbefalinger baseret på nuværende track) |
| `GET` | `/api/spotify/now-playing` | Nuværende track info |

WebSocket-beskeder (JSON):
- `set_volume` → sætter B&O-volumen
- `set_brightness` → sætter Hue-rumlysstyrke
- Server pusher: `init`, `volume_update`, `hue_rooms`, `hue_status`, `now_playing`, `device_added`

---

## 9. Frontend-arkitektur

- **Svelte 5** med runes (`$state`, `$derived`, `$effect`)
- **To-panel layout**: LYD (venstre) + LYS (højre), altid side-by-side (50/50)
- **Altid landskab** — ingen media queries, ingen portrait-support
- **Dim/clock**: Efter 30s inaktivitet dæmpes skærmen (ADB brightness → 12) og et ur vises. Touch (pointerdown) vækker.
- **Splash**: "EJDERSTED" splash ved load → tap dismiss → trigger `/api/kiosk` + fullscreen + wake lock
- **VolumeKnob.svelte**: Cirkulær knob-komponent til B&O-volumen
- **Vertical sliders**: Hue-rum har vertikale lysstyrke-sliders
- **Service worker**: `frontend/static/sw.js` — precache med versioneret cache-navn
- **Font**: Google Fonts Roboto Light (300) til ur-display

---

## 10. BeoLink multiroom

Begge højttalere (M5 + A9) spiller synkront via BeoLink.
Oprettes med ét POST-kald der tilføjer A9 som listener på M5's aktive kilde:

```bash
curl -X POST http://192.168.86.188:8080/BeoZone/Zone/ActiveSources/primaryExperience \
  -H "Content-Type: application/json" \
  -d '{"listener":{"jid":"3034.1200366.32115907@products.bang-olufsen.com"}}'
```

**Volume-nudge**: M5 starter BeoLink med lydstyrke 0 (B&O-quirk). Workaround:
læs nuværende volume og sæt den igen — det vækker lydudgangen:

```bash
# Læs M5-volume
curl http://192.168.86.188:8080/BeoZone/Zone/Sound/Volume
# Sæt den til samme level (nudge)
curl -X PUT http://192.168.86.188:8080/BeoZone/Zone/Sound/Volume/Speaker/Level \
  -H "Content-Type: application/json" -d '{"level":48}'
```

**Egenskaber**:
- Idempotent — kan kaldes flere gange uden duplikater
- Overlever pause/resume og skip track
- Virker direkte på Spotify-kilde (ingen radio-bridge nødvendig)
- Implementeret i `backend/spotify.py` → `_beolink_expand()`

**JIDs**:
- A9: `3034.1200366.32115907@products.bang-olufsen.com`
- M5: `2714.1200298.33798625@products.bang-olufsen.com`

---

## 11. Fejlfinding

### "Tablet viser gammelt UI"
→ Service worker cache. Bump `hue-vNN` i `frontend/static/sw.js`, byg, deploy, force-stop Chrome.

### "ADB: device not found"
→ Tabletens trådløse debugging-port skifter. Tjek ny port i Developer Options → Wireless debugging. `adb connect 192.168.86.15:<NY_PORT>`. Verificér med `adb devices`.

### "Hue-lamper reagerer ikke"
→ Tjek `hue_config.json` har gyldigt username. Hue bridge IP: `192.168.86.25`. Pair igen: `POST /api/hue/pair` (tryk fysisk knap på bridge først).

### "Server på Pi starter ikke"
→ `sudo journalctl -u hue -n 30` for logs. Tjek at `hue.service` er enabled: `sudo systemctl is-enabled hue`. Tjek Python-deps: `pip3 list | grep fastapi`.

### "Volumenknob virker ikke"
→ B&O BeoPlay A9 skal være tændt og tilgængelig på `192.168.86.153:8080`. Test: `curl http://192.168.86.153:8080/BeoZone/Zone/Sound/Volume/Speaker/Level`.

### "Skærm dæmpes ikke / ur vises ikke"
→ Dim-timer starter efter splash dismiss. Kun `pointerdown` (touch) resetter. ADB er installeret og parret på Pi — brightness-kommandoer kører fra begge. Tjek at `/api/brightness/{level}` virker.

### "Spotify virker ikke"
→ Tjek `spotify_config.json` findes på Pi. Tjek auth: `curl -sk https://192.168.86.16:8443/api/spotify/status`. Tokens kan udløbe — re-auth kræver `python3.13 spotify_auth.py` på Mac og kopier config til Pi.

---

## 12. Git

- **Aktiv branch**: `main`
- **Default branch på GitHub**: `master`
- **Backup-branch**: `category-swipe-backup` (gammel experimental swipe-UI)

---

## 13. Dependencies

**Backend** (`pip install -r backend/requirements.txt`):
- fastapi, uvicorn[standard], httpx, zeroconf

**Frontend** (`cd frontend && npm install`):
- SvelteKit, Svelte 5, Vite

**Produktion (Pi)**:
- Python 3.13 (forudinstalleret på Debian 13 trixie)
- Ingen Node.js nødvendig (frontend er pre-built)
- systemd service: `hue.service`

**Udvikling (Mac)**:
- Python 3.13
- Node.js (til frontend build)
- ADB (Android Debug Bridge)
- sshpass (`brew install hudochenkov/sshpass/sshpass`)
- mkcert (til lokale TLS-certs)
