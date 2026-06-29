#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${1:-${repo_root}/.venv}"

python3 -m venv "${venv}"
"${venv}/bin/python" -m pip install --editable "${repo_root}"
echo "Ready. Run: ${venv}/bin/openai-tunnel-kit --help"
