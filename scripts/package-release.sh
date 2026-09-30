#!/usr/bin/env bash
# Build the download: dist/margin-<version>.zip, everything a user needs and
# nothing else (no venv, tests, dev tooling, caches). Checked for keys.
#
#   ./scripts/package-release.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import json; print(json.load(open('extension/manifest.json'))['version'])")
NAME="margin-$VERSION"
STAGE=$(mktemp -d)/$NAME
mkdir -p "$STAGE" dist

git ls-files -z extension sidecar scripts install.sh README.md LICENSE NOTICE ISSUES.md engines.example.toml keys.env.example \
  | while IFS= read -r -d '' f; do
      case "$f" in
        sidecar/tests/*|scripts/e2e/*|*/__pycache__/*|*.test.js) continue ;;
      esac
      mkdir -p "$STAGE/$(dirname "$f")"
      cp -p "$f" "$STAGE/$f"
    done
mkdir -p "$STAGE/docs" && cp -p docs/*.png "$STAGE/docs/"

# Never ship a key: the same patterns the sidecar scrubs from its log.
if grep -rIEl '(AIza[0-9A-Za-z_-]{30,}|AQ\.Ab8[A-Za-z0-9_-]{10,}|sk-(ant|proj|or)-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{30,}|ghp_[A-Za-z0-9]{30,})' "$STAGE"; then
  echo "A key-like string is in the release; refusing to package." >&2
  exit 1
fi

rm -f "dist/$NAME.zip"
(cd "$(dirname "$STAGE")" && zip -qr -X "$OLDPWD/dist/$NAME.zip" "$NAME")
echo "dist/$NAME.zip  $(du -h "dist/$NAME.zip" | cut -f1)  $(unzip -l "dist/$NAME.zip" | tail -1 | awk '{print $2}') files"
