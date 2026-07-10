"""
Login flow for the portal SSO (`_as=person` path), replayed from the captured flow.

Captured sequence (see /tmp/re/mitm.jsonl session 1):
  1. GET  /uc/login/publicKey              → RSA pubkey
  2. POST /uc/verify/sendCodeEncrypt       → SMS sent (action=smsLogin)
  3. POST /uc/login                        → main gsid (a038ac…-gsid-)
  4. POST /app_api/user/getAuthCode        → UC_CODE_…
  5. POST /portal/person/authCodeLogin     → person-gsid (470977…-gsid-person)

Phone + smsCode are RSA-PKCS#1-v1.5 encrypted under the pubkey.
`identifier` is also RSA-encrypted under the same pubkey.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Optional

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .auth import _atomic_write

BASE = "https://portal-jcss.police.hangzhou.gov.cn"
SESSION_PATH = os.path.join(os.path.dirname(__file__), ".session.json")
TIMEOUT = 20   # every portal call is bounded; the SSO endpoints can hang otherwise

GUC_HEADERS = {
    "guc-platform": "app",
    "guc-accountType": "person",
    "guc-endpoint": "C",
    "guc-accountSource": "inner",
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "okhttp/4.9.3",  # mPaaS okhttp default
}

DEVICE_ID = "b5329e9e1ff7482d93526da7a63de4df"  # from the captured flow; can rotate


def _wrap_pem(b64key: str) -> str:
    """Wrap a base64 RSA SubjectPublicKeyInfo blob with PEM headers (the
    `/uc/login/publicKey` response is the raw base64 body, no headers)."""
    b64key = b64key.strip().replace("\n", "")
    lines = [b64key[i:i+64] for i in range(0, len(b64key), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"


def _rsa_encrypt(pubkey: str, plaintext: str) -> str:
    """RSA-PKCS#1-v1.5 encrypt (matches what the captured ciphertext looks like)."""
    pem = pubkey if pubkey.startswith("-----") else _wrap_pem(pubkey)
    pub = serialization.load_pem_public_key(pem.encode())
    ct = pub.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ct).decode()


def _get_pubkey(s: requests.Session) -> str:
    r = s.get(
        f"{BASE}/uc/login/publicKey",
        params={"__noCache__": int(time.time() * 1000)},
        headers={k: v for k, v in GUC_HEADERS.items() if k != "Content-Type"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["data"]


def _send_otp(s: requests.Session, phone: str, pubkey_pem: str) -> None:
    enc_phone = _rsa_encrypt(pubkey_pem, phone)
    body = {
        "phone": enc_phone,
        "encrypt": True,
        "action": "smsLogin",
        "publicKey": pubkey_pem,
    }
    r = s.post(f"{BASE}/uc/verify/sendCodeEncrypt",
               headers={k: v for k, v in GUC_HEADERS.items() if k != "User-Agent"},
               data=json.dumps(body), timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(f"sendCode failed: {j}")


def _do_login(s: requests.Session, phone: str, sms_code: str,
              pubkey_pem: str) -> dict:
    enc_id = _rsa_encrypt(pubkey_pem, phone)
    enc_phone = _rsa_encrypt(pubkey_pem, phone)
    enc_code = _rsa_encrypt(pubkey_pem, sms_code)
    body = {
        "identifier": enc_id,
        "credential": {"phone": enc_phone, "smsCode": enc_code},
        "loginType": "smsLogin",
        "encrypt": True,
        "deviceOsType": "Android",
        "publicKey": pubkey_pem,
        "deviceId": DEVICE_ID,
    }
    r = s.post(f"{BASE}/uc/login",
               headers={k: v for k, v in GUC_HEADERS.items() if k != "User-Agent"},
               data=json.dumps(body), timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(f"login failed: {j}")
    return j["data"]  # {gsid, accessToken, refreshToken, userName}


def _get_auth_code(s: requests.Session, gsid: str) -> str:
    r = s.post(f"{BASE}/app_api/user/getAuthCode",
               headers={k: v for k, v in GUC_HEADERS.items() if k != "User-Agent"},
               data=json.dumps({"token": gsid}), timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(f"getAuthCode failed: {j}")
    return j["data"]  # "UC_CODE_..."


def _person_login(s: requests.Session, auth_code: str) -> str:
    r = s.post(f"{BASE}/portal/person/authCodeLogin",
               headers={k: v for k, v in GUC_HEADERS.items() if k != "User-Agent"},
               data=json.dumps({"authCode": auth_code}), timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(f"person_login failed: {j}")
    return j["data"]  # person-gsid


@dataclass
class LoginResult:
    main_gsid: str
    person_gsid: str
    user_name: str
    access_token: str
    refresh_token: str
    auth_code: str


def login_full(phone: str, sms_code: str,
               device_id: Optional[str] = None) -> LoginResult:
    """One-shot: send OTP was already done; you supply code. Returns both gsids
    plus the access/refresh tokens (previously dropped)."""
    if device_id:
        global DEVICE_ID
        DEVICE_ID = device_id
    s = requests.Session()
    pub = _get_pubkey(s)
    main = _do_login(s, phone, sms_code, pub)
    code = _get_auth_code(s, main["gsid"])
    person = _person_login(s, code)
    return LoginResult(
        main_gsid=main["gsid"],
        person_gsid=person,
        user_name=main.get("userName", ""),
        access_token=main.get("accessToken", ""),
        refresh_token=main.get("refreshToken", ""),
        auth_code=code,
    )


def save_session(r: LoginResult, path: str = SESSION_PATH) -> None:
    """Persist the login result atomically (0600). `person_gsid` is the session
    the mgop wfjb.auth replay signs against; the access/refresh tokens are kept
    for the eventual portal-refresh path. NOTE: this does not by itself mint an
    x-token — the mgop `sign` (native SDK) is still required to rebuild a replay
    template from a fresh gsid (see auth.py)."""
    payload = dict(asdict(r))
    payload["saved_at"] = int(time.time())
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def load_session(path: str = SESSION_PATH) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return None


def request_otp(phone: str) -> str:
    """Step 1 only: fetch pubkey + send OTP. Returns the pubkey (caller may want
    to verify it). Throws on failure."""
    s = requests.Session()
    pub = _get_pubkey(s)
    _send_otp(s, phone, pub)
    return pub


# ---- smoke test (mirrors the captured flow exactly) ----
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        phone = sys.argv[1]
        sms = sys.argv[2]
        r = login_full(phone, sms)
        save_session(r)
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2))
        print(f"session saved -> {SESSION_PATH}")
    else:
        print("usage: python -m police_report.portal_login <phone> <sms_code>")
