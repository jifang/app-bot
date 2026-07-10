"""
auto_refresh.py — cron-friendly mgop wfjb auth replay.

Refreshes the wfjb x-token by replaying the captured mgop wfjb.auth request.
Handles three outcomes:
  - 200 + JSON body containing `token`  → success, persists new token
  - 200 + empty body                    → throttled (gateway per-replay quota
                                          exhausted); back off, do nothing
  - non-200 or 4xx/5xx                  → gsid / sign dead; mark stale + exit
                                          non-zero (cron mail can pick this up)

The replay is the same triple (gsid, sign, ts) as the .auth_replay.json template
because the mPaaS gateway does not re-check freshness (per FINDINGS-jwztc).
When the gateway throttles the triple, the cron entry is harmless until the
operator re-captures by running a fresh login + opening the wfjb H5 once.

After a successful refresh, .env and .token.json are updated in place; the new
JWT is decoded to extract `exp` and stamped for `--exp` printing in `whoami`.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
REPLAY_PATH = ROOT / ".auth_replay.json"
TOKEN_PATH = ROOT / ".token.json"
ENV_PATH = ROOT / ".env"
DEAD_FLAG = ROOT / ".token_expired"


def _decode_exp(token: str) -> int:
    pad = lambda s: s + "=" * (-len(s) % 4)
    return json.loads(base64.urlsafe_b64decode(pad(token.split(".")[1]))).get("exp", 0)


def _write_token(new_token: str) -> None:
    """Persist the new x-token in BOTH .token.json and .env (the env var is
    what the CLI picks up at startup)."""
    exp = _decode_exp(new_token)
    cna = ""
    if TOKEN_PATH.is_file():
        cna = json.load(open(TOKEN_PATH)).get("cna", "")
    json.dump({"x_token": new_token, "cna": cna, "exp": exp},
              open(TOKEN_PATH, "w"), indent=2)
    if ENV_PATH.is_file():
        env = ENV_PATH.read_text()
        env = re.sub(r"^WFJB_X_TOKEN=.*$", f"WFJB_X_TOKEN={new_token}", env, flags=re.M)
        ENV_PATH.write_text(env)


def _clear_dead_flag() -> None:
    if DEAD_FLAG.is_file():
        DEAD_FLAG.unlink()


def _set_dead_flag(reason: str) -> None:
    DEAD_FLAG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {reason}\n")


def main() -> int:
    if not REPLAY_PATH.is_file():
        print("auto_refresh: no .auth_replay.json — run `cli login` then re-capture once", file=sys.stderr)
        _set_dead_flag("no replay template")
        return 2

    tpl = json.load(open(REPLAY_PATH))
    url = tpl["url"]
    hdrs = {k: v for k, v in tpl["headers"].items()
            if k.lower() not in ("content-length", "host", "accept-encoding")}
    body = bytes.fromhex(tpl["body_hex"])

    try:
        r = requests.post(url, headers=hdrs, data=body, timeout=20)
    except requests.RequestException as e:
        print(f"auto_refresh: network error: {e}", file=sys.stderr)
        return 1

    if r.status_code != 200:
        # gsid is dead (401) or gateway mismatch (403/4xx). The CLI will tell the
        # user this on the next manual `cli whoami`. Mark the flag + return
        # non-zero so cron can mail.
        msg = f"http {r.status_code} (gsid likely dead)"
        print(f"auto_refresh: {msg}", file=sys.stderr)
        _set_dead_flag(msg)
        return 1

    if not r.content:
        # Throttled — the same (gsid, sign, ts) replayed too many times. Not
        # fatal; cron will try again in 15 min. Don't clear the dead flag.
        print("auto_refresh: throttled (empty 200). Replay quota exhausted for this triple; "
              "run login + open wfjb H5 to capture a new (gsid, sign, ts) when convenient.")
        return 0

    j = r.json()
    if j.get("code") != 200 or "data" not in j or "token" not in j["data"]:
        msg = f"unexpected response: {r.text[:200]}"
        print(f"auto_refresh: {msg}", file=sys.stderr)
        _set_dead_flag(msg)
        return 1

    new_token = j["data"]["token"]
    _write_token(new_token)
    _clear_dead_flag()
    exp_human = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_decode_exp(new_token)))
    print(f"auto_refresh: OK new x-token valid until {exp_human}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
