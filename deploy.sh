#!/usr/bin/env bash
# Deploy HUE_EJDERSTED to a configured Raspberry Pi target.
# Usage: PI_HOST=simmoe@host HUB_SITE=garden ./deploy.sh <home|garden> [--no-build]

set -euo pipefail

TARGET="${1:-}"
NO_BUILD="${2:-}"

if [[ -z "$TARGET" || "$TARGET" == "--help" || "$TARGET" == "-h" ]]; then
  echo "Usage: $0 <home|garden> [--no-build]"
  echo
  echo "Configure with environment variables from your global/local secret setup:"
  echo "  PI_HOST=simmoe@host-or-ip"
  echo "  PI_PASS=...                 # optional if SSH key + passwordless sudo works"
  echo "  PI_REPO_DIR=/home/simmoe/HUE_EJDERSTED"
  echo "  HUB_SITE=home|garden"
  echo "  HUB_PUBLIC_URL=https://host:8443"
  echo "  KIOSK_ADB_SERIAL=ip:5555    # optional"
  echo "  KIOSK_URL=https://host:8443 # optional"
  exit 1
fi

if [[ "$TARGET" != "home" && "$TARGET" != "garden" ]]; then
  echo "Target must be home or garden, got: $TARGET" >&2
  exit 2
fi
if [[ -n "$NO_BUILD" && "$NO_BUILD" != "--no-build" ]]; then
  echo "Unknown option: $NO_BUILD" >&2
  exit 2
fi

: "${PI_HOST:?Set PI_HOST in environment}"
PI_REPO_DIR="${PI_REPO_DIR:-/home/simmoe/HUE_EJDERSTED}"
SERVICE_NAME="${SERVICE_NAME:-hue}"
HUB_SITE="${HUB_SITE:-$TARGET}"
HUB_PUBLIC_URL="${HUB_PUBLIC_URL:-${KIOSK_URL:-}}"
SOURCE_COMMIT="$(git rev-parse HEAD)"
EXPECTED_COMMIT="${DEPLOY_COMMIT:-$SOURCE_COMMIT}"

if [[ "$SOURCE_COMMIT" != "$EXPECTED_COMMIT" ]]; then
  echo "Refusing deploy: HEAD $SOURCE_COMMIT does not match DEPLOY_COMMIT $EXPECTED_COMMIT" >&2
  exit 2
fi
if [[ "${ALLOW_DIRTY_DEPLOY:-false}" != "true" && -n "$(git status --porcelain)" ]]; then
  echo "Refusing deploy from a dirty working tree. Commit first or set ALLOW_DIRTY_DEPLOY=true." >&2
  exit 2
fi
if [[ "$HUB_SITE" != "$TARGET" ]]; then
  echo "Refusing deploy: target '$TARGET' does not match HUB_SITE '$HUB_SITE'" >&2
  exit 2
fi

EXPECTED_CAMERA_MODE="viewer"
if [[ "$TARGET" == "garden" ]]; then
  EXPECTED_CAMERA_MODE="publisher"
fi
HUB_CAMERA_MODE="${HUB_CAMERA_MODE:-$EXPECTED_CAMERA_MODE}"
if [[ "$HUB_CAMERA_MODE" != "$EXPECTED_CAMERA_MODE" ]]; then
  echo "Refusing deploy: $TARGET requires HUB_CAMERA_MODE=$EXPECTED_CAMERA_MODE" >&2
  exit 2
fi

if [[ "$HUB_SITE" == "garden" ]]; then
  HUB_FEATURE_CAMERA="${HUB_FEATURE_CAMERA:-true}"
  HUB_FEATURE_AUDIO="${HUB_FEATURE_AUDIO:-true}"
  HUB_FEATURE_HUE="${HUB_FEATURE_HUE:-false}"
  HUB_FEATURE_SPOTIFY="${HUB_FEATURE_SPOTIFY:-true}"
  HUB_FEATURE_PODCASTS="${HUB_FEATURE_PODCASTS:-true}"
  HUB_FEATURE_PLAYLISTS="${HUB_FEATURE_PLAYLISTS:-true}"
  HUB_FEATURE_ADBKIOSK="${HUB_FEATURE_ADBKIOSK:-true}"
elif [[ "${HUB_FEATURE_CAMERA:-true}" == "true" && -z "${HUB_GARDEN_HUB_URL:-}" ]]; then
  echo "Refusing home deploy: HUB_GARDEN_HUB_URL is required for the camera viewer" >&2
  exit 2
fi

if [[ -n "${HUB_GARDEN_HUB_URL:-}" && ! "$HUB_GARDEN_HUB_URL" =~ ^https://[^/]+([/].*)?$ ]]; then
  echo "HUB_GARDEN_HUB_URL must be an absolute HTTPS URL" >&2
  exit 2
fi
if [[ "$HUB_SITE" == "home" && ! "$HUB_PUBLIC_URL" =~ ^https:// ]]; then
  echo "Home uses private internal HTTPS; set HUB_PUBLIC_URL or KIOSK_URL to https://..." >&2
  exit 2
fi

ssh_run() {
  local remote_cmd="$1"
  if [[ -n "${PI_PASS:-}" && "$(command -v sshpass || true)" ]]; then
    sshpass -p "$PI_PASS" ssh "$PI_HOST" "$remote_cmd"
  elif [[ -n "${PI_PASS:-}" && "$(command -v expect || true)" ]]; then
    expect <<EXPECT
set timeout 120
set password {$PI_PASS}
spawn ssh -o StrictHostKeyChecking=accept-new $PI_HOST "$remote_cmd"
expect {
  -re "(?i)password.*:" { send "\$password\r"; exp_continue }
  eof
}
catch wait result
exit [lindex \$result 3]
EXPECT
  else
    ssh "$PI_HOST" "$remote_cmd"
  fi
}

scp_copy() {
  local source="$1"
  local dest="$2"
  if [[ -n "${PI_PASS:-}" && "$(command -v sshpass || true)" ]]; then
    sshpass -p "$PI_PASS" scp -r "$source" "$dest"
  elif [[ -n "${PI_PASS:-}" && "$(command -v expect || true)" ]]; then
    expect <<EXPECT
set timeout 120
set password {$PI_PASS}
spawn scp -r -o StrictHostKeyChecking=accept-new "$source" "$dest"
expect {
  -re "(?i)password:" { send "\$password\r"; exp_continue }
  eof
}
catch wait result
exit [lindex \$result 3]
EXPECT
  else
    scp -r "$source" "$dest"
  fi
}

SUDO="sudo"
if [[ -n "${PI_PASS:-}" ]]; then
  SUDO="echo '$PI_PASS' | sudo -S"
fi

if [[ "$NO_BUILD" != "--no-build" ]]; then
  echo "→ Building frontend for $TARGET..."
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    . "$HOME/.nvm/nvm.sh"
  fi
  (cd frontend && npm run build)
fi

echo "→ Updating repo on $PI_HOST..."
ssh_run "set -e; mkdir -p '$PI_REPO_DIR'"

echo "→ Syncing backend source..."
scp_copy backend "$PI_HOST:$PI_REPO_DIR/"

if [[ -d scripts ]]; then
  echo "→ Syncing scripts..."
  scp_copy scripts "$PI_HOST:$PI_REPO_DIR/"
  ssh_run "chmod +x '$PI_REPO_DIR'/scripts/*.sh 2>/dev/null || true"
fi

if [[ -d backend/static ]]; then
  echo "→ Syncing static build..."
  scp_copy backend/static "$PI_HOST:$PI_REPO_DIR/backend/"
fi

for local_file in gemini_api_key.txt hub_globals.json spotify_config.json hue_config.json devices.json; do
  if [[ -f "$local_file" ]]; then
    echo "→ Syncing $local_file..."
    scp_copy "$local_file" "$PI_HOST:$PI_REPO_DIR/"
  fi
done

if [[ "$HUB_SITE" == "garden" ]]; then
  echo "→ Ensuring garden AAC/M4A playback support..."
  ssh_run "if ! command -v ffmpeg >/dev/null; then $SUDO apt-get update && $SUDO apt-get install -y ffmpeg; fi"
  echo "→ Refreshing Tailscale/Let's Encrypt TLS certificate..."
  ssh_run "command -v tailscale >/dev/null && [[ -x '$PI_REPO_DIR/scripts/provision-tls-cert.sh' ]] && '$PI_REPO_DIR/scripts/provision-tls-cert.sh'"
  if [[ -z "${HUB_PUBLIC_URL:-}" ]]; then
    HINT_URL=$(ssh_run "cat '$PI_REPO_DIR/certs/public-url.txt' 2>/dev/null" || true)
    HINT_URL="$(echo "$HINT_URL" | tr -d '\r' | tail -n 1)"
    if [[ -n "${HINT_URL:-}" ]]; then
      HUB_PUBLIC_URL="$HINT_URL"
      echo "→ Using Tailscale public URL: $HUB_PUBLIC_URL"
    fi
  fi
  if [[ ! "$HUB_PUBLIC_URL" =~ ^https://[^/]+\.ts\.net(:[0-9]+)?/?$ ]]; then
    echo "Garden HUB_PUBLIC_URL must use its HTTPS Tailscale MagicDNS .ts.net hostname" >&2
    exit 2
  fi
fi

echo "→ Writing runtime environment..."
RUNTIME_ENV=$(mktemp)
{
  echo "HUB_SITE=$HUB_SITE"
  echo "HUB_CAMERA_MODE=$HUB_CAMERA_MODE"
  echo "HUB_RELEASE=$SOURCE_COMMIT"
  [[ -n "${HUB_PUBLIC_URL:-}" ]] && echo "HUB_PUBLIC_URL=$HUB_PUBLIC_URL"
  [[ -n "${HUB_GARDEN_HUB_URL:-}" ]] && echo "HUB_GARDEN_HUB_URL=$HUB_GARDEN_HUB_URL"
  [[ -n "${HUB_CAMERA_PUBLISHER_HOSTS:-}" ]] && echo "HUB_CAMERA_PUBLISHER_HOSTS=$HUB_CAMERA_PUBLISHER_HOSTS"
  [[ -n "${HUB_FEATURE_CAMERA:-}" ]] && echo "HUB_FEATURE_CAMERA=$HUB_FEATURE_CAMERA"
  [[ -n "${HUB_FEATURE_AUDIO:-}" ]] && echo "HUB_FEATURE_AUDIO=$HUB_FEATURE_AUDIO"
  [[ -n "${HUB_FEATURE_HUE:-}" ]] && echo "HUB_FEATURE_HUE=$HUB_FEATURE_HUE"
  [[ -n "${HUB_FEATURE_SPOTIFY:-}" ]] && echo "HUB_FEATURE_SPOTIFY=$HUB_FEATURE_SPOTIFY"
  [[ -n "${HUB_FEATURE_PODCASTS:-}" ]] && echo "HUB_FEATURE_PODCASTS=$HUB_FEATURE_PODCASTS"
  [[ -n "${HUB_FEATURE_PLAYLISTS:-}" ]] && echo "HUB_FEATURE_PLAYLISTS=$HUB_FEATURE_PLAYLISTS"
  [[ -n "${HUB_FEATURE_ADBKIOSK:-}" ]] && echo "HUB_FEATURE_ADBKIOSK=$HUB_FEATURE_ADBKIOSK"
  [[ -n "${HUB_KIOSK_PHONE_IP:-}" ]] && echo "HUB_KIOSK_PHONE_IP=$HUB_KIOSK_PHONE_IP"
  [[ -n "${HUB_KIOSK_ADB_SERIAL:-${KIOSK_ADB_SERIAL:-}}" ]] && echo "HUB_KIOSK_ADB_SERIAL=${HUB_KIOSK_ADB_SERIAL:-$KIOSK_ADB_SERIAL}"
  [[ -n "${HUB_KIOSK_MULTIAPP_PACKAGE:-}" ]] && echo "HUB_KIOSK_MULTIAPP_PACKAGE=$HUB_KIOSK_MULTIAPP_PACKAGE"
  [[ -n "${HUB_AUDIO_DEFAULT_TARGET:-}" ]] && echo "HUB_AUDIO_DEFAULT_TARGET=$HUB_AUDIO_DEFAULT_TARGET"
  [[ -n "${HUB_AUDIO_SPOTIFY_DEVICE:-}" ]] && echo "HUB_AUDIO_SPOTIFY_DEVICE=$HUB_AUDIO_SPOTIFY_DEVICE"
  if [[ "$HUB_SITE" == "garden" ]]; then
    echo "GOOGLE_APPLICATION_CREDENTIALS=${HUB_GOOGLE_APPLICATION_CREDENTIALS:-$PI_REPO_DIR/runtime/firebase/garden-camera-evidence.json}"
  fi
} > "$RUNTIME_ENV"
scp_copy "$RUNTIME_ENV" "$PI_HOST:/tmp/hue.runtime.env"
rm -f "$RUNTIME_ENV"
ssh_run "$SUDO mkdir -p /etc/hue && $SUDO mv /tmp/hue.runtime.env /etc/hue/runtime.env && $SUDO chmod 600 /etc/hue/runtime.env"
ssh_run "grep -q '^EnvironmentFile=-/etc/hue/runtime.env$' /etc/systemd/system/hue.service || $SUDO sed -i '/^Environment=PYTHONUNBUFFERED=1$/a EnvironmentFile=-/etc/hue/runtime.env' /etc/systemd/system/hue.service; $SUDO systemctl daemon-reload"

echo "→ Restarting $SERVICE_NAME..."
ssh_run "$SUDO systemctl restart '$SERVICE_NAME'"
sleep 2

STATUS=$(ssh_run "$SUDO systemctl is-active '$SERVICE_NAME' 2>/dev/null")
if [[ "$STATUS" == *"active"* ]]; then
  echo "✓ Deploy OK — $SERVICE_NAME active"
else
  echo "✗ Service not active"
  ssh_run "$SUDO journalctl -u '$SERVICE_NAME' --no-pager -n 20 2>&1"
  exit 1
fi

echo "→ Verifying deployed profile and release..."
HEALTH_CURL="curl --fail --silent --show-error"
if [[ "$HUB_SITE" == "home" ]]; then
  HEALTH_CURL="$HEALTH_CURL --insecure"
fi
HEALTH_JSON="$(ssh_run "$HEALTH_CURL '$HUB_PUBLIC_URL/api/health'")"
HEALTH_JSON="$HEALTH_JSON" EXPECTED_SITE="$HUB_SITE" EXPECTED_RELEASE="$SOURCE_COMMIT" python3 -c \
  'import json,os; d=json.loads(os.environ["HEALTH_JSON"]); assert d.get("ok") is True; assert d.get("site")==os.environ["EXPECTED_SITE"]; assert d.get("release")==os.environ["EXPECTED_RELEASE"]'
echo "✓ Health check matches $HUB_SITE @ ${SOURCE_COMMIT:0:12}"

if [[ -n "${KIOSK_ADB_SERIAL:-}" && -n "${KIOSK_URL:-}" ]]; then
  echo "→ Refreshing kiosk browser..."
  ssh_run "adb connect '$KIOSK_ADB_SERIAL' 2>/dev/null; if adb -s '$KIOSK_ADB_SERIAL' get-state 2>/dev/null | grep -q '^device$'; then adb -s '$KIOSK_ADB_SERIAL' shell am start -a android.intent.action.VIEW -d '$KIOSK_URL' com.android.chrome; else echo 'No kiosk phone via ADB'; fi"
fi
