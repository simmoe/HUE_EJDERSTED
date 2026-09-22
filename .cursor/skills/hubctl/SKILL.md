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

`status` curls `/api/health` and `/api/kiosk/status` (ADB heartbeat, also in Firestore `ejdersted/kiosk_{site}`).

If `kiosk.adb` is false, do not `reload` that site. Say the tablet is unreachable.

Do not `reload` for favicon/PWA/icon work. iOS Safari caches `/apple-touch-icon.png` at the origin even in a private window; that is a phone Settings → Safari website-data problem, not a kiosk CDP problem.
