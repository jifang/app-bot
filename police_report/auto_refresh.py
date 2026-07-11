"""auto_refresh.py — cron entry point for lazy wfjb x-token refresh.

This is now a thin wrapper over TokenProvider (police_report/token_provider.py),
which owns the single locked replay+classify+persist path. Behaviour:

  * Lazy — TokenProvider checks the cached JWT before attempting the saved replay.
    A frozen replay is only a short bootstrap aid: it cannot renew once its signed
    timestamp ages out (rs=7003), so this job is also a health monitor rather than
    a promise of unattended long-term refresh.
  * Explicit classification — including SIGNATURE_EXPIRED (rs=7003) separately
    from PORTAL_EXPIRED and transient gateway/network failures.

Exit code (what cron mails on):
  * OK, or a transient failure while the cached token
    is still valid                                                        -> 0
  * unrecoverable (SIGNATURE_EXPIRED / PORTAL_EXPIRED / BAD_TEMPLATE), or
    nothing usable left                                                   -> 1

When it exits non-zero the operator recovers with `cli login` + a fresh capture.
"""
from __future__ import annotations

import sys

from .token_provider import RefreshOutcome, default_provider

# Non-OK outcomes that never resolve on their own — always surface to the operator.
_UNRECOVERABLE = {
    RefreshOutcome.SIGNATURE_EXPIRED,
    RefreshOutcome.PORTAL_EXPIRED,
    RefreshOutcome.BAD_TEMPLATE,
}


def _recovery_hint(outcome: RefreshOutcome) -> str:
    if outcome is RefreshOutcome.SIGNATURE_EXPIRED:
        return "capture a fresh wfjb.auth request, then run save-replay + refresh"
    if outcome is RefreshOutcome.PORTAL_EXPIRED:
        return "renew the portal login, then capture a fresh wfjb.auth request"
    if outcome is RefreshOutcome.BAD_TEMPLATE:
        return "create a fresh replay template with save-replay"
    return "retry later"


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
              f"{_recovery_hint(res.outcome)}", file=sys.stderr)
        return 1

    # Transient (throttled / network) but the cached token is still usable.
    print(f"auto_refresh: {res.outcome.value} — {res.detail}; "
          f"cached token still valid ({ttl/60:.0f} min), will retry next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
