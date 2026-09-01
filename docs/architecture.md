# Ejdersted Hub Architecture

`HUE_EJDERSTED` is one application and one release line deployed to two
independent physical hubs. Device differences are runtime configuration, never
long-lived Git branches.

| Profile | Physical site | Camera role | Network contract |
|---|---|---|---|
| `home` | Ejderstedgade/Vesterbro | read-only `viewer` | Private internal HTTPS; no public ingress or port forwarding |
| `garden` | Kolonihaven | `publisher` and feed owner | Trusted HTTPS through Tailscale MagicDNS |

Private and HTTPS describe different properties. The home hub uses HTTPS on its
private network, but that does not make it internet-accessible. The garden hub
is reachable only by members of the tailnet; it is not publicly port-forwarded.

## Surveillance data flow

```text
Garden Android kiosk
  └─ getUserMedia() → JPEG snapshot every ~2 seconds
       └─ HTTPS POST /api/camera/snapshot
            └─ Garden Pi
                 ├─ latest snapshot
                 ├─ motion/person detection
                 └─ security/evidence state

Ejderstedgade browser
  └─ same-origin GET /api/camera/*
       └─ Ejderstedgade backend (read-only proxy)
            └─ verified Tailscale HTTPS
                 └─ Garden Pi
```

The garden Pi is the sole owner of snapshots and security state. The
Ejderstedgade backend proxies only reads (`status`, `latest.jpg`, security state
and evidence). It never proxies upload, arming, ADB or brightness mutations.
The home browser therefore never needs a cross-origin camera URL and can never
turn itself into a publisher.

Garden publishing is allowed only when both conditions hold:

1. runtime role is `publisher` on the `garden` profile; and
2. the request comes from a configured garden kiosk host.

Tailscale membership alone does not grant write access.

## Runtime configuration

Configuration is supplied by machine/global environment and written to
`/etc/hue/runtime.env` during deploy. `/api/config` exposes only browser-safe
fields; the upstream garden URL remains backend-only.

Key fields:

- `HUB_SITE=home|garden`
- `HUB_CAMERA_MODE=viewer|publisher`
- `HUB_GARDEN_HUB_URL=https://kolonihave-pi.tail7947c4.ts.net:8443`
- `HUB_CAMERA_PUBLISHER_HOSTS=<comma-separated kiosk source IPs>`
- `HUB_PUBLIC_URL`, feature flags, kiosk/ADB values and audio target settings

`home` requires `viewer`; `garden` requires `publisher`. Invalid sites, roles
and booleans fail fast instead of silently selecting a different deployment.

## Operations and releases

- `main` is the canonical code line for both hubs.
- A deploy records the exact Git commit in `HUB_RELEASE`.
- `GET /api/health` reports release, profile and camera role.
- `deploy.sh home` and `deploy.sh garden` validate that target and profile match.
- Garden TLS uses a Tailscale-issued Let's Encrypt certificate for its MagicDNS
  hostname. Never configure the upstream with a raw `100.x` address because the
  certificate name will not match.

## Playback session

Music and podcasts share one session on both hubs. `activeTransport` is
`spotify` or `podcast`; the kiosk now-playing card follows that session, not
speaker Mozart events. Engines are interchangeable backends:

- home: Spotify Connect + B&O DLNA
- garden: librespot + mpg123/ffmpeg over BlueALSA

Claiming one engine stops the other without wiping the paused queue/position.
Podcast polls must not steal a claimed music session.

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
