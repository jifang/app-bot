"""TokenProvider — the single locked source of truth for the x-token.

Everything here is offline: the HTTP `poster` and the `clock` are injected, so
tests exercise real provider logic without touching the network.
"""
from __future__ import annotations

import base64
import json
import os
import stat

import pytest

from police_report import auth
from police_report.token_provider import (
    RefreshOutcome,
    TokenProvider,
    TokenUnavailable,
)


# --- helpers ---------------------------------------------------------------

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


class _Poster:
    """Records calls and returns queued responses (or raises queued exceptions)."""
    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    def __call__(self, url, **kw):
        self.calls += 1
        r = self._results.pop(0) if self._results else _Resp(200, b"")
        if isinstance(r, Exception):
            raise r
        return r


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _make(tmp_path, *, poster, clock, cached_exp=None, template=True, margin_s=600):
    replay = tmp_path / ".auth_replay.json"
    if template:
        replay.write_text(json.dumps({
            "url": auth.MGOP_URL,
            "headers": {"api": "mgop.trustway.wfjb.auth", "sign": "s", "sid": "g"},
            "body_hex": "",
        }))
    token_path = tmp_path / ".token.json"
    if cached_exp is not None:
        token_path.write_text(json.dumps(
            {"x_token": _jwt(cached_exp), "cna": "C", "exp": cached_exp}))
    env = tmp_path / ".env"
    env.write_text("WFJB_X_TOKEN=OLD\n")
    return TokenProvider(
        token_path=str(token_path), env_path=str(env), replay_path=str(replay),
        lock_path=str(tmp_path / ".token.lock"), state_path=str(tmp_path / ".token_state.json"),
        margin_s=margin_s, poster=poster, clock=clock,
    )


# --- classification --------------------------------------------------------

def test_refresh_ok_persists_and_reports_ok(tmp_path):
    clock = _Clock()
    new = _jwt(int(clock.now) + 3600)
    p = _make(tmp_path, poster=_Poster(_Resp(200, body={"code": 200, "data": {"token": new}})),
              clock=clock)
    res = p.refresh(force=True)
    assert res.outcome is RefreshOutcome.OK
    assert res.token == new
    # persisted to both stores, 0600
    assert json.load(open(p.token_path))["x_token"] == new
    assert f"WFJB_X_TOKEN={new}" in open(p.env_path).read()
    assert stat.S_IMODE(os.stat(p.token_path).st_mode) == 0o600


def test_refresh_empty_200_without_gateway_status_is_gateway_error(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(200, b"")), clock=_Clock())
    assert p.refresh(force=True).outcome is RefreshOutcome.GATEWAY_ERROR


def test_refresh_rs_7003_is_signature_expired(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(
        200, b"", headers={
            "rs": "7003",
            "memo": "%E9%AA%8C%E7%AD%BE%E6%97%B6%E9%97%B4%E6%88%B3%E6%A0%A1%E9%AA%8C%E5%A4%B1%E8%B4%A5",
        })), clock=_Clock())
    res = p.refresh(force=True)
    assert res.outcome is RefreshOutcome.SIGNATURE_EXPIRED
    assert "rs=7003" in res.detail
    assert "验签时间戳校验失败" in res.detail


def test_refresh_rs_4001_is_gateway_error_not_throttle(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(
        200, b"", headers={"rs": "4001", "memo": "%E6%9C%8D%E5%8A%A1%E8%AF%B7%E6%B1%82%E8%B6%85%E6%97%B6"})),
        clock=_Clock())
    res = p.refresh(force=True)
    assert res.outcome is RefreshOutcome.GATEWAY_ERROR
    assert "服务请求超时" in res.detail


def test_refresh_http_429_is_throttled(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(429, b"")), clock=_Clock())
    assert p.refresh(force=True).outcome is RefreshOutcome.THROTTLED


def test_refresh_non_200_is_portal_expired(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(401, b"nope")), clock=_Clock())
    assert p.refresh(force=True).outcome is RefreshOutcome.PORTAL_EXPIRED


def test_refresh_code_not_200_is_portal_expired(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(200, body={"code": 401, "msg": "expired"})),
              clock=_Clock())
    assert p.refresh(force=True).outcome is RefreshOutcome.PORTAL_EXPIRED


def test_refresh_network_error(tmp_path):
    import requests
    p = _make(tmp_path, poster=_Poster(requests.ConnectionError("boom")), clock=_Clock())
    assert p.refresh(force=True).outcome is RefreshOutcome.NETWORK_ERROR


def test_refresh_missing_template_is_bad_template(tmp_path):
    p = _make(tmp_path, poster=_Poster(), clock=_Clock(), template=False)
    assert p.refresh(force=True).outcome is RefreshOutcome.BAD_TEMPLATE


def test_refresh_foreign_host_is_bad_template(tmp_path):
    p = _make(tmp_path, poster=_Poster(_Resp(200, b"")), clock=_Clock())
    (tmp_path / ".auth_replay.json").write_text(json.dumps({
        "url": "https://evil.example.com/app/mgop", "headers": {}, "body_hex": ""}))
    res = p.refresh(force=True)
    assert res.outcome is RefreshOutcome.BAD_TEMPLATE
    # must NOT have sent captured headers anywhere
    assert p.poster.calls == 0


# --- lazy refresh / get_token ---------------------------------------------

def test_refresh_lazy_returns_cached_without_network(tmp_path):
    clock = _Clock()
    poster = _Poster()   # would raise-empty if called; assert it is not
    p = _make(tmp_path, poster=poster, clock=clock, cached_exp=int(clock.now) + 3600)
    res = p.refresh(force=False)
    assert res.outcome is RefreshOutcome.OK
    assert poster.calls == 0


def test_get_token_returns_cached_when_fresh(tmp_path):
    clock = _Clock()
    poster = _Poster()
    p = _make(tmp_path, poster=poster, clock=clock, cached_exp=int(clock.now) + 3600)
    assert p.get_token() == _jwt(int(clock.now) + 3600)
    assert poster.calls == 0


def test_get_token_refreshes_when_stale(tmp_path):
    clock = _Clock()
    new = _jwt(int(clock.now) + 3600)
    poster = _Poster(_Resp(200, body={"code": 200, "data": {"token": new}}))
    p = _make(tmp_path, poster=poster, clock=clock, cached_exp=int(clock.now) + 60)  # inside margin
    assert p.get_token() == new
    assert poster.calls == 1


def test_get_token_returns_still_valid_cached_on_throttle(tmp_path):
    clock = _Clock()
    cached = _jwt(int(clock.now) + 120)   # near expiry but still valid
    poster = _Poster(_Resp(429, b""))     # explicit rate limit
    p = _make(tmp_path, poster=poster, clock=clock, cached_exp=int(clock.now) + 120)
    assert p.get_token() == cached        # degrade gracefully, don't raise yet


def test_get_token_raises_when_no_usable_token(tmp_path):
    clock = _Clock()
    poster = _Poster(_Resp(200, b"", headers={"rs": "7003"}))
    p = _make(tmp_path, poster=poster, clock=clock, cached_exp=int(clock.now) - 10)  # expired
    with pytest.raises(TokenUnavailable) as e:
        p.get_token()
    assert e.value.outcome is RefreshOutcome.SIGNATURE_EXPIRED


# --- structured state ------------------------------------------------------

def test_state_file_written_every_attempt(tmp_path):
    clock = _Clock()
    p = _make(tmp_path, poster=_Poster(_Resp(200, b"", headers={"rs": "7003"})),
              clock=_Clock())
    p.refresh(force=True)
    state = json.load(open(p.state_path))
    assert state["outcome"] == "SIGNATURE_EXPIRED"
    assert "attempt_ts" in state
