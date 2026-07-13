"""MGOP request signing — pluggable so TokenProvider can mint without replay.

Production signers:

  * OfflineMgopSigner — pure Python (GatewayUtil.md5Sign, reversed 2026-07-12)
  * BridgeMgopSigner — HTTP to a local helper (emulator / LSPosed hook)

Canon (from ``com.alibaba.gov.rpc.util.GatewayUtil.md5Sign``):

    MD5( UTF-8( ``{gatewaySignSecret}&api={api}&sid={gsid}&ts={ts}&data={b64(body)}`` ) )

``data`` is standard Base64 of the raw JSON body (not the raw JSON string).

Replay of a frozen `(gsid, sign, ts)` is *not* a signer; see AndroidCaptureMinter
for capture-based minting that still uses the genuine APK.
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional, Protocol, runtime_checkable

import requests

DEFAULT_API = "mgop.trustway.wfjb.auth"
DEFAULT_APPID = "421424f5fe7b444782907d18955b8e1a"
DEFAULT_BODY = b'{"platformId":8}'
# From app module_config.gateway.gatewaySignSecret (not user-specific).
DEFAULT_GATEWAY_SIGN_SECRET = "cgpQrzzC2VpmFCcIW0xa+2Oj"


@runtime_checkable
class MgopSigner(Protocol):
    def sign(self, *, api: str, appid: str, gsid: str, ts: int,
             body: bytes) -> str:
        """Return the MGOP `sign` hex/string for the given request fields."""


class SignerError(RuntimeError):
    """Signer unavailable or rejected the request."""


class FakeMgopSigner:
    """Deterministic stand-in for unit tests (not accepted by real MGOP)."""

    def __init__(self, signature: str = "fake-sign"):
        self.signature = signature
        self.calls: list[dict] = []

    def sign(self, *, api: str, appid: str, gsid: str, ts: int,
             body: bytes) -> str:
        self.calls.append({
            "api": api, "appid": appid, "gsid": gsid, "ts": ts, "body": body,
        })
        return self.signature


def mgop_sign_string(*, secret: str, api: str, gsid: str, ts,
                     body: bytes) -> str:
    """Build the GatewayUtil.md5Sign plaintext (for tests / debugging)."""
    data_b64 = base64.b64encode(body).decode("ascii")
    return (
        f"{secret}&api={api}&sid={gsid}&ts={ts}&data={data_b64}"
    )


class OfflineMgopSigner:
    """Pure-Python MGOP sign matching ``GatewayUtil.md5Sign``."""

    def __init__(self, secret: str = DEFAULT_GATEWAY_SIGN_SECRET):
        self.secret = secret

    def sign(self, *, api: str, appid: str, gsid: str, ts: int,
             body: bytes) -> str:
        del appid  # appid is a request header, not part of the sign canon
        plain = mgop_sign_string(
            secret=self.secret, api=api, gsid=gsid, ts=ts, body=body)
        return hashlib.md5(plain.encode("utf-8")).hexdigest()


class BridgeMgopSigner:
    """Call a local HTTP bridge that invokes SecurityGuard on-device.

    Expected contract (POST JSON → JSON):

        POST {base}/sign
        {"api","appid","gsid","ts","body_hex"} -> {"sign":"..."}

    Set `MGOP_SIGNER_URL` (default http://127.0.0.1:8765) when the emulator
    helper / reverse-tunnelling is up.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8765",
                 timeout: float = 30.0,
                 poster=requests.post):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poster = poster

    def sign(self, *, api: str, appid: str, gsid: str, ts: int,
             body: bytes) -> str:
        try:
            resp = self.poster(
                f"{self.base_url}/sign",
                json={
                    "api": api,
                    "appid": appid,
                    "gsid": gsid,
                    "ts": ts,
                    "body_hex": body.hex(),
                },
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise SignerError(f"signer bridge unreachable: {e}") from e
        if resp.status_code != 200:
            raise SignerError(f"signer bridge http {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise SignerError(f"signer bridge non-JSON: {resp.text[:200]}") from e
        sign = data.get("sign")
        if not sign:
            raise SignerError(f"signer bridge missing sign: {data!r}")
        return str(sign)


def default_bridge_signer(url: Optional[str] = None) -> BridgeMgopSigner:
    return BridgeMgopSigner(base_url=url or os.environ.get(
        "MGOP_SIGNER_URL", "http://127.0.0.1:8765"))


def default_offline_signer(secret: Optional[str] = None) -> OfflineMgopSigner:
    return OfflineMgopSigner(
        secret=secret or os.environ.get(
            "MGOP_SIGN_SECRET", DEFAULT_GATEWAY_SIGN_SECRET))
