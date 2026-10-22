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
