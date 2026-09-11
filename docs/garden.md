# Garden Kiosk

The garden setup uses the same app as the home kiosk, but with a `garden` profile.

Initial scope:

- Pi hosts the web app over HTTPS.
- Android phone is the kiosk and camera.
- `CameraCard` uses the phone browser's `navigator.mediaDevices.getUserMedia()`.
- Only the configured Android kiosk may publish camera snapshots. Remote browsers
  render the latest kiosk snapshot instead of opening their own camera.
- The garden Pi owns snapshots, person detection and evidence. Ejderstedgade
  consumes the feed through its own read-only backend proxy.
- Hue and every Vesterbro B&O route are disabled. Garden audio uses only the
  configured BlueALSA speaker and the local `Ejdersted Garden` Connect endpoint.
- Remote access should use Tailscale or another private tunnel, not public camera port forwarding.

## TLS (Let's Encrypt via Tailscale)

Do **not** keep a self-signed cert on the garden hub. Chrome periodically drops
trust (`ERR_CERT_AUTHORITY_INVALID`), snapshot uploads stop, and presence flips
to a stale/blind state even though the phone camera is still running.

Use Tailscale's Let's Encrypt integration instead:

1. Enable **HTTPS Certificates** in the Tailscale admin DNS settings.
2. On the Pi: `./scripts/provision-tls-cert.sh && sudo systemctl restart hue`
3. Point the Android kiosk at the MagicDNS URL from `certs/public-url.txt`
   (install Tailscale on the phone, same tailnet, MagicDNS on).
4. Enable the renew timer: `scripts/hue-tls-renew.service` + `.timer`

`deploy.sh garden` refreshes the cert when Tailscale is available.

## Runtime Config

Garden-specific values should come from the machine/global setup as environment
variables, not project-local env or JSON files.

Typical garden feature flags:

```bash
HUB_SITE=garden
HUB_CAMERA_MODE=publisher
HUB_CAMERA_PUBLISHER_HOSTS=<garden-phone-lan-or-tailscale-ip>
HUB_FEATURE_CAMERA=true
HUB_FEATURE_AUDIO=true
HUB_FEATURE_HUE=false
HUB_FEATURE_SPOTIFY=true
HUB_FEATURE_PODCASTS=true
HUB_FEATURE_PLAYLISTS=true
HUB_FEATURE_ADBKIOSK=true
HUB_AUDIO_SPOTIFY_DEVICE="Ejdersted Garden"
```

## Firestore

Garden shares the saved playlist library with home through
`ejdersted/radioPlaylists`, but its physical player state lives in
`ejdersted/player_garden`. The Vesterbro kiosk uses `ejdersted/player_home`.

Do not put playback runtime back into the shared playlist-library document; a
track change in one physical hub must not pause or advance the other hub.

## Audio isolation

Garden playback never falls back to an arbitrary Spotify device or to the
Vesterbro B&O/DLNA routes:

- Spotify tracks and episodes require the exact `Ejdersted Garden` Connect
  device. If its long-running connection becomes stale, the backend restarts
  `librespot` once and resolves the exact device again.
- Before Spotify starts, the backend verifies that the configured garden
  BlueALSA speaker is online. If it cannot connect, the UI receives
  `Gå hen og tænd højttaleren`.
- RSS/MP3 podcasts play locally through `mpg123`.
- Sveriges Radio AAC/M4A episodes play locally through `ffmpeg`; `ffmpeg` is
  therefore a garden Pi runtime dependency.
- The home profile uses its own Spotify target and B&O/DLNA path. It never uses
  garden BlueALSA.

## Voice commands

Garden does not use Chrome's `SpeechRecognition`: that service consistently
returned `onnomatch` on the garden Android device. The kiosk records a five
second Opus/WebM clip with `MediaRecorder`, temporarily releasing the camera
stream while the microphone is active. The backend transcribes the clip with
the existing Gemini integration and passes the resulting text through the same
`/api/spotify/voice` command parser used by typed and home-kiosk voice input.
The camera stream resumes automatically after recording.

## USB gadget (Mac recovery)

Garden Pi 5 has official `rpi-usb-gadget`. USB-C on the board is the OTG
port. Plug that into the Mac (data cable). The Pi appears as Ethernet;
SSH `simmoe@10.12.194.1`. The Mac can also power the board, so this works
when Fossibot and Alohomora are dead.

Do not use the USB-A ports for this. Leave gadget **on**.

## Pi power (Fossibot USB)

Verified 2026-09-02: the Pi 5 stays up on a Fossibot **USB output** (board
USB-C). A running Pi is enough load that the F2400 USB 5-minute idle timeout
does not trip — observed 20+ minutes and counting. Prefer the 100 W USB-C
port if it is free. Relæ 5 V/GND remains on the Pi header.

The old powerbank/UPS island is optional. USB sleep was the empty-port
problem, not “USB cannot power a Pi”.

## AC policy (SwitchBot) — floor, hold, resume

Always on for the garden hub; there is no mode switch. After each Fossibot
poll (`power.py`), top layer wins:

- **floor** SoC ≤ 15 % and AC on → press (AC off). Beats everything and
  burns a hold-on, so the outlet does not flap at the threshold.
- **hold** a tap on the kiosk's 230 V card: AC on/off until a wall-clock
  deadline chosen on the wheel — `1h · 2h · 5h · tomorrow` (tomorrow = next
  day's solar on-time, 08:00 if solar is off). Persisted in
  `power_state.json` as `{ "hold": { acOn, until, duration } }`. `auto`
  drops it.
- **resume** SoC ≥ 25 % and AC off → press (AC on).
- 15–25 % → no opinion.

WS `set_power_hold {acOn, duration}` / `clear_power_hold`; REST
`POST /api/power/hold` with the same body or `{ "clear": true }`. Every press
the Pi makes lands in Firestore `ejdersted/fossibot_garden/events` with
`source: floor | rule | hold`.

The night rule (AC off outside the sun window unless someone is home) is
**not** built: 230 V also feeds the router, so it would cut the Pi's uplink.
First move the Huawei to 12 V from the Fossibot (USB-C PD trigger cable or a
DC5521 port), then add the rule.

The finger belongs on the Fossibot **AC** button, never the main power
switch (that would kill USB and the Pi).

## Garden lights after mains

Lamps on Fossibot 230 V boot **on**. When the **resume rule** pressed within
the last 3 min and AC comes back, a sweep starts: wait ~20 s, then send a
generic `OFF` through `light_bus` (retries for 15 min until each lamp is
online and off). A hold-on from the kiosk or a finger on the Fossibot is
Simon opening the hut and does not touch the lights.

`light_bus` is the only handler (on/off/dim/colour/white). Flare is the
Tuya adapter. The bed rail is `protocol: zigbee` (Sonoff dongle on the
garden Pi, existing IKEA PAN — do not re-form). RODRET stays locally
bound. A later lamp is another `protocol` in `garden_lights.json`,
not a second policy.

Gårdlys scenes: `kraftig` / `dæmpet` (warm white) and `fest` (slow
tequila-sunrise wash). Seng is tap on/off, ±, long-press fade. Toilet
is a TRADFRI bulb plus motion sensor: the sensor binds locally to the
bulb; the kiosk only speaks to the bulb.

## Deploy

```bash
PI_HOST=simmoe@kolonihave-pi \
HUB_SITE=garden \
HUB_CAMERA_MODE=publisher \
HUB_PUBLIC_URL=https://kolonihave-pi.tail7947c4.ts.net:8443 \
./deploy.sh garden
```

Export `PI_HOST`, optional `PI_PASS`, `KIOSK_ADB_SERIAL`, `KIOSK_URL`, and
garden `HUB_*` values from the global/local secret setup before deploy.

## Remote Dashboard POC

Open the read-only `/dashboard` on the certificate-matching MagicDNS URL:

```text
https://kolonihave-pi.tail7947c4.ts.net:8443/dashboard
```

The feed uses snapshots rather than continuous video: the Android kiosk keeps the camera
open locally and uploads the latest JPEG frame to the Pi every couple of seconds.
The dashboard polls that latest image. It deliberately has no ADB, brightness or
alarm mutation controls.

The same viewer behavior is used inside the main kiosk UI for non-kiosk browsers:
opening the garden hub over Tailscale shows the Android kiosk feed, not the
remote browser's own webcam.

## Security Presence Detection

The Android kiosk still owns the camera and uploads JPEG snapshots to
`/api/camera/snapshot`. Remote browsers remain viewers only.

Presence detection is now staged:

1. A cheap whole-frame motion gate runs on every snapshot. It downsamples the
   frame, normalizes luminance, compares against a slowly learned baseline and
   flags radical image changes as candidates.
2. Candidate frames, plus periodic health checks, run through a local
   `yolov8n.onnx` person detector using `onnxruntime` on the Pi CPU.
3. The state machine requires repeated person confirmations before reporting
   `Nogen hjemme`. If snapshots are stale, too dark, or the model fails, the
   state becomes `Kamera blindt` or `Ukendt`, never a false `Ingen hjemme`.
   `Tjekker...` has a hard 20-second timeout; if no person is confirmed, the
   current stable frame becomes the new motion baseline and state returns to
   `Ingen hjemme`.

Runtime files:

- `runtime/models/yolov8n.onnx` is downloaded on demand and is not committed.
- `runtime/camera/presence.json` stores local security state.
- `runtime/camera/events/{eventId}/snapshot.jpg` stores local fallback evidence.

Security state is exposed through `/api/camera/status` and
`/api/security/garden`. The global Firestore document is
`ejdersted/security_garden`, separate from playlist/player documents.

Firebase Storage evidence uploads use:

```text
ejdersted/garden/events/{eventId}/snapshot.jpg
```

Storage upload is backend-owned. The garden Pi uses the dedicated
`garden-camera-evidence@p5-diary-ca5f7.iam.gserviceaccount.com` account through
`GOOGLE_APPLICATION_CREDENTIALS`. Its bucket role is `Storage Object Creator`,
so it can create uniquely named evidence but cannot read, overwrite or delete
existing bucket data. Browser clients cannot write directly. The credential is
mode `600` on the Pi and backed up only in the encrypted global vault.

`storage.rules` permits public reads only below the evidence path and denies all
client writes. The backend uploads through the Google Cloud Storage API using
bucket IAM, while evidence links read through the Firebase Storage API. If cloud
upload fails, evidence is still kept locally and served through
`/api/security/evidence/{eventId}.jpg`.

## Solar array (installed)

One module, bought as [500W DAH Solar Full Screen Double Glass](https://www.solaroutlet.dk/shop/500w-dah-solar-full-screen-double-glass-pv-module/).
Family is **DHN-54Z16/DG** (Full Screen listing: **DHN-54Z16/DG/FS-500**). Shop
page is marketing-only; electrical numbers below are the 500 W row of that
family's datasheet (STC). Shop thickness is 28 mm; most datasheets print 30 mm.

| | |
| --- | --- |
| Pmax | 500 W (+5 % bin) |
| Vmp / Imp | 33.9 V / 14.75 A |
| Voc / Isc | 39.9 V / 15.7 A |
| Voc temp. coeff. | −0.25 %/°C |
| Cells / bifacial | 108 TOPCon, up to ~80–85 % bifacial |
| Size / weight | 1962 × 1134 mm, ~26.6 kg |
| Connectors | MC4 |

Fossibot F2400 PV input (manual): **11.5–50 V, 20 A, 500 W**, XT90. One of these
modules sits inside that window: Vmp 33.9 V, cold-weather Voc still ~43 V at
−10 °C. Nameplate is already the station's watt ceiling; BLE showing 40–100 W
is weather/angle/clip, not a smaller panel.

Do **not** series a second module (Voc ≈ 80 V). Parallel would share voltage
but Isc ≈ 31 A against a 20 A input, and nameplate 1000 W against a 500 W
clip. Same-class pairing only if we ever add more — never mix 12 V / 18 V
classes with this one.

Fossibot samples (SoC, solar W, out W, `acOn`/`usbOn`) are written to Firestore
`ejdersted/fossibot_garden` plus `samples/{yyyyMMddTHHmm}` every five minutes,
and immediately when a port or online/charging flag flips. Intended rules for
p5-diary-ca5f7. Live rules (fetched 2026-08-30) are still the 2022 test-mode
catch-all until 2027-03-24; the fossibot match uses that same window. Do not
deploy these rules to p5-firebase-eebc1.
