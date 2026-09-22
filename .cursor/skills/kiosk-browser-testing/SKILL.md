---
name: kiosk-browser-testing
description: Debug an Ejdersted Android kiosk only when Simon reports a device bug or asks to reach the tablet. Do not use this skill to visually QA a UI change.
---

# Kiosk device access

Simon does visual QA. Use `scripts/hubctl status` first. Use
`scripts/hubctl reload home|garden` only when the kiosk will show the
change **and** heartbeat says ADB is up. Do not open a browser.

Use this skill only when Simon says a kiosk misbehaves or asks you to
talk to the tablet.

- Diagnose from the code path and the message he saw.
- Reload through `hubctl reload` (DevTools `Page.reload`). Never a VIEW intent.
- Close duplicate tabs with `/json/close/{id}` before reloading.
- Never ask Simon to swipe, navigate, refresh, or escape kiosk mode when ADB can.
- Mac LAN for the local kiosk; Tailscale for the other site.
