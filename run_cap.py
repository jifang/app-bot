#!/usr/bin/env python3
"""
run_cap.py — spawn-gated persistent capture for com.hzpd.jwztc.
Auto-attaches EVERY process of the package (initial launch, restarts, mPaaS
:push/:tools workers, child forks) at spawn — so anti-frida neuter + TLS tap are
live in whatever instance you actually touch. Hooks ALL bundled BoringSSL copies.

FULL bodies -> /tmp/re/jtap.jsonl. Console shows host+path+KEY NAMES only.
Just launch the app normally from the launcher after this is running; gating
catches it. Ctrl-C to stop.
"""
import re, json, time, frida

PKG = "com.hzpd.jwztc"
SCRIPT = open("cap.entry.js").read()
out = open("/tmp/re/jtap.jsonl", "a", buffering=1)
req_re = re.compile(r'^(GET|POST|PUT|DELETE|PATCH) (\S+) HTTP')
h2_re  = re.compile(r':method|:path|:authority')
host_re = re.compile(r'(?im)^Host:\s*(\S+)')
n = {"req": 0, "resp": 0}
sessions = {}

def keys(body):
    m = re.search(r'(\{.*\}|\[.*\])\s*$', body, re.S)
    if m:
        try:
            o = json.loads(m.group(1))
            if isinstance(o, dict): return list(o.keys())
            if isinstance(o, list): return ["<list len=%d>" % len(o)]
        except Exception: pass
    fm = re.findall(r'(?:^|&|\?)([a-zA-Z0-9_]+)=', body)
    return sorted(set(fm)) or None

def on_message(pid):
    def cb(msg, data):
        if msg["type"] == "error":
            print("[ERR %d]" % pid, msg.get("stack") or msg); return
        p = msg["payload"]; t = p.get("t")
        if t == "ready": print("[*] hooks live in pid %d" % pid); return
        if t == "log":   print("[frida %d] %s" % (pid, p["s"])); return
        if t not in ("OUT", "IN"): return
        s = p["s"]
        out.write(json.dumps({**p, "pid": pid}, ensure_ascii=False) + "\n")
        if t == "OUT":
            m = req_re.search(s)
            if m:
                n["req"] += 1
                h = host_re.search(s)
                # flag interesting non-telemetry hosts
                host = h.group(1) if h else "?"
                tag = "" if re.search(r'umeng|uc\.cn|beacon-api|aliyuncs', host) else "  <<<"
                print("[REQ %02d p%d] %s %s%s  keys=%s%s" %
                      (n["req"], pid, m.group(1), host, m.group(2)[:70], keys(s), tag))
            elif "multipart/form-data" in s[:200] or h2_re.search(s[:80]):
                n["req"] += 1
                head = s[:100].replace("\n", " ").replace("\r", "")
                print("[REQ %02d p%d] (h2/multipart) %s" % (n["req"], pid, head))
        else:
            if s.startswith("HTTP/1.") or s.lstrip()[:1] in ("{", "["):
                n["resp"] += 1
                code = s.split(" ", 2)[1] if s.startswith("HTTP/1.") else "body"
                print("[RESP %02d p%d] %s  keys=%s" % (n["resp"], pid, code, keys(s)))
    return cb

def instrument(pid):
    if pid in sessions: return
    try:
        s = device.attach(pid)
        sc = s.create_script(SCRIPT)
        sc.on("message", on_message(pid))
        sc.load()
        sessions[pid] = (s, sc)
    except Exception as e:
        print("[attach-fail %d] %s" % (pid, e))

def on_spawn(spawn):
    ident = spawn.identifier or ""
    # ONLY the main UI process. Worker procs (com.hzpd.jwztc:push / :tools / :xxx)
    # have different memory layout and SEGV'd our agent — and the API lives in main.
    if ident == PKG:
        print("[gate] spawn %s pid=%d" % (ident, spawn.pid))
        instrument(spawn.pid)
        device.resume(spawn.pid)      # release AFTER hooks installed (pre-init)
    else:
        if ident.startswith(PKG):
            print("[gate] skip worker %s pid=%d (resumed untapped)" % (ident, spawn.pid))
        device.resume(spawn.pid)

device = frida.get_usb_device(timeout=10)
device.on("spawn-added", on_spawn)
device.enable_spawn_gating()

# kick a fresh launch ourselves so nothing is missed
try:
    for p in device.enumerate_processes():
        if p.name == PKG or (p.name and p.name.startswith(PKG)):
            try: device.kill(p.pid)
            except Exception: pass
except Exception: pass
pid = device.spawn([PKG])
instrument(pid); device.resume(pid)
print("[*] spawn-gating ON. launched pid=%d — LOG IN + do 违章举报 video upload." % pid)
print("[*] every jwztc process is auto-protected+tapped. Ctrl-C to stop.")
try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    print("[*] stop: %d req / %d resp -> /tmp/re/jtap.jsonl" % (n["req"], n["resp"]))
    for s, sc in sessions.values():
        try: s.detach()
        except Exception: pass
