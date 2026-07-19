# Ejdersted Hub Architecture

`HUE_EJDERSTED` is one kiosk/hub application with runtime profiles.

- `home`: Vesterbro apartment kiosk with B&O, Philips Hue, Spotify, podcasts and ADB kiosk controls.
- `garden`: kolonihave kiosk with the Android phone camera as the first feature. Home-only integrations are disabled until the garden gets speakers/lights.

## Runtime Shape

```text
Android phone browser
  - renders Svelte kiosk UI
  - uses getUserMedia() for the phone camera
  - is the only garden client allowed to publish camera snapshots
  - talks to Pi over HTTPS/WebSocket

Raspberry Pi
  - runs FastAPI backend
  - serves the Svelte static build
  - optionally controls the Android phone through ADB
  - runs garden camera motion gate + local ONNX person detector
  - exposes the hub over HTTPS, normally port 8443
```

## Configuration

The app reads runtime config from environment variables supplied by the machine
or global setup. If no overrides are present, the backend uses home-compatible
defaults.

Important fields:

- `site`: `home` or `garden`
- `publicUrl`: the URL the kiosk should open
- `features`: toggles for `camera`, `audio`, `hue`, `spotify`, `podcasts`, `playlists`, `adbKiosk`
- `kiosk`: Android phone IP / ADB serial
- `audio.spotifyDevice`: garden's required Spotify Connect endpoint, normally `Ejdersted Garden`
- `speakers`: fixed B&O speakers to pre-seed when `audio` is enabled

The browser receives safe config via `GET /api/config`.

Common overrides include `HUB_SITE`, `HUB_PUBLIC_URL`,
`HUB_FEATURE_CAMERA`, `HUB_FEATURE_AUDIO`, `HUB_FEATURE_HUE`,
`HUB_FEATURE_SPOTIFY`, `HUB_FEATURE_PODCASTS`, `HUB_FEATURE_PLAYLISTS`,
`HUB_FEATURE_ADBKIOSK`, `HUB_KIOSK_PHONE_IP`, and `HUB_KIOSK_ADB_SERIAL`.

## Firestore Shape

Home and garden share playlist library data, but not physical player state.

- `ejdersted/radioPlaylists` is the shared library for saved radio playlists and
  saved songs.
- `ejdersted/player_home` is the Vesterbro player runtime: current queue, index,
  playing flag, transport and podcast state.
- `ejdersted/player_garden` is the garden player runtime with the same shape,
  independent from Vesterbro.
- `ejdersted/security_garden` is the garden security/presence state: armed flag,
  presence state, alert flag, camera/model health, timestamps, confidence and
  evidence metadata.

The legacy `ejdersted/playlists` document is only used as a one-time seed when a
site-specific player document does not exist yet.

## Garden Security Camera

Garden presence is a staged local pipeline. The Android kiosk uploads snapshots;
the Pi first runs cheap whole-frame motion detection, then confirms candidate
frames with a local `yolov8n.onnx` model through `onnxruntime`. A state machine
requires repeated confirmations before setting `presence=home`.

The UI can show `Ingen hjemme`, `Tjekker...`, `Nogen hjemme`, `Ukendt`,
`Kamera blindt` or `ALARM`. Stale snapshots, low light or model failures do not
collapse to `Ingen hjemme`.

On the first `empty -> home` transition, the backend stores an evidence snapshot
under `ejdersted/garden/events/{eventId}/snapshot.jpg` in Firebase Storage using
a dedicated create-only service account. Storage rules permit evidence reads but
deny every client write; the Pi uploads through bucket IAM and cannot overwrite
or delete existing objects. A local fallback remains under `runtime/camera/events/`.
