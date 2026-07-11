"""Tests for the token store, endpoint validation, and refresh classification.

These cover the pure logic the auth findings were about — no network calls.
"""
from __future__ import annotations

import base64
import json
import os
import stat

import pytest

from police_report import auth


def _jwt(exp: int | None) -> str:
    """Build a syntactically valid JWT whose payload carries `exp` (or none)."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    payload = {"sub": "x"} if exp is None else {"sub": "x", "exp": exp}
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.sig"


# ---- decode_exp -----------------------------------------------------------

def test_decode_exp_reads_exp():
    assert auth.decode_exp(_jwt(1893456000)) == 1893456000


def test_decode_exp_none_when_missing():
    assert auth.decode_exp(_jwt(None)) is None


def test_decode_exp_none_on_garbage():
    assert auth.decode_exp("not-a-jwt") is None


# ---- save_token: sole store is .token.json --------------------------------

def test_save_token_writes_token_json_0600_not_env(tmp_path):
    tok = _jwt(1893456000)
    token_path = str(tmp_path / ".token.json")
    env_path = str(tmp_path / ".env")
    with open(env_path, "w") as f:
        f.write("WFJB_PHONE=13800000000\nWFJB_X_TOKEN=OLD\n")

    auth.save_token(tok, "CNA1", token_path=token_path, env_path=env_path)

    data = json.load(open(token_path))
    assert data == {"x_token": tok, "cna": "CNA1", "exp": 1893456000}
    assert open(env_path).read() == "WFJB_PHONE=13800000000\nWFJB_X_TOKEN=OLD\n"
    assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600


def test_save_token_preserves_existing_cna(tmp_path):
    token_path = str(tmp_path / ".token.json")
    auth.save_token(_jwt(1), "KEEPME", token_path=token_path, env_path=str(tmp_path / ".env"))
    auth.save_token(_jwt(2), token_path=token_path, env_path=str(tmp_path / ".env"))
    assert json.load(open(token_path))["cna"] == "KEEPME"


# ---- validate_mgop_url ----------------------------------------------------

def test_validate_mgop_url_accepts_exact_endpoint():
    auth.validate_mgop_url(auth.MGOP_URL)   # no raise


@pytest.mark.parametrize("bad", [
    "http://mapi-jcss.police.hangzhou.gov.cn/app/mgop",     # not https
    "https://evil.example.com/app/mgop",                     # wrong host
    "https://mapi-jcss.police.hangzhou.gov.cn/steal",        # wrong path
    "https://mapi-jcss.police.hangzhou.gov.cn.evil.com/app/mgop",
])
def test_validate_mgop_url_rejects(bad):
    with pytest.raises(RuntimeError):
        auth.validate_mgop_url(bad)


# ---- save_replay_template refuses a poisoned destination ------------------

def test_save_replay_template_rejects_foreign_host(tmp_path):
    capture = tmp_path / "mitm.jsonl"
    capture.write_text(json.dumps({
        "host": "evil.example.com",
        "path": "/app/mgop",
        "req_headers": {"api": "mgop.trustway.wfjb.auth", "sign": "abc"},
        "body_hex": "",
    }) + "\n")
    with pytest.raises(RuntimeError):
        auth.save_replay_template(str(capture), out_path=str(tmp_path / ".auth_replay.json"))


def test_save_replay_template_accepts_body_hex_and_legacy_b64_key(tmp_path):
    capture = tmp_path / "mitm.jsonl"
    # Mix: one legacy req_body_b64, one body_hex — last wfjb.auth wins.
    capture.write_text(
        json.dumps({
            "host": auth.MGOP_HOST, "path": auth.MGOP_PATH,
            "req_headers": {"api": "mgop.trustway.wfjb.auth", "sign": "old"},
            "req_body_b64": "7b7d",
        }) + "\n" + json.dumps({
            "host": auth.MGOP_HOST, "path": auth.MGOP_PATH,
            "req_headers": {"api": "mgop.trustway.wfjb.auth", "sign": "new"},
            "body_hex": "7b2261223a317d",
        }) + "\n")
    out = str(tmp_path / ".auth_replay.json")
    assert auth.save_replay_template(str(capture), out_path=out) is True
    tpl = json.load(open(out))
    assert tpl["url"] == auth.MGOP_URL
    assert tpl["body_hex"] == "7b2261223a317d"
    assert tpl["headers"]["sign"] == "new"
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


# ---- refresh_token guards -------------------------------------------------

def test_refresh_token_missing_template_raises(tmp_path):
    with pytest.raises(RuntimeError):
        auth.refresh_token(replay_path=str(tmp_path / "nope.json"))
