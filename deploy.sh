#!/bin/bash
# Deploy HUE_EJDERSTED til Raspberry Pi
# Brug: ./deploy.sh [--build]  (--build rebuilder frontend først)

set -e

PI_HOST="simmoe@192.168.86.16"
PI_PASS="k18Medh18"
SSH="sshpass -p $PI_PASS ssh $PI_HOST"
SCP="sshpass -p $PI_PASS scp"

# Rebuild frontend hvis --build flag (sørg for Node: nvm eller PATH)
if [[ "$1" == "--build" ]]; then
  echo "→ Building frontend..."
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    . "$HOME/.nvm/nvm.sh"
  fi
  (cd frontend && npm run build)
fi

# Git push
echo "→ Pushing to GitHub..."
git add -A && git commit -m "deploy $(date +%H:%M)" 2>/dev/null || true
git push

# Pi: pull + restart
echo "→ Pulling on Pi..."
$SSH "cd ~/HUE_EJDERSTED && git pull"

# Kopier static files (ikke i git)
echo "→ Syncing static files..."
$SCP -r backend/static $PI_HOST:~/HUE_EJDERSTED/backend/

# Restart service
echo "→ Restarting service..."
$SSH "echo '$PI_PASS' | sudo -S systemctl restart hue"

sleep 2

# Verify
STATUS=$($SSH "echo '$PI_PASS' | sudo -S systemctl is-active hue 2>/dev/null")
if [[ "$STATUS" == "active" ]]; then
  echo "✓ Deploy OK — hue.service active"
else
  echo "✗ Service ikke aktiv!"
  $SSH "echo '$PI_PASS' | sudo -S journalctl -u hue --no-pager -n 10 2>&1"
  exit 1
fi

# Refresh tablet browser (force-stop Chrome + reopen kiosk URL via ADB on Pi)
echo "→ Refreshing tablet..."
$SSH "ADB_SERIAL='192.168.86.15:5555' && \
  adb connect \$ADB_SERIAL 2>/dev/null; \
  STATE=\$(adb -s \$ADB_SERIAL get-state 2>/dev/null) && \
  if [ \"\$STATE\" = 'device' ]; then \
    adb -s \$ADB_SERIAL shell am force-stop com.android.chrome && \
    sleep 1 && \
    adb -s \$ADB_SERIAL shell am start -a android.intent.action.VIEW -d 'https://192.168.86.16:8443' && \
    echo '✓ Tablet refreshed'; \
  else \
    echo '⚠ Ingen tablet fundet via ADB'; \
  fi"
