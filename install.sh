#!/bin/sh
# Capsule installer for macOS and Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.sh | sh
#
# What it does, in order:
#   1. installs `uv` (which brings its own Python) if you do not have it;
#   2. downloads the latest Capsule release into ~/Capsule/app (an upgrade replaces only the
#      program; your archive lives elsewhere and is never touched);
#   3. runs `capsule setup --no-key` (no questions; add your Anthropic key later in the
#      extension, or never: Capsule archives pages without one);
#   4. on macOS, starts Capsule now and at every login (a per-user LaunchAgent, no admin);
#      elsewhere, starts it now;
#   5. checks that it is running and tells you where the Chrome extension folder is.
#
# Settings (environment variables, all optional):
#   CAPSULE_HOME            where to install (default: ~/Capsule)
#   CAPSULE_VERSION         a release tag such as v0.2.0 (default: the latest release)
#   CAPSULE_INSTALL_SOURCE  install from this local folder instead of downloading (testing)
#   CAPSULE_AUTOSTART=0     do not register the login item; just start Capsule now
#   CAPSULE_PORT            backend port, for testing only (the extension expects 8765)
set -eu

REPO="mjlawless/capsule-genealogy"
HOME_DIR="${CAPSULE_HOME:-$HOME/Capsule}"
APP="$HOME_DIR/app"
PORT="${CAPSULE_PORT:-8765}"
LABEL="io.capsule.serve"

say() { printf '%s\n' "$*"; }
fail() { printf 'Capsule install failed: %s\n' "$*" >&2; exit 1; }

say "Installing Genealogy Capsule into $APP"

# 1. uv -----------------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
  else
    say "Installing uv (the tool that runs Capsule)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null || fail "could not install uv"
    PATH="$HOME/.local/bin:$PATH"
  fi
fi
UV="$(command -v uv)" || fail "uv is not on PATH after installing it"

# 2. the program --------------------------------------------------------------------------
mkdir -p "$HOME_DIR"
HOME_DIR="$(cd "$HOME_DIR" && pwd -P)"
APP="$HOME_DIR/app"

# One install at a time per folder: two overlapping runs would swap each other's files and
# clean up each other's rollback copies (Codex review). `mkdir` is atomic; the lock is released
# on every way out (the EXIT trap here, then rollback, then the normal end).
LOCK="$HOME_DIR/.install-lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  fail "another Capsule install is already running in $HOME_DIR. If none is, remove the folder $LOCK and run this again."
fi
release_lock() { rmdir "$LOCK" 2>/dev/null || true; }
trap release_lock EXIT

STAGE="$HOME_DIR/app.new.$$"
mkdir -p "$STAGE"
# A staging folder left by an earlier, interrupted run is not anyone's any more (we hold the lock).
for leftover in "$HOME_DIR"/app.new "$HOME_DIR"/app.new.*; do
  if [ -e "$leftover" ] && [ "$leftover" != "$STAGE" ]; then mv "$leftover" "$HOME_DIR/app.stale.$$.$(basename "$leftover")"; fi
done
if [ -n "${CAPSULE_INSTALL_SOURCE:-}" ]; then
  say "Copying Capsule from $CAPSULE_INSTALL_SOURCE"
  (cd "$CAPSULE_INSTALL_SOURCE" && tar -cf - --exclude ./.git --exclude ./.venv --exclude ./data --exclude '*.db' .) \
    | (cd "$STAGE" && tar -xf -)
else
  TAG="${CAPSULE_VERSION:-}"
  if [ -z "$TAG" ]; then
    TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)"
  fi
  [ -n "$TAG" ] || fail "could not find the latest Capsule release"
  say "Downloading Capsule $TAG..."
  curl -fsSL "https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz" \
    | tar -xzf - -C "$STAGE" --strip-components=1 || fail "could not download Capsule $TAG"
fi
[ -f "$STAGE/pyproject.toml" ] || fail "the download does not look like Capsule"

# A custom archive folder (CAPSULE_DATA_DIR) must reach EVERY way Capsule starts: this run,
# the login item, and later upgrades run without the variable set. It is remembered beside
# the program and written as .env where each launcher starts (Codex review).
SAVED_DATA_DIR="$HOME_DIR/data-dir.txt"
# What the CURRENT installation uses, so a failed upgrade restarts it on its own archive.
PREVIOUS_DATA_DIR=""
if [ -f "$SAVED_DATA_DIR" ]; then PREVIOUS_DATA_DIR="$(cat "$SAVED_DATA_DIR")"; fi
if [ -z "${CAPSULE_DATA_DIR:-}" ] && [ -f "$SAVED_DATA_DIR" ]; then
  CAPSULE_DATA_DIR="$(cat "$SAVED_DATA_DIR")"
fi
if [ -n "${CAPSULE_DATA_DIR:-}" ]; then
  case "$CAPSULE_DATA_DIR" in /*) ;; *) CAPSULE_DATA_DIR="$PWD/$CAPSULE_DATA_DIR" ;; esac
  # Spelled canonically (no `..`, `.` or symlinks), so the check below sees the real place.
  # The folder is created first (Capsule would create it anyway) so it can be resolved.
  mkdir -p "$CAPSULE_DATA_DIR" || fail "cannot create the archive folder $CAPSULE_DATA_DIR"
  CAPSULE_DATA_DIR="$(cd "$CAPSULE_DATA_DIR" && pwd -P)"
  export CAPSULE_DATA_DIR
fi

# Never inside the program folders: an upgrade swaps those out and deletes the old copy, so an
# archive kept there would be deleted with it (Codex review). Checked BEFORE anything moves.
if [ -n "${CAPSULE_DATA_DIR:-}" ]; then
  case "$CAPSULE_DATA_DIR/" in
    "$HOME_DIR"/app/*|"$HOME_DIR"/app.*)
      fail "your archive folder ($CAPSULE_DATA_DIR) is inside Capsule's program folder, which upgrades replace. Move it somewhere else (for example $HOME_DIR/archive) and set CAPSULE_DATA_DIR to the new place." ;;
  esac
fi

# Whatever setting put it there (a .env, a legacy ./data, a launcher's working folder), an
# archive found INSIDE the current program folder would be deleted with the old program. Refuse
# rather than guess where it belongs (Codex review).
if [ -d "$APP" ] && [ -n "$(find "$APP" -name capsule.db -print 2>/dev/null | head -n 1)" ]; then
  fail "an archive (capsule.db) is stored inside $APP, which an upgrade replaces. Move that folder out of $APP and set CAPSULE_DATA_DIR to its new place, then run this again."
fi

# Every health probe is bounded: something that accepts the connection and never answers must
# not hang the installer past its deadlines (Codex review).
healthy() { curl -fsS --connect-timeout 2 --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }
listeners() { lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true; }
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# What has been done so far, so a failure at ANY later point can be undone (see rollback).
STOPPED=""
SWAPPED=""
PREVIOUS=""
ROLLING_BACK=""

# Upgrades: stop the Capsule that is running now, so the health check below can only ever be
# answered by the NEW one; refuse if something that is not Capsule holds the port.
stop_capsule() {
  if [ "$(uname -s)" = "Darwin" ] && [ -f "$PLIST" ]; then
    # The login item is shared by name; only unload it if it runs THIS installation.
    other="$(plutil -extract ProgramArguments.3 raw -o - "$PLIST" 2>/dev/null)"
    if [ "$other" = "$APP" ]; then
      launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && STOPPED=1 || true
    elif [ -n "$other" ] && [ ! -e "$other" ]; then
      # Left behind by a copy that has since been deleted: it can start nothing and guards no
      # archive, so it is simply replaced (found on a real Windows upgrade, v0.2.1).
      launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
      rm -f "$PLIST"
      say "Removed an old login item left by a Capsule copy that no longer exists."
    else
      fail "another copy of Capsule, in ${other:-an unknown folder}, is set to start at login ($PLIST), and it keeps its own archive. To switch to this installation, remove that login item first (launchctl bootout gui/$(id -u)/$LABEL; rm \"$PLIST\"), then run the installer again."
    fi
  fi
  for pid in $(listeners); do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
    case "$cmd" in
      # Only OUR installation is stopped. A Capsule run from anywhere else (a developer
      # checkout, another install) keeps its archive next to it, and replacing it with this
      # one would quietly open an empty archive instead (Codex review).
      # "$APP/" with its separator: a sibling such as app-dev is NOT this installation.
      # ...and only Capsule's server itself: something else that merely mentions the folder
      # (say, a file server pointed at it) is not ours to stop; the port check below refuses.
      *"$APP/"*capsule*serve*) kill "$pid" 2>/dev/null && STOPPED=1 || true ;;
      *capsule*serve*)
        fail "another Capsule is already running from a different folder (process $pid). Stop it first; its archive is not touched." ;;
    esac
  done
  i=0
  while [ -n "$(listeners)" ] || healthy; do
    i=$((i + 1))
    [ "$i" -le 20 ] || return 1
    sleep 1
  done
}

# One way to start Capsule by hand that always means THIS installation: it starts in
# CAPSULE_HOME (where the custom-archive .env lives) on the installed port (Codex review).
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
LAUNCHER="$HOME_DIR/start-capsule.sh"

# Values go into XML, and a real folder name can contain & < > (Codex review).
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

start_capsule() {
  started=""
  # Owner-only: `capsule serve` prints the pairing token, and its output goes to this log
  # (Codex review). Created that way before either launch path writes to it.
  (umask 077 && mkdir -p "$HOME_DIR/logs" && : >> "$HOME_DIR/logs/serve.log")
  chmod 700 "$HOME_DIR/logs"
  chmod 600 "$HOME_DIR/logs/serve.log"
  if [ "$(uname -s)" = "Darwin" ] && [ "${CAPSULE_AUTOSTART:-1}" != "0" ]; then
    AGENTS="$HOME/Library/LaunchAgents"
    mkdir -p "$AGENTS"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$(xml "$UV")</string><string>run</string><string>--project</string><string>$(xml "$APP")</string>
    <string>capsule</string><string>serve</string><string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml "$HOME_DIR")</string>
$(if [ -n "${CAPSULE_DATA_DIR:-}" ]; then
  # Only a real value: an EMPTY variable would stop the .env setting from loading (Codex review).
  printf '  <key>EnvironmentVariables</key><dict>\n'
  printf '    <key>CAPSULE_DATA_DIR</key><string>%s</string>\n' "$(xml "$CAPSULE_DATA_DIR")"
  printf '  </dict>\n'
fi)
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$(xml "$HOME_DIR/logs/serve.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$HOME_DIR/logs/serve.log")</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
      started="login item"
    else
      printf 'Note: could not register Capsule to start at login; starting it now instead.\n' >&2
    fi
  fi
  if [ -z "$started" ]; then
    # Fully detached: every descriptor goes to the log and the shell is replaced by the
    # server, so nothing holds the terminal (with `curl | sh`, a held pipe never closes).
    # Started directly, never through the launcher file: if writing that file is what failed,
    # the rollback must still be able to start the previous Capsule.
    (cd "$HOME_DIR" && exec nohup "$UV" run --project "$APP" capsule serve --port "$PORT") \
      </dev/null >"$HOME_DIR/logs/serve.log" 2>&1 &
    started="now"
  fi
}

wait_for_capsule() {
  i=0
  until healthy; do
    i=$((i + 1))
    [ "$i" -le 60 ] || return 1
    sleep 1
  done
}

# From the moment anything is stopped, ANY failure (a failed command under `set -e`, a write to
# a full disk, an interrupted run) ends here: the previous program is put back and started
# again, so you are never left without a working Capsule (Codex review).
rollback() {
  [ -z "$ROLLING_BACK" ] || return 0
  ROLLING_BACK=1
  trap - EXIT
  if [ -n "${1:-}" ]; then printf 'Capsule install failed: %s\n' "$1" >&2; fi
  # Keyed on the BACKUP existing, not on the swap completing: if moving the new copy into place
  # is what failed, the old one is still set aside and must come back (Codex review).
  # The previous Capsule restarts on ITS archive, not on the one this run was asked to use; the
  # saved setting is only written once the new install is confirmed running (Codex review).
  if [ -n "$PREVIOUS_DATA_DIR" ]; then
    CAPSULE_DATA_DIR="$PREVIOUS_DATA_DIR"
    export CAPSULE_DATA_DIR
    printf '%s\n' "$PREVIOUS_DATA_DIR" > "$SAVED_DATA_DIR" 2>/dev/null || true
  else
    unset CAPSULE_DATA_DIR
    rm -f "$SAVED_DATA_DIR"
  fi
  # .env exactly as it was before this run touched it (kept aside just before the write).
  if [ -n "${ENV_BACKUP:-}" ]; then
    if [ -n "${ENV_EXISTED:-}" ] && [ -f "$ENV_BACKUP" ]; then
      mv "$ENV_BACKUP" "$HOME_DIR/.env"
    elif [ -z "${ENV_EXISTED:-}" ]; then
      rm -f "$HOME_DIR/.env"  # this run created it; there was none before
    fi
  fi
  rm -f "$HOME_DIR/.env.new.$$"
  if [ -n "$PREVIOUS" ] && [ -e "$PREVIOUS" ]; then
    printf 'Putting the previous Capsule back...\n' >&2
    stop_capsule || true
    if [ -e "$APP" ]; then mv "$APP" "$HOME_DIR/app.failed.$$"; fi
    mv "$PREVIOUS" "$APP"
    start_capsule
    wait_for_capsule && printf 'The previous Capsule is running again.\n' >&2
  elif [ -n "$STOPPED" ] && [ -e "$APP" ]; then
    printf 'Starting the existing Capsule again...\n' >&2
    start_capsule
    wait_for_capsule && printf 'The existing Capsule is running again.\n' >&2
  fi
  release_lock
  exit 1
}
trap 'rollback ""' EXIT

stop_capsule || fail "port $PORT is in use by another program; close it and run this again"

if [ -e "$APP" ]; then PREVIOUS="$HOME_DIR/app.previous.$$"; mv "$APP" "$PREVIOUS"; fi
mv "$STAGE" "$APP"
SWAPPED=1

{
  printf '#!/bin/sh\n# Start Capsule (written by install.sh).\n'
  printf 'cd %s && exec %s run --project %s capsule serve --port %s\n' \
    "$(sq "$HOME_DIR")" "$(sq "$UV")" "$(sq "$APP")" "$PORT"
} > "$LAUNCHER" || rollback "could not write $LAUNCHER"
chmod +x "$LAUNCHER" || rollback "could not make $LAUNCHER runnable"

say "Preparing Capsule (first time: a minute or two)..."
"$UV" sync --project "$APP" --frozen --quiet || rollback "could not install Capsule's dependencies"

# 3. setup --------------------------------------------------------------------------------
# From the folder the backend starts in, so setup reads the same .env and archive and shows
# the token the running backend will actually use (Codex review).
(cd "$HOME_DIR" && CAPSULE_INSTALLER=1 "$UV" run --project "$APP" capsule setup --no-key) || rollback "capsule setup did not finish"

# 4. start it -----------------------------------------------------------------------------
start_capsule

# 5. check --------------------------------------------------------------------------------
wait_for_capsule || rollback "Capsule did not start; see $HOME_DIR/logs/serve.log"

# Only now, with the new Capsule running, is the archive setting saved for later starts. This
# run and its login item already have it from the environment (Codex review).
if [ -n "${CAPSULE_DATA_DIR:-}" ]; then
  ENV_FILE="$HOME_DIR/.env"
  # Kept until the end, so a failure from here restores it exactly (see rollback).
  ENV_EXISTED=""
  if [ -f "$ENV_FILE" ]; then cp -p "$ENV_FILE" "$ENV_FILE.previous.$$"; ENV_EXISTED=1; fi
  ENV_BACKUP="$ENV_FILE.previous.$$"
  printf '%s\n' "$CAPSULE_DATA_DIR" > "$SAVED_DATA_DIR" || rollback "could not write $SAVED_DATA_DIR"
  # Single-quoted and escaped for dotenv: unquoted, a ` #` in a real folder name starts a
  # comment and the rest of the path is silently dropped (Codex review).
  dotenv_value="$(printf '%s' "$CAPSULE_DATA_DIR" | sed -e 's/\\/\\\\/g' -e "s/'/\\\\'/g")"
  # Only the archive line changes; every other setting in .env (a key, extension IDs) is
  # kept. Written to a temporary file and moved into place, so it is never half-written.
  # Owner-only from the first byte: .env can hold an API key, and the temporary copy must not be
  # readable by other users even for a moment (Codex review).
  old_umask="$(umask)"
  umask 077
  {
    if [ -f "$ENV_FILE" ]; then grep -Ev '^[[:space:]]*(export[[:space:]]+)?CAPSULE_DATA_DIR[[:space:]]*=' "$ENV_FILE" || true; fi
    printf "CAPSULE_DATA_DIR='%s'\n" "$dotenv_value"
  } > "$ENV_FILE.new.$$" || rollback "could not write $ENV_FILE"
  umask "$old_umask"
  chmod 600 "$ENV_FILE.new.$$" || rollback "could not protect $ENV_FILE"
  mv "$ENV_FILE.new.$$" "$ENV_FILE" || rollback "could not write $ENV_FILE"
fi
trap release_lock EXIT  # done: nothing left to roll back, but the lock is still released
if [ -n "${ENV_BACKUP:-}" ]; then rm -f "$ENV_BACKUP"; fi

# The new program runs; earlier copies of the PROGRAM (never the archive) can go. Belt and
# braces: a copy holding a capsule.db is kept, whatever the checks above believed.
for old in "$HOME_DIR"/app.previous.* "$HOME_DIR"/app.stale.* "$HOME_DIR"/app.failed.*; do
  [ -e "$old" ] || continue
  if [ -n "$(find "$old" -name capsule.db -print 2>/dev/null | head -n 1)" ]; then
    say "Kept $old: it contains an archive (capsule.db)."
  else
    rm -rf "$old"
  fi
done
release_lock

say ""
say "Capsule is running (http://127.0.0.1:$PORT)."
if [ "$started" = "login item" ]; then
  say "It will also start by itself whenever you log in."
else
  say "To start it again later: \"$LAUNCHER\""
fi
say ""
say "Already added Capsule to Chrome (from the Chrome Web Store)? Then you're done: click its"
say "icon and it connects by itself. Otherwise, the last step, in Google Chrome:"
say "  1. Open chrome://extensions and turn on Developer mode (top right)."
say "  2. Click 'Load unpacked' and choose this folder:"
say "       $APP/extension"
say "  3. Pin the Capsule icon, open a record page, and click Capture."
say ""
say "No Anthropic key yet? That's fine: pages are archived now and can be read later."
