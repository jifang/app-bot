"""MgopSigner + TokenMinter + TokenProvider mint fallback."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from police_report import auth
from police_report.minter import (
    AndroidCaptureMinter,
    MintError,
    SignerBackedMinter,
)
from police_report.signer import BridgeMgopSigner, FakeMgopSigner, SignerError
from police_report.token_provider import RefreshOutcome, TokenProvider


def _jwt(exp: int) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'HS256'})}.{b64({'exp': exp})}.sig"


class _Resp:
    def __init__(self, status=200, content=b"", body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        if body is not None:
            content = json.dumps(body).encode()
        self.content = content
        self.text = content.decode() if content else ""

    def json(self):
        return json.loads(self.content)


# ---- signer ---------------------------------------------------------------

def test_fake_signer_records_call():
    s = FakeMgopSigner("SIG")
    assert s.sign(api="a", appid="b", gsid="g", ts=1, body=b"{}") == "SIG"
    assert s.calls[0]["gsid"] == "g"


def test_bridge_signer_happy_path():
    def poster(url, json=None, timeout=None):
        assert url.endswith("/sign")
        assert json["gsid"] == "G"
        return SimpleNamespace(status_code=200, text='{"sign":"ABC"}',
                               json=lambda: {"sign": "ABC"})

    s = BridgeMgopSigner(base_url="http://bridge", poster=poster)
    assert s.sign(api="a", appid="b", gsid="G", ts=9, body=b"x") == "ABC"


def test_bridge_signer_unreachable():
    import requests

    def poster(*a, **k):
        raise requests.ConnectionError("down")

    s = BridgeMgopSigner(poster=poster)
    with pytest.raises(SignerError):
        s.sign(api="a", appid="b", gsid="g", ts=1, body=b"")


# ---- SignerBackedMinter ---------------------------------------------------

def test_signer_backed_minter_mints(tmp_path):
    signer = FakeMgopSigner("S")
    sess = tmp_path / ".session.json"
    sess.write_text(json.dumps({"person_gsid": "PGSID", "main_gsid": "M"}))

    def auth_fn(**kw):
        assert kw["gsid"] == "PGSID"
        assert kw["sign"] == "S"
        return {"token": "JWT"}

    m = SignerBackedMinter(signer=signer, session_path=str(sess),
                           auth_fn=auth_fn, clock=lambda: 1000.0)
    assert m.mint() == "JWT"
    assert signer.calls[0]["ts"] == 1_000_000


def test_signer_backed_minter_needs_gsid(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "REPLAY_PATH", str(tmp_path / "no-replay.json"))
    m = SignerBackedMinter(signer=FakeMgopSigner(),
                           session_path=str(tmp_path / "missing.json"),
                           auth_fn=lambda **k: {})
    with pytest.raises(MintError, match="no gsid"):
        m.mint()


# ---- AndroidCaptureMinter (orchestrator, mocked) --------------------------

def test_android_capture_minter_extracts_token(tmp_path):
    import os
    capture = tmp_path / "mint.jsonl"
    capture.write_text("")

    def runner(cmd, **kw):
        joined = " ".join(cmd)
        if joined.endswith("devices") or "devices" in cmd:
            return SimpleNamespace(returncode=0, stdout="List\nemulator-5554\tdevice\n")
        if "getprop" in cmd:
            return SimpleNamespace(returncode=0, stdout="1\n")
        if "pm" in cmd and "path" in cmd:
            return SimpleNamespace(returncode=0, stdout="package:/data/app/base.apk\n")
        return SimpleNamespace(returncode=0, stdout="")

    ticks = {"t": 0}

    def clock():
        return ticks["t"]

    def sleeper(_):
        ticks["t"] += 1
        capture.write_text('{"host":"x"}\n')

    m = AndroidCaptureMinter(
        capture_path=str(capture),
        replay_path=str(tmp_path / ".auth_replay.json"),
        runner=runner,
        sleeper=sleeper,
        clock=clock,
        wait_s=10,
        save_replay=lambda path, out_path=None: os.path.getsize(path) > 0,
        get_token_from_mitm=lambda path: ("FRESH_JWT", "cna"),
    )
    assert m.mint() == "FRESH_JWT"


# ---- TokenProvider mint fallback ------------------------------------------

def test_provider_mints_after_signature_expired(tmp_path):
    replay = tmp_path / ".auth_replay.json"
    replay.write_text(json.dumps({
        "url": auth.MGOP_URL,
        "headers": {"api": "mgop.trustway.wfjb.auth", "sid": "g", "ts": "1"},
        "body_hex": "",
    }))
    token_path = tmp_path / ".token.json"
    env = tmp_path / ".env"
    env.write_text("")

    new = _jwt(2_000_000)
    poster_calls = {"n": 0}

    def poster(*a, **k):
        poster_calls["n"] += 1
        return _Resp(200, b"", headers={"rs": "7003"})

    p = TokenProvider(
        token_path=str(token_path), env_path=str(env), replay_path=str(replay),
        lock_path=str(tmp_path / ".lock"), state_path=str(tmp_path / ".state"),
        poster=poster, clock=lambda: 1_000_000.0, minter=lambda: new,
    )
    res = p.refresh(force=True)
    assert res.outcome is RefreshOutcome.OK
    assert res.token == new
    assert json.load(open(token_path))["x_token"] == new
    assert poster_calls["n"] == 1


def test_provider_mint_failure_surfaces_mint_failed(tmp_path):
    p = TokenProvider(
        token_path=str(tmp_path / ".token.json"),
        env_path=str(tmp_path / ".env"),
        replay_path=str(tmp_path / "missing.json"),
        lock_path=str(tmp_path / ".lock"),
        state_path=str(tmp_path / ".state"),
        poster=lambda *a, **k: _Resp(200, b""),
        clock=lambda: 1.0,
        minter=lambda: (_ for _ in ()).throw(RuntimeError("no emu")),
    )
    res = p.refresh(force=True)
    assert res.outcome is RefreshOutcome.MINT_FAILED
    assert "no emu" in res.detail


def test_offline_signer_matches_captured_replay():
    from police_report.signer import OfflineMgopSigner, DEFAULT_BODY
    # From .auth_replay.json (2026-07-12 capture) — sign uses b64(body).
    sid = "f0e8826b1d6242b3b65502aa07b91eaa-gsid-"
    ts = 1783841585527
    want = "e81e0a306731622652a58cb66119b4b9"
    got = OfflineMgopSigner().sign(
        api="mgop.trustway.wfjb.auth",
        appid="421424f5fe7b444782907d18955b8e1a",
        gsid=sid, ts=ts, body=DEFAULT_BODY)
    assert got == want
