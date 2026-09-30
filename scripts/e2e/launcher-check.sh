#!/usr/bin/env bash
# The login launcher, checked without touching the real one (macOS only).
#
#   ./scripts/e2e/launcher-check.sh
#
# Builds the launcher the way the installer does, in a temporary folder, with a
# stand-in start.sh that only records that it ran. Then:
#   1. the app is one macOS can recognise again: a bundle id, hidden from the
#      Dock, a valid signature (else each start is a new app to macOS and it asks
#      for Desktop access again);
#   2. a sidecar that is alive but slow to answer is left alone (it used to be
#      "started" again, which read start.sh on the Desktop and made macOS ask);
#   3. with nothing on the port, the sidecar is started.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO="$(pwd)"
TMP="$(mktemp -d)"
trap 'kill "${SLOW:-}" 2>/dev/null || true; rm -rf "$TMP"' EXIT

HOME="$TMP/home"; mkdir -p "$HOME"
ROOT="$TMP/margin"; mkdir -p "$ROOT/scripts"
MARK="$TMP/started"
printf '#!/bin/sh\necho started >> %q\n' "$MARK" > "$ROOT/scripts/start.sh"
chmod +x "$ROOT/scripts/start.sh"
LABEL="digital.saksham.margin"
APP="$HOME/Applications/Margin Sidecar.app"
LOG="$TMP/sidecar.log"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
eval "$(sed -n '/^build_app() {/,/^}/p' "$REPO/scripts/login-item.sh")"
build_app "$TMP/notes" >/dev/null 2>&1

fail=0
check() { if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1"; fail=1; fi; }
plist="$APP/Contents/Info.plist"
check "bundle id" '[ "$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$plist" 2>/dev/null)" = "$LABEL.launcher" ]'
check "hidden from the Dock" '[ "$(/usr/libexec/PlistBuddy -c "Print :LSUIElement" "$plist" 2>/dev/null)" = "true" ]'
check "valid signature" 'codesign --verify --strict "$APP" 2>/dev/null'

# Alive but slow: accepts the connection, answers after 5 seconds.
python3 - "$PORT" <<'PY' &
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(8)
while True:
    c, _ = s.accept(); time.sleep(5)
    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"); c.close()
PY
SLOW=$!
for _ in $(seq 50); do nc -z 127.0.0.1 "$PORT" 2>/dev/null && break; sleep 0.1; done
osascript "$HOME/.margin/sidecar-launcher.applescript" >/dev/null; sleep 1
check "a slow sidecar is left alone" '[ ! -e "$MARK" ]'

kill "$SLOW"; wait "$SLOW" 2>/dev/null || true; SLOW=
osascript "$HOME/.margin/sidecar-launcher.applescript" >/dev/null; sleep 1
check "nothing on the port: started" '[ -e "$MARK" ]'

exit $fail
