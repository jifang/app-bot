#!/usr/bin/env bash
# Create a clean ARM64 AVD for headless wfjb minting (Option 1).
set -euo pipefail

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
AVD_NAME="${WFJB_AVD:-wfjb_arm64}"
PACKAGE="system-images;android-34;google_apis;arm64-v8a"
AVDMANAGER="$SDK/cmdline-tools/latest/bin/avdmanager"
SDKMANAGER="$SDK/cmdline-tools/latest/bin/sdkmanager"

if [[ ! -x "$AVDMANAGER" ]]; then
  echo "avdmanager not found at $AVDMANAGER" >&2
  exit 1
fi

echo "Ensuring system image $PACKAGE ..."
yes | "$SDKMANAGER" --install "$PACKAGE" >/dev/null || true

if "$AVDMANAGER" list avd | grep -q "Name: $AVD_NAME"; then
  echo "AVD $AVD_NAME already exists"
else
  echo "Creating AVD $AVD_NAME ..."
  echo no | "$AVDMANAGER" create avd -n "$AVD_NAME" -k "$PACKAGE" --force
fi

echo
echo "Next:"
echo "  0. Keep the emulator process alive in its own terminal (Cursor kills orphans):"
echo "       \$SDK/emulator/emulator -avd $AVD_NAME -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect"
echo "  1. Pull official APK from USB phone + install on AVD:"
echo "       bash police_report/scripts/pull_install_apk.sh all"
echo "  2. Log in once on the AVD (scrcpy / Android Studio Embedded; SMS / gov SSO)."
echo "  3. Terminal A: MITM_AUTH_ONLY=1 MITM_OUT=/tmp/re/mint.jsonl mitmdump -s mitm_addon.py -p 8080"
echo "     Point the emulator HTTP proxy at the host (Settings → Proxy, or:"
echo "       adb -e shell settings put global http_proxy <host-ip>:8080)"
echo "  4. Terminal B: WFJB_MINTER=android python -m police_report.cli mint --via android"
echo
echo "Bridge signer (LSPosed/helper) instead of capture:"
echo "  export MGOP_SIGNER_URL=http://127.0.0.1:8765"
echo "  export WFJB_MINTER=signer"
echo "  python -m police_report.cli mint --via signer"
