#!/bin/bash
# Lockdown kiosk-telefon (Samsung Galaxy A12) — kør fra Pi via SSH
# Usage: ssh pi "bash lockdown_tablet.sh <adb-serial>"

ADB="$1"
if [ -z "$ADB" ]; then echo "Usage: $0 <adb-serial>"; exit 1; fi

echo "=== 1. Disable ALL auto-updates ==="
adb -s $ADB shell pm disable-user --user 0 com.android.vending
adb -s $ADB shell settings put global ota_disable_automatic_update 1
adb -s $ADB shell settings put global auto_time 0
adb -s $ADB shell settings put global package_verifier_enable 0
adb -s $ADB shell settings put global verifier_verify_adb_installs 0

echo "=== 2. Mute ALL sounds ==="
for stream in 0 1 2 3 4 5 6 7 8 9 10; do
  adb -s $ADB shell cmd media_session volume --stream $stream --set 0 2>/dev/null
done
adb -s $ADB shell settings put system notification_sound ""
adb -s $ADB shell settings put system ringtone ""
adb -s $ADB shell settings put system alarm_alert ""
adb -s $ADB shell settings put global zen_mode 2
adb -s $ADB shell settings put system haptic_feedback_enabled 0
adb -s $ADB shell settings put system vibrate_when_ringing 0
adb -s $ADB shell settings put system sound_effects_enabled 0
adb -s $ADB shell settings put system dtmf_tone 0

echo "=== 3. Prevent sleep/restart ==="
adb -s $ADB shell settings put global stay_on_while_plugged_in 3
adb -s $ADB shell settings put system screen_off_timeout 1800000
adb -s $ADB shell settings put global auto_restart 0
adb -s $ADB shell settings put global auto_restart_enabled 0
adb -s $ADB shell settings put global scheduled_power_on_off_enabled 0

echo "=== 4. Disable notifications/interruptions ==="
adb -s $ADB shell settings put global heads_up_notifications_enabled 0
adb -s $ADB shell cmd notification set_dnd on 2>/dev/null || true

echo "=== 5. Switch ADB to fixed port 5555 ==="
adb -s $ADB tcpip 5555
sleep 3
adb connect 192.168.86.15:5555
sleep 2
adb devices

echo "=== DONE ==="
