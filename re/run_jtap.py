#!/usr/bin/env python3
"""
run_jtap.py — capture com.hzpd.jwztc TLS plaintext (login + 违章举报 endpoints).
FULL request/response reassembled bodies -> disk (jtap.jsonl).
Stdout/chat: method + host + path + param/JSON KEY NAMES only, never values
(login token / phone / session stay on disk).

Usage: python3 run_jtap.py [outfile]
  spawns the app fresh so nothing is missed and anti-frida runs after our hooks.
"""
import sys, re, json, time, frida

PKG = "com.hzpd.jwztc"
outfile = sys.argv[1] if len(sys.argv) > 1 else "/tmp/re/jtap.jsonl"

out = open(outfile, "a", buffering=1)
req_re = re.compile(r'^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) (\S+) HTTP')
host_re = re.compile(r'(?im)^Host:\s*(\S+)')
n = {"out": 0, "in": 0}

def keys(body):
    # body may be headers+json; grab last {...} or [...]
    m = re.search(r'(\{.*\}|\[.*\])\s*$', body, re.S)
    if not m:
        return None
    try:
        o = json.loads(m.group(1))
        if isinstance(o, dict): return list(o.keys())
        if isinstance(o, list): return ["<list len=%d>" % len(o)]
    except Exception:
        # form-encoded?
        fm = re.findall(r'(?:^|&)([a-zA-Z0-9_]+)=', body)
        return sorted(set(fm)) or None
    return None

def on_message(msg, data):
    if msg["type"] == "error":
        print("[ERR]", msg.get("stack") or msg); return
    p = msg["payload"]; t = p.get("t")
    if t == "ready":
        print("[*] hooks live — go ahead and log in / tap 违章举报"); return
    if t == "log":
        print("[frida]", p.get("s")); return
    if t not in ("OUT", "IN"):
        return
    s = p["s"]
    out.write(json.dumps(p, ensure_ascii=False) + "\n")   # full plaintext on disk
    if t == "OUT":
        m = req_re.search(s)
        if m:
            n["out"] += 1
            host = (host_re.search(s) or [None, "?"])[1] if host_re.search(s) else "?"
            print("[REQ %02d] %s %s%s  keys=%s" %
                  (n["out"], m.group(1), host, m.group(2), keys(s)))
    else:
        if "HTTP/1." in s[:20] or s.lstrip().startswith(("{", "[")):
            n["in"] += 1
            code = s.split(" ", 2)[1] if s.startswith("HTTP/1.") else "200?"
            print("[RESP %02d] %s  keys=%s" % (n["in"], code, keys(s)))

device = frida.get_usb_device(timeout=10)
pid = device.spawn([PKG])
session = device.attach(pid)
script = session.create_script(open("jtap.entry.js").read())
script.on("message", on_message)
script.load()
device.resume(pid)
print("[*] spawned + attached pid=%d, full capture -> %s" % (pid, outfile))
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("[*] detach: %d req, %d resp" % (n["out"], n["in"]))
    session.detach()
