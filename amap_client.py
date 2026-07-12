#!/usr/bin/env python3
"""
amap_client.py — pure-Python AOS request client.

Builds and sends AOS requests against the 高德 (Amap) ride-hailing backend.
Uses the running oracle_server (frida-attached) for sign + amapEncode.

What we know:
- URL pattern: POST https://<host>/ws/<path>?ent=2&in=<base64>&csid=<uuid>[&stts=...&stid=...]
- Body is sent as PLAINTEXT application/x-www-form-urlencoded
  (the `in=` query carries an AES/DES-encrypted body that the native AOS
   layer builds from the same plaintext).  Some endpoints have BOTH the
   plaintext form body and the `in=` ciphertext.
- Sign: md5(<body>@<aosKey>).hexdigest() (lowercase)
- amapEncode is native (libserverkey.so) — uses an oracle HTTP bridge
  unless the offline implementation has been added.

Usage:
  # Start oracle_server.py first (it attaches frida to Amap)
  python3 amap_client.py call \\
      --host m5-zb.amap.com \\
      --path /ws/boss/order/before/departure/passenger/location \\
      --body "adcode=330100&appChannel=C3221&..." \\
      [--stid <uuid>] [--stts <ms>] \\
      [--headers "Ap-Tid=xxx,Cookie=yyy"]
"""
import argparse
import json
import sys
import time
import urllib.parse
import uuid
import requests

from urllib.parse import urlencode

# AOS constants (captured from live process)
DEFAULT_HOST = "m5-zb.amap.com"
DEFAULT_SCHEME = "https"
DEFAULT_ORACLE = "http://127.0.0.1:8765"

# Default headers as observed on /ws/boss/* calls in the Ajx3 capture
DEFAULT_HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; Redmi Note 8 Build/Xiaomi-Redmi Note 8) AliApp(AMAP/16.19.0)",
    "X-Requested-With": "XMLHttpRequest",
}


def oracle_call(oracle_url, method, payload=None):
    """Talk to the oracle HTTP server."""
    if method == "GET":
        r = requests.get(f"{oracle_url}/{payload or ''}", timeout=10)
    else:
        r = requests.post(f"{oracle_url}/{method}", json=payload or {}, timeout=10)
    r.raise_for_status()
    return r.json()


def build_aos_request(
    body: str,
    *,
    oracle_url: str = DEFAULT_ORACLE,
    stts: int = None,
    stid: str = None,
    extra_query: dict = None,
):
    """
    Build the `in=...` (encrypted body), the `sign` field, and the `ent=2&csid=...`
    query params that the native AOS layer normally adds on top of the Ajx3 body.

    Returns (in_ciphertext, sign, csid, stts, stid)
    """
    if stts is None:
        stts = int(time.time() * 1000)
    if stid is None:
        stid = uuid.uuid4().hex

    # amapEncode the body — oracle will run the native call
    enc = oracle_call(oracle_url, "encode", {"input": body})
    in_b64 = enc["encoded"]

    # sign: md5(body + "@" + aosKey)
    sig = oracle_call(oracle_url, "aosrequest", {"param_str": body})
    sign = sig["sign"]

    csid = uuid.uuid4().hex
    return in_b64, sign, csid, stts, stid


def make_url(host, path, *, in_b64, sign, csid, stts=None, stid=None, extra_query=None):
    """Build the final URL with ent=2&in=...&csid=... query."""
    q = {
        "ent": 2,
        "in": in_b64,
        "csid": csid,
    }
    if sign:
        # The native layer puts sign in body? Let me try URL first, can move to body if needed
        q["sign"] = sign
    if stts is not None:
        q["stts"] = stts
    if stid is not None:
        q["stid"] = stid
    if extra_query:
        q.update(extra_query)
    qs = urlencode(q)
    return f"https://{host}{path}?{qs}"


def call(
    *,
    host: str = DEFAULT_HOST,
    path: str,
    body: str,
    oracle_url: str = DEFAULT_ORACLE,
    stts: int = None,
    stid: str = None,
    extra_query: dict = None,
    extra_headers: dict = None,
    timeout: int = 10,
    verbose: bool = False,
):
    """
    Build the encrypted body + sign + headers, POST to the AOS endpoint,
    return parsed JSON.
    """
    in_b64, sign, csid, stts, stid = build_aos_request(
        body,
        oracle_url=oracle_url,
        stts=stts,
        stid=stid,
        extra_query=extra_query,
    )
    url = make_url(host, path, in_b64=in_b64, sign=sign, csid=csid,
                   stts=stts, stid=stid, extra_query=extra_query)

    headers = dict(DEFAULT_HEADERS_BASE)
    if extra_headers:
        headers.update(extra_headers)

    if verbose:
        print(f"[client] POST {url[:120]}...")
        print(f"[client] body ({len(body)} chars): {body[:200]}{'...' if len(body) > 200 else ''}")

    r = requests.post(url, data=body, headers=headers, timeout=timeout)
    if verbose:
        print(f"[client] <- {r.status_code} ({len(r.content)}B)")
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def cmd_call(args):
    body = args.body
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    if not body:
        print("error: --body or --body-file required", file=sys.stderr)
        sys.exit(2)
    code, resp = call(
        host=args.host,
        path=args.path,
        body=body,
        oracle_url=args.oracle,
        stts=args.stts,
        stid=args.stid,
        extra_query=dict(p.split("=", 1) for p in args.extra_query) if args.extra_query else None,
        extra_headers=dict(p.split("=", 1) for p in args.extra_headers) if args.extra_headers else None,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    print(f"HTTP {code}")
    if isinstance(resp, (dict, list)):
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        print(resp)


def cmd_build(args):
    """Just build (sign, in=, url) without sending."""
    body = args.body or open(args.body_file).read() if args.body_file else None
    if not body:
        print("error: --body or --body-file required", file=sys.stderr)
        sys.exit(2)
    in_b64, sign, csid, stts, stid = build_aos_request(
        body, oracle_url=args.oracle,
        stts=args.stts, stid=args.stid,
    )
    url = make_url(args.host, args.path, in_b64=in_b64, sign=sign, csid=csid,
                   stts=stts, stid=stid)
    out = {
        "url": url,
        "sign": sign,
        "in": in_b64,
        "csid": csid,
        "stts": stts,
        "stid": stid,
        "body": body,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", default=DEFAULT_ORACLE, help="oracle_server URL")
    ap.add_argument("--host", default=DEFAULT_HOST)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("call", help="build+send a request")
    c.add_argument("--path", required=True, help="e.g. /ws/boss/order/before/departure/passenger/location")
    c.add_argument("--body")
    c.add_argument("--body-file")
    c.add_argument("--stts", type=int)
    c.add_argument("--stid")
    c.add_argument("--extra-query", action="append", default=[], help="key=value (repeatable)")
    c.add_argument("--extra-headers", action="append", default=[], help="Header: value (repeatable)")
    c.add_argument("--timeout", type=int, default=10)
    c.add_argument("--verbose", "-v", action="store_true")
    c.set_defaults(func=cmd_call)

    b = sub.add_parser("build", help="only build (no HTTP send)")
    b.add_argument("--path", required=True)
    b.add_argument("--body")
    b.add_argument("--body-file")
    b.add_argument("--stts", type=int)
    b.add_argument("--stid")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()