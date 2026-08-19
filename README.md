# HUE_EJDERSTED

FastAPI + SvelteKit kiosk/hub for Ejdersted.

The app supports runtime profiles:

- `home`: Vesterbro kiosk with B&O, Philips Hue, Spotify, podcasts, playlists and ADB kiosk controls.
- `garden`: kolonihave kiosk with the Android phone camera first, while home-only integrations are disabled.

The two devices use the same `main` code line with separate runtime profiles:

| Target | Camera role | Access |
|---|---|---|
| Ejderstedgade (`home`) | read-only garden viewer | private internal HTTPS; no public ingress |
| Kolonihaven (`garden`) | camera publisher/owner | Tailscale HTTPS via MagicDNS |

Garden remote access is through Tailscale. Open
`https://kolonihave-pi.tail7947c4.ts.net:8443/dashboard`; do not use the raw
Tailscale IP because its hostname does not match the TLS certificate.
Ejderstedgade displays the same feed through a read-only same-origin backend
proxy. Neither hub is exposed by public camera port forwarding.

See:

- `docs/architecture.md`
- `docs/home.md`
- `docs/garden.md`
- `KIOSK.md`

## Local Development

Backend:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
python backend/main.py
```

Frontend:

```bash
cd frontend
npm ci
npm run dev
```

Static build for FastAPI:

```bash
cd frontend
npm run build
```

`frontend/svelte.config.js` writes the static build to `backend/static`, which is served by `backend/main.py`.

## Runtime Config

Runtime config comes from environment variables supplied by the machine/global
setup, not from project-local `.env` or JSON files. If no overrides are present,
the backend uses home-compatible defaults.

Common overrides:

```bash
HUB_SITE=garden
HUB_PUBLIC_URL=https://host:8443
HUB_CAMERA_MODE=publisher
HUB_GARDEN_HUB_URL=https://kolonihave-pi.tail7947c4.ts.net:8443 # home only
HUB_CAMERA_PUBLISHER_HOSTS=... # garden only
HUB_FEATURE_CAMERA=true
HUB_FEATURE_AUDIO=false
HUB_FEATURE_HUE=false
HUB_FEATURE_SPOTIFY=false
HUB_FEATURE_PODCASTS=false
HUB_FEATURE_PLAYLISTS=false
HUB_FEATURE_ADBKIOSK=true
HUB_KIOSK_PHONE_IP=...
HUB_KIOSK_ADB_SERIAL=...:5555
```

## Deploy

Export deploy/runtime variables from your global setup, then deploy:

```bash
PI_HOST=simmoe@host HUB_SITE=garden HUB_CAMERA_MODE=publisher \
HUB_PUBLIC_URL=https://kolonihave-pi.tail7947c4.ts.net:8443 \
./deploy.sh garden
```

`deploy.sh` refuses a mismatched target/profile or a dirty/unpinned source tree,
records the deployed commit in `/api/health`, and requires the home camera
viewer to have `HUB_GARDEN_HUB_URL`.

Do not commit real passwords, Hue usernames, Spotify tokens, Firebase config, device state or TLS certificates.
