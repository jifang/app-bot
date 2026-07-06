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

## CLI

```bash
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
