#!/usr/bin/env bash
# Install the Flet APK on the running emulator, start it, and check it stays up
# without a Python traceback (docs/flet_ui_plan.md P8). Used by build.yml.
#   android_smoke.sh <apk> <out-dir> <label>
set -uo pipefail
apk="$1"; out="$2"; label="$3"
mkdir -p "$out"
aapt2=$(ls "$ANDROID_HOME"/build-tools/*/aapt2 | sort -V | tail -1)
package=$("$aapt2" dump packagename "$apk")
echo "package: $package"
adb wait-for-device
adb shell 'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 1; done'
adb shell input keyevent 82 || true   # unlock
adb install -r -g "$apk" || exit 1
adb logcat -c
adb shell wm size > "$out/$label-size.txt"
adb shell monkey -p "$package" -c android.intent.category.LAUNCHER 1 >/dev/null
status=0
for second in $(seq 1 90); do
  sleep 1
  if [ $((second % 30)) -eq 0 ]; then
    adb exec-out screencap -p > "$out/$label-${second}s.png"
  fi
done
adb logcat -d > "$out/$label-logcat.txt"
if ! adb shell pidof "$package" >/dev/null; then
  echo "::error::$label: the app is not running after 90 s"; status=1
fi
if grep -E "Traceback \(most recent call last\)|FATAL EXCEPTION" "$out/$label-logcat.txt" >/dev/null; then
  echo "::error::$label: Python traceback or fatal exception in logcat"
  grep -n -A25 -E "Traceback \(most recent call last\)|FATAL EXCEPTION" "$out/$label-logcat.txt" | head -80
  status=1
fi
# Back key: steps back inside the app, then leaves it (the process may stay cached).
adb shell input keyevent 4; sleep 2
adb exec-out screencap -p > "$out/$label-after-back.png"
exit $status
