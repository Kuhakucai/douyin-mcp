#!/usr/bin/env bash
# Installs douyin-mcp into a project-local virtual environment on macOS/Linux.
# It is idempotent and never overwrites an existing .env file.

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_root"

while (($#)); do
  case "$1" in
    -h|--help)
      echo "Usage: bash ./easy-install.sh"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

resolve_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && \
      "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

python_command="$(resolve_python)" || {
  echo "Python 3.11 or later is required. Install it and run this script again." >&2
  exit 1
}
venv_python="$project_root/.venv/bin/python"

echo "[1/5] Using Python: $($python_command --version)"
if [[ ! -x "$venv_python" ]]; then
  echo "[2/5] Creating .venv virtual environment..."
  "$python_command" -m venv .venv
else
  echo "[2/5] Reusing existing .venv virtual environment."
fi

echo "[3/5] Installing douyin-mcp runtime dependencies..."
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -e .

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[4/5] Created .env from .env.example."
else
  echo "[4/5] Keeping existing .env; it will not be overwritten."
fi

echo "[5/5] Initializing and running diagnostics..."
"$venv_python" -m douyin_creator_mcp.cli init
"$venv_python" -m douyin_creator_mcp.cli doctor

cat <<'EOF'

Installation complete. Next steps:
1. Read PLATFORM_COMPLIANCE.md and the current Douyin platform terms.
2. If you understand the risk and have the necessary authorization, run:
   ./.venv/bin/douyin-mcp acknowledge-platform-risk --yes
3. Add the mcp_config from the init output to your MCP client configuration.
4. For first login run: ./.venv/bin/douyin-mcp login --timeout 180
5. Video transcripts remain disabled after the default install. Ask the Agent to configure the optional local FFmpeg and faster-whisper dependencies if needed.
EOF
