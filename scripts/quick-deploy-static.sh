#!/usr/bin/env bash
# Fast frontend-only deploy: build locally, sync backend/static, restart service.
# Configure with environment variables from your global/local secret setup.

set -euo pipefail

TARGET="${1:-garden}"

: "${PI_HOST:?Set PI_HOST in environment}"
PI_REPO_DIR="${PI_REPO_DIR:-/home/simmoe/HUE_EJDERSTED}"
SERVICE_NAME="${SERVICE_NAME:-hue}"

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

echo "-> Building frontend..."
(cd frontend && npm run build)

echo "-> Syncing static build to $PI_HOST..."
scp_copy backend/static "$PI_HOST:$PI_REPO_DIR/backend/"

echo "OK: static synced — hue left running. Reload the kiosk tab."
