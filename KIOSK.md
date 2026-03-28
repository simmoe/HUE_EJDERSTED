# KIOSK — Komplet opslagsdokument til sprogmodeller

> Denne fil indeholder alt, en AI-assistent behøver for at starte, genstarte,
> deploye og fejlfinde kiosk-setuppet i **HUE_EJDERSTED**-projektet.

---

## 1. Overblik

En Samsung Android-tablet kører Chrome i fuldskærmskiosk-mode og viser en
touch-baseret home automation UI (lysstyring via Philips Hue + volumenkontrol
for B&O-højttaler). Backend er en FastAPI Python-server der kører på en Mac.

---

## 2. Netværk

| Enhed | IP | Port |
|---|---|---|
| Mac (server) | `192.168.86.158` | `8000` |
| Android-tablet (Samsung) | `192.168.86.173` | ADB: `34111` |
| Philips Hue Bridge | `192.168.86.25` | HTTPS (clipv2) |
| B&O BeoPlay A9 | `192.168.86.153` | `8080` (Mozart API) |

ADB serial: **`192.168.86.173:34111`** (trådløs debugging).

---

## 3. Projektstruktur

```
/Users/simon/Documents/Git/HUE_EJDERSTED/
├── backend/
│   ├── main.py          ← FastAPI server (start her)
│   ├── hue.py           ← Hue bridge integration
│   ├── static/          ← SvelteKit build-output (serveres af FastAPI)
│   └── requirements.txt
├── frontend/
│   ├── src/             ← SvelteKit 5 kildekode
│   ├── static/
│   │   └── sw.js        ← Service worker (cache-version!)
│   ├── package.json
│   └── svelte.config.js
├── devices.json         ← Persisteret B&O-enhedsliste
├── hue_config.json      ← Hue bridge IP + username (parret)
├── KIOSK.md             ← Denne fil
└── requirements.txt
```

---

## 4. Hurtigstart — "genstart kiosk"

Kør disse trin fra projektets rodmappe:

```bash
# 0. Gå til projektrod
cd /Users/simon/Documents/Git/HUE_EJDERSTED

# 1. Forbind ADB (hvis ikke allerede tilsluttet)
adb connect 192.168.86.173:34111

# 2. Stop evt. kørende server
lsof -ti:8000 | xargs kill -9 2>/dev/null

# 3. Start server
python3.13 backend/main.py
# → "Home Hub → http://localhost:8000"
# Serveren kører kiosk ADB-kommandoer automatisk ved opstart (lifespan)

# 4. Åbn Chrome på tablet
adb -s 192.168.86.173:34111 shell am start \
  -a android.intent.action.VIEW \
  -d "http://192.168.86.158:8000"
```

Det er det. Serveren sætter automatisk landskab, lysstyrke 255 og slår volume-HUD fra via ADB ved startup (se `lifespan` i `backend/main.py`).

---

## 5. Frontend-deploy (hvis kode er ændret)

```bash
cd /Users/simon/Documents/Git/HUE_EJDERSTED/frontend

# 1. Bump service worker cache-version (VIGTIGT — ellers cacher browseren gammelt)
#    Find nuværende version, f.eks. hue-v17, og sæt den til hue-v18:
sed -i '' 's/hue-v17/hue-v18/g' static/sw.js

# 2. Byg frontend (output → backend/static/)
npm run build

# 3. Genstart server
cd ..
lsof -ti:8000 | xargs kill -9 2>/dev/null
python3.13 backend/main.py

# 4. Force-reload Chrome på tablet
adb -s 192.168.86.173:34111 shell am force-stop com.android.chrome
adb -s 192.168.86.173:34111 shell am start \
  -a android.intent.action.VIEW \
  -d "http://192.168.86.158:8000"
```

**Cache-version**: Filen `frontend/static/sw.js` har `const CACHE = 'hue-vNN'`.
Bump ALTID dette tal inden build — ellers ser tabletten den gamle version.

---

## 6. ADB kiosk-kommandoer (reference)

Disse kører automatisk ved serverstart OG kan trigges via `POST /api/kiosk`:

```bash
ADB="adb -s 192.168.86.173:34111"

# Lås landskab
$ADB shell settings put system accelerometer_rotation 0
$ADB shell settings put system user_rotation 1

# Manuel lysstyrke, maks
$ADB shell settings put system screen_brightness_mode 0
$ADB shell settings put system screen_brightness 255

# Fjern volume-HUD overlay
$ADB shell appops set com.android.systemui SYSTEM_ALERT_WINDOW deny
```

For at genaktivere volume-HUD: `... SYSTEM_ALERT_WINDOW allow`.

---

## 7. API-endpoints (vigtigste)

| Metode | Endpoint | Beskrivelse |
|---|---|---|
| `WS` | `/ws` | WebSocket — init state + push updates |
| `GET` | `/api/devices` | Liste over B&O-enheder |
| `POST` | `/api/devices` | Tilføj B&O-enhed manuelt |
| `GET` | `/api/hue/status` | Hue bridge status (paired/ip) |
| `POST` | `/api/hue/pair` | Par Hue bridge (tryk fysisk knap først) |
| `PUT` | `/api/brightness/{level}` | Sæt tablet-skærmlysstyrke via ADB (0–255) |
| `POST` | `/api/kiosk` | Kør alle ADB kiosk-kommandoer |

WebSocket-beskeder (JSON):
- `set_volume` → sætter B&O-volumen
- `set_brightness` → sætter Hue-rumlysstyrke
- Server pusher: `init`, `volume_update`, `hue_rooms`, `hue_status`, `now_playing`, `device_added`

---

## 8. Frontend-arkitektur

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

## 9. Fejlfinding

### "Tablet viser gammelt UI"
→ Service worker cache. Bump `hue-vNN` i `frontend/static/sw.js`, byg, genstart, force-stop Chrome.

### "ADB: device not found"
→ `adb connect 192.168.86.173:34111` — trådløs debugging skal være slået til i Developer Options på tabletten. Verificér med `adb devices`.

### "Hue-lamper reagerer ikke"
→ Tjek `hue_config.json` har gyldigt username. Hue bridge IP: `192.168.86.25`. Pair igen: `POST /api/hue/pair` (tryk fysisk knap på bridge først).

### "Server starter ikke"
→ Tjek port 8000 er fri: `lsof -ti:8000`. Kill eventuel zombie. Kræver Python 3.13: `python3.13 --version`.

### "Volumenknob virker ikke"
→ B&O BeoPlay A9 skal være tændt og tilgængelig på `192.168.86.153:8080`. Test: `curl http://192.168.86.153:8080/BeoZone/Zone/Sound/Volume/Speaker/Level`.

### "Skærm dæmpes ikke / ur vises ikke"
→ Dim-timer starter efter splash dismiss. Kun `pointerdown` (touch) resetter — IKKE keydown (volumeknap holdes af tablet-cover). Tjek at `/api/brightness/{level}` virker: `curl -X PUT http://localhost:8000/api/brightness/12`.

---

## 10. Git

- **Aktiv branch**: `main`
- **Default branch på GitHub**: `master`
- **Backup-branch**: `category-swipe-backup` (gammel experimental swipe-UI)

---

## 11. Dependencies

**Backend** (`pip install -r backend/requirements.txt`):
- fastapi, uvicorn, httpx, zeroconf

**Frontend** (`cd frontend && npm install`):
- SvelteKit, Svelte 5, Vite

**System**:
- Python 3.13
- Node.js (til frontend build)
- ADB (Android Debug Bridge) — skal kunne nå tablet via netværk
