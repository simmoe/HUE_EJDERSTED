#!/usr/bin/env bash
# Move the existing garden Chrome tab onto the MagicDNS URL that matches
# the Let's Encrypt cert. Uses DevTools Page.navigate — never an Android
# VIEW intent (that opens a second tab that keeps the camera).
#
# From the Mac, garden kiosk on LAN (or after ADB over Tailscale):
#   ./scripts/kiosk-goto-public-url.sh
#   ADB_SERIAL=192.168.8.135:5555 ./scripts/kiosk-goto-public-url.sh
#   ./scripts/kiosk-goto-public-url.sh --always-on-vpn

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB_SERIAL="${ADB_SERIAL:-192.168.8.135:5555}"
ALWAYS_ON=0
URL=""

for arg in "$@"; do
  case "$arg" in
    --always-on-vpn) ALWAYS_ON=1 ;;
    https://*) URL="$arg" ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$URL" ]]; then
  if [[ ! -f "$ROOT/certs/public-url.txt" ]]; then
    echo "Missing $ROOT/certs/public-url.txt — run provision-tls-cert.sh on the Pi." >&2
    exit 1
  fi
  URL="$(tr -d '\r' < "$ROOT/certs/public-url.txt")"
fi

if [[ "$URL" == *"192.168."* ]] || [[ "$URL" == *"://100."* ]]; then
  echo "Refusing IP URL $URL — that is the cert-name mismatch." >&2
  exit 1
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not on PATH" >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "node not on PATH" >&2
  exit 1
fi

adb connect "$ADB_SERIAL" >/dev/null
state="$(adb -s "$ADB_SERIAL" get-state 2>/dev/null | tr -d '\r' || true)"
if [[ "$state" != "device" ]]; then
  echo "ADB $ADB_SERIAL is not a device (state=${state:-none})." >&2
  exit 1
fi

if [[ "$ALWAYS_ON" -eq 1 ]]; then
  # Tailscale MagicDNS then resolves the cert name. Lockdown stays off so
  # LAN still works if the VPN drops.
  adb -s "$ADB_SERIAL" shell settings put global always_on_vpn_app com.tailscale.ipn
  adb -s "$ADB_SERIAL" shell settings put global always_on_vpn_lockdown 0
  echo "→ always-on VPN = Tailscale (lockdown off)"
fi

PID="$(adb -s "$ADB_SERIAL" shell pidof com.android.chrome | tr -d '\r' | awk '{print $1}')"
if [[ -z "$PID" ]]; then
  echo "Chrome is not running on $ADB_SERIAL" >&2
  exit 1
fi

adb -s "$ADB_SERIAL" forward --remove tcp:9222 >/dev/null 2>&1 || true
# Samsung Chrome publishes @chrome_devtools_remote (no pid). The pid-scoped
# socket exists on some builds but returns empty replies on the A12.
if ! adb -s "$ADB_SERIAL" forward tcp:9222 localabstract:chrome_devtools_remote; then
  adb -s "$ADB_SERIAL" forward tcp:9222 "localabstract:chrome_devtools_remote_${PID}"
fi

export KIOSK_GOTO_URL="$URL"
node <<'NODE'
const url = process.env.KIOSK_GOTO_URL;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function listTabs() {
  const res = await fetch("http://127.0.0.1:9222/json/list");
  if (!res.ok) throw new Error(`DevTools list ${res.status}`);
  const tabs = await res.json();
  return tabs.filter((t) => t.type === "page");
}

function isKiosk(tab) {
  return /8443|192\.168\.8\.133|kolonihave-pi|hue/i.test(tab.url || "");
}

(async () => {
  let pages = [];
  for (let i = 0; i < 20; i++) {
    try {
      pages = await listTabs();
      if (pages.length) break;
    } catch {}
    await sleep(250);
  }
  if (!pages.length) {
    console.error("No Chrome page targets on :9222");
    process.exit(1);
  }

  const keep = pages.find(isKiosk) || pages[0];
  for (const tab of pages) {
    if (tab.id !== keep.id && isKiosk(tab)) {
      await fetch(`http://127.0.0.1:9222/json/close/${tab.id}`).catch(() => {});
    }
  }

  const wsUrl = keep.webSocketDebuggerUrl;
  if (!wsUrl) {
    console.error("Tab has no webSocketDebuggerUrl");
    process.exit(1);
  }

  await new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timer = setTimeout(() => {
      ws.close();
      reject(new Error("CDP timeout"));
    }, 8000);
    ws.addEventListener("open", () => {
      ws.send(JSON.stringify({ id: 1, method: "Page.enable" }));
      ws.send(
        JSON.stringify({
          id: 2,
          method: "Page.navigate",
          params: { url },
        })
      );
    });
    ws.addEventListener("message", (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.id === 2) {
        clearTimeout(timer);
        ws.close();
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve();
      }
    });
    ws.addEventListener("error", () => {
      clearTimeout(timer);
      reject(new Error("CDP socket error"));
    });
  });

  console.log("→ navigated kiosk tab to", url);
})().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
NODE
