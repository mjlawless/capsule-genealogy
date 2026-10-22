#!/usr/bin/env bash
# Build the double-click installers attached to each GitHub release:
#
#   installers/package-installers.sh <output dir>
#
#   Install-Capsule.cmd       Windows; CRLF line endings, or cmd.exe misreads it.
#   Install-Capsule-mac.zip   macOS; holds "Install Capsule.command". Zipped because Chrome
#                             drops the executable bit from a bare download, and Finder's
#                             unzip restores it.
#
# The popup's first-run screen links to them as releases/latest/download/<name>, so the names
# are a contract: keep them in step with SETUP_DOWNLOADS in extension/popup.js.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
out="${1:?usage: package-installers.sh <output dir>}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"

python3 - "$repo/installers/Install-Capsule.cmd" "$out/Install-Capsule.cmd" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
open(sys.argv[2], "w", encoding="utf-8", newline="\r\n").write(text)
PY

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$repo/installers/Install Capsule.command" "$work/"
chmod 755 "$work/Install Capsule.command"
rm -f "$out/Install-Capsule-mac.zip"
(cd "$work" && zip -q "$out/Install-Capsule-mac.zip" "Install Capsule.command")
echo "installers: $out/Install-Capsule.cmd, $out/Install-Capsule-mac.zip"
