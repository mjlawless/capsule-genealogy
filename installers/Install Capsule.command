#!/bin/sh
# Genealogy Capsule installer for macOS: double-click to run. It runs the same one-line install as the
# README (https://github.com/mjlawless/capsule-genealogy#install-macos-or-windows) in this
# Terminal window, so you can watch what it does.
#
# macOS asks before opening a downloaded script that isn't signed. If it says it can't verify
# this file: open System Settings > Privacy & Security, scroll down, and click "Open Anyway".
echo "Installing Genealogy Capsule..."
echo
# Downloaded first, not piped: a failed download piped into sh runs nothing and still "succeeds"
# (Codex review), which would tell the user Capsule was installed when it wasn't.
script="$(mktemp)" || exit 1
trap 'rm -f "$script"' EXIT
if curl -LsSf https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.sh -o "$script" \
  && [ -s "$script" ] && sh "$script"; then
  echo
  echo "Done. You can close this window and go back to Chrome."
else
  echo
  echo "The install did not finish; the messages above say why."
  echo "Help: https://github.com/mjlawless/capsule-genealogy/issues"
  exit 1
fi
