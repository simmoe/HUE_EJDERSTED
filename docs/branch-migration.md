# Branch migration runbook

The repository history is linear: the former garden feature work and TLS work
are successors of the old default branch, not independent products. The safe
end state is one canonical `main` branch deployed with different runtime
profiles.

## Current migration

1. Merge the dual-kiosk architecture PR into `main`.
2. Deploy that exact resulting commit to garden with
   `HUB_SITE=garden,HUB_CAMERA_MODE=publisher`.
3. Deploy the same commit to Ejderstedgade with
   `HUB_SITE=home,HUB_CAMERA_MODE=viewer`.
4. Verify `/api/health` on both hubs reports the same release and the expected
   site/role.
5. Verify the garden publishes and the home proxy displays fresh snapshots.
6. Only then change GitHub's default branch from stale `master` to `main`.

Do not delete or rewrite `master`, `feature/garden-camera-kiosk` or the TLS
feature branch during validation. After both hubs have run the common release,
preserve any required historical tips as `archive/*` tags and remove obsolete
branches in a separate, explicitly approved operation.

## Rollback

Record the previously deployed release from `/api/health` before each deploy.
If validation fails, redeploy that known commit with the same machine-local
profile. Do not roll back by switching to a device-specific branch.
