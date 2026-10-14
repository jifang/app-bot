#!/usr/bin/env python3
"""
run_sftap.py — capture okhttp3/AjxHttp requests (the 顺风车 list loader) to disk
so they can be replayed. Full request (url+headers+body) -> disk JSONL.
Chat/stdout shows only method+url (headers/body, which carry the session cookie,
stay on disk only).

Usage: python3 run_sftap.py <pid> [bundle] [outfile]
"""
import sys, json, time, frida

pid = int(sys.argv[1])
bundle = sys.argv[2] if len(sys.argv) > 2 else "/tmp/re/sftap.bundle.js"
outfile = sys.argv[3] if len(sys.argv) > 3 else "/tmp/re/sf_requests.jsonl"

out = open(outfile, "a", buffering=1)
n = {"i": 0}

def on_message(message, data):
    if message["type"] != "send":
        if message["type"] == "error":
            print("[ERR]", message.get("stack"))
        return
    p = message["payload"]
    t = p.get("t")
    if t == "req":
        n["i"] += 1
        out.write(json.dumps(p, ensure_ascii=False) + "\n")   # full, on disk
        url = p.get("url", "")
        host = url.split("/")[2] if "://" in url else "?"
        flag = ""
        if any(k in url.lower() for k in ("order", "trip", "sf", "shunfeng", "pinche", "carpool", "list", "boss/car")):
            flag = "   <<< candidate"
        print("[%03d %s] %s %s%s" % (n["i"], p.get("via"), p.get("method"), url, flag))
    elif t == "ajx":
        print("\n=== AjxHttpLoader methods ===")
        for m in p["methods"]:
            print("  ", m)
    elif t == "log":
        print(p["s"])
    elif t == "ready":
        print("[*] sftap ready — pull-to-refresh the 顺风车 list now")

device = frida.get_usb_device(timeout=10)
session = device.attach(pid)
script = session.create_script(open(bundle).read())
script.on("message", on_message)
script.load()
print("[*] attached pid=%d, requests -> %s" % (pid, outfile))
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("[*] detach, %d requests captured" % n["i"])
    session.detach()
