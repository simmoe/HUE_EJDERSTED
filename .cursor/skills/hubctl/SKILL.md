---
name: hubctl
description: Deploy static/backend, read kiosk heartbeat, and reload kiosk Chrome via scripts/hubctl. Use instead of rediscovering SSH passwords, scp, ADB, or CDP.
---

# hubctl

Run from the repo root. Never print vault passwords.

```bash
scripts/hubctl status
scripts/hubctl static garden|home|both
scripts/hubctl backend garden|home|both
scripts/hubctl reload home|garden
```

Before the first deploy after 28 Sep 2026, read `docs/til-den-anden-agent.md`. Do not rebuild the garden page from current `main` to repair Sunday's overwrite.

`hubctl backend` uploads Python only. It does not copy `backend/static` and it does not write `static-id`. The live page is `/home/simmoe/HUE_EJDERSTED/served`, which a copy of `backend/` cannot replace once that directory exists and the new process is running.

`hubctl static` builds a fresh page from published `main`, stamps the commit into `index.html` and `build-id`, and swaps it onto `served/` only after the uploaded `build-id` matches HEAD. It never uploads the checkout's `backend/static`.

Do not scp `backend/`, `backend/static`, or `served` by hand.

`status` curls `/api/health` and `/api/kiosk/status` (ADB heartbeat, also in Firestore `ejdersted/kiosk_{site}`). Trust `buildId` over `staticId` when both are set. `staticMismatch` means the stamp is not the page.

If `kiosk.adb` is false, do not `reload` that site. Say the tablet is unreachable.

Do not `reload` for favicon/PWA/icon work. iOS Safari caches `/apple-touch-icon.png` at the origin even in a private window; that is a phone Settings → Safari website-data problem, not a kiosk CDP problem.
