"""WfjbClient <-> TokenProvider integration.

Idempotent reads refresh-and-retry exactly once on failure; submit_report
refreshes BEFORE posting and never retries afterward (a blind retry could file a
duplicate real police report).
"""
from __future__ import annotations

import json

import pytest

from police_report.client import ViolationReport, WfjbClient, WfjbError
from police_report.token_provider import RefreshOutcome, RefreshResult, TokenUnavailable


class _Resp:
    def __init__(self, *, status=200, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeSession:
    """Stands in for requests.Session: queued responses per verb, records headers."""
    def __init__(self, gets=(), posts=()):
        self.headers = {}
        self._gets = list(gets)
        self._posts = list(posts)
        self.get_calls = 0
        self.post_calls = 0

    def get(self, url, **kw):
        self.get_calls += 1
        return self._gets.pop(0)

    def post(self, url, **kw):
        self.post_calls += 1
        return self._posts.pop(0)


class _FakeProvider:
    def __init__(self, token="NEWTOKEN", ok=True):
        self.token = token
        self.ok = ok
        self.refresh_calls = 0
        self.get_token_calls = 0

    def refresh(self, *, force=False):
        self.refresh_calls += 1
        if self.ok:
            return RefreshResult(RefreshOutcome.OK, self.token, "minted")
        return RefreshResult(RefreshOutcome.THROTTLED, None, "throttled")

    def get_token(self, *, min_ttl_s=None):
        self.get_token_calls += 1
        if not self.ok:
            raise TokenUnavailable(RefreshOutcome.THROTTLED, "throttled")
        return self.token


_OK = {"code": 200, "data": {"ok": 1}}
_AUTH_FAIL = {"code": 401, "msg": "token expired"}


def _client(session, provider):
    c = WfjbClient("OLDTOKEN", provider=provider)
    c.s = session
    return c


def test_read_refreshes_and_retries_once_on_failure():
    session = _FakeSession(gets=[_Resp(body=_AUTH_FAIL), _Resp(body=_OK)])
    provider = _FakeProvider("NEWTOKEN")
    c = _client(session, provider)

    assert c.report_history() == {"ok": 1}
    assert provider.refresh_calls == 1
    assert session.get_calls == 2                 # original + one retry
    assert session.headers["X-Token"] == "NEWTOKEN"   # token was swapped in


def test_read_does_not_retry_more_than_once():
    session = _FakeSession(gets=[_Resp(body=_AUTH_FAIL), _Resp(body=_AUTH_FAIL)])
    c = _client(session, _FakeProvider("NEWTOKEN"))
    with pytest.raises(WfjbError):
        c.report_history()
    assert session.get_calls == 2                 # not a third attempt


def test_read_propagates_when_refresh_cannot_help():
    session = _FakeSession(gets=[_Resp(body=_AUTH_FAIL)])
    c = _client(session, _FakeProvider(ok=False))   # refresh yields no token
    with pytest.raises(WfjbError):
        c.report_history()
    assert session.get_calls == 1                 # no retry when refresh gave nothing


def test_read_without_provider_raises_immediately():
    session = _FakeSession(gets=[_Resp(body=_AUTH_FAIL)])
    c = _client(session, None)
    with pytest.raises(WfjbError):
        c.report_history()
    assert session.get_calls == 1


def _report():
    return ViolationReport.from_coords(
        longitude=120.0, latitude=30.0, vio_license_plate="浙A00000", vio_type="sxbd",
        vio_time="2026-07-03 19:05:00", area_code="330106", area_name="杭州市西湖区",
        vio_address="x", current_address="y", phone="13800000000", name="张三",
        vio_describe="z", video_list=[1])


def test_submit_refreshes_before_posting_and_posts_once():
    session = _FakeSession(posts=[_Resp(body={"code": 200, "data": {"xlh": "R1"}})])
    provider = _FakeProvider("NEWTOKEN")
    c = _client(session, provider)

    assert c.submit_report(_report()) == {"xlh": "R1"}
    assert provider.get_token_calls == 1          # refreshed BEFORE submit
    assert session.headers["X-Token"] == "NEWTOKEN"
    assert session.post_calls == 1


def test_submit_never_retries_on_failure():
    session = _FakeSession(posts=[_Resp(body=_AUTH_FAIL)])
    provider = _FakeProvider("NEWTOKEN")
    c = _client(session, provider)

    with pytest.raises(WfjbError):
        c.submit_report(_report())
    assert session.post_calls == 1                # exactly once — no duplicate report
    assert provider.refresh_calls == 0           # and no post-response refresh/retry
