#!/usr/bin/env python3
"""
run_oracle.py — attach to the LIVE Amap process, load the AOS signing oracle,
enumerate the crypto surface, and run a couple of test signs.

Usage: python3 run_oracle.py [bundle] [pkg]
Requires frida-server (matching host) running as root; app already started.
"""
import sys, json, frida

bundle = sys.argv[1] if len(sys.argv) > 1 else "/tmp/re/oracle.bundle.js"
pkg = sys.argv[2] if len(sys.argv) > 2 else "com.autonavi.minimap"

ready = {"v": False}

def on_message(message, data):
    if message["type"] == "send":
        p = message["payload"]
        if isinstance(p, dict) and p.get("type") == "methods":
            print("=== crypto surface ===")
            for cls, meths in p["data"].items():
                print("\n# " + cls)
                for m in meths:
                    print("  " + m)
        elif isinstance(p, dict) and p.get("type") == "ready":
            ready["v"] = True
            print("\n[*] oracle ready")
        else:
            print("[send]", p)
    elif message["type"] == "error":
        print("[SCRIPT-ERROR]", message.get("stack", message))

device = frida.get_usb_device(timeout=10)
target = int(pkg) if pkg.isdigit() else pkg
session = device.attach(target)
with open(bundle) as f:
    script = session.create_script(f.read())
script.on("message", on_message)
script.load()

# wait for ready
import time
for _ in range(50):
    if ready["v"]:
        break
    time.sleep(0.1)

ex = script.exports_sync
print("\n=== probes ===")
print("version() =", ex.version())
print("aosKey()  =", ex.aoskey())

# sign(byte[]) — mirror captured inputs
for t in ["amap7aANDH161900", "hello"]:
    print("sign(%r) = %s" % (t, ex.sign(t)))

# AOS param codec round-trip (this is the `in=` transform)
sample = '{"dur":0,"pv":20240428,"dtype":"qt","ts":"1783305040"}'
enc = ex.amapencode(sample)
print("\namapEncode(sample) =", enc)
print("amapDecode(enc)    =", ex.amapdecode(enc))
encv2 = ex.amapencodev2(sample)
print("amapEncodeV2(sample) =", encv2)
print("amapDecodeV2(encv2)  =", ex.amapdecodev2(encv2))

print("\n[*] session stays open; Ctrl-C to detach")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    session.detach()
