"""PortalLoginFlow — shared Session/pubkey across OTP + complete (mocked)."""
from __future__ import annotations

import json

import pytest

from police_report import portal_login


class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return _Resp({"success": True, "data": "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gunVTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxKLxcOQmk" + "A" * 200 + "QIDAQAB"})

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw.get("data")))
        if url.endswith("/uc/verify/sendCodeEncrypt"):
            return _Resp({"success": True})
        if url.endswith("/uc/login"):
            return _Resp({"success": True, "data": {
                "gsid": "main-gsid-aaaaaaaa",
                "accessToken": "access-token-bbbbbbbb",
                "refreshToken": "refresh-token-cccccccc",
                "userName": "测试",
            }})
        if url.endswith("/app_api/user/getAuthCode"):
            return _Resp({"success": True, "data": "UC_CODE_dddddddd"})
        if url.endswith("/portal/person/authCodeLogin"):
            return _Resp({"success": True, "data": "person-gsid-eeeeeeee"})
        raise AssertionError(f"unexpected POST {url}")


def test_flow_reuses_session_and_pubkey(monkeypatch):
    enc_calls = []

    def fake_encrypt(pubkey, plaintext):
        enc_calls.append((pubkey[:20], plaintext))
        return "ENC"

    monkeypatch.setattr(portal_login, "_rsa_encrypt", fake_encrypt)
    session = _FakeSession()
    flow = portal_login.PortalLoginFlow("13800000000", session=session)

    pub1 = flow.request_otp()
    assert flow.pubkey == pub1
    assert any(c[0] == "GET" for c in session.calls)
    assert any("/uc/verify/sendCodeEncrypt" in c[1] for c in session.calls if c[0] == "POST")

    gets_before = sum(1 for c in session.calls if c[0] == "GET")
    result = flow.complete("123456")
    gets_after = sum(1 for c in session.calls if c[0] == "GET")
    # complete must NOT re-fetch pubkey when flow already has one
    assert gets_after == gets_before
    assert result.user_name == "测试"
    assert result.main_gsid == "main-gsid-aaaaaaaa"
    assert result.person_gsid == "person-gsid-eeeeeeee"
    # same pubkey used for encrypt during login
    assert all(c[0] == pub1[:20] for c in enc_calls)


def test_redact_login_result_hides_secrets():
    r = portal_login.LoginResult(
        main_gsid="abcdefghijklmnop",
        person_gsid="1234567890abcdef",
        user_name="张三",
        access_token="tokentokentoken",
        refresh_token="refreshrefresh",
        auth_code="UC_CODE_XYZ",
    )
    d = portal_login.redact_login_result(r)
    assert d["user_name"] == "张三"
    assert "abcdefghijklmnop" not in json.dumps(d)
    assert d["main_gsid"].startswith("abcd")
    assert "…" in d["main_gsid"]
