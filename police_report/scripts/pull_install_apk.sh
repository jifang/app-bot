#!/usr/bin/env bash
# Pull official 警察叔叔 APK from a USB device and/or install onto the wfjb AVD.
set -euo pipefail

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
export PATH="$SDK/platform-tools:$PATH"
PKG=com.hzpd.jwztc
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/vendor"
APK="$OUT_DIR/$PKG.apk"
SERIAL_PHONE="${WFJB_PHONE_SERIAL:-}"
SERIAL_EMU="${WFJB_EMU_SERIAL:-emulator-5554}"

mkdir -p "$OUT_DIR"

pick_phone() {
  if [[ -n "$SERIAL_PHONE" ]]; then
    echo "$SERIAL_PHONE"
    return
  fi
  # Prefer a physical device that has the package installed.
  while read -r serial _; do
    [[ -z "$serial" || "$serial" == "List" ]] && continue
    [[ "$serial" == emulator-* ]] && continue
    if adb -s "$serial" shell pm path "$PKG" >/dev/null 2>&1; then
      echo "$serial"
      return
    fi
  done < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
  return 1
}

cmd="${1:-all}"

case "$cmd" in
  pull|all)
    phone=$(pick_phone) || { echo "no USB device with $PKG installed" >&2; exit 1; }
    path=$(adb -s "$phone" shell pm path "$PKG" | head -1 | tr -d '\r' | sed 's/^package://')
    echo "pulling from $phone:$path -> $APK"
    adb -s "$phone" pull "$path" "$APK"
    ls -lh "$APK"
    [[ "$cmd" == pull ]] && exit 0
    ;&
  install)
    [[ -f "$APK" ]] || { echo "missing $APK — run: $0 pull" >&2; exit 1; }
    adb devices | grep -q "^${SERIAL_EMU}[[:space:]]" || {
      echo "emulator $SERIAL_EMU not running — start wfjb_arm64 first" >&2
      exit 1
    }
    echo "installing on $SERIAL_EMU ..."
    adb -s "$SERIAL_EMU" install -r -t "$APK"
    adb -s "$SERIAL_EMU" shell pm path "$PKG"
    echo "smoke launch..."
    adb -s "$SERIAL_EMU" shell am start -W -n "$PKG/.LaunchActivity" | tail -5
    ;;
  *)
    echo "usage: $0 [pull|install|all]" >&2
    exit 2
    ;;
esac
