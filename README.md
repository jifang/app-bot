# app-bot

Personal project for **reverse-engineering two Chinese Android apps**:

1. **警察叔叔** (`com.hzpd.jwztc`) — Hangzhou police app — for automating
   traffic-violation reports. See `police_report/` and `docs/FINDINGS-jwztc.md`.
2. **高德地图** (`com.autonavi.minimap`) — Amap — for calling the ride-hailing
   (打车) AOS backend directly. See `docs/FINDINGS.md`.

`AGENT.md` is the working contract. All commits go straight to `main`.

## Project layout

```
app-bot/
├── AGENT.md / CLAUDE.md          # agent working contract + hard rules
├── README.md                     # this file
│
├── docs/                         # RE write-ups (human-facing)
│   ├── FINDINGS.md               #   Amap RE chronology + sign formula + status
│   ├── FINDINGS-jwztc.md         #   警察叔叔 RE chronology + protection layers
│   └── TODO.md                   #   Amap ride-hailing pending work
│
├── re/                           # reverse-engineering tooling (run scripts from here)
│   ├── amap_client.py            #   AOS HTTP client (build / call)
│   ├── oracle_server.py          #   AOS crypto oracle HTTP bridge (frida-attached)
│   ├── mitm_addon.py             #   mitmproxy addon for jwztc traffic (no-frida path)
│   ├── run_*.py                  #   python frida wrappers — `python3 run_<name>.py ...`
│   ├── *.entry.js / recon.js     #   frida source scripts (bundled via compile/)
│   └── compile/                  #   frida-compile workspace (npm) + mgop_oracle*.entry.js v1→v6
│
├── police_report/                # 警察叔叔 (Hangzhou) report automation — code + report records
│   ├── README.md                 #   full usage guide (auth, submit, OCR, …)
│   ├── auth.py / client.py / token_provider.py / minter.py / signer.py / portal_login.py
│   ├── resolve.py + dicts.json   #   human name → wfjb code
│   ├── scripts/                  #   CLI (`cli.py`), cron, overlay OCR, video helpers, adb/avd
│   ├── tests/                    #   pytest suite
│   ├── reports/                  #   🚫 filed report.*.json records + report.example.json (real ones gitignored)
│   ├── docs/superpowers/         #   design specs (mgop reverse, overlay OCR, …)
│   ├── vendor/                   #   🚫 APK + rootAVD — gitignored, re-pull via scripts/
│   └── TODO.md                   #   police-side pending work
│
├── police_report_sh/             # Shanghai portal filer (playwright + API), own README / package.json
│
└── camera/                       # 🚫 ALL dashcam media, shared by both filers — gitignored
    ├── raw/                      #   original DVR footage (not managed)
    ├── images/                   #   plate / scene stills
    └── reported/                 #   submitted clips + images (never overwrite; suffix on collision)
```

Data lives only in `camera/` (media) and `police_report/reports/` (JSON records);
code directories hold no footage.

Build artifacts (`*.bundle.js`, `node_modules/`, `__pycache__/`) and any local
secrets / PII / capture files are gitignored — see `.gitignore`.

---

## Ride-hailing quick start (2026-07-12 round; run from `re/`)

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

| Path | Role |
|------|------|
| `oracle_server.py` | HTTP bridge: `sign` / `amapEncode` / `amapDecode` / `aosKey` / `version` over `127.0.0.1:8765` |
| `oracle.entry.js` + `oracle.bundle.js` | frida bundle the server hot-loads (enumerate `serverkey`/`AosEncryptor`) |
| `amap_client.py` | pure-Python sign + wire-URL build + `requests` caller (CLI: `build` / `call`) |
| `driver_fetch2.entry.js` + `driver_fetch2.bundle.js` | Ajx3 `ModuleRequest.fetch` hijack — RPC `ex.call({path, body, aosSign})` to dispatch through the live app's sign/session |
| `docs/FINDINGS.md` | chronological RE log + endpoint map + sign formula + status banner |
| `docs/TODO.md` | ranked pending work with retry-instructions |
| `*.entry.js` (`recon`/`sfnet`/`tap`/`tap.entry`/etc.) | earlier capture scripts — rebuild any missing `.bundle.js` via `compile/` |

---

## Police-app quick start

See `police_report/README.md`. The CLI (`police_report/scripts/cli.py`) accepts:
- `refresh` — replay-based, current production path
- `mint --via {auto|offline|signer|android}` — alternative minter path
- `login`, `whoami`, `submit`, `upload`, `dict`, `history`, `save-replay`

`WFJB_MINTER` env var picks the active minter (`offline` by default; the
fallback path is exercised automatically when `refresh` classifies
`SIGNATURE_EXPIRED` / `BAD_TEMPLATE`).