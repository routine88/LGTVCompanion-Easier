#!/usr/bin/env bash
# The same checks .github/workflows/easy-mode-ci.yml defines, run on this
# machine instead of on GitHub Actions (that workflow is manual-only now).
#
#   test      - python -m pytest -q in EasyMode/, on Python 3.10 and 3.12
#   gui-smoke - tests/gui_smoke.py against a mock TV
#
# The workflow's windows-latest leg can't run here; say so, don't pretend.
# Called by the pre-push hook (scripts/install-hooks.sh wires it up).
set -euo pipefail
cd "$(dirname "$0")/../EasyMode"
export LGTV_EASY_NO_SLEEP_WATCH=1

step() { printf '\n==> %s\n' "$*"; }

step "pytest on Python 3.12 (/usr/bin/python3)"
/usr/bin/python3 -m pytest -q

step "pytest on Python 3.10"
if command -v uv >/dev/null 2>&1; then
    uv run --no-project --quiet --python 3.10 --with pytest \
        python -m pytest -q -p no:cacheprovider
else
    echo "SKIPPED: uv not found, no Python 3.10 with pytest available" >&2
    exit 1
fi

step "GUI smoke (tests/gui_smoke.py)"
if command -v xvfb-run >/dev/null 2>&1; then
    xvfb-run -a /usr/bin/python3 tests/gui_smoke.py
else
    DISPLAY="${DISPLAY:-:0}" /usr/bin/python3 tests/gui_smoke.py
fi

printf '\nAll local CI steps passed. Not run here: the windows-latest matrix leg.\n'
