# KIOSK — Komplet opslagsdokument til sprogmodeller

> Denne fil indeholder alt, en AI-assistent behøver for at starte, genstarte,
> deploye og fejlfinde kiosk-setuppet i **HUE_EJDERSTED**-projektet.

---

## 1. Overblik

En Android-telefon kører Chrome eller kiosk-app i fuldskærm og viser en
touch-baseret home automation UI. Backend er en FastAPI Python-server der kører
på en Raspberry Pi eller en Mac.

Appen bruger runtime-profiler:

- `home`: Vesterbro-kiosk med B&O, Philips Hue, Spotify, podcasts og ADB-kiosk.
- `garden`: kolonihave-kiosk med telefonens kamera først og home-only moduler deaktiveret.

Se også `docs/architecture.md`, `docs/home.md` og `docs/garden.md`.

### Kiosk-skærm — Samsung Galaxy A12

| Specifikation | Værdi |
|---|---|
| **Model** | Samsung Galaxy A12 (SM-A125F m.fl.) |
| **Skærm** | 6,5" PLS LCD, 60 Hz, 20∶9 |
| **Panel (fysiske pixels)** | **720 × 1600** (portræt, kort kant × lang) — i **landskab** er det **1600 × 720** fysisk |
| **Kabinet (H×B×D)** | **164 × 75,8 × 8,9** mm |
| **Det web ser (CSS-pixels)** | **Ikke** 1600×720. Chrome bruger **logiske/layout-pixels**; typisk **`devicePixelRatio` ≈ 2**, så landskab ofte ca. **800 × 360** `innerWidth`×`innerHeight` (halvdelen ≈ **400 × 360** pr. kolonne). |

**Hvorfor det føles “mere kompakt” på telefonen**

- **Density / DPR:** Én **CSS px** svarer til flere **hardware-pixels** (fx 2×). UI måles i CSS px — derfor færre “enheder” end 1600×720.
- **Samsung “Skærmstørrelse” / “Skriftstørrelse”** (Indstillinger → Skærm): gør indhold tættere eller luftigere uden at ændre panellets fysiske 720×1600.
- **`width=device-width`** i `app.html`: viewport = den logiske bredde Android giver siden — følger systemets density-bucket.

**Tjek på enheden** (Chrome → Fjernfejl / `chrome://inspect`, eller midlertidig `console.log`):  
`innerWidth`, `innerHeight`, `devicePixelRatio`, `screen.width`, `screen.height`.

Reference: [GSMArena — Galaxy A12](https://www.gsmarena.com/samsung_galaxy_a12-10604.php).

---

## 2. Netværk

Home-profilens enheder har faste IP'er via Google Home DHCP-reservationer.
Garden-profilens konkrete IP'er og credentials kommer fra global/maskin-lokal
secret setup som environment variables, ikke fra projekt-lokale `.env` eller
JSON-filer.

**Garden dashboard via Tailscale**: Det uafhængige garden-dashboard åbnes udefra
via Tailscale, ikke via Vesterbro LAN og ikke ved at refreshe Android-kioskerne.
Brug `https://100.111.167.54:8443/dashboard` eller
`https://kolonihave-pi:8443/dashboard` hvis MagicDNS virker. Dashboardet er kun
viewer/control-flade; garden-kiosken selv kører stadig lokalt på garden-Pi'en.

**Garden audio target**: Androiden er kun kiosk/UI. Pi'en er audio-controller og
skal sende Spotify-afspilning til kendte output targets. Aktuelt kendt target er
`garden_storm_lite` (`bluealsa`, `NowGo Storm Lite`,
`F4:4E:FD:57:3A:AB`). Nye Bluetooth-højttalere tilføjes manuelt i
global/local config; frontend skal kun kunne vise status og kalde "forbind igen"
på et kendt target, hvis højttaleren har været koblet til en telefon.

| Enhed | IP | Port | Bemærkning |
|---|---|---|---|
| Raspberry Pi 5 (home server) | `192.168.86.16` | `8443` (HTTPS) | SSH user: `simmoe`; password/key is local-only |
| Raspberry Pi (garden, Tailscale) | `100.111.167.54` / `kolonihave-pi` | `8443` (HTTPS) | Bruges til `/dashboard` udefra |
| Mac (dev) | `192.168.86.13` | `8443` (HTTPS) | Kun til udvikling |
| Kiosk-telefon (Samsung Galaxy A12) | `192.168.86.15` | ADB: variabel | Trådløs ADB port skifter ved genstart |
| Philips Hue Bridge | `192.168.86.25` | HTTPS (clipv2) | |
| B&O BeoPlay A9 | `192.168.86.153` | `8080` (Mozart API) | |
| B&O BeoSound M5 | `192.168.86.188` | `8080` (Mozart API) | BeoLink multiroom med A9 |

**ADB**: Telefonens trådløse debugging-port skifter ved hver genstart.
Find ny port i Developer Options → Wireless debugging.
Eksempel: `adb connect 192.168.86.15:36873`

**HTTPS**: Garden bruger Let's Encrypt-certifikater udstedt via Tailscale
(`scripts/provision-tls-cert.sh`). Chrome stoler på dem uden interstitial.
Kiosk-URL skal bruge MagicDNS-navnet (fx `https://kolonihave-pi.<tailnet>.ts.net:8443`),
ikke rå LAN-IP — ellers matcher certifikatet ikke. Installér Tailscale på
Android-kiosken (samme tailnet + MagicDNS), eller lav en LAN-DNS-rewrite til Pi'en.

Home/dev kan stadig bruge mkcert i `certs/` lokalt.

Aktiver først **HTTPS Certificates** i Tailscale admin → DNS, kør derefter på Pi'en:

```bash
./scripts/provision-tls-cert.sh
sudo cp scripts/hue-tls-renew.service scripts/hue-tls-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hue-tls-renew.timer
sudo systemctl restart hue
```

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
├── deploy.sh            ← Profilbaseret deploy-script: Mac → Pi
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
ssh "$PI_HOST" "sudo systemctl status hue --no-pager"

# Genstart
ssh "$PI_HOST" "sudo systemctl restart hue"

# Se logs
ssh "$PI_HOST" "sudo journalctl -u hue -f"
```

**Kiosk URL (Galaxy A12 / Chrome)**: `https://192.168.86.16:8443`

---

## 5. Hurtigstart — "genstart kiosk"

Kør disse trin fra projektets rodmappe på Mac'en:

```bash
# Fra Pi (SSH) — port 5555 kan sættes via `adb tcpip 5555` + lockdown_tablet.sh
ssh "$PI_HOST"

# 1. Forbind ADB
adb connect 192.168.86.15:5555

# 2. Sæt landscape + immersive + fjern volume-HUD + åbn Chrome
ADB="192.168.86.15:5555"
adb -s $ADB shell settings put system accelerometer_rotation 0
adb -s $ADB shell settings put system user_rotation 1
adb -s $ADB shell settings put global policy_control "immersive.full=com.android.chrome"
adb -s $ADB shell appops set com.android.systemui SYSTEM_ALERT_WINDOW deny
adb -s $ADB shell am force-stop com.android.chrome
adb -s $ADB shell am start -a android.intent.action.VIEW -d "https://192.168.86.16:8443" com.android.chrome
```

**NB**: Den fysiske volumenknap sidder i klemme pga. kiosk-kabinettet. Kør `SYSTEM_ALERT_WINDOW deny`
(og gerne `TOAST_WINDOW deny`) efter genstart — **også via** `POST /api/kiosk` ved splash.

### Kan man bare «slukke for system overlays»?

**Nej, ikke på den måde man ofte tror.** I Android betyder «overlays» typisk enten:

- **Tredjepartsapps** der tegner ovenpå andre apps (*Indstillinger → Apps → Vis over andre apps*), eller  
- **Udviklertilstand → Deaktiver HW-overlays** (kun compositor/GPU-sti — intet med volumen-UI at gøre).

**Samsungs volumen-panel** er en integreret del af `com.android.systemui`. Det er *ikke* et almindeligt
«draw on top»-overlay, så der findes **ingen én global ADB-toggle** der slår *alle* systemdialoger fra.

- `appops … SYSTEM_ALERT_WINDOW deny` hjælper **når det virker på din One UI-build**, men **er ikke garanteret**
  mod hardware-volumen-hold (stuck key) — Samsung kan stadig vise panelet.
- **Uden root**: den eneste pålidelige løsning er at **løsne den fysiske knap** eller **blokere
  input-enheden** (kræver typisk root / specialværktøj).

Første gang skal det self-signed certifikat accepteres i Chrome (Avanceret → Fortsæt).

---

## 6. Deploy (Mac → Pi)

> **Regel**: Deploy kun til Pi når en feature er klar til test på kiosk-telefonen.  
> Under fejlretning og iterativ udvikling: brug `npm run dev` lokalt (Vite på :5173).

```bash
# Home
./deploy.sh home

# Garden
./deploy.sh garden
```

Export deploy/runtime-værdier fra global setup før deploy, fx `PI_HOST`,
`PI_PASS`, `HUB_SITE`, `HUB_PUBLIC_URL`, `HUB_KIOSK_ADB_SERIAL` og `KIOSK_URL`.
Real credentials, IP overrides and kiosk URLs skal holdes uden for projekttræet.

**Cache-version**: Filen `frontend/static/sw.js` har `const CACHE = 'hue-vNN'`.
Bump ALTID dette tal inden build — ellers ser telefonen den gamle version.

---

## 7. ADB kiosk-kommandoer (reference)

Disse kan trigges via `POST /api/kiosk`. ADB er installeret på Pi'en og parret med kiosk-telefonen.
Backend finder dynamisk telefonens ADB-serial via `_get_adb_serial()` (kører `adb devices`).

```bash
ADB="adb -s 192.168.86.15:5555"

# Lås landskab
$ADB shell settings put system accelerometer_rotation 0
$ADB shell settings put system user_rotation 1

# Manuel lysstyrke, maks
$ADB shell settings put system screen_brightness_mode 0
$ADB shell settings put system screen_brightness 255

# Skjul SystemUI-overlays (volume-HUD, toasts — virker ikke altid mod Samsung volume-dialog)
$ADB shell cmd appops set com.android.systemui SYSTEM_ALERT_WINDOW deny
$ADB shell cmd appops set com.android.systemui TOAST_WINDOW deny

# Immersive mode (fjern system bars)
$ADB shell settings put global policy_control "immersive.full=com.android.chrome"
```

For at genaktivere: `cmd appops set com.android.systemui SYSTEM_ALERT_WINDOW allow` (og evt. `TOAST_WINDOW allow`).

**Bemærk**: Uden `adb tcpip 5555` (eller første `lockdown_tablet.sh`) skifter trådløs ADB-port ved genstart — ellers brug **5555** som i netværkstabellen.

---

## 8. API-endpoints (vigtigste)

| Metode | Endpoint | Beskrivelse |
|---|---|---|
| `WS` | `/ws` | WebSocket — init state + push updates |
| `GET` | `/api/devices` | Liste over B&O-enheder |
| `POST` | `/api/devices` | Tilføj B&O-enhed manuelt |
| `GET` | `/api/hue/status` | Hue bridge status (paired/ip) |
| `POST` | `/api/hue/pair` | Par Hue bridge (tryk fysisk knap først) |
| `PUT` | `/api/brightness/{level}` | Sæt skærmlysstyrke på kiosk-telefon via ADB (0–255) |
| `POST` | `/api/kiosk` | Kør alle ADB kiosk-kommandoer |
| `GET` | `/api/spotify/status` | Spotify auth status |
| `POST` | `/api/spotify/voice` | Stemmesøgning (EN/DA) → afspil på M5 + BeoLink A9 |
| `POST` | `/api/spotify/resume` | Genoptag afspilning |
| `POST` | `/api/spotify/pause` | Pause afspilning |
| `POST` | `/api/spotify/skip` | Næste track (Spotify Connect) |
| `POST` | `/api/spotify/previous` | Forrige track / start forfra (Spotify Connect) |
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
- **Dim/clock**: Efter 30s inaktivitet dæmpes skærmen (ADB brightness → 60) og et ur vises. Touch (pointerdown) vækker.
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

### "Kiosk-telefonen viser gammelt UI"
→ Service worker cache. Bump `hue-vNN` i `frontend/static/sw.js`, byg, deploy, force-stop Chrome.

### "ADB: device not found"
→ Telefonens trådløse debugging-port skifter. Tjek ny port i Developer Options → Wireless debugging. `adb connect 192.168.86.15:<NY_PORT>`. Verificér med `adb devices`.

### "Hue-lamper reagerer ikke"
→ Tjek `hue_config.json` har gyldigt username. Hue bridge IP: `192.168.86.25`. Pair igen: `POST /api/hue/pair` (tryk fysisk knap på bridge først).

### "Server på Pi starter ikke"
→ `sudo journalctl -u hue -n 30` for logs. Tjek at `hue.service` er enabled: `sudo systemctl is-enabled hue`. Tjek Python-deps: `pip3 list | grep fastapi`.

### "Volumenknob virker ikke"
→ B&O BeoPlay A9 skal være tændt og tilgængelig på `192.168.86.153:8080`. Test: `curl http://192.168.86.153:8080/BeoZone/Zone/Sound/Volume/Speaker/Level`.

### "Skærm dæmpes ikke / ur vises ikke"
→ Dim-timer starter efter splash dismiss. Kun `pointerdown` (touch) resetter. ADB er installeret og parret på Pi — brightness-kommandoer kører fra begge. Tjek at `/api/brightness/{level}` virker.

### "Volumeknap i klemme / Samsung volume-slider dækker kiosken"
→ Se **§5** (*Kan man bare «slukke for system overlays»?*). Kør `POST /api/kiosk` (splash) eller manuelt `cmd appops set … deny`. Hvis panelet stadig kommer: One UI ignorer det ofte ved **holdt hardware-tast** — overvej fysisk at løsne knappen, root/key-layout, eller kiosk-app med device owner.

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
