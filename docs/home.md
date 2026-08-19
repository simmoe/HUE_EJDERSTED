# Home / Ejderstedgade Kiosk

The `home` profile is the private Ejderstedgade/Vesterbro installation. It is
not exposed through public DNS, router port forwarding or public ingress.
Internal HTTPS does not change that boundary: HTTPS protects transport, while
the LAN/firewall controls reachability.

Enabled features:

- B&O Mozart volume and now-playing
- Philips Hue room brightness
- Spotify voice/search/playback
- Podcast and playlist views
- Android kiosk controls through ADB
- Read-only garden surveillance card as an additional kiosk page

## Garden surveillance viewer

The home phone never opens its own camera and the home Pi never accepts camera
uploads. The browser requests same-origin `/api/camera/*` resources from the
home Pi. That backend proxies only read operations to the garden Pi over
verified Tailscale HTTPS.

Configure the certificate-matching MagicDNS name, not the raw Tailscale IP:

```bash
HUB_SITE=home
HUB_CAMERA_MODE=viewer
HUB_GARDEN_HUB_URL=https://kolonihave-pi.tail7947c4.ts.net:8443
```

The proxy exposes snapshot/status/evidence reads. It does not proxy camera
uploads, alarm changes, ADB commands or kiosk brightness controls.

If no environment overrides exist, the backend uses home-compatible defaults to
preserve the existing behavior. Values that differ from defaults should come
from the machine/global setup as environment variables, not project-local env or
JSON files.

## Deploy

```bash
PI_HOST=simmoe@home-hub \
HUB_SITE=home \
HUB_CAMERA_MODE=viewer \
HUB_GARDEN_HUB_URL=https://kolonihave-pi.tail7947c4.ts.net:8443 \
./deploy.sh home
```

Export deploy credentials and host-specific values from the global/local secret
setup before running deploy. Keep real passwords, tokens, Hue usernames, Spotify
config and Firebase config out of the project tree.
