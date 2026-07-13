# 高德地图 API RE — Findings

Chronological RE log. Sections are time-ordered. Status-as-of-now is in
[`README.md`](./README.md) and pending work is in [`TODO.md`](./TODO.md).

## Status (2026-07-12)

- **Sign reverse** ✓ — `md5("amap7a" + extra + "@" + aosKey)` reproduced offline.
  Re-derivation in §"AOS sign — full reverse" below.
- **amapEncode** ✗ — key is in `libserverkey.so` (OLLVM); still runs via the frida
  oracle through `oracle_server.py`.
- **End-to-end replay** ✓ — `amap_client.py build` builds wire URLs;
  `amap_client.py call` POSTs and gets `HTTP 200` + JSON. Live cookie / Ap-Tid /
  asac / wua still missing → server returns `code 14 用户登录校验失败`.
- **In-app driver** ✓ — `driver_fetch2.entry.js` delegates to the running app's
  `ModuleRequest.fetch`; reaches the same `code 14` because the live session is
  now stale.

File map (full list in [`README.md`](./README.md)):

| Path | Role |
|------|------|
| `oracle_server.py` | HTTP bridge to the frida-attached AOS crypto oracle |
| `oracle.entry.js` / `oracle.bundle.js` | enumerate `serverkey`/`AosEncryptor`; RPC sign / amapEncode / amapDecode / aosKey |
| `amap_client.py` | pure-Python request builder + caller |
| `driver_fetch2.entry.js` / `driver_fetch2.bundle.js` | Ajx3 `ModuleRequest.fetch` hijack |
| `recon.entry.js` / `sfnet.entry.js` / … | earlier capture scripts (workflow unchanged; rebuild `.bundle.js` via `compile/`) |
| `FINDINGS.md` (this file) | the chronology |
| `TODO.md` | ranked pending work |

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
| Samsung SM-S711B (S23 FE) | arm64-v8a, Android 16, **unrooted, Knox** | android is the right platform, but no root |
| iPhone 11 Pro | A13, iOS 18.6.2 | **Dead end** — A13 not checkm8-able, no public JB for 18.x, no frida-server |
| **Redmi Note 8 (ginkgo)** *(added later)* | arm64-v8a, Android 11, rooted (Magisk 28.1, Zygisk on) | **the working rig** — all post-2026-07-06 captures come from this device |

If you're picking this up: rediscover the Redmi Note 8 (or any rooted
arm64 Android with frida-server 17.15.3 at `/data/local/tmp/fs17`). The
Samsung and iPhone are dead-ends.

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

---

# SIGNING ORACLE — solved, sign reproduced offline (2026-07-06)

Attached an RPC oracle (`oracle.entry.js` → `oracle.bundle.js`, driver
`run_oracle.py`) to the live process and enumerated the AOS crypto surface.

## serverkey (com.autonavi.server.aos.serverkey) — native, libserverkey.so
Key methods:
- `String sign(byte[])`            — **plain uppercase MD5** of the input (no salt inside)
- `String amapEncode(String)` / `amapEncodeV2` / `amapDecode` / `amapDecodeV2`
                                   — the reversible `in=` param codec (DES + base64)
- `byte[] amapEncodeBinary(byte[])`, `amapEncodeBinaryV2`
- `String getAosKey()`             — returns the sign salt (below)
- `String getSpm(s,s,s,s,s)`, `getAosChannel()`, plus per-partner keys
  (`getWXSecret`, `getQQSecret`, `getTaobaoSecret`, `get360Secret`, …)
- `String getVersion()` → `16.08.0.1`

## AosEncryptor (com.amap.bundle.network.context.AosEncryptor)
- `String sign(byte[])`, `HashMap virtualV2Sign(String,String,String,boolean)` /
  `virtualV2Sign(byte[],…)` — returns the header bundle (sign + wua)
- `String getWua()`, `getMiniWua()`, `getUMID()`, `xxTeaEncrypt(String/byte[])`,
  `whiteBoxSign(String[])`, `withSecurityGuardSign()`, `isVirtualV2Sign()`

## THE SIGN (verified offline, no device)
```
aosKey = "xnaEwInMxaMQ2m0cw6Y1bDm7ns0YVxYS9v7JlC8I"     # = serverkey.getAosKey()
sign   = md5( signString + "@" + aosKey ).hexdigest()   # serverkey.sign() itself is just md5
```
Proof: `md5("amap7aANDH161900@"+aosKey)` = `f055c7aa08fb6418f22c08037e3b8be7`
== the value captured in live traffic. `serverkey.sign(b"hello")` =
`5D41402ABC4B2A76B9719D911017C592` = uppercase md5("hello"). => the caller assembles
`<params>@<aosKey>`, then MD5. **Fully reproducible in pure python.**

## The `in=` codec
`amapEncode(json)` ⇄ `amapDecode(blob)` round-trips exactly (base64 body). Same
output as `amapEncodeV2`. Uses the rotating 8-byte DES keys captured earlier
(IV `0102030405060708`). Two ways to build `in=` for a forged request:
1. Oracle-as-backend: call `rpc amapencode(json)` on the device (works now).
2. Offline: port DES/CBC/PKCS5 with the captured key + the key-selection logic
   (TODO: confirm which key index maps to which endpoint).

## Oracle usage
```
cd compile && npm i frida-java-bridge
frida-compile ../oracle.entry.js -o ../oracle.bundle.js
python3 run_oracle.py oracle.bundle.js $(adb shell pidof com.autonavi.minimap)
# rpc.exports: sign(str), amapencode(str), amapdecode(str), aoskey(), version()
```

## Remaining to "call 打车 API directly"
- Forge one `/ws/boss/car/order/*` request: build param JSON → `in=`(amapEncode) →
  sign → `?ent=2&in=…&csid=<uuid>` → POST. Compare response to the live app.
- Need the exact per-request param schema for the car endpoints (drive the booking
  flow with recon attached to capture the plaintext + which headers/params are required).
- For unattended offline use: port `amapEncode` (DES) to python; sign is already offline.

## New oracle files (this repo)
- `oracle.entry.js` — RPC crypto oracle (sign / amapEncode / amapDecode / aosKey)
- `run_oracle.py`   — attach-by-pid driver; enumerates surface + runs verification signs

---

# 顺风车 (hitch) ORDER LIST — captured & refreshable (2026-07-06)

Goal: read the logged-in user's active shared-ride (顺风车 driver) order list.
Done. (Own-account self-access; live cookies/PII kept on disk, never committed.)

## Why the earlier hooks were blind
顺风车 list is **Ajx3-native** — Amap's RN-like framework: native views, JS logic,
and its data network is NOT okhttp3 / `com.android.okhttp` / WebView. So:
- native AOS codec tap (amapEncode/Decode): 0 order hits
- `com.android.okhttp` + `okhttp3` hooks: 0 hits
- Chrome DevTools (enabled via frida `WebView.setWebContentsDebuggingEnabled` on the
  UI thread): 0 targets — confirms it is NOT a WebView/H5.

## The Ajx3 network layer (Java-side — no mitm/reboot needed)
`com.autonavi.minimap.ajx3.modules.net.ModuleRequest`:
- `void fetch(String key, String optionsJson, JsFunctionCallback cb)`  — request out
- `void binaryFetch(String, String, JsFunctionCallback)`
- `notifyJs(cb, int, int, [long,] int, String, String)`                — response to JS
- inner `ModuleRequest$AjxCallback.onSuccess(AosResponse)` / `onFailure(...)`
`optionsJson` keys: `url, method, headers, timeout, async, csid, bodytransfer,
aosSign, wua, body`. So the request already carries `aosSign` (our cracked
`MD5(str+"@"+aosKey)`) and `wua`; the native layer adds cookie/ent and sends it.

## Endpoint
```
POST  m5-zb.amap.com/ws/amap/hitch/driver/travel/recommend_order_list
body  application/x-www-form-urlencoded (PLAINTEXT): adcode=<city>&appChannel=…   (~408B)
```
Related poller seen on the same screen:
`POST /ws/boss/order/before/departure/passenger/location` (location heartbeat).

## Response shape (values redacted)
```
{ code:int, message:str, result:bool, timestamp:int, version:str,
  data: { orderList:[N],            # the shared-ride orders (N=10 observed)
          filterLists:[5], haveNextPage:bool, rcmdBatchId:str,
          displayTrafficRestrictionsFilter:bool } }
```
Refresh = re-fire the endpoint (UI 刷新 or replay) → fresh `orderList` + new batch.
Verified repeatable (two captures, distinct timestamps, 10 orders each).

## Capture tooling (this repo — code only; captures are gitignored)
- `sfnet.entry.js` / `run_sfnet.py` — tap `ModuleRequest.fetch`+`notifyJs`; full
  req/resp → disk (`sf_net.jsonl`), stdout shows only host/path + response keys.
- `netscan.entry.js` — enumerate loaded network/webview/bridge classes.
- `tap.entry.js` / `run_tap.py`   — decrypted AOS param tap (amapEncode/Decode) +
  HTTP; used to catch the passport login flow.
- `sftap.entry.js` / `run_sftap.py` — okhttp3 request capture (ruled okhttp out here).

## Remaining: standalone puller (TODO, not built)
Two builds — (a) frida-drive `ModuleRequest.fetch` with fresh csid (reuses live
session + crypto; robust), or (b) pure-HTTP replay (map `aosSign`→wire header +
session cookie + `wua`, POST the form body). Body is plaintext, sign is offline —
(b) is feasible once the wire header/cookie mapping is pinned.

---

# 打车 (ride-hailing) FULL FLOW — captured, signed, automated (2026-07-12)

## What was captured

Drove 打车 (the main ride-hailing UI) on-device with sfnet hook + AOSRequest hook.
Captured the full set of endpoints hit on screen load (all AOS POSTs to
`m5-zb.amap.com`, body=application/x-www-form-urlencoded, params in `?ent=2&in=<base64>&csid=<uuid>`).

| Path | Purpose |
|------|---------|
| `/ws/boss/order/car/check_multi_order` | active-order check (also returns hitch/顺风车 list) |
| `/ws/boss/car/order/content_info` | car-list page content (carriers, prices, etc.) |
| `/ws/boss/car/carlist_page_info` | car-list page config |
| `/ws/boss/order/before/departure/passenger/location` | location heartbeat (every few s) |
| `/ws/boss/car/security/authorization/check` | per-route auth check |
| `/ws/boss/order/car/security/getContacts` | contacts fetch |
| `/ws/boss/car/access_guide` | "use guide" config |
| `/ws/boss/order/car/personal_center_page_external_info` | personal center data |
| `/ws/car/user/performance/match_result` | performance tracking |
| `/ws/car/user/get_sound_switch` | settings |
| `/ws/sharedtrip/taxi/carlist` | shared-ride car list |
| `/ws/ride/transport/report/behavior` | behaviour telemetry |
| `/ws/security/account/device_reporting` | device-reporting (biological probe) |
| `/ws/lbs/pickup/dispersion_spot` | pickup-dispersion query |
| `/ws/promotion-web/resource` | promo resources |
| `/ws/amap/ride/render/uiInfo/get` | UI config |
| `/ws/vip/jointly-channel` | VIP channel |

The hitch list itself was observed in the response of `check_multi_order` —
`data.hitchOrderInfo.hitchAmapOrderIdList`, `data.hitchDriverTravelInfo.hitchTravelCount`.

## AOS sign — full reverse

By hooking `serverkey.sign` and `MessageDigest.update` from a live driving session
the input string to the sign MD5 was captured. The format is:

```
sign_input = "amap7a" + <extra> + "@" + <aosKey>
sign       = md5(sign_input).hexdigest()   // lowercase
```

- `aosKey = "xnaEwInMxaMQ2m0cw6Y1bDm7ns0YVxYS9v7JlC8I"`
- `<extra>` is the assembled sign-extension (often empty). When non-empty it
  can be a 26-char token (`aksTUJZO0ckDAFqiiAXCUfrB`) — likely derived from
  the `adiu` (device id) or `wua` (wua token). The `aosSign.sign` list in the
  Ajx3 options tells the native code which fields to fold in (e.g. `["channel","adiu"]`).
- Verified: `md5("amap7a@<aosKey>")` = `422942e485bc93857384d612081099f3`
  matched the value the live app used for `check_multi_order`-style calls.

## amapEncode key

`amapEncode(String)` is a **native** method in `libserverkey.so` (confirmed via
smali: `Lcom/autonavi/jni/server/aos/ServerkeyNative;.amapEncode: native`).
`Cipher.init` hooks on the Java side do not fire — the key is in the native
lib. The rotating 8-byte DES keys captured by recon (`0jof8eg3`, `8f31d352`,
`k88upxsy`, `ri40hsah`, `u2xsyyet`, `4n1z6vlc`) are NOT what `amapEncode`
uses — they belong to a different code path (e.g. some Ajx3 module), and
attempting to decrypt the 1-arg `amapEncode` output with them fails.
A full offline implementation requires a native reverse of `libserverkey.so`
(out of scope for this session — the C++ is heavily OLLVM-obfuscated).

## What's been built (this round)

### `oracle_server.py` — HTTP bridge to the AOS crypto oracle
Attach frida to the live `com.autonavi.minimap`, run the existing `oracle` bundle,
and expose the AOS crypto surface over HTTP:

```
GET  /health
GET  /aoskey                    -> {"aosKey": "..."}
GET  /version                   -> {"version": "..."}
POST /sign        {"input":"…"}  -> {"sign": "<uppercase MD5 hex>"}
POST /encode      {"input":"…"}  -> {"encoded": "<amapEncode base64>"}
POST /decode      {"input":"…"}  -> {"decoded": "<plaintext>"}
POST /aosrequest  {"param_str":"…"}  -> {sign, in, aoskey}
```

The server holds the only frida session and serialises calls with a single
mutex; the Python clients hit it over loopback.

### `amap_client.py` — pure-Python AOS request builder
- `amap_client.py build` — produces `{url, sign, in, csid, stts, stid, body}`.
  The `in=` is the native `amapEncode` over the body; `sign` is the lowercased
  MD5 of `"amap7a" + extra + "@" + <aosKey>`. The result URL is the wire form
  the native AOS layer would build — drop-in replayable.
- `amap_client.py call` — POSTs the body to the URL. Tested end-to-end: the
  server returns `HTTP 200` with JSON, so the wire format is right; it returns
  `code: 3 "Params error"` (or `code: 14 "用户登录校验失败"`) because the replay
  doesn't carry the live app's `Ap-Tid`, session cookie and per-request
  `asac` token — those are minted by the running app and not yet extractable
  without deeper native hooking.

### `driver_fetch2.entry.js` — in-app fetch driver
The cleanest "automate" path: hook `com.autonavi.minimap.ajx3.modules.net.ModuleRequest.fetch`,
override the URL only, and let the running app do the sign + encrypt + send
with the live session. RPC:

```js
ex.call({path: "/ws/boss/...", body: "adcode=…&appChannel=…&…", aosSign: {...}})
```

Test results (same `check_multi_order` endpoint, captured body, replayed
through the app):

- before signing: server returns `{"code": 4, "message": "Signature verification failed"}`
- with body + `aosSign: {sign: ["channel","adiu"]}`: server returns
  `{"code": 14, "message": "用户登录校验失败"}` — sign accepted, only the
  user-session check remains. This is the closest we can get without the
  actual login session.

## Remaining
1. **Auth (server-side session/cookie)** — `code 14` says the user is not
   logged in. The app itself was on the home view (the user *was* logged in
   when we drove the 打车 UI), so the issue is probably that the body's
   `__requestId` + `localTime` + `wua` no longer matches the app's current
   session. For unattended use we either need a fresh login flow (out of
   scope) or to copy the cookies/`wua` from a live request.
2. **Offline `amapEncode`** — the 8-byte key is in `libserverkey.so`. Static
   RE with Ghidra + dynamic tracing of every DES call in the lib is the next
   step; we did not crack it in this session. Until then the oracle is the
   only practical backend.

## New files (this session)

| file | role |
|------|------|
| `oracle_server.py` | HTTP bridge to frida-attached AOS crypto oracle |
| `amap_client.py` | pure-Python AOS request builder + HTTP caller |
| `driver_fetch2.entry.js` | Ajx3 MR.fetch hijack → RPC call, in-app sign+send |

