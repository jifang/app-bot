# Phase 2 — Frida MGOP signing oracle

**Status:** SUCCESS 2026-07-12 — offline sign recovered; Frida used only for RE  
**Depends on:** Phase 1 black-box failed (wrong: data must be Base64).

## Result

`com.alibaba.gov.rpc.util.GatewayUtil.md5Sign(api, sid, ts, data)`:

```text
MD5_UTF8( "{gatewaySignSecret}&api={api}&sid={gsid}&ts={ts}&data={base64(body)}" )
```

- Secret from `module_config.gateway.gatewaySignSecret` (`getSignSecret()`).
- `data` = Base64 of raw JSON body (`eyJwbGF0Zm9ybUlkIjo4fQ==` for `{"platformId":8}`).
- Implemented as `police_report.signer.OfflineMgopSigner`.
- `WFJB_MINTER=offline` → `SignerBackedMinter` mints without mitm.

Verified: capture match + live `rs=1000` mint + `cli whoami`.

## Frida notes (kept for future)

- Antidetect: pthread watchdog neuter + swallow `abort` from sec libs.
- Dex at `/data/data/.../files/ali-s-v2/dex2oat/data.png` (zip of classes*.dex).
- App still dies under Frida after ~tens of seconds; enough for RE, not for daemon.

## Success criteria — met

`OfflineMgopSigner` + gsid → JWT without live capture.
