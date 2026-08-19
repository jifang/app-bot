#!/usr/bin/env bash
# install_cron.sh — install the unattended token refresh job.
#
# Schedule: every 15 minutes. The default minter (OfflineMgopSigner) keeps the
# x-token warm indefinitely; the job only exits non-zero when the gsid itself
# dies or the gateway is unreachable. A `cron` MAILTO will receive those
# non-zero exits (the signal to re-login + re-capture).
#
# Re-run this script any time to reset the schedule.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The package lives in the project root, two levels up from this script
# (police_report/scripts/). Run from there so `-m police_report.scripts.auto_refresh`
# resolves correctly.
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# CRITICAL: use an absolute path to a python that has requests + cryptography
# installed. pyenv shims resolve to the system Xcode Python under cron's
# minimal PATH, and that one has no third-party libs.
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/Caskroom/miniconda/base/bin/python3}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "install_cron: python3 not found at $PYTHON_BIN — set PYTHON_BIN env" >&2
    exit 1
fi
LINE="*/15 * * * *  cd '$PROJECT_ROOT' && PYTHONPATH='$PROJECT_ROOT' '$PYTHON_BIN' -m police_report.scripts.auto_refresh"
TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v -e 'police_report.scripts.auto_refresh' -e 'police_report.auto_refresh' > "$TMP" || true
echo "$LINE" >> "$TMP"
crontab "$TMP"
rm -f "$TMP"
echo "installed:"
crontab -l | grep police_report.scripts.auto_refresh
