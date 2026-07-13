# TODO — pending work

Last session: 2026-07-12 (ride-hailing sign + oracle + driver).
Device: must be **USB-attached rooted Redmi Note 8** (ginkgo, MIUI, Magisk 28.1,
Zygisk on, frida-server 17.15.3 at `/data/local/tmp/fs17`) for any frida-based step.

---

## P0 — unblock unattended AOS replays

### T1 · Capture Ap-Tid / asac / wua / cookies from the running app
**Why:** all our replays that reach the server get `code 14 用户登录校验失败`.
The live app has `Ap-Tid` (uid3), per-request `asac` SecurityGuard token, `wua`,
and cookies; we don't yet have a hook that lifts them out so the standalone
client can attach them to its forged requests.

**Approach:**
1. Bundle a fresh frida hook (`tap_session.entry.js`) that intercepts:
   - `serverkey.sign` and `MessageDigest.update` (already known to work from the
     prior sign-rebuild round) — re-use to mark the request boundary in time
   - `com.alipay.android.phone.mobilesecurity.adapter.SecurityGuard.getApdid`,
     `getAsac`, `signWithSecurityGuardSign` — SecurityGuard surface; dump every
     call's args+ret
   - `java.net.CookieManager` (or `okhttp3.CookieJar`) — capture the cookie
     set the running app has
2. From `sf_net.jsonl` we already captured (`code: 1` running-app requests) pick
   a single live request's URL + body + headers (XHR variant from `tap.entry.js`'s
   Ajx3 hook), align its timestamp with the SecurityGuard dump — that gives one
   end-to-end reference bundle.
3. Persist the bundle per request — `request → {headers, cookies, wua, asac,
   apdid, sign}` — in `/tmp/re/session.json`. The Python client loads it
   before each call and attaches everything to its forged request.

**Stub location:** `/Users/ji/Projects/app-bot/session.json` (gitignored).
**Deliverable:** `amap_client.py call` returns `data: {...}` instead of `code: 14`.

---

### T2 · Drive the booking flow to capture the price-estimate / create-order endpoints
**Why:** FINDINGS.md has the `check_multi_order`, `content_info`, carlist pages,
but **not** the price-estimate (`/ws/boss/car/order/estimate_*`) or order-creation
(`/ws/boss/car/order/create`) requests. These are the actual booking calls and
they need a destination selected in the UI.

**Approach:**
1. Use UIAutomator (`adb shell uiautomator dump`) to find the destination-edit
   `EditText` reliably, instead of pixel taps that drift across screen densities.
2. From a confirmed `findings.amap` tap-target map, drive: tap destination →
   type a Hangzhou POI → wait for `estimate_*` → tap "叫车" → wait for `create`.
3. Re-run `sfnet.entry.js` to capture full `in=` + body + response for each
   new step.
4. Add the captured endpoints + their JSON body schemas to FINDINGS.md.

**Deliverable:** new table row in `FINDINGS.md`; new bodies in
`/tmp/re/sf_net.jsonl`.

---

## P1 — finish the offline path

### T3 · Reverse the amapEncode 8-byte DES key from libserverkey.so
**Why:** every `amap_client.py build` currently needs the frida oracle to run.
All our fakes fail the moment the device isn't attached.

**Approach:**
1. Pull `libserverkey.so` (already in `/tmp/re/apk/lib/arm64-v8a/libserverkey.so`).
2. Ghidra decompile; the `.text` is OLLVM-obfuscated but `amapEncode` is one of
   the JNI exports — find its symbol and trace into the obfuscated CFG.
3. Alternative: dynamic — hook every `__aeseq`/`__des_setkey` in the lib from
   frida and dump the key material exactly when `amapEncode(String)` runs.
4. Confirm by encrypting the captured `{dur,pv,...}` JSON offline and matching
   the live `XYV//Ocu6iTzgv/...` style output byte-for-byte.
5. Drop the frida path from `amap_client.py`; the file shrinks to a 200-line
   no-deps module.

**Deliverable:** offline `amap_encode(json_str) -> base64` function in
`/Users/ji/Projects/app-bot/amap_client.py`. Commit removes `oracle_server.py`
from the README's required-to-run list.

---

### T4 · Reverse the `<extra>` token in the AOS sign
**Why:** one captured call showed `amap7aaksTUJZO0ckDAFqiiAXCUfrB@<aosKey>` —
we don't know how `aksTUJZO0ckDAFqiiAXCUfrB` is built. Most endpoints get the
empty-extra sign so this isn't blocking, but it limits the offline generic case.

**Approach:**
1. Hook `AosRequest.getAosCommonParam(boolean)` — dump the assembled common
   param JSON for each call, cross-reference the `aosSign.sign` list with
   `adiu`/`channel`/`wua` from the response.
2. The token looks like `aks…frB` (3-char prefix, base58-ish). Probably a
   one-way hash of `wua` or `adiu+timestamp`.

---

## P2 — robustness

### T5 · Reliable UI driving
**Why:** pixel-coordinate taps drift across screen sizes / DPI / states.
The tap that opened 打车 last time opened 代驾 this time. Wasted several hours
of session time chasing taps.

**Approach:**
1. Use `adb shell uiautomator dump /sdcard/ui.xml` + grep for `content-desc=`
   or `text=` matching Chinese button labels (打车 / 顺风车 / 确认 / 取消).
2. Or: hijack Ajx3's JS callback to dispatch the click via `JsFunctionCallback`
   — entirely UI-free.

**Deliverable:** drop-in `amap_drive.py` helper used by sfnet captures and by
the future T2 work.

---

### T6 · Pull session into a portable artifact
**Why:** every fresh reboot of the phone drops MMKV/SG; the captures from
2026-07-06 are gone, and we'd be stuck redoing the work if the device is
formatted.

**Approach:**
1. After each run, copy the SG files (`/data/data/com.autonavi.minimap/files/mmkv/`,
   `app_SGLib/`) and the Ajx3 caches to `/tmp/re/state-*.tgz`.
2. Add a `state.load` / `state.commit` step in `oracle_server.py` startup.

---

## P3 — second-line findings to revisit when time permits

- **Bypass AOS pinning for plain mitmproxy capture.** The trip is okhttp
  `CertificatePinner` + a native SHA1/MD5 over the GlobalSign chain. A
  bypass-only-no-resign gadget on a non-root device is still blocked; on
  this rooted device a small frida script can `CertificatePinner.check()` to
  return null + neutralise the native check. Would give full URL/header/cookie
  capture without the Ajx3 hop.
- **Phone-info cleaning.** Captured body contains a `phoneInfo` block with a
  `msg: "the phone is root, Device has su!"` warning. Server is sensitive
  to that string in some endpoints (e.g. `content_info`). Worth
  pre-processing the body's `phoneInfo` for any replay.
- **Endpoint coverage in `check_multi_order`.** Already returns the hitch
  list under `data.hitchOrderInfo`; verify it remains the case for the
  latest app version and for non-driver (rider) accounts.

---

## How to pick up next session

1. `adb devices` → rooted device must be on `d2989095` (or any other, just
   grab the pid and pass `--pid`).
2. `adb shell pidof com.autonavi.minimap` → grab PID.
3. `python3 oracle_server.py oracle.bundle.js --pid <PID> --port 8765 &` —
   starts the crypto bridge.
4. `python3 amap_client.py build --path /ws/boss/order/car/check_multi_order
   --body-file /tmp/re/check_multi_body.txt` — sanity check the offline sign
   formula still matches the live one.
5. If the bundle exits with `frida.ProcessNotFoundError`, the app was killed
   by SecurityGuard on a previous attach. Run `adb shell monkey -p
   com.autonavi.minimap -c android.intent.category.LAUNCHER 1` and rerun.

---

## Out of scope (call out so future-you doesn't try)

- **Reverse the iOS High德 app.** A13 is JB-dead, no A13 checkm8. Skip.
- **Build a binary patch of `libserverkey.so`.** Wrapper app's anti-tamper
  (signature check + SHA1 over DEX) prevents easy repack — only Xposed-style
  in-process hooking is viable here.
- **Crack the yyb `.dmg` zlib container.** Not `adb install`-able and 1.3 GB.
  Store copy is faster.