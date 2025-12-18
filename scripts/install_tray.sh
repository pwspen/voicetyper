#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
AUTOSTART_DIR="${HOME}/.config/autostart"
DESKTOP_FILE="${AUTOSTART_DIR}/voicetyper.desktop"
LAUNCHER="${REPO_DIR}/scripts/voicetyper-tray.sh"
TARGET_PYTHON="${UV_PYTHON:-/usr/bin/python3}"

check_uv() {
    if ! command -v uv >/dev/null 2>&1; then
        echo "uv is required (https://github.com/astral-sh/uv). Please install it and re-run." >&2
        exit 1
    fi
}

ensure_tray_deps() {
    if ! python3 - <<'PY'
import sys
try:
    import gi
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3  # noqa: F401
    except Exception:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: F401
    gi.require_version("Notify", "0.7")
    from gi.repository import Gtk, Notify  # noqa: F401
except Exception:
    sys.exit(1)
PY
    then
        echo "Installing GTK/AppIndicator bindings and notify tools (sudo required)..." >&2
        sudo apt-get update
        sudo apt-get install -y python3-gi gir1.2-ayatanaappindicator3-0.1 libnotify-bin xdotool
    fi
}

write_autostart() {
    mkdir -p "${AUTOSTART_DIR}"
    cat > "${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=Voicetyper
Comment=Voicetyper tray controller
Exec=${LAUNCHER}
X-GNOME-Autostart-enabled=true
Terminal=false
EOF
    chmod +x "${LAUNCHER}"
    echo "Autostart entry written to ${DESKTOP_FILE}"
}

prime_env() {
    cd "${REPO_DIR}"
    if [[ -x ".venv/bin/python" ]]; then
        current_py="$(readlink -f .venv/bin/python)"
        target_py="$(readlink -f "${TARGET_PYTHON}")"
        if [[ "${current_py}" != "${target_py}" ]]; then
            echo "Recreating venv with ${TARGET_PYTHON} for gi compatibility..."
            rm -rf .venv
        fi
    fi
    UV_PYTHON="${TARGET_PYTHON}" uv sync --python "${TARGET_PYTHON}"
}

main() {
    check_uv
    ensure_tray_deps
    prime_env
    write_autostart
    echo "Tray install complete. You can start it now with: ${LAUNCHER}"
}

main "$@"
