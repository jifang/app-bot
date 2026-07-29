# Offline MGOP `sign` — black-box recovery

**Status:** ✅ Phase 1 succeeded 2026-07-12 (canon recovered, minter shipped)
**Goal:** recover `sign = f(headers, body)` so mint needs no live mitm capture.

## Context

`mgop.trustway.wfjb.auth` requires a portal `gsid` plus a native MGOP `sign`.
Frozen `.auth_replay.json` dies with `rs=7003` when `ts` ages. End state is
`OfflineMgopSigner` in Python (default minter; opt out via `WFJB_MINTER=off`).

## Canon

`sign = md5( UTF-8( "{gatewaySignSecret}&api={api}&sid={gsid}&ts={ts}&data={base64(body)}" ) )`

recovered from `com.alibaba.gov.rpc.util.GatewayUtil.md5Sign` in the official
APK (`com.hzpd.jwztc`). `gatewaySignSecret = "cgpQrzzC2VpmFCcIW0xa+2Oj"` is a
build-time constant in `module_config.gateway.gatewaySignSecret`, not a
per-user secret — it would only change on a new app release.

`appid` is a request header (`421424f5fe7b444782907d18955b8e1a`) but does
**not** enter the canon.

## Implementation

* `police_report/signer.py:68` — `OfflineMgopSigner`
* `police_report/minter.py:51` — `SignerBackedMinter` wires signer + gsid +
  `mgop_auth` into a single mint
* `police_report/token_provider.py:_refresh_unlocked` falls back to the minter
  on `SIGNATURE_EXPIRED` / `BAD_TEMPLATE`; cached `.auth_replay.json` is now a
  gsid source, not a sign source
* `default_minter()` defaults to `OfflineMgopSigner`; no env var needed

## Verification

* `tests/test_signer_minter.py:183` — `OfflineMgopSigner.sign()` reproduces a
  captured 2026-07-12 replay `e81e0a306731622652a58cb66119b4b9` exactly
* Live mint on 2026-07-13 against `mapi-jcss.police.hangzhou.gov.cn` returned
  a `rs=1000` JWT with the canonical `sub=897780` and a fresh `ts`

## Phase 2 — not needed

Frida oracle / native RE was skipped because Phase 1 closed the loop. Keep the
spec on file as the audit trail; do not start Phase 2 unless
`gatewaySignSecret` changes in a new APK (signer unit test will catch it on the
first replay replay).

## Caveats

* Portal **gsid** expires on its own clock. Until `portal_refresh.py` lands,
  renew via `cli login` (SMS OTP) or in-app re-login.
* Sign canon ties to a build of `com.hzpd.jwztc`. If the gateway secret rotates
  in a future release, this signer will silently produce 7003 until updated;
  alert on `auto_refresh` exit-1 in `.token_state.json` to catch it.