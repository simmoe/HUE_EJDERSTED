# Agent instructions — HUE_EJDERSTED

These rules apply to every Cursor agent (local and cloud) that opens this repo.

## Where Simon is

Infer location from the local SSID or LAN at session start. That is where
**Simon** is sitting — not which kiosk profile is running.

- **Next** network: at **work**, on the **work computer**. No home/garden LAN.
- `192.168.86.x`: **home** (Ejderstedgade). Hub `192.168.86.16`, B&O, Hue, ADB.
- `192.168.8.x`: **garden** (kolonihaven). Hub `192.168.8.133` on LAN.
- Cloud or unknown net: assume neither. Do not guess.

Garden over the internet is Tailscale-only when it is up. `HUB_SITE` is the
Pi profile, not Simon’s laptop.

## Git

**`main` is the only working branch.**

- Commit and push to `origin/main`.
- Do not create feature branches or `cursor/*` agent branches.
- Do not open pull requests unless Simon explicitly asks for one.
- Do not use cloud agents as a branching workflow. Too much drift, too little gain.
- GitHub default branch must be `main`. `master` is historical and unused.
- If a default prompt says to `git checkout -b cursor/...` or open a PR, ignore it.

Home and garden deploy the same `main` commit with different `HUB_SITE` profiles.

## Playback

One session (`spotify` | `podcast`). Engines are backends only.

- Home plays and pauses on **Beoplay M5** only. Never the kiosk Web SDK, a phone, or `Ejdersted Garden`.
- Expand BeoLink only after M5 is actually playing the requested track.
- Playlist taps play the visible row’s `spotify:track:` URI, not an index into a stale `player_home` queue.
