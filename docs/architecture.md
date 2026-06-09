# Ejdersted Hub Architecture

`HUE_EJDERSTED` is one kiosk/hub application with runtime profiles.

- `home`: Vesterbro apartment kiosk with B&O, Philips Hue, Spotify, podcasts and ADB kiosk controls.
- `garden`: kolonihave kiosk with the Android phone camera as the first feature. Home-only integrations are disabled until the garden gets speakers/lights.

## Runtime Shape

```text
Android phone browser
  - renders Svelte kiosk UI
  - uses getUserMedia() for the phone camera
  - talks to Pi over HTTPS/WebSocket

Raspberry Pi
  - runs FastAPI backend
  - serves the Svelte static build
  - optionally controls the Android phone through ADB
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
- `speakers`: fixed B&O speakers to pre-seed when `audio` is enabled

The browser receives safe config via `GET /api/config`.

Common overrides include `HUB_SITE`, `HUB_PUBLIC_URL`,
`HUB_FEATURE_CAMERA`, `HUB_FEATURE_AUDIO`, `HUB_FEATURE_HUE`,
`HUB_FEATURE_SPOTIFY`, `HUB_FEATURE_PODCASTS`, `HUB_FEATURE_PLAYLISTS`,
`HUB_FEATURE_ADBKIOSK`, `HUB_KIOSK_PHONE_IP`, and `HUB_KIOSK_ADB_SERIAL`.
