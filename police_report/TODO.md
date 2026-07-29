# TODO

## gsid silent renewal via refresh_token

`police_report/portal_login.py:_do_login` already receives a `refreshToken`
from `/uc/login`, and `save_session` persists it in `.session.json` —
but nothing in the codebase ever calls it.

**Why:** the portal SSO `gsid` eventually expires (gov SSO typical TTL: ~30 d;
we don't know the exact policy). When that happens, the offline minter signs
correctly but MGOP rejects with `AUTH_EXPIRED`/`rs=...`, and `auto_refresh`
exits 1. Today the only recovery is `cli login` (interactive SMS OTP).

**Sketch (~30 lines, `police_report/portal_refresh.py`):**

1. POST `https://portal-jcss.police.hangzhou.gov.cn/uc/login/refreshToken`
   with the persisted `refresh_token` (same RSA-pubkey fetch pattern as
   `PortalLoginFlow`).
2. Reuse the response's new `gsid` to call `_get_auth_code` + `_person_login`
   and overwrite `.session.json` (atomic 0600).
3. Wire it into `TokenProvider._resolve_gsid` as a higher-priority source
   than the `.auth_replay.json` `sid` fallback, so we silently swap to the
   fresh gsid before minting.
4. Surface in `.token_state.json` when a refresh was used, so the operator
   sees "gsid auto-renewed" rather than just "OK".

**Done when:** killing the gsid on the server side and waiting > 1 cycle
results in `auto_refresh` continuing to mint `OK` without human action.

**Out of scope:** rotating the `refresh_token` itself (it can also die).
When that happens, fall back to `cli login` — accept the one-time OTP.