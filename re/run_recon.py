#!/usr/bin/env python3
"""
run_recon.py — spawn Amap under frida, inject recon.js, stream hook output.

Usage:
  python3 run_recon.py [pkg] [script] [logfile]

Defaults: pkg=com.autonavi.minimap, script=recon.js, logfile=/tmp/re/recon.log
Requires frida-server (matching host frida version) already running as root on the
USB device. Keeps running until Ctrl-C so you can drive the 打车 flow by hand.
"""
import sys, time, frida

pkg = sys.argv[1] if len(sys.argv) > 1 else "com.autonavi.minimap"
script_path = sys.argv[2] if len(sys.argv) > 2 else "recon.js"
log_path = sys.argv[3] if len(sys.argv) > 3 else "/tmp/re/recon.log"

logf = open(log_path, "w", buffering=1)

def emit(line):
    print(line, flush=True)
    logf.write(line + "\n")

def on_message(message, data):
    t = message.get("type")
    if t == "send":
        emit(str(message["payload"]))
    elif t == "log":
        emit(message.get("payload", ""))
    elif t == "error":
        emit("[SCRIPT-ERROR] " + message.get("stack", str(message)))

device = frida.get_usb_device(timeout=10)
emit("[*] device: %s" % device.name)

pid = device.spawn([pkg])
emit("[*] spawned %s pid=%d" % (pkg, pid))
session = device.attach(pid)

with open(script_path) as f:
    src = f.read()
script = session.create_script(src)
script.on("message", on_message)
script.load()
emit("[*] recon.js loaded, resuming")
device.resume(pid)

emit("[*] running — drive the 打车 flow now. Ctrl-C to stop.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    emit("[*] stopping")
    session.detach()
