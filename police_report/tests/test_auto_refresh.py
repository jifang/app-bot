"""auto_refresh is now a thin wrapper over TokenProvider.

Contract (exit code drives cron mail):
  * OK, or a transient failure while the cached token is still valid  -> 0
  * unrecoverable (portal dead / bad template), or nothing usable left -> 1
"""
from __future__ import annotations

from police_report import auto_refresh
from police_report.token_provider import RefreshOutcome, RefreshResult


class _FakeProvider:
    def __init__(self, outcome, *, ttl):
        self._res = RefreshResult(outcome, "T" if outcome is RefreshOutcome.OK else None, "d")
        self._ttl = ttl
        self.refreshed = 0

    def refresh(self, *, force=False):
        self.refreshed += 1
        return self._res

    def cached_ttl(self):
        return self._ttl


def test_ok_exits_zero():
    p = _FakeProvider(RefreshOutcome.OK, ttl=3600)
    assert auto_refresh.main(provider=p) == 0
    assert p.refreshed == 1


def test_throttled_but_token_still_valid_exits_zero():
    p = _FakeProvider(RefreshOutcome.THROTTLED, ttl=300)   # transient, token still good
    assert auto_refresh.main(provider=p) == 0


def test_throttled_and_token_expired_exits_one():
    p = _FakeProvider(RefreshOutcome.THROTTLED, ttl=-5)    # can't renew, nothing left
    assert auto_refresh.main(provider=p) == 1


def test_network_error_with_valid_token_exits_zero():
    p = _FakeProvider(RefreshOutcome.NETWORK_ERROR, ttl=200)
    assert auto_refresh.main(provider=p) == 0


def test_portal_expired_exits_one_even_if_token_valid():
    p = _FakeProvider(RefreshOutcome.PORTAL_EXPIRED, ttl=300)  # session dead, unrecoverable
    assert auto_refresh.main(provider=p) == 1


def test_bad_template_exits_one():
    p = _FakeProvider(RefreshOutcome.BAD_TEMPLATE, ttl=None)
    assert auto_refresh.main(provider=p) == 1
