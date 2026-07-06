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

from .auth import get_token_from_mitm
from .client import ViolationReport, WfjbClient, WfjbError


def _make_client(args) -> WfjbClient:
    token = args.x_token or os.environ.get("WFJB_X_TOKEN")
    cna = args.cna or os.environ.get("WFJB_CNA", "")
    if args.from_mitm:
        got = get_token_from_mitm(args.from_mitm)
        if not got:
            sys.exit(f"no wfjb x-token found in {args.from_mitm}")
        token, cna = got
    if not token:
        sys.exit("no token: pass --x-token, set WFJB_X_TOKEN, or use --from-mitm")
    return WfjbClient(token, cna)


def _report_from_json(path: str) -> ViolationReport:
    d = json.load(open(path, encoding="utf-8"))
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
        if args.cmd == "submit":
            report = _report_from_json(args.report_json)
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
