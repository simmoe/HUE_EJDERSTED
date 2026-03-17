# B&O Volume Controller

Simpel webapp til at styre volumen på Bang & Olufsen **Mozart-platform** højttalere (A9 gen 4, M5, Beosound Emerge, Beosound Level, m.fl.) over det lokale netværk.

Virker uanset lydkilde — AirPlay, Bluetooth, Spotify Connect osv. — fordi volumen styres direkte via højttalerens REST API, ikke via lydstrømmens protokol.

---

## Krav

- Python 3.9+
- Højttalere og computer på **samme lokale netværk** (WiFi eller LAN)
- B&O Mozart-platform enhed (A9 4. gen, M5, Beosound Emerge, Level, Theatre, Stage)

---

## Installation

```bash
cd HUE_EJDERSTED

# Installer afhængigheder (bruger Python 3.13 fra /usr/local/bin)
/usr/local/bin/python3.13 -m pip install -r requirements.txt
```

> På macOS med Xcode-problemer: brug `/usr/local/bin/python3.13` i stedet for `python3`.

## Start

```bash
/usr/local/bin/python3.13 app.py
```

Åbn derefter **http://localhost:5000** i en browser.

For at styre fra mobil: åbn **http://<din-macs-ip>:5000** på mobilens browser.  
Find din Macs IP med `ipconfig getifaddr en0` i Terminal.

---

## Auto-opdagelse

Appen søger automatisk efter B&O Mozart-enheder via **mDNS/Bonjour** (`_beoremote._tcp`).  
Opdages en enhed, tilføjes den automatisk og gemmes i `devices.json`.

Virker ikke auto-opdagelse (f.eks. pga. netværksindstillinger), kan du tilføje enheder manuelt:

1. Find højttalerens IP-adresse i din **routers DHCP-tabel** eller i **B&O-appen** under enhedsinfo.
2. Skriv IP'en i feltet "Tilføj enhed manuelt" i webapp'en.

---

## Højttalerens Mozart REST API

Appen kalder disse endepunkter direkte på højttaleren:

| Metode | URL | Beskrivelse |
|--------|-----|-------------|
| `GET`  | `http://<ip>:8080/BeoZone/Zone/Sound/Volume/Speaker/Level` | Hent nuværende volumen |
| `PUT`  | `http://<ip>:8080/BeoZone/Zone/Sound/Volume/Speaker/Level` | Sæt volumen (`{"level": 50}`) |

Ingen autentificering kræves på lokalt netværk.

---

## Fejlfinding

**Højttaleren vises som offline**  
- Tjek at højttaleren er på samme netværk
- Prøv at pinge: `ping <højttaler-ip>`
- Prøv direkte: `curl http://<ip>:8080/BeoZone/Zone/Sound/Volume/Speaker/Level`

**Auto-opdagelse finder ingenting**  
- mDNS kan blokeres af managed/enterprise netværk
- Tilføj IP manuelt — find IP i B&O-appen eller din routers interface

**"Invalid IP/hostname" for et hostname**  
- Prøv med IP-adressen i stedet
- Tjek at `.local`-navne virker: `ping BeosoundA9.local`
