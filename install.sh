#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "${AGENTOPS_CORE_ROOT:-}" ]; then
  CORE_REQUIREMENT="$AGENTOPS_CORE_ROOT"
elif [ -f "$ROOT/../agentops/pyproject.toml" ]; then
  CORE_REQUIREMENT="$ROOT/../agentops"
else
  CORE_REQUIREMENT="codex-agentops @ git+https://github.com/jeeHwon/agentops.git"
fi

for command_name in npm uv; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: $command_name" >&2
    exit 1
  fi
done
# Pin the adapter to the Omnigent release whose parser, Databricks route and
# telemetry contract are covered by this repository's tests. The optional
# extras are required for Databricks authentication and native OTel spans.
OMNIGENT_VERSION="0.17.0"
uv tool install --force "omnigent[databricks,tracing]==${OMNIGENT_VERSION}"

# Omnigent 0.17's brokered Databricks route is validated against Codex
# 0.146.0. Install that version in an adapter-owned directory; the user's
# normal `codex` command and version remain unchanged.
AOPS_DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/agentops-omnigent"
npm install --prefix "$AOPS_DATA_ROOT/codex" @openai/codex@0.146.0
uv tool install --python 3.11 --force --reinstall --with "$CORE_REQUIREMENT" "$ROOT"

cat <<'EOF'
Omnigent Adapter 설치가 완료되었습니다.

1. Agent 목록: aops-omni list
2. Agent 로드: aops-omni load <agent-id> --version <version>
3. 로컬 Agent 변환: aops-omni prepare <agent-folder>
4. REPL 실행: aops-omni run <agent-folder>
5. Web UI 실행: aops-omni serve <agent-folder>
EOF
