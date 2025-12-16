from __future__ import annotations

from dataclasses import dataclass
from typing import List

import sounddevice as sd


@dataclass
class InputDevice:
    index: int
    name: str
    default_samplerate: float
    channels: int


def list_input_devices() -> List[InputDevice]:
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append(
                InputDevice(
                    index=idx,
                    name=dev.get("name", f"Device {idx}"),
                    default_samplerate=dev.get("default_samplerate", 16000),
                    channels=dev.get("max_input_channels", 1),
                )
            )
    return devices


def default_input_device_index() -> int | None:
    try:
        default_index = sd.default.device[0]
        return default_index if default_index is not None and default_index >= 0 else None
    except Exception:
        return None
