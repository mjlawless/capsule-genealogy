#!/usr/bin/env bash
# CI only: check a freshly installed Capsule (install.sh / install.ps1 with CAPSULE_AUTOSTART=0).
#
#   CAPSULE_HOME=<install folder> scripts/ci-install-smoke.sh
#
# On Windows this runs in the SAME step as install.ps1: the runner ends a step's background
# processes when the step finishes, so a server the installer started is gone by the next step.
set -eu
: "${CAPSULE_HOME:?set CAPSULE_HOME to the install folder}"
curl -fsS http://127.0.0.1:8765/health
echo
# A web page cannot pair (only Capsule's own extension ID can).
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Origin: https://evil.example' http://127.0.0.1:8765/pair)
test "$code" = 403
# With UV_PYTHON_PREFERENCE=only-managed, Capsule must run on a Python that uv downloaded,
# not the runner's, as on a user's computer that has no Python of its own. Checked first,
# with the installed environment's own interpreter: `uv run` under only-managed could
# quietly rebuild a runner-Python environment on a downloaded one and hide the failure.
if [ "${UV_PYTHON_PREFERENCE:-}" = "only-managed" ]; then
  uv_pythons=$(uv python dir)
  venv_python="$CAPSULE_HOME/app/.venv/bin/python"
  [ -x "$venv_python" ] || venv_python="$CAPSULE_HOME/app/.venv/Scripts/python.exe"
  "$venv_python" -c "
import os, sys
root = os.path.normcase(os.path.realpath(sys.argv[1]))
base = os.path.normcase(os.path.realpath(sys.base_prefix))
print('Capsule runs on', sys.base_prefix)
sys.exit(os.path.commonpath([root, base]) != root)
" "$uv_pythons" || { echo "Capsule is not running on a uv-managed Python"; exit 1; }
fi
# A capture with no key is archived, with its extraction pending.
cd "$CAPSULE_HOME"
token_file=$(uv run --project "$CAPSULE_HOME/app" python -c "from capsule.config import token_path; print(token_path())")
token=$(tr -d '\r\n' < "$token_file")
curl -fsS -X POST http://127.0.0.1:8765/capture \
  -H "Content-Type: application/json" -H "X-Capsule-Token: $token" \
  -d '{"url":"https://example.org/record/1","html":"<html><body><p>Name: John Doe</p></body></html>"}' \
  | tee capture.json
echo
grep -q '"extraction_pending":true' capture.json
echo "install smoke: OK"
