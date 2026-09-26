#!/bin/bash
# Install the garden A12 CameraX publisher. Never the home kiosk.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APK="${1:-$ROOT/android/garden-camera/app/build/outputs/apk/debug/app-debug.apk}"
PKG="dk.ejdersted.gardencamera"
SERIAL="192.168.8.135:5555"
KEY=(-o BatchMode=yes -o IdentitiesOnly=yes -i "$HOME/.ssh/hue_ejdersted_ed25519")
HOST="simmoe@100.111.167.54"

if [[ ! -f "$APK" ]]; then
  echo "missing $APK" >&2
  exit 1
fi

scp "${KEY[@]}" "$APK" "$HOST:/tmp/garden-camera.apk"
ssh "${KEY[@]}" "$HOST" "adb connect $SERIAL >/dev/null
adb -s $SERIAL install -r /tmp/garden-camera.apk
adb -s $SERIAL shell pm grant $PKG android.permission.CAMERA
adb -s $SERIAL shell dumpsys deviceidle whitelist +$PKG >/dev/null
adb -s $SERIAL shell cmd appops set $PKG RUN_IN_BACKGROUND allow
adb -s $SERIAL shell cmd appops set $PKG RUN_ANY_IN_BACKGROUND allow
adb -s $SERIAL shell cmd appops set $PKG CAMERA allow
adb -s $SERIAL shell am start-foreground-service -n $PKG/.PublisherService
rm -f /tmp/garden-camera.apk
echo garden-camera installed"
