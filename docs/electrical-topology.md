# Garden electrical topology (loads)

Canonical map of **what draws from Fossibot** in the kolonihave hut. Use this
when discussing power budgeting, camera interval, DC/USB policy, or “should we
turn something on”. **Not** a solar/array diagram — measure **load (W / Wh)**
on these rails regardless of how much PV is coming in.

Default when nobody is there: **230 V AC off**. Pi + router + phone stay on the
Fossibot **12 V / 5 V** groups so the hub and kiosk keep working.

## Rails (Fossibot F2400)

| Rail | Physical | Loads in this repo’s mental model |
| --- | --- | --- |
| **12 V DC** | DC group (`dcOn` in Firestore samples) | **Huawei B535** (router) and **Raspberry Pi 5** each **directly** on Fossibot 12 V (Pi via **12 V → USB-C PD**; Pi was unreliable on Fossibot **5 V USB** — low-voltage warnings and shutdown). |
| **5 V USB** | USB group (`usbOn` in samples) | **Samsung A12** (kiosk + GardenCam) and **IKEA Vindstyrka** (temp/humidity), each on its **own** Fossibot **5 V USB** port. Phone stays ~100 % charged; count both as **continuous USB load**, not phone SOC. Pi reads Vindstyrka over **Zigbee** (coordinator on the Pi). |
| **230 V AC** | AC outlet (`acOn` in samples) | Garden **mains lamps** (Zigbee + Flare) when someone turns AC on. **Default off** when the hut is empty. Not used for Pi/router/phone. |

**Not on Fossibot rails**

- **Gate M5** (`have_laage`): own small solar / cell — separate from this diagram.
- **Sol panel → Fossibot PV input**: energy **in**, not part of load budgeting per rail above.

## One-line flow

```text
Fossibot battery
  ├─ 12 V DC ── Huawei router (direct)
  │            └─ Pi 5 (direct 12 V → USB-C PD)
  │                 ├─ USB Zigbee coordinator → Vindstyrka (RF only)
  │                 ├─ Tailscale, BLE (Fossibot), ONNX (camera path)
  │                 └─ HTTPS hub
  └─ 5 V USB ──── port A: Galaxy A12 (kiosk + camera upload)
                 └─ port B: IKEA Vindstyrka (USB power)

230 V AC (usually OFF) ── mains bulbs when AC on
```

## Firestore vs measurement

- **`ejdersted/fossibot_garden` samples**: `socPercent`, `solarWatts`, `inWatts`,
  `outWatts`, `usbOn`, `dcOn`, `acOn` — **whole-station**, not per-rail watts.
- To attribute **camera interval** or **person detection**: measure **USB W** (phone
  ± Pi if shared) or **Pi 5 V W** inline; compare **Wh per day** on similar
  weather days, not phone battery %.

## Software policy (intent)

- **Empty hut**: no extra camera rate because “someone might be home”.
- **Later**: motion-based **alarm** mode (burst images + light/sound) — separate
  from the steady low-rate feed documented in surveillance flow.

## See also

- `docs/garden.md` — Fossibot DC ports, AC policy, Pi power notes.
- `docs/architecture.md` — hub roles and camera data flow.
