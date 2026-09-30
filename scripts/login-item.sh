#!/usr/bin/env bash
# Keep the Margin sidecar running without a terminal (macOS).
#
#   ./scripts/login-item.sh install     set up, and keep it running from now on
#   ./scripts/login-item.sh uninstall   remove (a sidecar already running keeps running)
#   ./scripts/login-item.sh status
#
# Why an app and not a plain LaunchAgent: macOS refuses background services
# access to Desktop, Documents and Downloads ("Operation not permitted"). If
# Margin or your vault lives there, a LaunchAgent cannot even read start.sh.
# So a tiny hidden app, "Margin Sidecar", starts the sidecar instead. macOS
# asks once whether it may access your Desktop; that grant covers this one app
# and that one folder, nothing broader.
#
# A LaunchAgent opens the app at login and then once a minute. Each time, the
# app checks whether the sidecar answers and starts it only if it does not, so
# it also comes back by itself after a crash or after you stop a terminal copy.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LABEL="digital.saksham.margin"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="$HOME/Applications/Margin Sidecar.app"
LOG="$HOME/.margin/sidecar.log"
PORT="${MARGIN_PORT:-8766}"
DOMAIN="gui/$(id -u)"

build_app() {
  local vault="$1"
  local path_env="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin"
  local script="$HOME/.margin/sidecar-launcher.applescript"
  mkdir -p "$HOME/Applications" "$HOME/.margin"
  cat >"$script" <<APPLESCRIPT
on run
	do shell script "if ! /usr/bin/curl -s --max-time 2 http://127.0.0.1:$PORT/health >/dev/null; then export MARGIN_VAULT_PATH=" & quoted form of "$vault" & "; export MARGIN_PORT=$PORT; export PATH=" & quoted form of "$path_env" & "; nohup " & quoted form of "$ROOT/scripts/start.sh" & " >> " & quoted form of "$LOG" & " 2>&1 & fi"
end run
APPLESCRIPT
  rm -rf "$APP"
  osacompile -o "$APP" "$script"
  # Hidden: no Dock icon, no menu bar, it only ever runs for a moment.
  /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$APP/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $LABEL.launcher" "$APP/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string $LABEL.launcher" "$APP/Contents/Info.plist"
  # Editing Info.plist breaks the signature osacompile made; re-sign so macOS
  # keeps recognising it as the same app (and so keeps its Desktop permission).
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1
}

case "${1:-status}" in
  install)
    # The notes folder: as given, else the one you chose in install.sh, else the
    # one the running sidecar uses, so re-installing never moves your notes.
    VAULT="${MARGIN_VAULT_PATH:-}"
    [ -n "$VAULT" ] || VAULT="$(cat "$HOME/.margin/notes-folder" 2>/dev/null || true)"
    [ -n "$VAULT" ] || VAULT="$(curl -s --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null \
      | python3 -c 'import sys, json; print(json.load(sys.stdin).get("vault") or "")' 2>/dev/null || true)"
    [ -n "$VAULT" ] || VAULT="$HOME/MarginNotes"
    # A sidecar already running keeps serving the code it started with, and the
    # login item only starts one when none answers: stop it, so the one started
    # below is this version. A note it was writing is picked up again on start.
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"vault"'; then
      OLD=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)
      if [ -n "$OLD" ]; then
        echo "Stopping the running sidecar ($OLD), so this version starts."
        kill $OLD 2>/dev/null || true
        sleep 2
      fi
    fi
    build_app "$VAULT"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/open</string><string>-g</string><string>$APP</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
</dict></plist>
PLIST
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed. Notes → $VAULT"
    echo "App: $APP   Log: $LOG"
    echo "The first time it starts the sidecar, macOS asks whether \"Margin Sidecar\" may access"
    echo "your Desktop folder. Click Allow."
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    rm -rf "$APP"
    echo "Removed. A sidecar that is already running keeps running until you quit it or log out."
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then echo "Installed and loaded."; else echo "Not installed."; fi
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null; then
      echo "Sidecar answering on :$PORT."
    else
      echo "Sidecar not answering on :$PORT. Last log lines:"
      tail -5 "$LOG" 2>/dev/null || true
    fi
    ;;
  *) echo "usage: $0 install|uninstall|status" >&2; exit 2 ;;
esac
