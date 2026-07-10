"""auto_refresh.py — cron entry point for lazy wfjb x-token refresh.

This is now a thin wrapper over TokenProvider (police_report/token_provider.py),
which owns the single locked replay+classify+persist path. Behaviour:

  * Lazy — TokenProvider replays only when the cached JWT is near expiry, so a
    frequent cron cadence does not burn the mgop gateway's per-triple quota.
  * Explicit classification — OK / THROTTLED / PORTAL_EXPIRED / BAD_TEMPLATE /
    NETWORK_ERROR, recorded in `.token_state.json` for the operator.

Exit code (what cron mails on):
  * OK, or a transient failure (THROTTLED / NETWORK_ERROR) while the cached token
    is still valid                                                        -> 0
  * unrecoverable (PORTAL_EXPIRED / BAD_TEMPLATE), or nothing usable left  -> 1

When it exits non-zero the operator recovers with `cli login` + a fresh capture.
"""
from __future__ import annotations

import sys

from .token_provider import RefreshOutcome, default_provider

# Non-OK outcomes that never resolve on their own — always surface to the operator.
_UNRECOVERABLE = {RefreshOutcome.PORTAL_EXPIRED, RefreshOutcome.BAD_TEMPLATE}


def main(provider=None) -> int:
    p = provider or default_provider()
    res = p.refresh(force=False)   # lazy: no replay while the cached token is fresh

    if res.outcome is RefreshOutcome.OK:
        print(f"auto_refresh: OK ({res.detail})")
        return 0

    ttl = p.cached_ttl()
    still_valid = ttl is not None and ttl > 0
    if res.outcome in _UNRECOVERABLE or not still_valid:
        print(f"auto_refresh: {res.outcome.value} — {res.detail}; "
              "run `cli login` + open wfjb H5 to re-capture", file=sys.stderr)
        return 1

    # Transient (throttled / network) but the cached token is still usable.
    print(f"auto_refresh: {res.outcome.value} — {res.detail}; "
          f"cached token still valid ({ttl/60:.0f} min), will retry next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
