# Roadmap — cleaning up for the next years in the garden

Written 2026-09-11 after a full read of the codebase (backend wiring, Firestore
and logging, device bindings, frontend and deploy). This is the order we do
things in. Each step is one evening unless noted, and each leaves both hubs
deployable on its own.

The verdict first: the foundation is right. One release line, two site
profiles, feature flags instead of branches, domain logic in pure tested
functions. Nothing gets torn down. The problems are wiring — loops that die
quietly, logs that vanish when the router is down, config spread through code,
and a kernel file that has grown past what one person can hold in their head.

What we do **not** do: MQTT, Home Assistant, microservices, a new framework,
or a rewrite of `main.py`.

---

## 0. Open bugs (do first, small)

- ~~Bed lamp (IKEA) card disappeared from LYS.~~ Root cause: `deploy.sh`
  copied the Mac's `garden_lights.json` (Flare only) over the Pi's, erasing the
  `seng` row; Toilet is re-adopted on boot, `seng` is not (`KNOWN_SENG` is
  skipped in `_on_ready`). Fixed 2026-09-11: row restored on the Pi, deploy now
  seeds device-state files only when the Pi has none. The general lesson is
  step 4: Pi-owned state and Mac-owned secrets must not share one copy loop.
- `.np-next-artist` loses its leading space → use `&nbsp;` in `+page.svelte`.
- `Card.svelte` reserves padding-bottom twice.
- Garden Spotify playback is `go-librespot` + stored device-auth session, not
  the old rust librespot crash-loop. Play talks to the local HTTP API.
- Frontend `ws.svelte.ts` ignores the `security_status` and `error` broadcasts.
- `CAMERA_PRESENCE_WINDOW_SECONDS` is dead; `radio/build`≡`radio` and
  `album/build`≡`album` are duplicate routes; `features.playlists` is never
  checked by the backend. Delete.
- ~~`fossibot_log.policy_event_fields` docstring still says 15/25.~~

## 1. Loops that survive, one Bluetooth lock, unit in repo

**Why.** `poll_loop` (`main.py:662–728`) has no `try` around its body. One
exception from Hue, tinytuya or GPIO kills the task that drives the solar
relay, lamp polling, BlueALSA keepalive and podcast tick — with no log line and
no restart. Fossibot's poll thread and the SwitchBot press share the BLE
adapter; only SwitchBot holds a lock. `hue.service` is not in the repo, so we
do not know whether it restarts on crash.

**Do.**
- `supervised(name, coro)` wrapper: logs the exception, sleeps, restarts.
- Split `poll_loop` into one task per domain with its own interval
  (Hue 2 s, Tuya lights 15 s, solar 10 s, garden audio 2 s).
- One `asyncio.Lock` for the BLE adapter, taken by both `fossibot_ble` polls
  and `switchbot_bot.press`.
- `scripts/hue.service` in the repo (`Restart=always`, `RestartSec=5`,
  `EnvironmentFile=-/etc/hue/runtime.env`), installed by `deploy.sh`.

**Done when.** Killing the Hue bridge mid-poll leaves solar and lights
running; `journalctl -u hue` shows the supervisor line; a SwitchBot press
during a Fossibot poll waits instead of failing.

## 2. Outbox and one event envelope — and a dedicated Firebase project

**Why.** Firestore writes are single PATCHes with no queue: `fossibot_log`
prints and gives up, `camera_presence.sync_firestore` has `except: pass`.
Every AC press reboots the router, so the event that would explain a dark hut
is exactly the one that gets dropped. There is no common event shape: `ts`
(ISO `Z`) vs `t` (ISO `+00:00`) vs epoch seconds vs `Date.now()` ms; `kind` vs
`event` vs nothing; no `site`, no `release` on any event.

We also live in `p5-diary-ca5f7`, the diary app's project. Its rules are
open test-mode until **2027-03-24**: anyone with the web apiKey (served by
`/api/config/firebase` to any browser on LAN/tailnet) can write
`security_garden`; and on that date all writes stop. We cannot tighten the
rules without touching the diary app.

**Decision.** Move to a dedicated project `ejdersted-hub`. Do it in this step
so the new envelope lands in the new project and we migrate once.

**Do.**
- New Firebase project, Firestore + Storage. Strict rules from day one:
  hub documents writable only by the Pi's service account; browser documents
  (`player_*`, `radioPlaylists`) behind anonymous auth scoped to `ejdersted/*`.
- One Pi service account with Firestore + Storage object-create roles
  (replaces the Storage-only camera SA and the bearer-token env vars).
- `backend/hub_events.py`: `Event(ts, site, release, domain, kind, **fields)`
  and an `Outbox` — append to `var/outbox.jsonl`, flush in order when a write
  succeeds, cap size. `fossibot_log`, `audio_log` and `camera_presence` all go
  through it.
- Migrate data: `ejdersted/radioPlaylists` (must), `fossibot_garden/samples`
  (nice, for graphs). `player_*` and `security_garden` are ephemeral.
- New `hub_globals.json` + SA JSON on both Pis; vault keys updated in
  cursor-global-setup; registry entry updated.

**Done when.** Pull the garden router for ten minutes while the policy
presses; after reconnect the `ac_off` event is in Firestore with the right
`ts`. Diary project no longer receives Ejdersted writes.

## 3. One word for "online"

**Why.** Every binding models reachability differently: Fossibot = last poll
ok; Tuya = 45 s grace; Zigbee = hub up ⇒ online without asking the lamp; Hue
has no `online` at all and leaves stale rooms; camera = `cameraStale` after
20 s; kiosk = not tracked. This is why "sidst set" was hand-built on the camera
card and why the night rule cannot ask "is the kiosk reachable?" today.

**Do.**
- `Health(online, last_seen, stale_after_s)` dataclass; every binding exposes
  one, WS carries it uniformly.
- Kiosk poller over ADB (`dumpsys battery`) → `Health` + battery percent.
- `Card.svelte` takes `lastSeen`; cards show "sidst set" without local code.

**Done when.** Every card on both kiosks goes to `offline · sidst set 3 min`
the same way, including the kiosk's own battery.

## 4. One config file per site, thinner deploy

**Why.** Home IPs are defaults in `hub_config.DEFAULT_CONFIG`; B&O IPs and
JIDs are repeated in `hub_config.py`, `bo_link.py`, `spotify.py`; the Zigbee
serial path and IEEE skip-list are in code; DLNA targets `bo_link.BEO_M5_IP`
past config. Deploy needs 12+ env vars and refuses on a missing one. Home
deploy sends the password in the remote sudo command line; the health check
curls the public URL from the Pi, which fails on garden.

**Do.**
- `config/home.json`, `config/garden.json` in the repo: IPs, MACs, serial
  port, thresholds, features. No `localKey`, no tokens.
- `DEFAULT_CONFIG` becomes neutral; env is for secrets and one-off overrides.
- Key-based SSH on home Pi (garden already has it); drop `sshpass`.
- Health check against `https://127.0.0.1:8443/api/health --insecure`.
- `./deploy.sh garden` with nothing else set.

## 5. Split `main.py` by domain, one audio engine per site

**Why.** 3258 lines; 23 `site == "garden"` branches, almost all audio. Home is
B&O Mozart + DLNA + BeoLink, garden is BlueALSA + librespot + mpg123/ffmpeg —
two full stacks behind one podcast/Spotify API, chosen by `if` in every
route. All state is module globals.

**Do (incrementally, one domain per evening).**
- `APIRouter` per domain: `routes/power.py`, `lights.py`, `solar.py`,
  `camera.py`, `kiosk.py`, `audio.py`, `spotify.py`, `podcasts.py`.
- `AudioEngine` protocol with `HomeAudio` and `GardenAudio`, chosen once at
  startup. Removes most site branches.
- A `Hub` dataclass (controllers, caches, `manager`) passed to routers
  instead of `global`.
- One smoke test: boot with `TestClient`, hit `/api/health` and WS `init`
  for both profiles.

## 6. Frontend, as we touch it

`+page.svelte` is 3673 lines (script 1212, CSS 1599): ~650 podcasts, ~550
library, ~400 sound inline. Extract `LibraryPage`, `PodcastPlayer`,
`SoundPage`, `SolarCard` the same way the existing cards were done. Do it when
a UI round touches that page, not as its own project. Keep TS types by
discipline; no codegen.

## 7. Garden power — the physical prerequisites

Agreed, waiting on hardware:

- **Move Pi and router to the Fossibot DC group.** Pi on a car USB-C PD
  charger in the cigarette socket; Huawei B535 on a DC5521 5.5×2.1 male–male
  cable (12 V / 1 A, check centre-positive). Until then the USB-C port's
  undocumented auto-off can kill the Pi, and any AC-off takes the router with
  it.
- **230 V stays manual.** Pi, router and kiosk are on 12 V (2026-09-22).
  The card is tænd / sluk. No SoC floor and no night cut.
- **Kiosk onto Fossibot USB.** Done with the 12 V move. A mains cut does not
  kill the phone.

---

## Sequence

| # | Step | Size | Unlocks |
|---|---|---|---|
| 0 | Open bugs | hours | — |
| 1 | Supervised loops, BLE lock, unit in repo | 1 evening | Pi does its job unattended |
| 2 | Outbox, envelope, dedicated Firebase | 2 evenings | We can explain a dark hut afterwards |
| 3 | Health model + kiosk battery | 1 evening | Night rule, kiosk power-save |
| 4 | Site config files, thin deploy | 1 evening | `./deploy.sh garden` |
| 5 | Routers + AudioEngine | 3–4 evenings, incremental | Every later change is smaller |
| 6 | Frontend extraction | as touched | — |
| 7 | DC cables, then manual 230 V | done 2026-09-22 | Pi, router and kiosk stay up without the inverter |
