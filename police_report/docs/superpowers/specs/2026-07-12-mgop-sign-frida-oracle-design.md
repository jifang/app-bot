# Phase 2 — Frida MGOP signing oracle

**Status:** NOT NEEDED 2026-07-12 — Phase 1 black-box recovered the canon.
**Supersedes:** the "depends on Phase 1 failed" assumption in the original draft.

## Why this spec is dormant

The black-box canon in `2026-07-12-offline-mgop-sign-blackbox-design.md`
turned out to be sufficient: `MD5("{secret}&api=&sid=&ts=&data=b64(body)")` with
the build-time `gatewaySignSecret` reproduces every captured `sign`. The
offline signer is the production path; Frida was not used for the recovery.

## Result (kept for the audit trail)

`com.alibaba.gov.rpc.util.GatewayUtil.md5Sign(api, sid, ts, data)`:

```text
MD5_UTF8( "{gatewaySignSecret}&api={api}&sid={gsid}&ts={ts}&data={base64(body)}" )
```

- Secret from `module_config.gateway.gatewaySignSecret` (`getSignSecret()`).
- `data` = Base64 of raw JSON body (`eyJwbGF0Zm9ybUlkIjo4fQ==` for `{"platformId":8}`).
- Implemented as `police_report.signer.OfflineMgopSigner` (default minter).
- `WFJB_MINTER=offline` (now the default) → `SignerBackedMinter` mints without mitm.

Verified: capture match + live `rs=1000` mint + `cli whoami`.

## Frida notes (kept for future, only if `gatewaySignSecret` rotates)

- Antidetect: pthread watchdog neuter + swallow `abort` from sec libs.
- Dex at `/data/data/.../files/ali-s-v2/dex2oat/data.png` (zip of classes*.dex).
- App still dies under Frida after ~tens of seconds; enough for RE, not for daemon.

## When to revisit

Only if the gateway rotates `gatewaySignSecret` in a new APK release — the
unit test in `tests/test_signer_minter.py:183` would fail on the first replay
against the new template, and we'd need this Frida oracle to lift the new
secret out of the APK. Until then, do nothing.
