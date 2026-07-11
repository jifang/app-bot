# 警察叔叔 (com.hzpd.jwztc) API RE — Findings & Status (2026-07-06)

Hangzhou police app. Goal: automate **违章举报 (traffic-violation report)**.
API client lives in [`police_report/`](police_report/README.md); this doc records how
we got the traffic and the protections we had to beat.

## Target

- Package `com.hzpd.jwztc` (警察叔叔), app 3.14.26, Redmi Note 8 (ginkgo), Android 11, rooted (Magisk 28.1, Zygisk on).
- Built on the Alibaba mPaaS / mgop stack + `com.ali.mobisecenhance` hardening shell, with anti-root **and** anti-frida (`libmsaoaidsec.so`, `libalisecuritysdk.so`).

## Protection layer 1 — anti-root (solved: Magisk DenyList)

Fresh launch SIGBUS'd: `DEFENDER: message = your apk is root.` → `Fatal signal 7 (SIGBUS) ... tid ali_security`.
The `alijtca` shell self-crashes on a rooted device. Fix, no app patching:

```
magisk --denylist enable
magisk --denylist add com.hzpd.jwztc
```

Zygisk DenyList unmounts Magisk + hides root artifacts before the native detector scans. App then runs. (Shamiko if a future build digs deeper.)

## Protection layer 2 — anti-frida (lost the fight → pivoted to MITM)

With frida attached, crash became `DEFENDER: Hook factor 7 ... your apk is hooked` → same SIGBUS. Findings:

- Detection is **frida presence**, not our hooks — an empty no-op script still crashed. Vector: a periodic scanner thread (`ali_security`) in `libmsaoaidsec.so` reading `/proc/self/maps` + `/proc/self/task/*/stat` for frida artifacts. Confirmed by strings in the lib.
- **Neutering the watchdog** works for launch: hook `pthread_create`, and when `start_routine` belongs to `libmsaoaidsec.so`/`libalisecuritysdk.so`, redirect it to a no-op thread body. Install at spawn (pre-resume) so it's live before the sec libs load. See `antidetect.entry.js` / `cap.entry.js`.
- **But `libmsaoaidsec.so` has a second layer**: even with the watchdog neutered, it aborts *inside its own lib* (`pc 0x185d0`, `aborting: 0x0`) at `MainTabActivity`. Note: **Alibaba `crashsdk` installs its own signal handler**, so the real signal is hidden — look in `/data/data/com.hzpd.jwztc/crashsdk/logs/`, not the standard `Fatal signal` logline.
- Dead ends that made it worse: hooking **every** module exporting `SSL_write` → agent SEGV (non-TLS libs export same-named symbols with different ABI); a process-wide `strstr` replacement → 3s watchdog ANR; over-broad thread neuter (`_alijtca_` shell threads are load-bearing) → `NullPointerException: getSharedPreferences on null Application`.

**Decision:** frida is whack-a-mole against multi-layer msaoaidsec. Baseline proved the app is 100% stable with **DenyList + no frida**, so we captured at the network layer instead.

## Protection layer 3 — TLS (solved: MITM, no pinning)

The app does **not** pin certs — it trusts the system store. On the rooted device we
installed the mitmproxy CA as a **system** cert via tmpfs bind-mount (survives until reboot, no permanent `/system` write):

```
HASH=$(openssl x509 -inform PEM -subject_hash_old -in ~/.mitmproxy/mitmproxy-ca-cert.cer | head -1)
# push $HASH.0, then as root:
cp -f /system/etc/security/cacerts/* /data/local/tmp/cacerts/
cp $HASH.0 /data/local/tmp/cacerts/;  chmod 644; chcon u:object_r:system_file:s0
mount -o bind /data/local/tmp/cacerts /system/etc/security/cacerts
adb shell settings put global http_proxy 192.168.100.44:8080   # host LAN IP
```

Then `mitmdump -s mitm_addon.py`. This decrypts **everything** including the mgop RPC
gateway. App stays stable (no in-process hooks). Revert: `settings put global http_proxy :0` + `umount /system/etc/security/cacerts` (CA also gone on reboot).

**Two gotchas that cost hours when re-running on a different day/host:**
- **CA is a tmpfs bind-mount → gone on reboot** (and can be shadowed by a stale mount). Re-push + re-mount each session; verify `ls /system/etc/security/cacerts/$HASH.0` before capturing.
- **The capture host must egress China-local.** The gov WAF (`dbappwaf.cn`/`saaswaf.com`) geo-DNS returns different IP pools per resolver egress; if the host routes through an overseas VPN/split-route, mitm's upstream gets China-only IPs it **can't reach** → `502 Bad Gateway`, app shows `网络错误`, no wfjb traffic. Fix: route `police.hangzhou.gov.cn` + `dbappwaf.cn` + `saaswaf.com` **DIRECT** on the gateway (e.g. OpenClash rule) so the host egresses like the phone. If proxy is USB-only, `adb reverse tcp:8080 tcp:8080` + `http_proxy 127.0.0.1:8080` avoids a same-subnet requirement.

## The wfjb backend (违法举报)

Separate H5/mgop backend: **`https://wfjb.police.hangzhou.gov.cn:7443/wfjb-front-api/`**.
Auth = `x-token` JWT (+ `cna` cookie). Full API map, endpoints, dicts, and the
video-upload → submit flow are documented in **[`police_report/README.md`](police_report/README.md)**.

Key facts recorded there, worth flagging here:
- JWT is minted by `POST mapi-jcss.police.hangzhou.gov.cn/app/mgop` (`api: mgop.trustway.wfjb.auth`); needs a portal SSO `gsid` + an mgop `sign` (native SDK, **not reversed**) → from-scratch login not automated; token taken from a live session.
- **The mint call is only temporarily replayable.** Current evidence (2026-07-11) shows MGOP enforces the frozen signed timestamp: an aged `(gsid, sign, ts)` returns an empty HTTP `200` with response header `rs=7003` (`验签时间戳校验失败`). This is not throttling and does not show that the gsid expired; waiting cannot repair the matching `ts`/`sign`. Recovery is a fresh mitm capture of `wfjb.auth` → `save-replay` → `refresh`. Other empty-200 failures also use headers (observed `rs=4001` for gateway timeout), so the body alone must not be classified.
- Submit body **swaps lat/lng**: real longitude goes in JSON `latitude`, real latitude in `longitude`. Reproduce exactly.

## Files (this session)

| file | role |
|------|------|
| `cap.entry.js` / `run_cap.py` | frida: spawn-gated anti-frida neuter + all-TLS-lib SSL tap (superseded by MITM, kept for reference) |
| `antidetect.entry.js` | standalone msaoaidsec watchdog neuter |
| `jtap.entry.js` / `run_jtap.py` | generic BoringSSL SSL_read/write tap |
| `mitm_addon.py` | **the working capture** — mitmproxy addon, full flows → `/tmp/re/mitm.jsonl` |
| `police_report/` | the deliverable: Python client — auth/upload/submit + CLI, plus token **replay-refresh** (`cli refresh`), dashcam-overlay **OCR** (`extract_overlay.py`), and human-name→code **resolver** (`resolve.py`, backed by cached `dicts.json`) |

Captures (`/tmp/re/*.jsonl`) and `police_report/.token.json` / `.auth_replay.json` contain real PII (name/phone/token/`sign`) — all gitignored, keep off-repo.

## Open items

- Reverse or host the mgop `sign` implementation so a current `ts/sign` can be generated without a fresh device capture. Portal SSO login is partly implemented, but a fresh gsid alone cannot rebuild the signed mint request.
- `submit` files a **real** police report; client fails closed (`--confirm` required). Do not file false reports. (One real report filed this session: 回执 xlh 1493 / id 683341.)
