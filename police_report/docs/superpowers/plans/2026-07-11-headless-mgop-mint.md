# Headless MGOP Mint Service Implementation Plan

> **For agentic workers:** implement task-by-task. Steps use checkbox syntax.

**Goal:** Replace dead replay-only refresh with a pluggable mint path: `gsid` + fresh `sign(ts)` → `x-token`, starting with an Android emulator capture/bridge minter.

**Architecture:** `MgopSigner` produces `sign` for `(api, appid, gsid, ts, body)`. `TokenMinter` turns that (or an emulator capture) into an `x-token`. `TokenProvider` falls back to the minter when replay returns `SIGNATURE_EXPIRED` / missing template.

**Tech Stack:** Python 3, existing `auth.mgop_auth` / `TokenProvider`, Android SDK emulator + adb, mitmproxy auth-only capture.

---

## Files

| File | Role |
|---|---|
| `police_report/signer.py` | `MgopSigner` protocol + `BridgeMgopSigner` + fakes |
| `police_report/minter.py` | `TokenMinter` + `SignerBackedMinter` + `AndroidCaptureMinter` |
| `police_report/scripts/setup_wfjb_avd.sh` | Create ARM64 API 34 AVD |
| `police_report/token_provider.py` | Hook minter on signature expiry |
| `police_report/cli.py` | `mint` subcommand |
| `police_report/tests/test_signer_minter.py` | Unit tests |
| `police_report/docs/superpowers/specs/2026-07-11-headless-mgop-mint.md` | Spec snapshot |

### Task 1: Signer + minter interfaces + tests
### Task 2: AndroidCaptureMinter orchestration
### Task 3: TokenProvider fallback + CLI
### Task 4: AVD setup script + README
