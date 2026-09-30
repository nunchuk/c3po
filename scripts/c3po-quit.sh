#!/usr/bin/env bash
# Stop c3po. Closing the browser is NOT enough -- that only drops the WebSocket;
# the server keeps running (measured ~41 MB RSS) and holds the port.
#
#   bash scripts/c3po-quit.sh
#
# Sends SIGTERM so the server runs its shutdown handler, which stops the
# suggester and terminates the subtitle-overlay child process. SIGKILL would
# leave the overlay window orphaned on screen.
#
# English-only output: this also runs from C-3PO Quit.app without a terminal,
# and launchd's C locale makes non-ASCII byte processing fail.
set -uo pipefail

PORT="${C3PO_PORT:-8989}"
# Seconds to wait for a clean shutdown before escalating to SIGKILL. A clean
# shutdown now takes ~1s. It used to take 15s or hang outright while a page was
# connected (a /suggestions handler blocked uvicorn's graceful shutdown), which
# is why this is generous: SIGKILL skips the shutdown handler.
GRACE=30
# Keep in sync with config.overlay_visible_flag in c3po/config.py.
FLAG="$HOME/Library/Application Support/meeting-translator/overlay-visible"

info() { printf '%s\n' "$*"; }

# Match the server by the port it listens on, not by process name: a `pgrep -f
# c3po.server` also matches the shell that launched it (and this very script).
pids="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"

if [ -z "$pids" ]; then
  info "c3po is not running (nothing listening on ${PORT})."
  # Still clear the overlay flag: if the server was killed with -9 previously,
  # the flag file can survive and make a fresh start think the overlay is on.
  rm -f "$FLAG" 2>/dev/null
  exit 0
fi

for pid in $pids; do
  info "Stopping c3po server (PID ${pid})..."
  kill -TERM "$pid" 2>/dev/null || continue

  waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$GRACE" ]; then
      info "Did not exit in ${GRACE}s; sending SIGKILL."
      kill -9 "$pid" 2>/dev/null
      sleep 1
      break
    fi
    sleep 1
    waited=$((waited + 1))
    # Past 5s something is holding the shutdown up; say so rather than sit
    # silent until the SIGKILL.
    [ "$waited" -eq 5 ] && info "  still shutting down..."
  done
  if ! kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$GRACE" ]; then
    info "  exited cleanly after ${waited}s."
  fi
done

# The overlay is a child of the server and is normally terminated by its
# shutdown handler. Sweep up anything left over -- after a SIGKILL the handler
# never ran, and an orphaned always-on-top window cannot be closed from the UI.
overlay_pids="$(pgrep -f "c3po\.overlay" 2>/dev/null || true)"
if [ -n "$overlay_pids" ]; then
  info "Cleaning up orphaned subtitle overlay..."
  # shellcheck disable=SC2086
  kill -TERM $overlay_pids 2>/dev/null
  sleep 1
  # shellcheck disable=SC2086
  kill -9 $overlay_pids 2>/dev/null || true
fi

rm -f "$FLAG" 2>/dev/null

if lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  printf 'ERROR: something is still listening on %s.\n' "${PORT}" >&2
  exit 1
fi

info "c3po stopped."
