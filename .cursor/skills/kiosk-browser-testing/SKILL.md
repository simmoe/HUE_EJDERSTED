---
name: kiosk-browser-testing
description: Handles testing and debugging of the Ejdersted Android kiosk interfaces. Use whenever a change must be tested on either kiosk or the user is asked to retry an interaction.
---

# Kiosk browser testing

- Diagnose reported UI behavior from the relevant code path first. Treat specific UI messages according to the event that emits them; do not begin broad device troubleshooting when the message already proves the device capability worked.
- Deploy the changed frontend to **both** hubs (home and garden) in the same turn, then remotely reload **both** Android Chrome kiosks before asking Simon to test. A single-site deploy is not finished.
- Reload the existing kiosk tab through Chrome DevTools (`Page.reload`); do not use an Android VIEW intent for routine refreshes because Chrome opens duplicate tabs that can retain camera and microphone resources.
- If duplicate kiosk tabs already exist, close the stale tabs through the DevTools `/json/close/{id}` endpoint before reloading the remaining tab.
- Verify that Chrome opened the updated page and asset/cache version through ADB or remote debugging.
- Never ask Simon to swipe, navigate, refresh, or otherwise escape kiosk mode when ADB can perform the action.
- Use the kiosk address and deployment details from the project registry. Use Tailscale for the garden Pi when Simon is away from its LAN.
