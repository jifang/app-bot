#!/usr/bin/env python3
"""
oracle_server.py — expose the AOS signing/codec oracle (serverkey native) over HTTP.
Attach to the LIVE Amap process, run the oracle bundle, and serve:
  GET  /aoskey                       -> {"aosKey": "..."}
  GET  /version                      -> {"version": "..."}
  POST /sign        {"input": "..."} -> {"sign": "<md5 hex>"}
  POST /encode      {"input": "..."} -> {"encoded": "<base64>"}
  POST /decode      {"input": "..."} -> {"decoded": "..."}
  POST /aosrequest  {"input": "..."} -> {"sign": "...", "in": "...", "aoskey": "..."}

Use when running a pure-HTTP replay offline is not yet possible (amapEncode is a
native call inside libserverkey.so — its key is not yet extracted offline).

Usage:
  python3 oracle_server.py [pkg] [port] [host]
Defaults: pkg=com.autonavi.minimap, port=8765, host=127.0.0.1
"""
import sys
import json
import time
import argparse
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import frida


class OracleServer:
    def __init__(self, target, port, host):
        self.target = target
        self.port = port
        self.host = host
        self.session = None
        self.script = None
        self._lock = threading.Lock()

    def attach(self, bundle_path):
        device = frida.get_usb_device(timeout=10)
        pid = int(self.target) if str(self.target).isdigit() else None
        if pid is None:
            pid = device.get_process(self.target).pid
        self.session = device.attach(pid)
        with open(bundle_path) as f:
            src = f.read()
        self.script = self.session.create_script(src)
        self.script.load()
        # wait for ready
        for _ in range(50):
            time.sleep(0.1)
            try:
                ex = self.script.exports_sync
                _ = ex.aoskey()
                break
            except Exception:
                continue

    def call(self, method, *args):
        with self._lock:
            ex = self.script.exports_sync
            fn = getattr(ex, method)
            return fn(*args)

    def close(self):
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass


class Handler(BaseHTTPRequestHandler):
    oracle = None  # set on HTTPServer init

    def log_message(self, fmt, *args):
        sys.stderr.write("[http] " + (fmt % args) + "\n")

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n == 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            return {"_parse_error": str(e)}

    def do_GET(self):
        if self.path == "/aoskey":
            return self._send_json(200, {"aosKey": self.oracle.call("aoskey")})
        if self.path == "/version":
            return self._send_json(200, {"version": self.oracle.call("version")})
        if self.path == "/health":
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        body = self._read_body()
        try:
            if self.path == "/sign":
                inp = body.get("input", "")
                if not inp:
                    return self._send_json(400, {"error": "missing input"})
                return self._send_json(200, {"sign": self.oracle.call("sign", inp)})
            if self.path == "/encode":
                inp = body.get("input", "")
                if not inp:
                    return self._send_json(400, {"error": "missing input"})
                return self._send_json(200, {"encoded": self.oracle.call("amapencode", inp)})
            if self.path == "/decode":
                inp = body.get("input", "")
                if not inp:
                    return self._send_json(400, {"error": "missing input"})
                return self._send_json(200, {"decoded": self.oracle.call("amapdecode", inp)})
            if self.path == "/encodev2":
                inp = body.get("input", "")
                if not inp:
                    return self._send_json(400, {"error": "missing input"})
                return self._send_json(200, {"encoded": self.oracle.call("amapencodev2", inp)})
            if self.path == "/decodev2":
                inp = body.get("input", "")
                if not inp:
                    return self._send_json(400, {"error": "missing input"})
                return self._send_json(200, {"decoded": self.oracle.call("amapdecodev2", inp)})
            if self.path == "/aosrequest":
                # convenience: build the `in=` body and the `sign` field in one call
                # params: { "param_str": "<part1>+<part2>", "aos_key": "<optional override>" }
                param_str = body.get("param_str", "")
                if not param_str:
                    return self._send_json(400, {"error": "missing param_str"})
                in_b64 = self.oracle.call("amapencode", param_str)
                aos_key = body.get("aos_key") or self.oracle.call("aoskey")
                sign = self.oracle.call("sign", param_str + "@" + aos_key).lower()
                return self._send_json(200, {
                    "sign": sign,
                    "in": in_b64,
                    "aoskey": aos_key,
                })
            return self._send_json(404, {"error": "not found"})
        except Exception as e:
            return self._send_json(500, {"error": str(e)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", nargs="?", default="oracle.bundle.js")
    ap.add_argument("--pkg", default="com.autonavi.minimap")
    ap.add_argument("--pid", default=None)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    target = args.pid or args.pkg
    server = OracleServer(target, args.port, args.host)
    server.attach(args.bundle)
    print(f"[*] oracle attached to {target}")

    httpd = HTTPServer((args.host, args.port), Handler)
    Handler.oracle = server
    print(f"[*] oracle HTTP listening on http://{args.host}:{args.port}")
    print(f"    GET  /health")
    print(f"    GET  /aoskey")
    print(f"    GET  /version")
    print(f"    POST /sign     {{'input': '<bytes>'}}")
    print(f"    POST /encode   {{'input': '<json>'}}")
    print(f"    POST /decode   {{'input': '<base64>'}}")
    print(f"    POST /aosrequest {{'param_str': '<a>+<b>'}}  -> sign+in+aoskey")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()