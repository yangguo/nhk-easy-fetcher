#!/usr/bin/env bash
# One-click fetch of the newest NHK EASY article (text + audio).
# Usage:
#   ./scripts/fetch-latest.sh
#   OUTPUT_DIR=~/NHK-Easy AUDIO_MODE=m4a COOKIE_JAR=~/.nhk-easy-fetcher/auth/cookies.json ./scripts/fetch-latest.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/NHK-Easy}"
AUDIO_MODE="${AUDIO_MODE:-m4a}"
COOKIE_JAR="${COOKIE_JAR:-$HOME/.nhk-easy-fetcher/auth/cookies.json}"

case "$AUDIO_MODE" in
  off|m4a|mp3|manifest) ;;
  *)
    echo "Unknown AUDIO_MODE: $AUDIO_MODE (want off|m4a|mp3|manifest)" >&2
    exit 2
    ;;
esac

if [[ "$AUDIO_MODE" == "m4a" || "$AUDIO_MODE" == "mp3" ]] && ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install it first, e.g.: brew install ffmpeg" >&2
  exit 2
fi

if ! command -v nhk-easy >/dev/null 2>&1; then
  echo "nhk-easy not found, installing editable package..."
  python3 -m pip install -e "$ROOT_DIR"'[browser]'
fi

ensure_cookie_jar() {
  if [[ ! -f "$COOKIE_JAR" ]]; then
    mkdir -p "$(dirname "$COOKIE_JAR")"
    echo "Cookie jar not found, opening installed Chrome for one-time consent..." >&2
    if ! nhk-easy auth capture --cookie-jar "$COOKIE_JAR"; then
      echo "Cookie capture did not complete (see message above)." >&2
      exit 3
    fi
  fi

  if [[ ! -f "$COOKIE_JAR" ]]; then
    echo "Cookie jar still missing at $COOKIE_JAR" >&2
    exit 3
  fi

  chmod 600 "$COOKIE_JAR"

  if ! COOKIE_JAR="$COOKIE_JAR" python3 -c "import json, os; d=json.load(open(os.path.expanduser(os.environ['COOKIE_JAR']))); c=d.get('cookies') if isinstance(d.get('cookies'), dict) else {}; h=d.get('headers') if isinstance(d.get('headers'), dict) else {}; flat={k: v for k, v in d.items() if k not in ('cookies', 'headers') and not k.startswith('_')}; assert isinstance(d, dict) and (bool(c) or bool(h) or bool(flat))"; then
    echo "Cookie jar at $COOKIE_JAR looks empty or invalid. Removing it and capturing again..." >&2
    rm -f "$COOKIE_JAR"
    nhk-easy auth capture --cookie-jar "$COOKIE_JAR" || exit 3
    chmod 600 "$COOKIE_JAR"
  fi
}

echo "Fetching newest article to $OUTPUT_DIR (audio=$AUDIO_MODE)..."
fetch_args=(fetch-latest --audio "$AUDIO_MODE" --output "$OUTPUT_DIR")
if [[ "$AUDIO_MODE" != "off" ]]; then
  ensure_cookie_jar
  fetch_args+=(--auth cookie_jar --cookie-jar "$COOKIE_JAR")
fi
nhk-easy "${fetch_args[@]}"
echo "Done. Newest output under $OUTPUT_DIR/articles:"
ls -t "$OUTPUT_DIR/articles" 2>/dev/null | head -5 || true
