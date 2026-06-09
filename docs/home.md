# Home Kiosk

The home profile is the Vesterbro apartment setup.

Enabled features:

- B&O Mozart volume and now-playing
- Philips Hue room brightness
- Spotify voice/search/playback
- Podcast and playlist views
- Android kiosk controls through ADB
- Camera card as an additional kiosk page

If no environment overrides exist, the backend uses home-compatible defaults to
preserve the existing behavior. Values that differ from defaults should come
from the machine/global setup as environment variables, not project-local env or
JSON files.

## Deploy

```bash
./deploy.sh home
```

Export deploy credentials and host-specific values from the global/local secret
setup before running deploy. Keep real passwords, tokens, Hue usernames, Spotify
config and Firebase config out of the project tree.
