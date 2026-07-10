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

## Getting a token

The `x-token` is minted by the app's mgop gateway (`mgop.trustway.wfjb.auth`). A
from-scratch login is **not** implemented because it needs (1) the portal SSO
session `gsid` and (2) a `sign` computed by the mgop native SDK (not reversed).
See `auth.py` for the exact request shape.

For now, get the token from a logged-in session:

- **From a mitmproxy capture** (this repo's capture writes `/tmp/re/mitm.jsonl`):
  ```bash
  python -m police_report.cli --from-mitm /tmp/re/mitm.jsonl whoami
  ```
  `auth.get_token_from_mitm()` pulls the freshest `x-token` + `cna` for you.
- **Manually**: copy the `X-Token` header value from any wfjb request and pass
  `--x-token …` or set `WFJB_X_TOKEN`. Tokens expire (~1h; JWT `exp`).

### Refreshing without a re-login (replay)

The `mgop.trustway.wfjb.auth` call is **replayable** — the backend does not
re-check the captured `sign`/`ts` freshness, so re-POSTing it re-mints a fresh
`x-token` with no app interaction, as long as the portal session is still valid.

```bash
# once, from a live capture: save the replay template (.auth_replay.json, gitignored)
python -m police_report.cli save-replay /tmp/re/mitm.jsonl

# thereafter, any time the token is stale — no app, no proxy:
python -m police_report.cli refresh          # writes police_report/.token.json
```

`refresh` writes **both** `police_report/.token.json` and the `WFJB_X_TOKEN` line
in `.env` through one atomic (mode `0600`) persistence step, so the two stores
never diverge. Other CLI commands pick up the token automatically (preferring
whichever store holds the later-expiring JWT), so after `refresh` you can just
run `whoami` / `submit` without passing `--x-token`.
If `refresh` ever reports the session expired, re-establish it:

```bash
python -m police_report.cli login          # portal SSO (uses WFJB_PHONE), saves session
```

then re-capture the `wfjb.auth` request once (see above) so `refresh` works again.
(`login` alone can't mint an `x-token`: the mgop `sign` from the app's native SDK
is still required to rebuild the replay template — see `auth.py`.)
Notes:
- `refresh` hits `mapi-jcss.police.hangzhou.gov.cn` directly, so the host must be
  able to reach it (China-direct routing — see FINDINGS §3).
- All refresh goes through one `TokenProvider` (`token_provider.py`) under a file
  lock. It is **lazy** — it replays only when the cached JWT is within ~10 min of
  expiry — and classifies each attempt explicitly as `OK` / `THROTTLED` /
  `PORTAL_EXPIRED` / `BAD_TEMPLATE` / `NETWORK_ERROR`, recording the result in
  `police_report/.token_state.json` (gitignored) for the operator/cron.
- The gateway **throttles rapid identical replays** with an empty `200`. If the
  cached token is still valid, that's harmless (retry later); if it's near expiry
  and can't be renewed, `refresh`/the cron exits non-zero so you re-capture.
- Reads (`whoami`, `dict`, `history`) auto-refresh and retry once on an auth
  failure. `submit` refreshes *before* filing but never retries afterward — a
  blind retry could file a duplicate real report.

### `.env` (gitignored)

`police_report/.env` holds reporter identity + the current token — never committed:

```
WFJB_PHONE=...      # pre-filled into report.json when phone/name are blank/FILL_ME
WFJB_NAME=...
WFJB_X_TOKEN=...    # current token (transient); refresh with `cli refresh`
WFJB_CNA=...
```

The CLI auto-loads it. Durable refresh material lives in `.auth_replay.json`
(also gitignored) — that, not the token in `.env`, is what `refresh` replays.

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
