# Garden Kiosk

The garden setup uses the same app as the home kiosk, but with a `garden` profile.

Initial scope:

- Pi hosts the web app over HTTPS.
- Android phone is the kiosk and camera.
- `CameraCard` uses the phone browser's `navigator.mediaDevices.getUserMedia()`.
- B&O, Hue, Spotify, podcasts and playlists are disabled until the garden needs them.
- Remote access should use Tailscale or another private tunnel, not public camera port forwarding.

## Runtime Config

Garden-specific values should come from the machine/global setup as environment
variables, not project-local env or JSON files.

Typical garden feature flags:

```bash
HUB_SITE=garden
HUB_FEATURE_CAMERA=true
HUB_FEATURE_AUDIO=false
HUB_FEATURE_HUE=false
HUB_FEATURE_SPOTIFY=false
HUB_FEATURE_PODCASTS=false
HUB_FEATURE_PLAYLISTS=false
HUB_FEATURE_ADBKIOSK=true
```

## Deploy

```bash
./deploy.sh garden
```

Export `PI_HOST`, optional `PI_PASS`, `KIOSK_ADB_SERIAL`, `KIOSK_URL`, and
garden `HUB_*` values from the global/local secret setup before deploy.

## Remote Dashboard POC

Open `/dashboard` on the garden Pi URL, for example:

```text
https://192.168.8.133:8443/dashboard
```

The first proof of concept is a snapshot feed: the Android kiosk keeps the camera
open locally and uploads the latest JPEG frame to the Pi every couple of seconds.
The dashboard polls that latest image and can trigger basic kiosk controls through
the existing ADB endpoints. This is not continuous video streaming yet.
