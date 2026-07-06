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
import re
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
