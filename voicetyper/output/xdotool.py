from __future__ import annotations

import shlex
import subprocess


def send_text(text: str):
    if not text:
        return
    # Using --clearmodifiers to avoid stuck modifier keys
    cmd = ["xdotool", "type", "--clearmodifiers", text]
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        raise RuntimeError("xdotool not found on PATH; please install it.")


def send_key(key: str):
    if not key:
        return
    cmd = ["xdotool", "key", "--clearmodifiers", key]
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        raise RuntimeError("xdotool not found on PATH; please install it.")
