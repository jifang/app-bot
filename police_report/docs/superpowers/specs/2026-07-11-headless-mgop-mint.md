# Headless MGOP mint — design snapshot

**Status:** implementing Option 1 (emulator / bridge), Option 2 (offline reverse) later.

```text
Portal session (.session.json gsid)
  └─ MgopSigner.sign(api, appid, gsid, ts, body)
       ↓
  POST mgop.trustway.wfjb.auth
       ↓
  x-token → .token.json
```

## Interfaces

- `MgopSigner.sign(...)` → `str` signature
- `TokenMinter.mint()` → `str` x-token JWT
- `SignerBackedMinter`: gsid + signer + `mgop_auth`
- `BridgeMgopSigner`: HTTP to local Android helper (`MGOP_SIGNER_URL`)
- `AndroidCaptureMinter`: boot AVD → mitm auth-only → open app → save fresh replay / extract token

## TokenProvider

On replay `SIGNATURE_EXPIRED` / `BAD_TEMPLATE`, if a minter is configured, call it once, persist JWT, return `OK`. Otherwise surface the original outcome.

## Will not work (do not implement)

- Replay same template forever
- Bump `ts` without new `sign`
- Portal `refreshToken` as MGOP signer
