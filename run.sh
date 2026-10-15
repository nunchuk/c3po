#!/usr/bin/env bash
# Launch c3po: the FastAPI server (embedded WhisperLiveKit + reply panel),
# then open the browser. Run from the repo root.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "No .venv found. Create it first:" >&2
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip3 install -r requirements.txt" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
  echo "No .env found. Copy .env.example to .env and fill in AWS_BEARER_TOKEN_BEDROCK." >&2
  exit 1
fi

DISPLAY_HOST="${C3PO_DISPLAY_HOST:-localhost}"
PORT="${C3PO_PORT:-8989}"
URL="http://${DISPLAY_HOST}:${PORT}"

# Open the browser once the server is up (best effort).
( sleep 6 && command -v open >/dev/null && open "$URL" ) &

echo "Starting c3po on ${URL}"
echo "First run downloads the Whisper model."
exec python3 -m c3po.server
