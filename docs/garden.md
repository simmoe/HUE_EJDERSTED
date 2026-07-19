# Garden Kiosk

The garden setup uses the same app as the home kiosk, but with a `garden` profile.

Initial scope:

- Pi hosts the web app over HTTPS.
- Android phone is the kiosk and camera.
- `CameraCard` uses the phone browser's `navigator.mediaDevices.getUserMedia()`.
- Only the configured Android kiosk may publish camera snapshots. Remote browsers
  render the latest kiosk snapshot instead of opening their own camera.
- Hue and every Vesterbro B&O route are disabled. Garden audio uses only the
  configured BlueALSA speaker and the local `Ejdersted Garden` Connect endpoint.
- Remote access should use Tailscale or another private tunnel, not public camera port forwarding.

## Runtime Config

Garden-specific values should come from the machine/global setup as environment
variables, not project-local env or JSON files.

Typical garden feature flags:

```bash
HUB_SITE=garden
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
