# police_report

Python client for the Hangzhou police **违法举报 (wfjb)** backend, reverse-engineered
from the `com.hzpd.jwztc` (警察叔叔) app. For how the traffic was captured (anti-root,
anti-frida, MITM), see [`../FINDINGS-jwztc.md`](../FINDINGS-jwztc.md).

Backend: `https://wfjb.police.hangzhou.gov.cn:7443/wfjb-front-api/`
Auth: an `x-token` JWT on every request (+ a `cna` cookie).

> ⚠️ `submit_report` files a **real** traffic-violation report with Hangzhou police.
> The CLI refuses to send without `--confirm`. Do not file false reports.

## Install

```bash
pip install -r police_report/requirements.txt
```

## Authentication

Every wfjb API call carries an `x-token` JWT (plus a `cna` cookie). Understanding
where that token comes from — and what does *not* renew automatically — is the key
to running this unattended.

### The three layers

```
  gsid  ─────────────►  x-token  ─────────────►  wfjb-front-api calls
 (portal SSO session)   (~1h JWT)               (whoami / upload / submit …)

 long-lived; minted     minted per-refresh      authenticated by the X-Token
 by phone/gov SSO        by REPLAYING the        header + cna cookie
 login in the app        mgop mint call
```

1. **`gsid`** — a portal SSO session id, obtained when a human logs into the
   警察叔叔 app (phone+SMS or Alipay/gov SSO). Long-lived (days–weeks), but it
   *does* eventually expire. It is **not** minted by this toolchain.
2. **`x-token`** — a ~1 h JWT (has an `exp`), minted by POSTing the mgop gateway
   with a valid `gsid` + `sign`. This is what every wfjb call actually uses.
3. **The API calls** — `WfjbClient` sends `X-Token: <jwt>` + `Cookie: cna=<…>`.

### The mint call (mgop)

```
POST https://mapi-jcss.police.hangzhou.gov.cn/app/mgop
  api:  mgop.trustway.wfjb.auth
  sid / sessionid: <gsid>          # the portal session
  sign:            <md5>           # signed over the request by the mgop NATIVE SDK
  ts:              <epoch_ms>
  + appid / extra-ak / ttid / guc-* / user-agent headers   (see auth.py)
  body: {"platformId": 8}
  ->  {"code":200, "data":{"token":"<JWT>", "isReal":"1", …}}
```

Two inputs to this call are **not reproducible offline**, which is why a
from-scratch login isn't implemented:
- **`gsid`** comes from the portal SSO login (a separate app flow).
- **`sign`** is computed by the mgop native SDK (`libmsaoaidsec`/`libalisecuritysdk`);
  the algorithm/embedded key is **not reversed** (see `FINDINGS-jwztc.md`).

### Replay refresh — why no re-login is needed day-to-day

The mint call is **replayable**: the gateway does **not** re-check the captured
`sign`/`ts` for freshness. So re-POSTing the *exact same* `(gsid, sign, ts)`
triple re-mints a fresh ~1 h `x-token` — no app, no proxy, no SMS — **for as long
as the `gsid` inside it is still a live portal session.** That captured triple is
saved once as the replay template `.auth_replay.json`:

```bash
# once, from a live mitmproxy capture (this repo's addon writes /tmp/re/mitm.jsonl):
python -m police_report.cli save-replay /tmp/re/mitm.jsonl   # -> .auth_replay.json

# thereafter, any time the token is stale:
python -m police_report.cli refresh                          # re-mints, persists
```

### token vs `gsid` — what refreshes, and what doesn't

This is the distinction that matters when it breaks:

| You have | Can refresh the **x-token**? | Can renew the **gsid**? |
|---|---|---|
| `.auth_replay.json` (live gsid inside) | ✅ yes — `cli refresh`, indefinitely | ❌ no — the gsid is frozen in it |
| `.auth_replay.json` (gsid expired) | ❌ no — replay returns `PORTAL_EXPIRED` | ❌ no |
| `cli login` (fresh gsid via phone+SMS) | ❌ no — can't rebuild the template | ✅ gets a gsid, but see below |

`.auth_replay.json` **is enough to refresh the x-token**, but it can **never**
renew the `gsid` — replaying reuses the frozen one, it doesn't mint a new session.
When the portal session finally expires, the **only** fix is a fresh mitmproxy
**capture** of a logged-in `wfjb.auth` request (→ `save-replay`). `cli login` can
obtain a new gsid, but it **cannot** rebuild a working template from it, because a
new gsid needs a new matching `sign` and `sign` is native-SDK-only. So `login`
persists a session (`.session.json`) but does not, by itself, restore refresh.

**When has the gsid died?** `cli refresh` prints `PORTAL_EXPIRED`,
`.token_state.json` records `"outcome": "PORTAL_EXPIRED"`, and the cron exits
non-zero. That's the signal to re-capture.

### How the client manages the token (`TokenProvider`)

All token handling goes through one file-locked `TokenProvider`
(`token_provider.py`), so state can never diverge across the CLI and the cron:

- **Lazy** — returns the cached JWT while it has >~10 min of life; only replays
  when it's near expiry, so a frequent cron doesn't burn the gateway's per-triple
  replay quota.
- **Explicit outcomes** — every attempt is classified `OK` / `THROTTLED` /
  `PORTAL_EXPIRED` / `BAD_TEMPLATE` / `NETWORK_ERROR` and recorded in
  `.token_state.json` (non-secret) for the operator/cron.
- **Atomic persistence** — a new token is written to **both** `.token.json` and
  the `WFJB_X_TOKEN` line in `.env` together, mode `0600`.
- **Safe retries** — idempotent reads (`whoami`/`dict`/`history`) refresh and
  retry **once** on an auth failure; `submit` refreshes *before* filing but
  **never** retries afterward (a blind retry could file a duplicate real report).
- **Throttling** — the gateway answers a too-fast replay with an empty `200`.
  Harmless while the cached token is still valid; if it's near expiry and can't be
  renewed, `refresh`/the cron exits non-zero so you know to re-capture.

Callers don't touch any of this — the CLI attaches a provider automatically. After
`refresh`, just run `whoami` / `submit` with no `--x-token`.

### Bootstrapping the first token / re-capturing

The device-side capture (Magisk DenyList, mitmproxy CA as a system cert, the app
proxied with **China-direct routing**, then open the wfjb H5 once) is documented
in [`../FINDINGS-jwztc.md`](../FINDINGS-jwztc.md) §3. Once you have a capture:

```bash
python -m police_report.cli save-replay /tmp/re/mitm.jsonl   # durable template
python -m police_report.cli refresh                          # mint the first token
python -m police_report.cli whoami                           # verify the account
```

Alternatives that skip the template (one-off use, token expires ~1 h):

- `--from-mitm /tmp/re/mitm.jsonl` — pull the freshest `x-token` + `cna` straight
  from a capture (`auth.get_token_from_mitm`).
- `--x-token …` / `WFJB_X_TOKEN=…` — paste an `X-Token` value from a live session.

> `refresh` and the mint call hit `mapi-jcss.police.hangzhou.gov.cn` directly, so
> the host must egress **China-local**, or the gov WAF returns unreachable IPs
> (`502` / `网络错误`). See FINDINGS §3.

### Auth-related files (all gitignored)

| file | holds | secret? |
|---|---|---|
| `.auth_replay.json` | the frozen `(gsid, sign, ts)` mint request — the durable refresh material | **yes** |
| `.token.json` | current `x_token` + `cna` + decoded `exp` | **yes** |
| `.env` | reporter identity (`WFJB_PHONE`/`WFJB_NAME`) + `WFJB_X_TOKEN`/`WFJB_CNA` | **yes** |
| `.session.json` | portal login result from `cli login` (gsid/tokens) | **yes** |
| `.token_state.json` | last refresh outcome + timestamps (for the operator/cron) | no |

`.env` is auto-loaded by the CLI. Note the authoritative refresh material is
`.auth_replay.json`, **not** the token in `.env` — that token is transient.

## Auto-fill from dashcam video (OCR)

DVR clips burn `YYYY年M月D日 HH:MM:SS` + full address into the bottom-left of
every frame. `extract_overlay.py` OCRs that overlay (tesseract + chi_sim) and
maps it straight onto `vioTime` / `currentAddress`.

```bash
brew install tesseract tesseract-lang     # ships the chi_sim model
pip install -r police_report/requirements.txt

# single sample, 3.5s into the clip
python -m police_report.extract_overlay clip.mov --at 3.5

# sample every 2s across the whole clip — use to find the violation moment
python -m police_report.extract_overlay clip.mov --scan --interval 2

# merge straight into a report json (only vioTime/currentAddress/areaName touched)
python -m police_report.extract_overlay clip.mov --at 3.5 \
    --fill report.example.json -o report.json
```

`vioAddress` (the free-text "之江路南向近宋城" description) and `longitude`/
`latitude` aren't in the overlay and still need filling by hand or a geocoder.

## CLI

```bash
python -m police_report.cli login                  # portal SSO login, saves session
python -m police_report.cli refresh                # re-mint x-token via replay
python -m police_report.cli whoami                 # account info
python -m police_report.cli dict wflx              # 19 violation-type codes
python -m police_report.cli dict areas             # district (areaCode) tree
python -m police_report.cli history                # your past reports
python -m police_report.cli upload clip.mp4        # -> file id
python -m police_report.cli submit report.json --dry-run    # print payload only
python -m police_report.cli submit report.json --confirm    # REAL submit
```

## Library

```python
from police_report import WfjbClient, ViolationReport

c = WfjbClient(x_token="eyJ...", cna_cookie="yD/...")
vid = c.upload_video("clip.mp4")

report = ViolationReport.from_coords(
    longitude=120.21687, latitude=30.21141,   # real-world lng/lat; swap handled
    vio_license_plate="浙AXXXXX", vio_type="bagdyzxd",
    vio_time="2026-07-03 19:05:00",
    area_code="330106", area_name="杭州市西湖区",
    vio_address="之江路南向近宋城",
    current_address="浙江省杭州市西湖区之江路XXXX号",
    phone="13800000000", name="张三",
    vio_describe="变道未打转向灯，后车紧急刹车避让",
    video_list=[vid],
)
print(c.submit_report(report))     # files the report
```

## Name → code mapping (vioType / area)

You can write **human Chinese names** in `report.json` instead of codes:

```json
"vioType":  "不按规定使用转向灯",   // or the code "bagdyzxd"
"areaName": "西湖区"                // or "杭州市西湖区"; areaCode filled in for you
```

At submit time `resolve.py` maps these to backend codes against the cached dict
(`dicts.json`, refreshed by `cli dict wflx` / `dict areas`). It is **strict** and
runs *before* any network call:

- a name that matches nothing → `resolve error (not submitted)` with the valid list,
- an `areaCode`+`areaName` that disagree → error (fix one),
- a valid code passes through unchanged.

So a typo can never be silently filed. Refresh `dicts.json` if the dicts change.

## Coordinate swap (important)

The app serializes coordinates **swapped**: real *longitude* goes into the JSON
`latitude` field and real *latitude* into `longitude`. `from_coords(longitude=,
latitude=)` takes real-world values and reproduces the swap so the backend
geocodes correctly. `report.example.json` also uses real-world `longitude`/
`latitude`; the CLI applies the swap. Don't "fix" it.

## Flow

1. **Auth** — obtain `x-token` (see above).
2. **Upload** — `upload_video()` → `POST /file/upload` (multipart field `file`) → file id.
3. **Submit** — `submit_report()` → `POST /vio/report/submit` with `videoList:[id]`.

Static dicts (`violation_types()`, `areas()`) rarely change — cache them.
Address string + coords come from 天地图 (`api.tianditu.gov.cn`); supply your own.

## Dict snapshot (vioType / areaCode)

Snapshot of the two dicts, cached in `dicts.json` and used by the name→code
resolver. Regenerate anytime with `cli dict wflx` / `cli dict areas`. You can type
the 名称 directly in `report.json` (see *Name → code mapping* above).

### vioType — 19 codes

| code | 名称 | | code | 名称 |
|---|---|---|---|---|
| `jdcchd` | 机动车闯红灯 | | `mtccj` | 摩托车闯禁 |
| `sxbd` | 实线变道 | | `kslhccj` | 快速路货车闯禁 |
| `qxjs` | 强行加塞 | | `gjczwt` | 公交车站违法停放 |
| `jdcnxxs` | 机动车逆向行驶 | | `bagdyzxd` | 不按规定使用转向灯 |
| `jdcwdxs` | 机动车未按导向车道行驶 | | `zylj` | 高速公路占用路肩/应急车道（试行） |
| `jdcwcjxs` | 机动车未在机动车道内通行 | | `nxdc` | 高速公路逆行/倒车（试行） |
| `jdcbrx` | 机动车斑马线不让行 | | `gspsdl` | 高速公路抛洒滴漏（试行） |
| `kcdsj` | 开车打手机 | | `dhcwrx` | 大货车右转弯未让行 |
| `jdcwt` | 机动车违法停车 | | `kccyzk` | 客车超员载客 |
| `qcgzzj` | 小型汽车改装炸街 | | | |

Some types are area-restricted (the dict `bz` field lists valid areaCodes; 高速 types only in `330100`+`GS00x`).

### areaCode — 杭州市 (330100) districts

| code | 区 | | code | 区 |
|---|---|---|---|---|
| `330102` | 上城区 | | `330111` | 富阳区 |
| `330105` | 拱墅区 | | `330112` | 临安区 |
| `330106` | 西湖区 | | `330122` | 桐庐县 |
| `330108` | 滨江区 | | `330127` | 淳安县 |
| `330197` | 西湖景区 | | `330182` | 建德市 |
| `330192` | 高架快速路 | | `330109` | 萧山区 |
| `330196` | 绕城高速 | | `330110` | 余杭区 |
| `330113` | 临平区 | | `330114` | 钱塘区 |
