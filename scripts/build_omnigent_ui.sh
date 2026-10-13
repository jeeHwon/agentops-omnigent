#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/agentops-omnigent"
OUTPUT="${AOPS_OMNIGENT_WEB_DIST:-$DATA_ROOT/web-ui}"
OMNIGENT_BIN="$(command -v omnigent || true)"

case "$OUTPUT" in
  ""|"/"|"$HOME"|"$DATA_ROOT")
    echo "ERROR: unsafe Web UI output path: $OUTPUT" >&2
    exit 1
    ;;
esac

if [ -z "$OMNIGENT_BIN" ]; then
  echo "ERROR: omnigent CLI를 먼저 설치하세요." >&2
  exit 1
fi

OMNIGENT_PYTHON="$(head -1 "$OMNIGENT_BIN" | sed 's/^#!//')"
BASE_DIST="${AOPS_OMNIGENT_BASE_DIST:-$($OMNIGENT_PYTHON -c 'from omnigent.server.app import _WEB_UI_DIST; print(_WEB_UI_DIST)')}"
if [ ! -f "$BASE_DIST/index.html" ]; then
  echo "ERROR: Omnigent Web UI를 찾을 수 없습니다: $BASE_DIST" >&2
  exit 1
fi

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"
cp -R "$BASE_DIST/." "$OUTPUT/"
cp "$ROOT/omnigent-web/agentops-projects.js" "$OUTPUT/agentops-projects.js"

python3 - "$OUTPUT/index.html" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
script = '<script type="module" src="./agentops-projects.js"></script>'
if script not in text:
    if "</body>" not in text:
        raise SystemExit("ERROR: Omnigent index.html에 </body>가 없습니다.")
    text = text.replace("</body>", f"{script}</body>")
path.write_text(text, encoding="utf-8")
PY
echo "AgentOps Omnigent Web UI 준비 완료: $OUTPUT"
