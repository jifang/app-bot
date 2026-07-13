"""Token minters — produce a fresh x-token without a stale replay template.

Two paths:

  SignerBackedMinter
      gsid (portal session) + MgopSigner + auth.mgop_auth → JWT

  AndroidCaptureMinter
      boot/attach emulator, auth-only mitm, launch official APK, wait for one
      wfjb.auth capture, save replay (and/or extract JWT from response)

The capture minter still performs a live mint inside the genuine APK — it removes
physical-phone / manual-mitm dependency once the AVD + session are prepared.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from . import auth
from .portal_login import SESSION_PATH, load_session
from .signer import (
    DEFAULT_API,
    DEFAULT_APPID,
    DEFAULT_BODY,
    MgopSigner,
    SignerError,
)

PACKAGE = "com.hzpd.jwztc"
# Launchable activity from official APK badging (not MainTabActivity).
DEFAULT_ACTIVITY = os.environ.get(
    "WFJB_MINT_ACTIVITY", f"{PACKAGE}/.LaunchActivity")


@runtime_checkable
class TokenMinter(Protocol):
    def mint(self) -> str:
        """Return a fresh x-token JWT or raise MintError."""


class MintError(RuntimeError):
    """Could not mint an x-token."""


@dataclass
class SignerBackedMinter:
    """Mint via gsid + live MgopSigner + mgop POST."""

    signer: MgopSigner
    gsid: Optional[str] = None
    session_path: str = SESSION_PATH
    api: str = DEFAULT_API
    appid: str = DEFAULT_APPID
    body: bytes = DEFAULT_BODY
    clock: Callable[[], float] = time.time
    auth_fn: Callable = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.auth_fn is None:
            self.auth_fn = auth.mgop_auth

    def _resolve_gsid(self) -> str:
        if self.gsid:
            return self.gsid
        sess = load_session(self.session_path)
        if sess:
            gsid = sess.get("person_gsid") or sess.get("main_gsid")
            if gsid:
                return gsid
        # Fallback: sid frozen in last auth replay (portal session may still be live).
        try:
            rep = json.load(open(auth.REPLAY_PATH, encoding="utf-8"))
            sid = (rep.get("headers") or {}).get("sid") or (rep.get("headers") or {}).get("sessionid")
            if sid:
                return sid
        except (OSError, ValueError, TypeError):
            pass
        raise MintError(
            f"no gsid: set gsid=, create {self.session_path} via cli login, "
            f"or save a replay with sid")

    def mint(self) -> str:
        gsid = self._resolve_gsid()
        ts = int(self.clock() * 1000)
        try:
            sign = self.signer.sign(
                api=self.api, appid=self.appid, gsid=gsid, ts=ts, body=self.body)
        except SignerError as e:
            raise MintError(str(e)) from e
        try:
            data = self.auth_fn(gsid=gsid, sign=sign, ts=ts, appid=self.appid)
        except Exception as e:
            raise MintError(f"mgop_auth failed: {e}") from e
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise MintError(f"mgop_auth returned no token: {data!r}")
        return token


@dataclass
class AndroidCaptureMinter:
    """Orchestrate emulator + auth-only mitm to obtain a fresh mint.

    Requires: adb, emulator binary, installed official APK, working portal
    session on the AVD (or interactive login during the wait window).
    """

    avd_name: str = os.environ.get("WFJB_AVD", "wfjb_arm64")
    sdk_root: str = os.environ.get(
        "ANDROID_HOME", os.environ.get("ANDROID_SDK_ROOT",
                                       os.path.expanduser("~/Library/Android/sdk")))
    capture_path: str = os.environ.get("WFJB_MINT_CAPTURE", "/tmp/re/mint.jsonl")
    replay_path: str = auth.REPLAY_PATH
    package: str = PACKAGE
    activity: str = DEFAULT_ACTIVITY
    wait_s: float = float(os.environ.get("WFJB_MINT_WAIT_S", "180"))
    boot_timeout_s: float = 180.0
    # Injectables for tests
    runner: Callable = subprocess.run
    sleeper: Callable = time.sleep
    clock: Callable[[], float] = time.time
    save_replay: Callable = auth.save_replay_template
    get_token_from_mitm: Callable = auth.get_token_from_mitm

    def _adb(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        adb = os.path.join(self.sdk_root, "platform-tools", "adb")
        if not os.path.isfile(adb):
            adb = "adb"
        return self.runner([adb, *args], check=check, capture_output=True, text=True)

    def _emulator_bin(self) -> str:
        cand = os.path.join(self.sdk_root, "emulator", "emulator")
        return cand if os.path.isfile(cand) else "emulator"

    def _device_online(self) -> bool:
        try:
            r = self._adb("devices", check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        for line in (r.stdout or "").splitlines()[1:]:
            if "\tdevice" in line and "emulator" in line:
                return True
        return False

    def ensure_emulator(self) -> None:
        if self._device_online():
            return
        emu = self._emulator_bin()
        try:
            # Detached boot; we poll adb.
            self.runner(
                [emu, "-avd", self.avd_name, "-no-snapshot-save",
                 "-no-boot-anim", "-no-audio"],
                check=False, capture_output=True, text=True,
                start_new_session=True,
            )
        except OSError as e:
            raise MintError(f"cannot start emulator {self.avd_name!r}: {e}") from e
        deadline = self.clock() + self.boot_timeout_s
        while self.clock() < deadline:
            if self._device_online():
                # wait for package manager
                r = self._adb("shell", "getprop", "sys.boot_completed", check=False)
                if (r.stdout or "").strip() == "1":
                    return
            self.sleeper(2)
        raise MintError(f"emulator {self.avd_name!r} did not boot in {self.boot_timeout_s:.0f}s")

    def _package_installed(self) -> bool:
        r = self._adb("shell", "pm", "path", self.package, check=False)
        return r.returncode == 0 and "package:" in (r.stdout or "")

    def launch_app(self) -> None:
        if not self._package_installed():
            raise MintError(
                f"{self.package} not installed on emulator — "
                f"adb install the official APK, then retry")
        self._adb("shell", "am", "start", "-n", self.activity, check=False)
        # Also try opening the wfjb H5 host in case MainTab alone is not enough.
        self._adb(
            "shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", "https://wfjb.police.hangzhou.gov.cn:7443/",
            check=False,
        )

    def wait_for_capture(self) -> str:
        """Block until capture_path contains a wfjb.auth request; return path."""
        os.makedirs(os.path.dirname(self.capture_path) or ".", exist_ok=True)
        if not os.path.isfile(self.capture_path):
            open(self.capture_path, "a").close()
            try:
                os.chmod(self.capture_path, 0o600)
            except OSError:
                pass
        deadline = self.clock() + self.wait_s
        last_size = -1
        while self.clock() < deadline:
            try:
                size = os.path.getsize(self.capture_path)
            except OSError:
                size = 0
            if size != last_size and size > 0:
                last_size = size
                if self.save_replay(self.capture_path, out_path=self.replay_path):
                    return self.capture_path
            self.sleeper(2)
        raise MintError(
            f"no wfjb.auth in {self.capture_path} within {self.wait_s:.0f}s — "
            f"is MITM_AUTH_ONLY mitmproxy running and the app session live?")

    def mint(self) -> str:
        """Ensure emulator, launch app, wait for auth capture, return JWT.

        Expectation: operator (or a companion script) has
        `MITM_AUTH_ONLY=1 MITM_OUT=<capture_path> mitmdump -s mitm_addon.py`
        and device HTTP proxy pointing at it *before* or during wait.
        """
        self.ensure_emulator()
        self.launch_app()
        path = self.wait_for_capture()
        got = self.get_token_from_mitm(path)
        if got and got[0]:
            return got[0]
        # Fallback: template saved; caller/TokenProvider can replay immediately.
        # Try one forced replay here for a complete mint().
        try:
            return auth.refresh_token(replay_path=self.replay_path)
        except Exception as e:
            raise MintError(
                f"captured auth but could not extract/mint token: {e}") from e


def default_minter() -> Optional[TokenMinter]:
    """Pick a minter from env, or None if minting is not configured.

    WFJB_MINTER=offline → SignerBackedMinter + OfflineMgopSigner
    WFJB_MINTER=signer  → SignerBackedMinter + BridgeMgopSigner
    WFJB_MINTER=android → AndroidCaptureMinter
    unset / off         → None (replay-only)
    """
    mode = (os.environ.get("WFJB_MINTER") or "").strip().lower()
    if mode in ("", "0", "off", "none", "replay"):
        return None
    if mode in ("offline", "local", "python"):
        from .signer import default_offline_signer
        return SignerBackedMinter(signer=default_offline_signer())
    if mode in ("signer", "bridge"):
        from .signer import default_bridge_signer
        return SignerBackedMinter(signer=default_bridge_signer())
    if mode in ("android", "emulator", "capture"):
        return AndroidCaptureMinter()
    raise MintError(
        f"unknown WFJB_MINTER={mode!r} (use offline|signer|android|off)")
