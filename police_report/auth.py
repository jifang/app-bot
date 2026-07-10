"""
Auth for the wfjb backend.

The `x-token` JWT that every wfjb call needs is minted by the app's mgop gateway:

    POST https://mapi-jcss.police.hangzhou.gov.cn/app/mgop
    headers:
        api:               mgop.trustway.wfjb.auth
        appid:             421424f5fe7b444782907d18955b8e1a
        extra-ak:          sjwy7hsc+2001300008+dycyyb
        ttid:              zj_pml1tn5w+200100004+nzraldx_android_2.0
        guc-platform:      app
        guc-accounttype:   person
        guc-accountsource: inner
        guc-endpoint:      C
        user-agent:        000001@JCSS_android_3.14.26
        sid / sessionid:   <gsid>          # from the main-app portal login session
        ts:                <epoch_ms>
        sign:              <md5>           # signed over the request — algo lives in
                                           # the mgop native SDK, NOT yet reversed
        content-type:      application/json; charset=utf-8
    body: {"platformId": 8}
    -> {"code":200,"data":{"token":"<JWT>", "accountId":..., "isReal":"1", ...}}

Two things block a from-scratch login and are NOT solved here:
  1. `sid`/`sessionid` (gsid) comes from the portal SSO login (phone/password or
     Alipay/gov SSO) in the main app — a separate flow.
  2. `sign` is computed by the mgop native SDK; the algorithm is not reversed.

So for now, obtain the token out-of-band (see get_token_from_mitm / README) and
pass it to WfjbClient. mgop_auth() below is a faithful request builder for when
the sign + gsid are available.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from typing import Optional
from urllib.parse import urlparse

import requests

MGOP_HOST = "mapi-jcss.police.hangzhou.gov.cn"
MGOP_PATH = "/app/mgop"
MGOP_URL = f"https://{MGOP_HOST}{MGOP_PATH}"

TOKEN_PATH = os.path.join(os.path.dirname(__file__), ".token.json")
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def decode_exp(token: str) -> Optional[int]:
    """Return the JWT `exp` (epoch seconds) or None if it can't be decoded."""
    try:
        pad = lambda s: s + "=" * (-len(s) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad(token.split(".")[1])))
        return payload.get("exp")
    except Exception:
        return None


def _atomic_write(path: str, data: str, mode: int = 0o600) -> None:
    """Write `data` to `path` atomically with the given mode (default 0600),
    so a reader never sees a half-written secret and permissions are enforced."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".swp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_token(token: str, cna: str = "", *,
               token_path: str = TOKEN_PATH, env_path: str = ENV_PATH) -> None:
    """One authoritative, atomic (0600) persistence for the x-token.

    Writes `.token.json` AND updates `WFJB_X_TOKEN` in `.env` (adding the line if
    absent). Both are updated together so `cli refresh` and the CLI's env lookup
    can never diverge — the shadowing bug where `.env` kept an expired token.
    Preserves the existing `cna` when one isn't supplied.
    """
    if not cna and os.path.isfile(token_path):
        try:
            cna = json.load(open(token_path, encoding="utf-8")).get("cna", "")
        except (ValueError, OSError):
            cna = ""
    _atomic_write(token_path, json.dumps(
        {"x_token": token, "cna": cna, "exp": decode_exp(token)}, indent=2))
    if os.path.isfile(env_path):
        env = open(env_path, encoding="utf-8").read()
        if re.search(r"^WFJB_X_TOKEN=", env, flags=re.M):
            env = re.sub(r"^WFJB_X_TOKEN=.*$", f"WFJB_X_TOKEN={token}", env, flags=re.M)
        else:
            if env and not env.endswith("\n"):
                env += "\n"
            env += f"WFJB_X_TOKEN={token}\n"
        _atomic_write(env_path, env)


def validate_mgop_url(url: str) -> None:
    """Reject a replay URL that isn't the exact HTTPS MGOP auth endpoint.

    A replay template controls the destination the captured session headers are
    POSTed to; a poisoned capture/template must not be able to exfiltrate them to
    another host. Called before persisting a template and before every replay.
    """
    u = urlparse(url)
    if u.scheme != "https" or u.hostname != MGOP_HOST or u.path != MGOP_PATH:
        raise RuntimeError(
            f"refusing replay to unexpected endpoint {url!r}; "
            f"expected https://{MGOP_HOST}{MGOP_PATH}")


def mgop_auth(*, gsid: str, sign: str, ts: int,
              appid: str = "421424f5fe7b444782907d18955b8e1a") -> dict:
    """
    Faithful builder for the mgop wfjb.auth call. Requires a valid `gsid`
    (portal session) and `sign` (from the mgop SDK). Returns the token payload.
    Raises if you have not supplied a working sign — this is intentionally not a
    silent stub.
    """
    headers = {
        "api": "mgop.trustway.wfjb.auth",
        "appid": appid,
        "extra-ak": "sjwy7hsc+2001300008+dycyyb",
        "ttid": "zj_pml1tn5w+200100004+nzraldx_android_2.0",
        "guc-platform": "app",
        "guc-accounttype": "person",
        "guc-accountsource": "inner",
        "guc-endpoint": "C",
        "user-agent": "000001@JCSS_android_3.14.26",
        "sid": gsid,
        "sessionid": gsid,
        "ts": str(ts),
        "sign": sign,
        "content-type": "application/json; charset=utf-8",
        "accept-encoding": "gzip",
    }
    resp = requests.post(MGOP_URL, headers=headers, data=json.dumps({"platformId": 8}),
                         timeout=30)
    body = resp.json()
    if body.get("code") != 200 or "token" not in body.get("data", {}):
        raise RuntimeError(f"mgop auth failed: {resp.status_code} {resp.text[:200]}")
    return body["data"]


REPLAY_PATH = os.path.join(os.path.dirname(__file__), ".auth_replay.json")


def save_replay_template(jsonl_path: str, out_path: str = REPLAY_PATH) -> bool:
    """
    Extract the app's captured `mgop.trustway.wfjb.auth` request (URL + headers +
    body) from a mitm capture and save it as a replay template. The captured
    `sign`/`sid`/`ts` are reused on replay; the wfjb backend does NOT re-check
    their freshness (verified), so replaying re-mints a token without any app
    interaction — as long as the underlying portal session stays valid.
    Returns True if a template was saved.
    """
    tpl = None
    for line in open(jsonl_path, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        hdrs = {k.lower(): v for k, v in rec.get("req_headers", {}).items()}
        if "wfjb.auth" in hdrs.get("api", ""):
            url = "https://" + rec["host"] + rec["path"]
            validate_mgop_url(url)   # never persist a template that points elsewhere
            tpl = {
                "url": url,
                "headers": rec["req_headers"],
                "body_hex": rec.get("req_body_b64", ""),
            }
    if not tpl:
        return False
    _atomic_write(out_path, json.dumps(tpl, indent=2))
    return True


def refresh_token(replay_path: str = REPLAY_PATH) -> str:
    """
    Replay the saved wfjb.auth template -> a fresh `x-token` JWT, persisting it.
    Thin wrapper over TokenProvider (the single replay+classify+persist path);
    raises RuntimeError with the classified outcome on failure. Does not need the
    app or a proxy. Sibling state (.token.json/.env/state/lock) lives next to the
    replay template so a custom path stays self-contained.
    """
    from .token_provider import RefreshOutcome, TokenProvider

    base = os.path.dirname(replay_path) or "."
    provider = TokenProvider(
        replay_path=replay_path,
        token_path=os.path.join(base, ".token.json"),
        env_path=os.path.join(base, ".env"),
        lock_path=os.path.join(base, ".token.lock"),
        state_path=os.path.join(base, ".token_state.json"),
    )
    res = provider.refresh(force=True)
    if res.outcome is RefreshOutcome.OK and res.token:
        return res.token
    raise RuntimeError(f"{res.outcome.value}: {res.detail}")


def get_token_from_mitm(jsonl_path: str) -> Optional[tuple[str, str]]:
    """
    Pull the freshest (x_token, cna_cookie) out of a mitmproxy capture
    (the /tmp/re/mitm.jsonl this project produces). Handy for driving the client
    from a live session without re-deriving auth. Returns (x_token, cna) or None.
    """
    token = cna = None
    for line in open(jsonl_path, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        hdrs = {k.lower(): v for k, v in rec.get("req_headers", {}).items()}
        if "wfjb" in rec.get("host", "") and hdrs.get("x-token"):
            token = hdrs["x-token"]                       # last one wins = freshest
            m = re.search(r"cna=([^;]+)", hdrs.get("cookie", ""))
            if m:
                cna = m.group(1)
    return (token, cna or "") if token else None
