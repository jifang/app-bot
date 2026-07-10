"""The refreshed token must never be shadowed by a stale env token."""
from __future__ import annotations

import base64
import json

from police_report import cli


def _jwt(exp: int) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'HS256'})}.{b64({'exp': exp})}.sig"


def test_fresher_prefers_later_exp():
    old, new = _jwt(1000), _jwt(2000)
    assert cli._fresher(old, new) == new
    assert cli._fresher(new, old) == new


def test_fresher_handles_missing():
    tok = _jwt(1000)
    assert cli._fresher(None, tok) == tok
    assert cli._fresher(tok, None) == tok
    assert cli._fresher(None, None) is None


class _Args:
    x_token = None
    cna = None
    from_mitm = None


def _capture_client(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "WfjbClient",
                        lambda token, cna="", provider=None: captured.update(
                            token=token, cna=cna, provider=provider))
    return captured


def test_make_client_uses_provider_token(monkeypatch):
    """When the provider can supply a (fresh) token, that token is used and the
    provider is attached so reads/submit can refresh."""
    class _FakeProvider:
        def get_token(self, *, min_ttl_s=None):
            return _jwt(9999999999)

    prov = _FakeProvider()
    monkeypatch.setattr("police_report.token_provider.default_provider", lambda: prov)
    captured = _capture_client(monkeypatch)
    cli._make_client(_Args())
    assert captured["token"] == _jwt(9999999999)
    assert captured["provider"] is prov


def test_make_client_falls_back_to_fresher_when_provider_unavailable(monkeypatch):
    """If the provider can't mint (no replay template), fall back to whichever
    stored token expires later — never the stale one just because it's in env."""
    from police_report.token_provider import RefreshOutcome, TokenUnavailable

    stale_env = _jwt(1000)
    fresh_file = _jwt(9999999999)

    class _DeadProvider:
        def get_token(self, *, min_ttl_s=None):
            raise TokenUnavailable(RefreshOutcome.BAD_TEMPLATE, "no template")

    monkeypatch.setattr("police_report.token_provider.default_provider",
                        lambda: _DeadProvider())
    monkeypatch.setenv("WFJB_X_TOKEN", stale_env)
    monkeypatch.setattr(cli, "_load_token_file",
                        lambda: {"x_token": fresh_file, "cna": "C"})
    captured = _capture_client(monkeypatch)
    cli._make_client(_Args())
    assert captured["token"] == fresh_file   # not the stale env token
