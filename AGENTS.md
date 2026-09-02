# Agent instructions — HUE_EJDERSTED

These rules apply to every Cursor agent (local and cloud) that opens this repo.

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
