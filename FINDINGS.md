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

---

# BREAKTHROUGH — protocol recovered (2026-07-06)

## Wall broken
Rooted **Redmi Note 8 (ginkgo)**, arm64-v8a, Android 11 (SDK 30), Magisk 28.1.
Path that worked (Resume option 1):
1. `pm install` the **unmodified** `base.apk` **as root** — MIUI blocks plain
   `adb install` (`INSTALL_FAILED_USER_RESTRICTED`); `su -c 'pm install -r -g ...'`
   bypasses it. Valid Amap signature → SecurityGuard inits → **no CpuArchError**.
2. Host frida **17.15.3**; pushed matching `frida-server-17.15.3-android-arm64` to
   `/data/local/tmp/fs17`, ran as root.
3. `python3 run_recon.py` → spawn + inject `recon.bundle.js` (see toolchain note).

### frida 17 gotcha
`Java` / `ObjC` are **no longer globals**. A raw `-l script.js` throws
`ReferenceError: 'Java' is not defined`. Fix: bundle with `frida-compile` +
`npm i frida-java-bridge`, entry does `import Java from 'frida-java-bridge';
globalThis.Java = Java;`. Source = `recon.entry.js`, compiled → `recon.bundle.js`.
`console.log` in the script arrives to frida-python as message type **`log`**
(not `send`); `run_recon.py` handles it.

## AOS request format (all api.amap.com "ws" endpoints)
```
POST https://<host>/ws/<path>?ent=2&in=<params>&csid=<uuid>
  <host> e.g. m5-zb.amap.com, center.amap.com, render.amap.com, amap-aos-info-nogw.amap.com
  in    = URL-encoded base64 of the DES-encrypted param body (the "in" blob)
  csid  = per-request UUID
headers: Ap-Tid: <uid3>, Content-Type: application/x-www-form-urlencoded, ...
```
**打车 (ride-hailing) module confirmed live:** `POST https://m5-zb.amap.com/ws/boss/car/order/lottie_info?ent=2&in=...&csid=...`
→ target path prefix is **`/ws/boss/car/...`**.

## Signing algorithm (the `sign` field)
Native, but reachable from Java. Call chain (Frida stack):
```
com.amap.bundle.aosservice.request.AosRequest.buildHttpRequest:454
 └ com.amap.bundle.network.context.AosEncryptor.sign
    └ com.autonavi.server.aos.serverkey.sign          (Java static — CALL THIS)
       └ com.autonavi.jni.server.aos.ServerkeyNative.sign   [native, libserverkey.so]
          → MD5( "amap" + "7a" + <paramDigest> + SALT )
SALT = "@xnaEwInMxaMQ2m0cw6Y1bDm7ns0YVxYS9v7JlC8I"   (app secret, embedded)
```
Observed inputs → outputs:
- `amap7aANDH161900@xna…JlC8I` → `f055c7aa08fb6418f22c08037e3b8be7`
- `amap7avae8f5794f442dcb9hhci3d5348d58@xna…JlC8I` → `5b2c91c872b98c09d558a29af67ec58f`

`spm` uses a separate `MD5Util.getStringMD5` via `ServerkeyNative.getSpm` (tracking, not the API sign).

## Body / param crypto (captured via Cipher.init)
| Purpose | Alg | Key (ascii) | IV |
|---|---|---|---|
| `in=` param body | DES/CBC/PKCS5 | rotating 8-byte: `0jof8eg3`,`8f31d352`,`k88upxsy`,`ri40hsah`,`u2xsyyet`,`4n1z6vlc` | `0102030405060708` (or `lvain81q`) |
| some payloads | DESede/CBC/NoPadding | `0461d813b88c9261dc598d08` | `00000000` |
| adiu device-id | AES/CBC/PKCS5 | `amapadiu`×4 (32B) | all-zero |
| DES key transport | RSA/ECB/PKCS1 | (server pubkey) | — |

Plaintext param bodies were dumped pre-encryption, e.g.:
`{"app":"com.autonavi.minimap","uid":"…","appver":"16.19.0.2012",…}` and the
analytics `{"dur":0,"pv":20240428,"dtype":"qt","ts":"…","uuid":"…","ks":"…","aid":"…"}`.

## Pinning
Cert pinning present at **both** layers: `com.android.okhttp.CertificatePinner`
(okhttp is the AOSP-bundled `com.android.okhttp`, **not** `okhttp3`) + native
MD5/SHA1 over the GlobalSign / `*.amap.com` cert chain. Must bypass for mitm.

## Next steps (pick)
1. **Signing oracle via Frida RPC** — `rpc.exports = { sign: s => serverkey.sign(s),
   encparam: … }`; forge params off-device, let the app sign/encrypt. Fastest route
   to "call their API directly." (No native RE needed.)
2. **Drive the 打车 booking flow** on-device with recon attached → capture the full
   `/ws/boss/car/order/*` request+response set (estimate price, create order).
3. **mitm decrypt** — proxy → `192.168.100.44:8080`, bypass okhttp+native pinning via
   Frida, decrypt `in=` with the DES keys above to read/replay live traffic.

## New files (this repo)
- `recon.js` — readable v3 hook source (sign tracer + Cipher.init key dump + okhttp)
- `recon.entry.js` — frida-compile entry (adds Java-bridge import); → `recon.bundle.js`
- `run_recon.py` — spawn+inject driver (handles `log` messages, stays alive to drive UI)
