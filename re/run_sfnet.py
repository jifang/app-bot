#!/usr/bin/env python3
"""
run_sfnet.py — tap Ajx3 ModuleRequest to capture the 顺风车 list request+response.
FULL bodies -> disk (sf_net.jsonl). Stdout/chat shows only URL + size + top-level
JSON keys (structure), never values (session cookie / order PII stay on disk).

Usage: python3 run_sfnet.py <pid> [bundle] [outfile]
"""
import sys, json, time, frida
from urllib.parse import urlparse

pid = int(sys.argv[1])
bundle = sys.argv[2] if len(sys.argv) > 2 else "/tmp/re/sfnet.bundle.js"
outfile = sys.argv[3] if len(sys.argv) > 3 else "/tmp/re/sf_net.jsonl"

out = open(outfile, "a", buffering=1)
n = {"req": 0, "resp": 0}

def keys_of(s):
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return list(o.keys())
        if isinstance(o, list):
            return ["<list len=%d>" % len(o)]
    except Exception:
        return None
    return None

def summarize_options(opt):
    """extract url + param key names only, no values"""
    try:
        o = json.loads(opt)
        url = o.get("url") or o.get("URL") or ""
        host = urlparse(url).netloc
        path = urlparse(url).path
        pk = None
        for f in ("data", "params", "body", "postData"):
            if isinstance(o.get(f), dict):
                pk = list(o[f].keys()); break
        return host, path, list(o.keys()), pk
    except Exception:
        return "?", "?", None, None

def on_message(message, data):
    if message["type"] != "send":
        if message["type"] == "error":
            print("[ERR]", message.get("stack"))
        return
    p = message["payload"]
    t = p.get("t")
    if t == "REQ":
        n["req"] += 1
        out.write(json.dumps(p, ensure_ascii=False) + "\n")           # full on disk
        host, path, topk, pk = summarize_options(p.get("options", ""))
        print("[REQ %02d] %s%s%s\n         opt-keys=%s param-keys=%s"
              % (n["req"], host, path, " (bin)" if p.get("bin") else "", topk, pk))
    elif t == "RESP":
        n["resp"] += 1
        out.write(json.dumps(p, ensure_ascii=False) + "\n")           # full on disk
        # find the biggest string field = the body; report size + keys only
        body = max([p.get(k, "") for k in ("s4", "s3", "s2", "s1")], key=len)
        print("[RESP %02d] size=%dB top-keys=%s" % (n["resp"], len(body), keys_of(body)))
    elif t == "ready":
        print("[*] sfnet ready — tap 刷新 on the 顺风车 list now")

device = frida.get_usb_device(timeout=10)
session = device.attach(pid)
script = session.create_script(open(bundle).read())
script.on("message", on_message)
script.load()
print("[*] attached pid=%d, full capture -> %s" % (pid, outfile))
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("[*] detach: %d req, %d resp" % (n["req"], n["resp"]))
    session.detach()
