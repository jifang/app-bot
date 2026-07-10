"""TokenProvider — the single, file-locked source of truth for the wfjb x-token.

Replaces the two divergent refresh paths (auth.refresh_token + auto_refresh) with
one implementation that, under an inter-process file lock:

  * returns the cached JWT while it has comfortable TTL (lazy — no wasted replay),
  * otherwise replays the one saved MGOP request and classifies the result
    explicitly (OK / THROTTLED / PORTAL_EXPIRED / BAD_TEMPLATE / NETWORK_ERROR),
  * persists a new token atomically at mode 0600 (via auth.save_token), and
  * records the outcome in a non-secret `.token_state.json` for the operator/cron.

The HTTP `poster` and `clock` are injectable so the whole thing is testable
offline. Callers (WfjbClient, auto_refresh, the CLI) go through this — never the
raw replay — so token persistence and classification live in exactly one place.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import requests

from .auth import (
    ENV_PATH,
    REPLAY_PATH,
    TOKEN_PATH,
    decode_exp,
    save_token,
    validate_mgop_url,
)

LOCK_PATH = os.path.join(os.path.dirname(__file__), ".token.lock")
STATE_PATH = os.path.join(os.path.dirname(__file__), ".token_state.json")

# Skip the replay while the cached token still has at least this much life.
DEFAULT_MARGIN_S = 10 * 60


class RefreshOutcome(Enum):
    OK = "OK"                       # a fresh token was minted (or cache is fresh)
    THROTTLED = "THROTTLED"         # gateway per-triple quota hit (empty 200)
    PORTAL_EXPIRED = "PORTAL_EXPIRED"   # gsid/session dead — needs re-capture/login
    BAD_TEMPLATE = "BAD_TEMPLATE"   # missing/poisoned/undecodable replay template
    NETWORK_ERROR = "NETWORK_ERROR"     # transport failure reaching the gateway


@dataclass
class RefreshResult:
    outcome: RefreshOutcome
    token: Optional[str]
    detail: str


class TokenUnavailable(RuntimeError):
    """Raised when no usable token can be produced (cache dead + refresh failed)."""

    def __init__(self, outcome: RefreshOutcome, detail: str):
        super().__init__(f"{outcome.value}: {detail}")
        self.outcome = outcome
        self.detail = detail


class TokenProvider:
    def __init__(self, *,
                 token_path: str = TOKEN_PATH,
                 env_path: str = ENV_PATH,
                 replay_path: str = REPLAY_PATH,
                 lock_path: str = LOCK_PATH,
                 state_path: str = STATE_PATH,
                 margin_s: int = DEFAULT_MARGIN_S,
                 poster: Callable[..., object] = requests.post,
                 clock: Callable[[], float] = time.time):
        self.token_path = token_path
        self.env_path = env_path
        self.replay_path = replay_path
        self.lock_path = lock_path
        self.state_path = state_path
        self.margin_s = margin_s
        self.poster = poster
        self.clock = clock

    # ---- public API -------------------------------------------------------

    def refresh(self, *, force: bool = False) -> RefreshResult:
        """Ensure a current token. Lazy unless `force`: with the cached token
        still fresh and force=False, this replays nothing and reports OK."""
        with self._lock():
            return self._refresh_unlocked(force=force)

    def get_token(self, *, min_ttl_s: Optional[int] = None) -> str:
        """Return a usable x-token, refreshing if the cached one is within
        `min_ttl_s` (default: the provider margin) of expiry.

        Falls back to a still-valid cached token if a refresh attempt fails,
        and only raises TokenUnavailable when nothing usable remains.
        """
        threshold = self.margin_s if min_ttl_s is None else min_ttl_s
        with self._lock():
            cached, ttl = self._load_cached()
            if cached and ttl is not None and ttl > threshold:
                return cached
            result = self._refresh_unlocked(force=True)
            if result.outcome is RefreshOutcome.OK and result.token:
                return result.token
            if cached and ttl is not None and ttl > 0:
                return cached   # degraded: old token still valid, refresh failed
            raise TokenUnavailable(result.outcome, result.detail)

    # ---- internals --------------------------------------------------------

    def _refresh_unlocked(self, *, force: bool) -> RefreshResult:
        if not force:
            cached, ttl = self._load_cached()
            if cached and ttl is not None and ttl > self.margin_s:
                return RefreshResult(RefreshOutcome.OK, cached, "cached token still fresh")
        result = self._replay_and_classify()
        self._write_state(result)
        if result.outcome is RefreshOutcome.OK and result.token:
            save_token(result.token, token_path=self.token_path, env_path=self.env_path)
        return result

    def _replay_and_classify(self) -> RefreshResult:
        if not os.path.isfile(self.replay_path):
            return RefreshResult(RefreshOutcome.BAD_TEMPLATE, None,
                                 f"no replay template at {self.replay_path}")
        try:
            tpl = json.load(open(self.replay_path, encoding="utf-8"))
            validate_mgop_url(tpl["url"])
            hdrs = {k: v for k, v in tpl["headers"].items()
                    if k.lower() not in ("content-length", "host", "accept-encoding")}
            body = bytes.fromhex(tpl["body_hex"]) if tpl.get("body_hex") else b""
        except (ValueError, KeyError, RuntimeError) as e:
            return RefreshResult(RefreshOutcome.BAD_TEMPLATE, None, str(e))

        try:
            resp = self.poster(tpl["url"], headers=hdrs, data=body, timeout=30)
        except requests.RequestException as e:
            return RefreshResult(RefreshOutcome.NETWORK_ERROR, None, str(e))

        if resp.status_code != 200:
            return RefreshResult(RefreshOutcome.PORTAL_EXPIRED, None,
                                 f"http {resp.status_code} (gsid likely dead)")
        if not resp.content:
            return RefreshResult(RefreshOutcome.THROTTLED, None,
                                 "empty 200 — replay quota exhausted for this triple")
        try:
            j = resp.json()
        except ValueError:
            return RefreshResult(RefreshOutcome.PORTAL_EXPIRED, None,
                                 f"non-JSON 200: {resp.text[:120]}")
        token = (j.get("data") or {}).get("token")
        if j.get("code") == 200 and token:
            return RefreshResult(RefreshOutcome.OK, token, "minted")
        return RefreshResult(RefreshOutcome.PORTAL_EXPIRED, None,
                             f"declined: code={j.get('code')} msg={j.get('msg')}")

    def cached_ttl(self) -> Optional[float]:
        """Seconds until the cached token expires (negative if expired), or None
        if there is no readable cached token. Read-only; takes no lock."""
        return self._load_cached()[1]

    def _load_cached(self) -> tuple[Optional[str], Optional[float]]:
        """Return (token, seconds_until_exp) from the cached store, or (None, None)."""
        if not os.path.isfile(self.token_path):
            return None, None
        try:
            tok = json.load(open(self.token_path, encoding="utf-8")).get("x_token")
        except (ValueError, OSError):
            return None, None
        if not tok:
            return None, None
        exp = decode_exp(tok)
        return tok, (None if exp is None else exp - self.clock())

    def _write_state(self, result: RefreshResult) -> None:
        state = {
            "outcome": result.outcome.value,
            "detail": result.detail,
            "attempt_ts": int(self.clock()),
        }
        if result.outcome is RefreshOutcome.OK:
            state["ok_ts"] = int(self.clock())
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.state_path)

    @contextlib.contextmanager
    def _lock(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


# Convenience default instance for callers that don't need injection.
def default_provider() -> TokenProvider:
    return TokenProvider()
