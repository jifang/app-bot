"""
CLI for the wfjb 违法举报 client.

Token: pass --x-token / --cna, or set WFJB_X_TOKEN / WFJB_CNA, or --from-mitm <jsonl>.

Examples:
  python -m police_report.cli whoami
  python -m police_report.cli dict wflx
  python -m police_report.cli dict areas
  python -m police_report.cli history
  python -m police_report.cli upload path/to/clip.mp4
  python -m police_report.cli submit report.json --dry-run
  python -m police_report.cli submit report.json --confirm     # files a REAL report
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

from .auth import (
    ENV_PATH,
    REPLAY_PATH,
    TOKEN_PATH,
    decode_exp,
    get_token_from_mitm,
    save_replay_template,
)
from .client import ViolationReport, WfjbClient, WfjbError
from .resolve import ResolveError, resolve_report


def _load_env(path: str = ENV_PATH) -> None:
    """Load KEY=VALUE lines from the gitignored .env into os.environ (no override)."""
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()


def _load_token_file() -> dict:
    if os.path.isfile(TOKEN_PATH):
        try:
            return json.load(open(TOKEN_PATH, encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _make_client(args) -> WfjbClient:
    cna = args.cna or os.environ.get("WFJB_CNA", "")
    # Explicit tokens are used verbatim and stand alone — no provider, so a
    # caller-supplied token is never silently overridden by the stored one.
    if args.from_mitm:
        got = get_token_from_mitm(args.from_mitm)
        if not got:
            sys.exit(f"no wfjb x-token found in {args.from_mitm}")
        token, cna = got
        return WfjbClient(token, cna)
    if args.x_token:
        return WfjbClient(args.x_token, cna)

    # Stored-token path: let the provider mint a fresh token if the cached one is
    # stale (needs a replay template); fall back to whatever the two stores hold.
    from .token_provider import TokenUnavailable, default_provider
    provider = default_provider()
    tf = _load_token_file()
    try:
        token = provider.get_token()
    except TokenUnavailable:
        token = _fresher(os.environ.get("WFJB_X_TOKEN"), tf.get("x_token"))
    if not cna:
        cna = tf.get("cna", "")
    if not token:
        sys.exit("no token: run `refresh`/`login`, pass --x-token, set WFJB_X_TOKEN, or use --from-mitm")
    return WfjbClient(token, cna, provider=provider)


def _fresher(a: str | None, b: str | None) -> str | None:
    """Return whichever token expires later (missing/undecodable exp sorts oldest)."""
    if not a:
        return b
    if not b:
        return a
    return a if (decode_exp(a) or 0) >= (decode_exp(b) or 0) else b


def _report_from_json(path: str) -> ViolationReport:
    d = json.load(open(path, encoding="utf-8"))
    # Pre-fill reporter identity from .env when the field is blank or a placeholder.
    for key, env in (("phone", "WFJB_PHONE"), ("name", "WFJB_NAME")):
        val = str(d.get(key, "")).strip()
        if (not val or val.startswith("FILL_ME")) and os.environ.get(env):
            d[key] = os.environ[env]
    # Map human-friendly names (vioType / areaName) -> backend codes. Strict:
    # a name that matches nothing raises ResolveError *before* any submit.
    d = resolve_report(d)
    # Accept real-world coords under lng/lat and apply the wire swap for the user.
    if "longitude" in d and "latitude" in d and "coords" not in d:
        lng, lat = d.pop("longitude"), d.pop("latitude")
    else:
        c = d.pop("coords", {})
        lng, lat = c.get("longitude"), c.get("latitude")
    return ViolationReport.from_coords(
        longitude=lng, latitude=lat,
        vio_license_plate=d["vioLicensePlate"], vio_type=d["vioType"],
        vio_time=d["vioTime"], area_code=d["areaCode"], area_name=d["areaName"],
        vio_address=d["vioAddress"], current_address=d["currentAddress"],
        phone=d["phone"], name=d["name"], vio_describe=d["vioDescribe"],
        video_list=d.get("videoList", []), pic_list=d.get("picList", []),
    )


def main(argv=None):
    p = argparse.ArgumentParser(prog="police_report")
    p.add_argument("--x-token"); p.add_argument("--cna")
    p.add_argument("--from-mitm", help="pull token from a mitmproxy jsonl capture")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami")
    sub.add_parser("refresh")           # re-mint x-token via replay, no app needed
    lg = sub.add_parser("login")        # portal SSO login -> persists session
    lg.add_argument("phone", nargs="?", help="defaults to WFJB_PHONE from .env")
    sr = sub.add_parser("save-replay")  # save the wfjb.auth replay template
    sr.add_argument("mitm_jsonl")
    d = sub.add_parser("dict"); d.add_argument("which", choices=["wflx", "areas"])
    sub.add_parser("history")
    u = sub.add_parser("upload"); u.add_argument("path")
    s = sub.add_parser("submit")
    s.add_argument("report_json")
    s.add_argument("--dry-run", action="store_true", help="print payload, do not send")
    s.add_argument("--confirm", action="store_true",
                   help="actually file the report (REAL police report)")

    args = p.parse_args(argv)

    try:
        if args.cmd == "login":
            from .portal_login import SESSION_PATH, login_full, request_otp, save_session
            phone = args.phone or os.environ.get("WFJB_PHONE")
            if not phone:
                sys.exit("login: pass a phone number or set WFJB_PHONE in .env")
            try:
                request_otp(phone)
                print(f"OTP sent to {phone[:3]}****{phone[-4:]}.")
                code = input("enter SMS code: ").strip()
                if not code:
                    sys.exit("login: no SMS code entered")
                res = login_full(phone, code)
            except (RuntimeError, requests.RequestException) as e:
                sys.exit(f"login error: {e}")
            save_session(res)
            print(f"logged in as {res.user_name or '(unknown)'} — session saved -> {SESSION_PATH}")
            print("NOTE: mgop `sign` is still required to rebuild the replay template "
                  "from this session; re-capture the wfjb.auth request once to enable `refresh`.")
            return

        if args.cmd == "save-replay":
            ok = save_replay_template(args.mitm_jsonl)
            print(f"replay template saved -> {REPLAY_PATH}" if ok
                  else f"no wfjb.auth request found in {args.mitm_jsonl}")
            return
        if args.cmd == "refresh":
            from .token_provider import RefreshOutcome, default_provider
            res = default_provider().refresh(force=True)   # persists atomically (0600)
            if res.outcome is not RefreshOutcome.OK or not res.token:
                sys.exit(f"refresh {res.outcome.value}: {res.detail}")
            import time
            token = res.token
            exp = decode_exp(token) or 0
            print(f"refreshed x-token -> {TOKEN_PATH} (+ .env)")
            print(f"  {token[:14]}...{token[-6:]} | exp {time.strftime('%H:%M:%S', time.localtime(exp))}"
                  f" ({(exp - time.time())/60:.0f} min)")
            return

        if args.cmd == "submit":
            try:
                report = _report_from_json(args.report_json)
            except ResolveError as e:
                sys.exit(f"resolve error (not submitted): {e}")
            payload = report.to_payload()
            if not args.confirm or args.dry_run:
                print("DRY RUN — not submitting. Payload:")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                if not args.confirm:
                    print("\nAdd --confirm to file this REAL report.")
                return
            client = _make_client(args)
            res = client.submit_report(report)
            print("SUBMITTED. 回执:", json.dumps(res, ensure_ascii=False))
            return

        client = _make_client(args)
        if args.cmd == "whoami":
            print(json.dumps(client.user_info(), ensure_ascii=False, indent=2))
        elif args.cmd == "dict":
            data = client.violation_types() if args.which == "wflx" else client.areas()
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.cmd == "history":
            print(json.dumps(client.report_history(), ensure_ascii=False, indent=2))
        elif args.cmd == "upload":
            vid = client.upload_video(args.path)
            print("uploaded, id =", vid)
    except WfjbError as e:
        sys.exit(f"wfjb error: {e}")


if __name__ == "__main__":
    main()
