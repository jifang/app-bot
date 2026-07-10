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

import json
import os
import re
import time
from typing import Optional

import requests

MGOP_URL = "https://mapi-jcss.police.hangzhou.gov.cn/app/mgop"


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
            tpl = {
                "url": "https://" + rec["host"] + rec["path"],
                "headers": rec["req_headers"],
                "body_hex": rec.get("req_body_b64", ""),
            }
    if not tpl:
        return False
    json.dump(tpl, open(out_path, "w"), indent=2)
    return True


def refresh_token(replay_path: str = REPLAY_PATH) -> str:
    """
    Replay the saved wfjb.auth template -> a fresh `x-token` JWT. Raises if the
    template is missing or the backend declines (e.g. the portal session finally
    expired — then a new capture is needed). Does not need the app or a proxy.
    """
    if not os.path.isfile(replay_path):
        raise RuntimeError(
            f"no replay template at {replay_path}. Capture one first with "
            f"`save_replay_template(<mitm.jsonl>)` while logged in."
        )
    tpl = json.load(open(replay_path, encoding="utf-8"))
    hdrs = {k: v for k, v in tpl["headers"].items()
            if k.lower() not in ("content-length", "host", "accept-encoding")}
    body = bytes.fromhex(tpl["body_hex"]) if tpl.get("body_hex") else b""

    # The mgop gateway throttles rapid identical replays with an empty 200 body.
    # Retry a few times with backoff before giving up.
    last = ""
    for attempt in range(4):
        resp = requests.post(tpl["url"], headers=hdrs, data=body, timeout=30)
        if resp.content:
            try:
                j = resp.json()
            except ValueError:
                last = f"non-JSON {resp.status_code}: {resp.text[:120]}"
            else:
                tok = (j.get("data") or {}).get("token")
                if j.get("code") == 200 and tok:
                    return tok
                if j.get("code") != 200:
                    raise RuntimeError(
                        f"refresh declined: code={j.get('code')} msg={j.get('msg')}. "
                        f"Portal session may have expired — re-capture needed."
                    )
                last = "200 but no token"
        else:
            last = f"empty {resp.status_code} body (gateway throttling)"
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(
        f"refresh failed after retries: {last}. The existing token may still be "
        f"valid; wait a bit and retry, or re-capture if the session expired."
    )


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
