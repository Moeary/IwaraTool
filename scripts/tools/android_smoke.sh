#!/usr/bin/env bash
# Install the Flet APK on the running emulator, start it, and check it stays up
# without a Python traceback (docs/flet_ui_plan.md P8). Used by build.yml.
#   android_smoke.sh <apk> <out-dir> <label>
set -uo pipefail

# Write on the device and pull: on multi-display devices (foldables) screencap prints a
# warning to stdout, which `exec-out screencap -p >` would put in front of the PNG.
shot() {
    adb shell screencap -p /data/local/tmp/smoke.png >/dev/null 2>&1
    adb pull /data/local/tmp/smoke.png "$1" >/dev/null
}
apk="$1"; out="$2"; label="$3"
mkdir -p "$out"
aapt2=$(ls "$ANDROID_HOME"/build-tools/*/aapt2 | sort -V | tail -1)
package=$("$aapt2" dump packagename "$apk")
echo "package: $package"
adb wait-for-device
adb shell 'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 1; done'
adb shell input keyevent 82 || true   # unlock
# A slow CI emulator often shows "Pixel Launcher isn't responding" over the app, which also
# swallows the back key below: hide error dialogs and close any that are already open.
adb shell settings put global hide_error_dialogs 1 || true
close_dialogs() { adb shell am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS >/dev/null 2>&1 || true; }
adb install -r -g "$apk" || exit 1
adb logcat -c
adb shell wm size > "$out/$label-size.txt"
adb shell monkey -p "$package" -c android.intent.category.LAUNCHER 1 >/dev/null
status=0
for second in $(seq 1 90); do
  sleep 1
  if [ $((second % 30)) -eq 0 ]; then
    close_dialogs
    shot "$out/$label-${second}s.png"
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
# Back key on the home page leaves the app (Shell.back): the launcher should be in front.
close_dialogs
adb shell input keyevent 4; sleep 2
shot "$out/$label-after-back.png"
focus=$(adb shell dumpsys window | grep -m1 -E "mCurrentFocus|mFocusedApp" || true)
echo "after back: $focus" | tee "$out/$label-after-back.txt"
case "$focus" in
  *"$package"*) echo "::warning::$label: still in the app after the back key on the home page" ;;
esac
exit $status
