# 高德地图 API RE — Findings & Status (as of 2026-07-03)

## Goal
Reverse-engineer 高德打车 API calls (ride-hailing module lives inside 高德地图,
package `com.autonavi.minimap`). Intent: call their API directly.

## Target
- App: 高德地图 (Amap) — 打车 is a module within it, not standalone.
- Package: `com.autonavi.minimap`
- Version: **16.19.0.2012** (versionCode 161900), minSdk 21, targetSdk 35
- ABI: arm64-v8a only, `extractNativeLibs=true`
- APK: single `base.apk`, 163 MB, no splits.

## Devices available
| Device | Detail | Verdict for RE |
|---|---|---|
| Samsung SM-S711B (S23 FE) | arm64-v8a, Android 16, **unrooted, Knox** | Android is the right platform, but no root |
| iPhone 11 Pro | A13, iOS 18.6.2 | **Dead end** — A13 not checkm8-able, no public JB for 18.x, no frida-server |

adb: `/Users/jifang/Library/Android/sdk/platform-tools/adb`
Device id: `RZCWB0V8NZN`

## Toolchain installed (macOS arm64)
- frida 17.15.3 (core + frida-tools) — installed via **Aliyun mirror** (PyPI + Tsinghua both failed/403 on China network)
- objection
- mitmproxy 12.2.3 (`mitmdump`)
- apktool 3.0.2
- Android build-tools 36.1.0 (zipalign, apksigner, aapt2), JDK 17 (keytool)
- mitmproxy CA generated: `~/.mitmproxy/mitmproxy-ca-cert.pem`
- Mac LAN IP (proxy target): **192.168.100.44:8080**
- NOTE: Samsung has **Clash Meta VPN** running — will interfere with proxy routing; disable/route-around when capturing.

## What we tried

### 1. yyb installer file (dead end)
User's file: `~/Downloads/com.autonavi.minimap_yybinstaller_2100200129_eb91f761784b7a50.dmg`
- Not a dmg. `file` = zlib compressed data, 1.3 GB.
- Tencent 应用宝 (yyb) proprietary container: thousands of concatenated zlib
  members with length-prefix framing. First member = 512 zero bytes (header/TOC).
- Attempted zlib member-walk → only reconstructed garbage (181 of ~21k members,
  output not a zip). `bsdtar`/`7z` reject it. Not `adb install`-able.
- **Abandoned.** Not worth cracking Tencent's format.

### 2. Clean APK via store install + adb pull (worked)
- User installed 高德地图 from store, opened it (ran fine — store signature valid).
- `adb shell pm path` → pulled `/data/app/.../base.apk` (163 MB) to `/tmp/re/apk/base.apk`.
- This is the clean, device-matched, Amap-signed APK. **Keep this.**

### 3. objection patchapk + Frida gadget (BLOCKED)
- `objection patchapk -s base.apk --architecture arm64` succeeded: injected gadget,
  rewrote `NewMapActivity` smali `loadLibrary`, zipalign, self-signed → `base.objection.apk`.
- BUG in objection: put gadget in `lib/arm64/` (invalid Android ABI dir; must be
  `arm64-v8a`). Fixed manually → `test.aligned.apk` (gadget in `lib/arm64-v8a/`,
  re-signed with our debug key `/tmp/re/debug.jks`, pass `android`).
- **Both installs bounce to `CpuArchErrorActivity` then `System.exit(0)`.** App never
  reaches `NewMapActivity`, so gadget never loads.

## ROOT CAUSE (the wall)
Smali trace of the bail:
- `CpuArchErrorManager.a(Context)` launches `CpuArchErrorActivity` + `System.exit(0)`.
- Called from `com.autonavi.minimap.app.init.DumpCrash` (runs in Application init,
  BEFORE any activity) at two checks:
  1. `NativeHandler.isSoLoaded == false` && `soLoadErrorMsg` non-empty (native crash lib).
  2. **`com.autonavi.server.aos.serverkey.getVersion()` throws `UnsatisfiedLinkError`.**

`serverkey` + `libsgmain.so` / `libserverkey.so` / `libappintegrity.so` /
`libsecuritydefence.so` = **Alibaba SecurityGuard**. This native stack:
- is the same code that **signs API requests**, AND
- is **bound to the APK signing certificate**.

Any repack changes the signature → SecurityGuard refuses to init → `UnsatisfiedLinkError`
→ CpuArchError → exit. The store copy runs fine; only the resign breaks it. => the
tripwire is **signature-tamper detection**, confirmed, not corruption on our side.

### Why gadget can't win here
- Repack is required to inject a gadget → repack changes signature → tamper trip.
- Even ignoring that, the check runs in `Application` init, before `NewMapActivity`
  where the gadget loads — too late to hook.

## Conclusion
`patchapk` / Frida-gadget is **not viable** for this app. Standard path for a
SecurityGuard-hardened app = **root + `frida-server` on the UNMODIFIED APK**
(signature stays valid, `serverkey` initializes, frida attaches externally).

Blocker: the only Android device is an unrooted Samsung with Knox (rooting =
permanent Knox e-fuse trip; destructive). iOS device unusable (A13, no JB).

## Resume options (pick one later)
1. **Spare/rooted Android device** — root w/ Magisk, `adb install` the *original*
   `base.apk` (keep it), push arch-matched `frida-server`, run, then:
   `frida -U -n "高德地图" -l recon.js`. Best path.
2. **Root the Samsung** — destructive (Knox). Only if Samsung Pay / Secure Folder /
   banking / warranty are all expendable.
3. **Rooted emulator** — Genymotion/AVD + Magisk; risk: `libsecuritydefence.so`
   emulator detection may still bail; needs ARM-translation image for arm64 libs.
4. **Static RE** — Ghidra on `libserverkey.so` / `libsgmain.so` to recover the
   request-signing algorithm offline. Alibaba OLLVM-obfuscated; slowest, no device risk.

Once frida attaches (option 1-3), the plan:
- `objection -g com.autonavi.minimap explore` → `android sslpinning disable`
- `frida -U -n <app> -l recon.js` → dump sign inputs / crypto / OkHttp (see recon.js)
- mitmdump capture (device proxy → 192.168.100.44:8080; kill Clash first)
- match the `sign` field in captured requests to a digest/HMAC output → recover algo
- OR hook the JNI signer and Frida-RPC it (let app sign forged params).

## Files in /tmp/re/
- `apk/base.apk` — clean Amap-signed original (163 MB) — **the artifact to instrument w/ root**
- `apk/base.objection.apk`, `apk/test.aligned.apk` — patched (blocked, kept for reference)
- `recon.js` — Frida hooks: MessageDigest/Mac/Cipher + OkHttp request dump
- `runbook.md` — original step plan
- `smali_out/` — apktool-decoded smali of base.apk (searchable)
- `debug.jks` — our signing key (pass: android)
