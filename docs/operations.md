# Operations guide

This document describes how to run `nhk-easy-fetcher` on a schedule on your own machine. Scheduled jobs should write to a **user-local output tree** (for example `~/NHK-Easy`), use **Asia/Tokyo** for “today” semantics, and **stop after repeated authorization or source-contract failures** instead of retrying forever.

**Never log cookie values, `Authorization` headers, or full signed media URLs** (HLS manifests with `hdnts` query tokens). Redirect stdout/stderr to local log files with restrictive permissions (`chmod 600`).

## Recommended daily fetch

```console
nhk-easy fetch --date today --audio m4a --output ~/NHK-Easy \
  --auth cookie_jar \
  --cookie-jar ~/.nhk-easy-fetcher/auth/cookies.json
```

`--date today` resolves using the configured timezone (`Asia/Tokyo` by default). For “fetch the newest article regardless of calendar date”, use `fetch-latest` instead.

After a run, optionally verify artifacts and clean stale temp files:

```console
nhk-easy verify --output ~/NHK-Easy
nhk-easy cleanup --output ~/NHK-Easy --older-than 7
```

## Exit codes and failure-stop policy

| Code | Meaning | Scheduler action |
| --- | --- | --- |
| 0 | Success or skipped | Reset failure counter |
| 1 | Retryable error (network, transient 5xx) | Log and retry next schedule |
| 2 | Usage/config error | Fix configuration; do not auto-retry |
| 3 | Authorization required/changed | Increment failure counter; stop after threshold |
| 4 | Source contract changed | Increment failure counter; stop after threshold |
| 5 | Partial success | Log; treat as attention needed |

**Policy:** if three consecutive scheduled runs exit with code **3** or **4**, stop launching further fetches until you manually run `nhk-easy probe` (or `nhk-easy auth capture` for code 3) and fix the issue. The wrapper examples below implement this with a small counter file under `~/.nhk-easy-fetcher/scheduler/`.

## Shared wrapper logic (bash)

Save as `~/bin/nhk-easy-scheduled-fetch.sh` (adjust paths):

```bash
#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-$HOME/NHK-Easy}"
COOKIE_JAR="${COOKIE_JAR:-$HOME/.nhk-easy-fetcher/auth/cookies.json}"
LOG_DIR="${LOG_DIR:-$HOME/.nhk-easy-fetcher/logs}"
FAILURE_FILE="${FAILURE_FILE:-$HOME/.nhk-easy-fetcher/scheduler/consecutive_failures}"
MAX_FAILURES="${MAX_FAILURES:-3}"
TZ="${TZ:-Asia/Tokyo}"

mkdir -p "$LOG_DIR" "$(dirname "$FAILURE_FILE")"
chmod 700 "$(dirname "$FAILURE_FILE")" "$LOG_DIR"

failures=0
if [[ -f "$FAILURE_FILE" ]]; then
  failures=$(<"$FAILURE_FILE")
fi

if [[ "$failures" -ge "$MAX_FAILURES" ]]; then
  echo "Stopped after $failures consecutive auth/contract failures. See $LOG_DIR" >&2
  exit 0
fi

log="$LOG_DIR/fetch-$(date +%Y%m%d).log"
# Log exit code and summary only — never cookies or signed URLs.
set +e
nhk-easy fetch --date today --audio m4a --output "$OUTPUT_DIR" \
  --auth cookie_jar --cookie-jar "$COOKIE_JAR" >>"$log" 2>&1
code=$?
set -e

if [[ "$code" -eq 3 || "$code" -eq 4 ]]; then
  failures=$((failures + 1))
  echo "$failures" >"$FAILURE_FILE"
  chmod 600 "$FAILURE_FILE"
elif [[ "$code" -eq 0 ]]; then
  echo 0 >"$FAILURE_FILE"
  nhk-easy cleanup --output "$OUTPUT_DIR" --older-than 7 >>"$log" 2>&1 || true
fi

exit "$code"
```

## macOS launchd

`~/Library/LaunchAgents/com.example.nhk-easy-fetch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.nhk-easy-fetch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/bin/nhk-easy-scheduled-fetch.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TZ</key>
    <string>Asia/Tokyo</string>
    <key>OUTPUT_DIR</key>
    <string>/Users/YOU/NHK-Easy</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/YOU/.nhk-easy-fetcher/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOU/.nhk-easy-fetcher/logs/launchd.err.log</string>
</dict>
</plist>
```

Load: `launchctl load ~/Library/LaunchAgents/com.example.nhk-easy-fetch.plist`

## Linux systemd timer

`~/.config/systemd/user/nhk-easy-fetch.service`:

```ini
[Unit]
Description=NHK EASY daily fetch (personal)

[Service]
Type=oneshot
Environment=TZ=Asia/Tokyo
Environment=OUTPUT_DIR=%h/NHK-Easy
ExecStart=%h/bin/nhk-easy-scheduled-fetch.sh
```

`~/.config/systemd/user/nhk-easy-fetch.timer`:

```ini
[Unit]
Description=Daily NHK EASY fetch (07:30 Asia/Tokyo)

[Timer]
OnCalendar=*-*-* 07:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable: `systemctl --user daemon-reload && systemctl --user enable --now nhk-easy-fetch.timer`

## cron

```cron
# m h dom mon dow  command
TZ=Asia/Tokyo
30 7 * * * /home/YOU/bin/nhk-easy-scheduled-fetch.sh
```

Ensure `TZ=Asia/Tokyo` is set in the crontab or inside the wrapper so `--date today` matches JST publication days.

## Windows Task Scheduler

1. Create `C:\Users\YOU\bin\nhk-easy-scheduled-fetch.cmd`:

```bat
@echo off
set TZ=Asia/Tokyo
set OUTPUT_DIR=C:\Users\YOU\NHK-Easy
set COOKIE_JAR=C:\Users\YOU\.nhk-easy-fetcher\auth\cookies.json
set LOG_DIR=C:\Users\YOU\.nhk-easy-fetcher\logs
set FAILURE_FILE=C:\Users\YOU\.nhk-easy-fetcher\scheduler\consecutive_failures
set MAX_FAILURES=3

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set /a failures=0
if exist "%FAILURE_FILE%" set /p failures=<"%FAILURE_FILE%"
if %failures% GEQ %MAX_FAILURES% exit /b 0

nhk-easy fetch --date today --audio m4a --output "%OUTPUT_DIR%" ^
  --auth cookie_jar --cookie-jar "%COOKIE_JAR%" >> "%LOG_DIR%\fetch.log" 2>&1
set CODE=%ERRORLEVEL%

if %CODE%==3 goto bump
if %CODE%==4 goto bump
if %CODE%==0 echo 0> "%FAILURE_FILE%"
exit /b %CODE%

:bump
set /a failures=%failures%+1
echo %failures%> "%FAILURE_FILE%"
exit /b %CODE%
```

2. Task Scheduler → Create Task → Triggers: daily 07:30 → Actions: start `nhk-easy-scheduled-fetch.cmd` → “Run only when user is logged on” (cookie jar access).

## GitHub Actions

The repository’s `source-contract.yml` workflow is for **structural** site checks only. It does not download article bodies or audio, and must not upload NHK content artifacts. Personal scheduled fetching belongs on your machine, not in CI.

## Security checklist

- Cookie jar: `chmod 600` (Unix) or ACL-restricted (Windows).
- Logs: no cookie values, bearer tokens, or full `hdnts` URLs.
- Output tree: keep under your home directory; do not sync to public cloud folders without reviewing NHK terms.
