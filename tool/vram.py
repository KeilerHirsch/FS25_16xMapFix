#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  16x Map Fix -- cross-vendor GPU VRAM detection (Windows)
#  "The Man, The Mythos, The Legend : KeilerHirsch"   (GNU GPL v3 or later)
#
#  Reports the primary graphics card's physical video memory so the companion
#  mod's texture-streaming budget can be set from real hardware instead of a
#  guessed constant.
#
#  Primary source is the display-adapter class registry key -- a vendor-neutral
#  value that NVIDIA, AMD and Intel drivers all populate, and which (unlike WMI
#  Win32_VideoController.AdapterRAM) is NOT capped at 4 GB:
#
#    HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-...}\<NNNN>\
#        HardwareInformation.qwMemorySize
#
#  It is read for every adapter and the maximum is taken, which picks the
#  discrete GPU over an integrated one. The value may be stored as a REG_QWORD
#  (an int) or as a REG_BINARY blob (little-endian bytes); both are handled.
#  If the registry yields nothing, nvidia-smi is tried; if that also fails the
#  caller falls back to its own default.
"""Cross-vendor GPU VRAM detection for Windows (NVIDIA / AMD / Intel)."""

from __future__ import annotations

import subprocess  # nosec B404 -- only runs nvidia-smi with a fixed list-form argv (no shell); see nvidia_smi_vram_bytes

#: Windows "Display adapters" device class.
DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)

#: Preferred first (uncapped 64-bit), then the legacy 32-bit fallback.
_MEMORY_PROPERTIES = (
    "HardwareInformation.qwMemorySize",
    "HardwareInformation.MemorySize",
)

_BYTES_PER_GIB = 1024 ** 3
_BYTES_PER_MIB = 1024 ** 2
_NVIDIA_SMI_TIMEOUT_S = 10


def _coerce_positive_int(value: object) -> int | None:
    """Coerce a winreg value to a positive int.

    The memory size may arrive as an ``int`` (REG_QWORD/REG_DWORD) or as raw
    little-endian ``bytes`` (REG_BINARY) depending on the driver -- an integrated
    GPU on the same machine was observed storing it as REG_BINARY while the
    discrete card used REG_QWORD. Anything else, or a non-positive result, is
    treated as "unknown".
    """
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, (bytes, bytearray)):
        number = int.from_bytes(value, "little")
        return number if number > 0 else None
    return None


def registry_vram_bytes() -> int:
    """Largest adapter VRAM (bytes) recorded in the registry, or 0 if none.

    Returns 0 on non-Windows hosts (no ``winreg``) or if nothing is readable.
    """
    try:
        import winreg  # Windows-only; imported lazily so the module loads anywhere
    except ImportError:
        return 0

    best = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS_KEY) as root:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, index)
                except OSError:
                    break  # no more subkeys
                index += 1
                if not subkey_name.isdigit():  # adapters are 0000, 0001, ...
                    continue
                try:
                    with winreg.OpenKey(root, subkey_name) as adapter:
                        for prop in _MEMORY_PROPERTIES:
                            try:
                                raw, _ = winreg.QueryValueEx(adapter, prop)
                            except OSError:
                                continue
                            size = _coerce_positive_int(raw)
                            if size is not None:
                                best = max(best, size)
                                break  # prefer qwMemorySize over MemorySize
                except OSError:
                    continue
    except OSError:
        return 0
    return best


def nvidia_smi_vram_bytes() -> int:
    """Total VRAM (bytes) of the largest NVIDIA GPU via nvidia-smi, or 0."""
    try:
        result = subprocess.run(  # nosec B603 B607 -- nvidia-smi is a standard system tool resolved via PATH; fixed list-form argv, no shell, no untrusted input
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    best = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():  # value is in MiB
            best = max(best, int(line) * _BYTES_PER_MIB)
    return best


def detect_vram_bytes() -> int:
    """Best-effort physical VRAM of the primary GPU, in bytes (0 if unknown)."""
    return registry_vram_bytes() or nvidia_smi_vram_bytes()


def detect_vram_gib() -> float | None:
    """Detected VRAM in GiB, or ``None`` if it could not be determined."""
    raw = detect_vram_bytes()
    return raw / _BYTES_PER_GIB if raw else None


def recommended_budget_gib(
    vram_gib: float, headroom_gib: float = 2.0, floor_gib: float = 2.0
) -> float:
    """Texture-streaming budget for the given VRAM.

    Rounds the (slightly-under-nominal) reported VRAM to the nearest whole GiB,
    then reserves ``headroom_gib`` for the OS/desktop, never dropping below
    ``floor_gib``. An 8 GB card (~7.99 GiB reported) therefore yields 6 GiB.
    """
    return float(max(floor_gib, round(vram_gib) - headroom_gib))


if __name__ == "__main__":
    gib = detect_vram_gib()
    if gib is None:
        print("VRAM: not detected")
    else:
        print(f"VRAM: {gib:.2f} GiB  ->  recommended texture budget "
              f"{recommended_budget_gib(gib):.1f} GiB")
