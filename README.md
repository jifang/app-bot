# app-bot

Personal project for **reverse-engineering two Chinese Android apps**:

1. **警察叔叔** (`com.hzpd.jwztc`) — Hangzhou police app — for automating
   traffic-violation reports. See `police_report/` and `FINDINGS-jwztc.md`.
2. **高德地图** (`com.autonavi.minimap`) — Amap — for calling the ride-hailing
   (打车) AOS backend directly. See `FINDINGS.md`.

`AGENT.md` is the working contract. All commits go straight to `main`.

---

## Ride-hailing quick start (2026-07-12 round)

The driving test rig: **USB-attached rooted Redmi Note 8** (ginkgo, MIUI,
Magisk 28.1, Zygisk on, frida-server 17.15.3 at `/data/local/tmp/fs17`),
with `com.autonavi.minimap` PID discoverable via
`adb shell pidof com.autonavi.minimap`.

```
# 1. Start the crypto bridge (frida-attached; one per host).
python3 oracle_server.py oracle.bundle.js --pid <PID> --port 8765 &

# 2a. Pure-Python client build (no HTTP send) — for replay inspection.
python3 amap_client.py build --path /ws/boss/order/car/check_multi_order \
    --body-file /tmp/re/check_multi_body.txt

# 2b. Pure-Python client call — wire-format POST.
python3 amap_client.py call --path /ws/boss/order/car/check_multi_order \
    --body-file /tmp/re/check_multi_body.txt

# 3. In-app fetch driver (RPC) — uses the live app's sign/session.
#    frida -H <host> driver_fetch2.bundle.js -p <PID>
#    Then in the python side:
ex.call({path: "/ws/boss/...", body: "adcode=…", aosSign: {...}})
```

Files:

| Path | Purpose |
|------|---------|
| `oracle_server.py` | HTTP bridge: `sign` / `amapEncode` / `amapDecode` / `aosKey` / `version` |
| `oracle.bundle.js` | frida bundle the server uses (built from `oracle.entry.js`) |
| `amap_client.py` | offline sign + build wire URL; or send via `requests` |
| `driver_fetch2.entry.js` | Ajx3 `ModuleRequest.fetch` hijack — run inside app for end-to-end |
| `FINDINGS.md` | full RE log + endpoint map + sign formula + what's still pending |
| `TODO.md` | ranked pending work with retry-instructions |
| `FINDINGS-jwztc.md` | police-app RE log |

---

## Police-app quick start

See `police_report/README.md`. The `cli.py` accepts `refresh` (replay-based,
current production path) and `mint` (new; offline/signer/android minter)
subcommands; `WFJB_MINTER` env var picks the active minter.