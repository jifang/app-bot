#!/usr/bin/env python3
"""
run_tap.py — attach to the LIVE Amap process and capture the real-user login
+ authenticated flow in plaintext (taps serverkey.amapEncode/amapDecode).

Usage: python3 run_tap.py [bundle] [pid] [logfile]
Leave it running, then on the device: 我的 -> log in with the real account (SMS),
open 打车, do a price query. Auth material shows up tagged *AUTH*.
"""
import sys, time, frida

bundle = sys.argv[1] if len(sys.argv) > 1 else "/tmp/re/tap.bundle.js"
pid = int(sys.argv[2]) if len(sys.argv) > 2 else None
logpath = sys.argv[3] if len(sys.argv) > 3 else "/tmp/re/capture.log"

logf = open(logpath, "a", buffering=1)
n = {"i": 0}

def emit(s):
    print(s, flush=True)
    logf.write(s + "\n")

def on_message(message, data):
    if message["type"] == "send":
        p = message["payload"]
        if isinstance(p, dict) and p.get("t") == "cap":
            n["i"] += 1
            star = "  <<< AUTH" if "*AUTH*" in p["tag"] else ""
            emit("\n[%04d %s] %s%s\n%s" % (n["i"], p["dir"], p["tag"], star, p["s"]))
        elif isinstance(p, dict) and p.get("t") == "ready":
            emit("[*] tap ready — log in on the device now")
        else:
            emit("[send] " + str(p))
    elif message["type"] == "error":
        emit("[SCRIPT-ERROR] " + message.get("stack", str(message)))

device = frida.get_usb_device(timeout=10)
session = device.attach(pid)
with open(bundle) as f:
    script = session.create_script(f.read())
script.on("message", on_message)
script.load()
emit("[*] attached pid=%s, capturing to %s" % (pid, logpath))

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    emit("[*] detach — %d events captured" % n["i"])
    session.detach()
