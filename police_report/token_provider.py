"""TokenProvider — the single, file-locked source of truth for the wfjb x-token.

Replaces the two divergent refresh paths (auth.refresh_token + auto_refresh) with
one implementation that, under an inter-process file lock:

  * returns the cached JWT while it has comfortable TTL (lazy — no wasted replay),
  * otherwise replays the one saved MGOP request and classifies the result,
    including signed-timestamp expiry (rs=7003) separately from portal expiry,
  * persists a new token atomically at mode 0600 (via auth.save_token) *before*
    best-effort state updates,
  * respects throttle backoff (`next_retry_at`) unless explicitly bypassed, and
  * records the outcome in a non-secret `.token_state.json` for the operator/cron.

The HTTP `poster` and `clock` are injectable so the whole thing is testable
offline. Callers (WfjbClient, auto_refresh, the CLI) go through this — never the
raw replay — so token persistence and classification live in exactly one place.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional
from urllib.parse import unquote

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

# Throttle backoff steps (seconds): 5 → 15 → 30 → 60 min, then capped.
BACKOFF_STEPS_S = (5 * 60, 15 * 60, 30 * 60, 60 * 60)


class RefreshOutcome(Enum):
    OK = "OK"                       # a fresh token was minted (or cache is fresh)
    THROTTLED = "THROTTLED"         # explicit rate limit (HTTP 429) or active backoff
    SIGNATURE_EXPIRED = "SIGNATURE_EXPIRED"  # rs=7003; fresh signed capture required
    AUTH_EXPIRED = "AUTH_EXPIRED"   # 401/403 — gsid/session dead; re-login + capture
    UPSTREAM_ERROR = "UPSTREAM_ERROR"  # HTTP 5xx
    PROTOCOL_ERROR = "PROTOCOL_ERROR"  # non-JSON / unexpected shape
    GATEWAY_ERROR = "GATEWAY_ERROR"  # other MGOP gateway failure (e.g. rs=4001)
    BAD_TEMPLATE = "BAD_TEMPLATE"   # missing/poisoned/undecodable replay template
    NETWORK_ERROR = "NETWORK_ERROR"     # transport failure reaching the gateway
    MINT_FAILED = "MINT_FAILED"     # configured TokenMinter could not produce a JWT

    # Backward-compat alias used by older docs/scripts.
    PORTAL_EXPIRED = "AUTH_EXPIRED"


# Replay is dead but a live signer/capture minter may still recover.
_MINTABLE = frozenset({
    RefreshOutcome.SIGNATURE_EXPIRED,
    RefreshOutcome.BAD_TEMPLATE,
})


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
                 clock: Callable[[], float] = time.time,
                 rng: Callable[[], float] = random.random,
                 minter: Optional[Callable[[], str]] = None):
        self.token_path = token_path
        self.env_path = env_path
        self.replay_path = replay_path
        self.lock_path = lock_path
        self.state_path = state_path
        self.margin_s = margin_s
        self.poster = poster
        self.clock = clock
        self.rng = rng
        # minter: zero-arg callable returning a fresh JWT (TokenMinter.mint).
        self.minter = minter

    # ---- public API -------------------------------------------------------

    def refresh(self, *, force: bool = False,
                bypass_backoff: bool = False) -> RefreshResult:
        """Ensure a current token. Lazy unless `force`: with the cached token
        still fresh and force=False, this replays nothing and reports OK.

        Respects `next_retry_at` from `.token_state.json` unless
        `bypass_backoff=True` (CLI: `refresh --bypass-backoff`).
        """
        with self._lock():
            return self._refresh_unlocked(force=force,
                                          bypass_backoff=bypass_backoff)

    def get_token(self, *, min_ttl_s: Optional[int] = None,
                  allow_degraded: bool = True,
                  bypass_backoff: bool = False) -> str:
        """Return a usable x-token, refreshing if the cached one is within
        `min_ttl_s` (default: the provider margin) of expiry.

        `allow_degraded=True` (reads): if refresh fails, return a still-valid
        cached token even when below `min_ttl_s`.
        `allow_degraded=False` (upload/submit): refuse anything below
        `min_ttl_s` — never risk a mid-flight expiry on a write.
        """
        threshold = self.margin_s if min_ttl_s is None else min_ttl_s
        with self._lock():
            cached, ttl = self._load_cached()
            if cached and ttl is not None and ttl > threshold:
                return cached
            result = self._refresh_unlocked(force=True,
                                            bypass_backoff=bypass_backoff)
            if result.outcome is RefreshOutcome.OK and result.token:
                return result.token
            if allow_degraded and cached and ttl is not None and ttl > 0:
                return cached
            # Strict writes: cached below threshold (even if >0) is not enough.
            raise TokenUnavailable(result.outcome, result.detail)

    # ---- internals --------------------------------------------------------

    def _refresh_unlocked(self, *, force: bool,
                          bypass_backoff: bool = False) -> RefreshResult:
        if not force:
            cached, ttl = self._load_cached()
            if cached and ttl is not None and ttl > self.margin_s:
                return RefreshResult(RefreshOutcome.OK, cached,
                                     "cached token still fresh")

        if not bypass_backoff:
            blocked = self._backoff_block()
            if blocked is not None:
                return blocked

        result = self._replay_and_classify()
        if result.outcome in _MINTABLE and self.minter is not None:
            result = self._mint_fallback(result)
        if result.outcome is RefreshOutcome.OK and result.token:
            # Credentials first — state is observational and must not block save.
            save_token(result.token, token_path=self.token_path,
                       env_path=self.env_path)
        self._write_state_best_effort(result)
        return result

    def _mint_fallback(self, prior: RefreshResult) -> RefreshResult:
        """Replace a dead replay with a live TokenMinter when configured."""
        try:
            token = self.minter()
        except Exception as e:
            return RefreshResult(
                RefreshOutcome.MINT_FAILED, None,
                f"{prior.outcome.value}: {prior.detail}; mint failed: {e}")
        if not token:
            return RefreshResult(
                RefreshOutcome.MINT_FAILED, None,
                f"{prior.outcome.value}: {prior.detail}; mint returned empty token")
        return RefreshResult(RefreshOutcome.OK, token,
                             f"minted via TokenMinter (after {prior.outcome.value})")

    def _backoff_block(self) -> Optional[RefreshResult]:
        state = self._read_state()
        next_retry = state.get("next_retry_at")
        if not isinstance(next_retry, (int, float)):
            return None
        now = self.clock()
        if next_retry <= now:
            return None
        wait = int(next_retry - now)
        detail = state.get("detail") or "rate-limit cooldown"
        return RefreshResult(
            RefreshOutcome.THROTTLED, None,
            f"backoff {wait}s remaining (next_retry_at={int(next_retry)}): {detail}")

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
            template_fp = self._template_fp(hdrs)
        except (ValueError, KeyError, RuntimeError) as e:
            return RefreshResult(RefreshOutcome.BAD_TEMPLATE, None, str(e))

        try:
            resp = self.poster(tpl["url"], headers=hdrs, data=body, timeout=30)
        except requests.RequestException as e:
            return RefreshResult(RefreshOutcome.NETWORK_ERROR, None, str(e))

        result = self._classify_response(resp)
        # Attach fingerprint on the result via detail side-channel in state writer.
        result._template_fp = template_fp  # type: ignore[attr-defined]
        return result

    def _classify_response(self, resp) -> RefreshResult:
        response_headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        rs = response_headers.get("rs", "").strip()
        gateway_detail = self._gateway_detail(response_headers)
        status = resp.status_code

        if status == 429:
            return RefreshResult(RefreshOutcome.THROTTLED, None,
                                 gateway_detail or "http 429 rate limited")
        if status in (401, 403):
            return RefreshResult(RefreshOutcome.AUTH_EXPIRED, None,
                                 gateway_detail or
                                 f"http {status} (gsid likely dead)")
        if 500 <= status <= 599:
            return RefreshResult(RefreshOutcome.UPSTREAM_ERROR, None,
                                 gateway_detail or f"http {status}")
        if status != 200:
            return RefreshResult(RefreshOutcome.GATEWAY_ERROR, None,
                                 gateway_detail or f"http {status}")
        if not resp.content:
            # MGOP reports failures in response headers while deliberately sending
            # an empty HTTP 200 body. rs=7003 is not throttling: the frozen ts/sign
            # pair has aged out and can never recover by waiting or retrying.
            if rs == "7003":
                return RefreshResult(
                    RefreshOutcome.SIGNATURE_EXPIRED, None,
                    gateway_detail or
                    "rs=7003 signature timestamp validation failed; "
                    "fresh wfjb.auth capture required")
            return RefreshResult(
                RefreshOutcome.GATEWAY_ERROR, None,
                gateway_detail or "empty 200 without MGOP rs header")
        try:
            j = resp.json()
        except ValueError:
            return RefreshResult(RefreshOutcome.PROTOCOL_ERROR, None,
                                 f"non-JSON 200: {resp.text[:120]}")
        body_rs = str(j.get("rs", "")).strip()
        if body_rs == "7003":
            return RefreshResult(
                RefreshOutcome.SIGNATURE_EXPIRED, None,
                "rs=7003 signature timestamp validation failed; "
                "fresh wfjb.auth capture required")
        token = (j.get("data") or {}).get("token")
        if j.get("code") == 200 and token:
            return RefreshResult(RefreshOutcome.OK, token, "minted")
        if j.get("code") in (401, 403):
            return RefreshResult(RefreshOutcome.AUTH_EXPIRED, None,
                                 f"declined: code={j.get('code')} msg={j.get('msg')}")
        return RefreshResult(RefreshOutcome.GATEWAY_ERROR, None,
                             f"declined: code={j.get('code')} msg={j.get('msg')}")

    @staticmethod
    def _gateway_detail(headers: dict[str, str]) -> str:
        """Decode MGOP's empty-response diagnostic headers without guessing.

        Observed failures use an empty HTTP 200 body and carry `rs`, URL-encoded
        `memo`, and `tips` headers. Preserve those facts in operator state so
        callers can distinguish an expired signature from an expired portal.
        """
        parts = []
        if headers.get("rs"):
            parts.append(f"rs={headers['rs']}")
        for key in ("memo", "tips"):
            if headers.get(key):
                parts.append(f"{key}={unquote(headers[key])}")
        return " ".join(parts)

    @staticmethod
    def _template_fp(headers: dict) -> str:
        """Non-secret fingerprint of the frozen triple (sid + ts only)."""
        lower = {str(k).lower(): str(v) for k, v in headers.items()}
        raw = f"{lower.get('sid', '')}|{lower.get('ts', '')}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

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

    def _read_state(self) -> dict:
        if not os.path.isfile(self.state_path):
            return {}
        try:
            return json.load(open(self.state_path, encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def _write_state_best_effort(self, result: RefreshResult) -> None:
        try:
            self._write_state(result)
        except OSError as e:
            print(f"warning: could not write token state: {e}", file=sys.stderr)

    def _write_state(self, result: RefreshResult) -> None:
        prev = self._read_state()
        now = int(self.clock())
        state = {
            "outcome": result.outcome.value,
            "detail": result.detail,
            "attempt_ts": now,
            "success_count": int(prev.get("success_count") or 0),
            "consecutive_throttle": int(prev.get("consecutive_throttle") or 0),
        }
        fp = getattr(result, "_template_fp", None) or prev.get("template_fp")
        if fp:
            state["template_fp"] = fp

        if result.outcome is RefreshOutcome.OK:
            state["ok_ts"] = now
            state["success_count"] = state["success_count"] + 1
            state["consecutive_throttle"] = 0
            # Clear backoff on success.
            state.pop("next_retry_at", None)
        elif result.outcome is RefreshOutcome.THROTTLED:
            n = state["consecutive_throttle"] + 1
            state["consecutive_throttle"] = n
            delay = BACKOFF_STEPS_S[min(n - 1, len(BACKOFF_STEPS_S) - 1)]
            # Up to 20% jitter so parallel crons don't sync-stampede.
            delay = int(delay * (1.0 + 0.2 * self.rng()))
            state["next_retry_at"] = now + delay
        else:
            # Non-throttle failures: don't extend a throttle cooldown.
            if "next_retry_at" in prev and prev["next_retry_at"] > now:
                state["next_retry_at"] = prev["next_retry_at"]

        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

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
    minter_fn = None
    try:
        from .minter import default_minter
        m = default_minter()
        if m is not None:
            minter_fn = m.mint
    except Exception:
        minter_fn = None
    return TokenProvider(minter=minter_fn)
