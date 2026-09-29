#!/usr/bin/env bash
# Open c3po: probe the server, start it if needed, then open the browser.
#
#   bash scripts/c3po-open.sh
#
# This is the whole launcher logic. C-3PO.app is only a thin .app wrapper around
# it (see make-overlay-app.sh), so changing behaviour means editing this file --
# no need to regenerate the app.
#
# Output is deliberately English-only: the .app runs this without a terminal and
# pipes it through osascript on failure, where a C locale plus non-ASCII bytes
# gives "illegal byte sequence".
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${C3PO_PORT:-8989}"
DISPLAY_HOST="${C3PO_DISPLAY_HOST:-localhost}"
URL="http://${DISPLAY_HOST}:${PORT}"
SERVER_LOG="$HOME/.c3po-server.log"

# The server loads the Whisper model before binding the port; measured at 80s on
# this machine (not just the first run -- the model is re-loaded every start).
# Poll instead of sleeping a fixed amount, and keep generous headroom: a machine
# under load is slower, and a timeout here looks like "the app is broken".
READY_TIMEOUT=180

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

# Non-blocking macOS notification. Best-effort: never let a missing/denied
# notification stop the launch.
notify() {
  osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true
}

# --noproxy '*' is required: with a proxy configured, curl to localhost returns
# HTTP 000 and the server looks dead when it is fine.
probe() { curl -s -m 3 -o /dev/null --noproxy '*' "http://127.0.0.1:${PORT}/"; }

# ---------- 1. already running? ----------
if probe; then
  info "c3po already running on ${URL}"
else
  # ---------- 2. preflight ----------
  PY="$REPO/.venv/bin/python"
  [ -x "$PY" ] || die "No .venv in $REPO. Create it:
  python3 -m venv .venv && source .venv/bin/activate && pip3 install -r requirements.txt"

  [ -f "$REPO/.env" ] || die "No .env in $REPO. Copy .env.example to .env and set AWS_BEARER_TOKEN_BEDROCK."

  if ! grep -qE '^AWS_BEARER_TOKEN_BEDROCK=.+' "$REPO/.env"; then
    die "AWS_BEARER_TOKEN_BEDROCK is empty in $REPO/.env (Bedrock API key required)."
  fi

  # WhisperLiveKit shells out to a real ffmpeg binary; without it the recorder
  # connects and then dies with ffmpeg_not_found and no transcript appears.
  command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg not found in PATH. Install it: brew install ffmpeg"

  # Something on the port that is not answering our probe: bail out rather than
  # start a second server that cannot bind.
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    die "Port ${PORT} is in use but not answering. Check: lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
  fi

  # ---------- 3. start the server ----------
  info "Starting c3po server (loads the Whisper model, ~80s)..."

  # Tell the user something is happening. The app is LSUIElement, so there is no
  # Dock icon, no window and no terminal -- an 80s silent wait is indistinguishable
  # from "the app did not launch". Notifications are non-blocking, unlike a dialog.
  notify "C-3PO" "Starting up -- the page opens in about 80 seconds."

  cd "$REPO" || die "cannot cd to $REPO"

  # .venv/bin/python is a universal binary. From a terminal it runs arm64, but a
  # GUI-launched app can inherit an x86_64 preference and then start the very
  # same binary in x86_64 mode -- where the arm64-only wheels (pydantic_core,
  # ctranslate2, ...) die with "incompatible architecture (have 'arm64', need
  # 'x86_64')". So pin the arm64 slice explicitly.
  #
  # Detect the hardware with sysctl, NOT `uname -m`: under Rosetta `uname -m`
  # answers "x86_64", which is false precisely in the case that needs the fix.
  # hw.optional.arm64 reports the real machine either way.
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] \
     && command -v arch >/dev/null 2>&1; then
    run_py=(arch -arm64 "$PY")
  else
    run_py=("$PY")
  fi

  PYTHONPATH="$REPO" nohup "${run_py[@]}" -m c3po.server >"$SERVER_LOG" 2>&1 &
  server_pid=$!

  waited=0
  until probe; do
    if ! kill -0 "$server_pid" 2>/dev/null; then
      printf 'ERROR: server exited during startup. Last lines of %s:\n\n' "$SERVER_LOG" >&2
      tail -15 "$SERVER_LOG" >&2
      exit 1
    fi
    if [ "$waited" -ge "$READY_TIMEOUT" ]; then
      printf 'ERROR: server did not become ready in %ss. Last lines of %s:\n\n' \
        "$READY_TIMEOUT" "$SERVER_LOG" >&2
      tail -15 "$SERVER_LOG" >&2
      exit 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
  info "Server ready after ${waited}s (log: $SERVER_LOG)"
fi

# ---------- 4. open the browser ----------
open "$URL" || die "could not open $URL"

# ---------- 5. what the user still has to do by hand ----------
# Recording cannot be started programmatically -- browsers require a user
# gesture for microphone capture.
cat <<EOF

c3po is open at ${URL}

In the page:
  1. Open settings and set Microphone to "BlackHole 2ch"
  2. Press record
  3. In Zoom/Teams, set the speaker to "BlackHole 2ch"

EOF
