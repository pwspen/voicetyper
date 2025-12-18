#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_DIR}"

# Use system Python for GTK gi bindings unless overridden.
export UV_PYTHON="${UV_PYTHON:-/usr/bin/python3}"

# Keep logging unbuffered so tray notifications show promptly.
export PYTHONUNBUFFERED=1

exec uv run --python "${UV_PYTHON}" python -m voicetyper.tray "$@"
