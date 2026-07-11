"""
mitm_addon.py — capture com.hzpd.jwztc traffic via mitmproxy (no frida, app stable).

Disk: by default only 警务 / upload hosts (INTERESTING). Set MITM_AUTH_ONLY=1 to
keep solely `wfjb.auth`. File mode 0600; rotates when over MITM_MAX_BYTES.

Console: method + host + path + body KEY NAMES only (no token/phone/session values).
"""
import json
import os
import re
from mitmproxy import http

OUT_PATH = os.environ.get("MITM_OUT", "/tmp/re/mitm.jsonl")
AUTH_ONLY = os.environ.get("MITM_AUTH_ONLY") == "1"
# Default 50 MiB; set 0 to disable rotation.
MAX_BYTES = int(os.environ.get("MITM_MAX_BYTES", str(50 * 1024 * 1024)))

os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)


def _open_out():
    return os.fdopen(os.open(OUT_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600),
                     "a", buffering=1)


OUT = _open_out()
TELEMETRY = re.compile(
    r"umeng\.com|uc\.cn|beacon-api|aaid|applog|quicktracking|unify_logs|/collect", re.I)
INTERESTING = re.compile(
    r"police\.hangzhou\.gov\.cn|mpaas|aliyuncs|oss|vod|upload", re.I)
n = {"i": 0}


def _keys(body: bytes, ctype: str):
    if not body:
        return None
    if "json" in ctype:
        try:
            o = json.loads(body)
            if isinstance(o, dict):
                return list(o.keys())
            if isinstance(o, list):
                return ["<list len=%d>" % len(o)]
        except Exception:
            pass
    if "multipart" in ctype:
        names = re.findall(rb'name="([^"]+)"', body[:4000])
        files = re.findall(rb'filename="([^"]+)"', body[:4000])
        return {"fields": [x.decode(errors="replace") for x in names],
                "files": [x.decode(errors="replace") for x in files]}
    if "form-urlencoded" in ctype:
        return sorted(set(re.findall(
            r"(?:^|&)([^=&]+)=", body.decode(errors="replace"))))
    return "<%d bytes %s>" % (len(body), ctype or "?")


def _maybe_rotate():
    global OUT
    if MAX_BYTES <= 0:
        return
    try:
        size = os.path.getsize(OUT_PATH)
    except OSError:
        return
    if size < MAX_BYTES:
        return
    OUT.close()
    rotated = OUT_PATH + ".1"
    try:
        os.replace(OUT_PATH, rotated)
    except OSError:
        pass
    OUT = _open_out()


def response(flow: http.HTTPFlow):
    req, resp = flow.request, flow.response
    req_headers = dict(req.headers)
    normalized = {k.lower(): v for k, v in req_headers.items()}
    is_wfjb_auth = (
        req.host == "mapi-jcss.police.hangzhou.gov.cn"
        and "wfjb.auth" in normalized.get("api", "")
    )
    host = req.host
    keep_disk = is_wfjb_auth if AUTH_ONLY else bool(INTERESTING.search(host))
    if keep_disk:
        _maybe_rotate()
        rec = {
            "method": req.method, "host": req.host, "path": req.path,
            "req_headers": req_headers,
            "body_hex": req.content.hex() if req.content else "",
            "status": resp.status_code,
            "resp_headers": dict(resp.headers),
            "resp_body": (resp.get_text(strict=False)[:200000]
                          if resp.content else ""),
        }
        OUT.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if TELEMETRY.search(host + req.path) and not INTERESTING.search(host):
        return  # mute noise
    n["i"] += 1
    tag = "  <<<" if INTERESTING.search(host) else ""
    rk = _keys(req.content, req.headers.get("content-type", ""))
    rj = None
    try:
        rj = (list(json.loads(resp.get_text(strict=False)).keys())
              if "json" in resp.headers.get("content-type", "") else None)
    except Exception:
        rj = None
    print("[%02d] %s %s%s%s\n     req-keys=%s\n     resp %s keys=%s"
          % (n["i"], req.method, host, req.path[:90], tag, rk, resp.status_code, rj))
