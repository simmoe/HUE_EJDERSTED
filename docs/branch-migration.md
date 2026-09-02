# Branch policy

`main` is the only live line. Agents commit and push there. No feature
branches, no `cursor/*` cloud-agent branches, no PRs unless explicitly asked.

`master` is a stale historical snapshot. Do not deploy it.

Leftover remotes (`cursor/*`, `feature/garden-camera-kiosk`) are obsolete.
Their work is already on `main` (TLS via later commits; dual-kiosk via PR #3;
playback session via the `main` tip).

## Rollback

Record the previously deployed release from `/api/health` before each deploy.
If validation fails, redeploy that known commit with the same machine-local
profile. Do not roll back by switching branch.
