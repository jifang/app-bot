"""
mitm_addon.py — capture com.hzpd.jwztc traffic via mitmproxy (no frida, app stable).
FULL request/response (headers+body) -> /tmp/re/mitm.jsonl on disk.
Console: method + host + path + body KEY NAMES only (no token/phone/session values).
Highlights the police portal + upload/OSS hosts; mutes pure telemetry.
"""
import json, os, re
from mitmproxy import http

OUT_PATH = os.environ.get("MITM_OUT", "/tmp/re/mitm.jsonl")
AUTH_ONLY = os.environ.get("MITM_AUTH_ONLY") == "1"
os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
OUT = os.fdopen(os.open(OUT_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600),
                "a", buffering=1)
TELEMETRY = re.compile(r"umeng\.com|uc\.cn|beacon-api|aaid|applog|quicktracking|unify_logs|/collect", re.I)
INTERESTING = re.compile(r"police\.hangzhou\.gov\.cn|mpaas|aliyuncs|oss|vod|upload", re.I)
n = {"i": 0}

def _keys(body: bytes, ctype: str):
    if not body:
        return None
    if "json" in ctype:
        try:
            o = json.loads(body)
            if isinstance(o, dict): return list(o.keys())
            if isinstance(o, list): return ["<list len=%d>" % len(o)]
        except Exception:
            pass
    if "multipart" in ctype:
        names = re.findall(rb'name="([^"]+)"', body[:4000])
        files = re.findall(rb'filename="([^"]+)"', body[:4000])
        return {"fields": [x.decode(errors="replace") for x in names],
                "files": [x.decode(errors="replace") for x in files]}
    if "form-urlencoded" in ctype:
        return sorted(set(re.findall(r"(?:^|&)([^=&]+)=", body.decode(errors="replace"))))
    return "<%d bytes %s>" % (len(body), ctype or "?")

def response(flow: http.HTTPFlow):
    req, resp = flow.request, flow.response
    req_headers = dict(req.headers)
    normalized = {k.lower(): v for k, v in req_headers.items()}
    is_wfjb_auth = (
        req.host == "mapi-jcss.police.hangzhou.gov.cn"
        and "wfjb.auth" in normalized.get("api", "")
    )
    if AUTH_ONLY and not is_wfjb_auth:
        return
    rec = {
        "method": req.method, "host": req.host, "path": req.path,
        "req_headers": req_headers, "req_body_b64": req.content.hex() if req.content else "",
        "status": resp.status_code, "resp_headers": dict(resp.headers),
        "resp_body": resp.get_text(strict=False)[:200000] if resp.content else "",
    }
    OUT.write(json.dumps(rec, ensure_ascii=False) + "\n")

    host = req.host
    if TELEMETRY.search(host + req.path) and not INTERESTING.search(host):
        return  # mute noise
    n["i"] += 1
    tag = "  <<<" if INTERESTING.search(host) else ""
    rk = _keys(req.content, req.headers.get("content-type", ""))
    rj = None
    try:
        rj = list(json.loads(resp.get_text(strict=False)).keys()) if "json" in resp.headers.get("content-type","") else None
    except Exception:
        rj = None
    print("[%02d] %s %s%s%s\n     req-keys=%s\n     resp %s keys=%s"
          % (n["i"], req.method, host, req.path[:90], tag, rk, resp.status_code, rj))
