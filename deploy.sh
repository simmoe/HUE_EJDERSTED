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

# Gemini til radio (én linje i projektroden — ikke i git)
if [[ -f gemini_api_key.txt ]]; then
  echo "→ Syncing gemini_api_key.txt (radio på Pi)..."
  $SCP gemini_api_key.txt $PI_HOST:~/HUE_EJDERSTED/
fi

# Firebase m.m. (hub_globals.json — ikke i git)
if [[ -f hub_globals.json ]]; then
  echo "→ Syncing hub_globals.json (Firebase + evt. andre globals)..."
  $SCP hub_globals.json $PI_HOST:~/HUE_EJDERSTED/
fi

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

# Refresh kiosk phone (Galaxy A12) Chrome — reopen hub URL (ADB on Pi eller serial)
echo "→ Refreshing kiosk phone…"
$SSH "ADB_SERIAL='192.168.86.15:5555' && \
  adb connect \$ADB_SERIAL 2>/dev/null; \
  STATE=\$(adb -s \$ADB_SERIAL get-state 2>/dev/null) && \
  if [ \"\$STATE\" = 'device' ]; then \
    adb -s \$ADB_SERIAL shell am force-stop com.android.chrome && \
    sleep 1 && \
    adb -s \$ADB_SERIAL shell am start -a android.intent.action.VIEW -d 'https://192.168.86.16:8443' && \
    echo '✓ Kiosk-telefon opdateret'; \
  else \
    echo '⚠ Ingen kiosk-telefon via ADB'; \
  fi"
