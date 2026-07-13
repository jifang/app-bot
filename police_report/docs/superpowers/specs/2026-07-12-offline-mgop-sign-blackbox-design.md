# Offline MGOP `sign` — black-box recovery

**Status:** Phase 1 failed 2026-07-12 — escalate Phase 2  
**Goal:** recover `sign = f(headers, body)` so mint needs no live mitm capture.

## Context

`mgop.trustway.wfjb.auth` requires a portal `gsid` plus a native MGOP `sign`.
Frozen `.auth_replay.json` dies with `rs=7003` when `ts` ages. End state is
`OfflineMgopSigner` in Python (`WFJB_MINTER=offline`).

## Phase 1 — black-box (this doc)

### Inputs (candidates)

From captured `wfjb.auth` requests:

| Field | Role |
|---|---|
| `api` | `mgop.trustway.wfjb.auth` |
| `appid` | MGOP app id |
| `sid` / `sessionid` | portal gsid |
| `ts` | ms timestamp |
| body | usually `{"platformId":8}` |
| `extra-ak`, `ttid`, `user-agent` | optional canon fields |

Observed `sign` is 32 lowercase hex → primary hypothesis **MD5**. Also try
SHA-1 / SHA-256 truncated to 32 hex, and HMAC-MD5 with known/empty keys.

### Method

1. Collect ≥1 (prefer ≥2) auth samples from `.auth_replay.json` and mitm jsonl.
2. Enumerate string canons: field permutations of the core set, separators
   (`""`, `"&"`, `"="` kv, `,`), body as raw / UTF-8 / hex / `md5(body)`.
3. Hash each candidate; exact-match captured `sign`.
4. Confirm on a second independent capture before declaring success.
5. On success: implement `OfflineMgopSigner` + wire minter; verify
   `cli mint` → `rs=1000` JWT.
6. On failure after a bounded search: stop Phase 1; escalate Phase 2 (Frida oracle).

### Out of scope (Phase 1)

- Frida / LSPosed hooks
- Static RE of `libalisecuritysdk.so`
- Emulator signer bridge

### Success criteria

`sign(api, appid, gsid, ts, body)` reproduced offline for ≥2 captures, and a
fresh-`ts` mint returns JWT (`rs=1000`).

### Failure criteria

No exact match after the agreed search space → document attempts; start Phase 2
design (Frida I/O dump → then RE / port).

## Phase 1 results (2026-07-12)

**Verdict: no match.**

Tried against 3 captures (incl. `.auth_replay.json` with full body):

- MD5 / SHA1-32 / SHA256-32 of value-joins, `key=value` joins, sorted query
  strings, body raw/hex/md5, ± `extra-ak` / `ttid` / UA
- HMAC-MD5 with empty / appid / sid / `extra-ak` fragments as keys
- ~5e5+ candidates; **0 exact hits**

Evidence sign is **not** plain MD5(canon of visible headers+body):

- `libalisecuritysdk.so` present; dex heavily packed (shell) — no clear Java
  `getSign` strings
- H5 uses `ZWJSBridge` (native), no client-side MD5 of the mgop request

**Next:** Phase 2 — Frida/LSPosed signing oracle on rooted device (hook native
signer I/O), then port or keep bridge. Mitm capture remains fallback.
