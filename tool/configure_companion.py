#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  16x Map Fix -- VRAM auto-config for the companion mod
#  "The Man, The Mythos, The Legend : KeilerHirsch"   (GNU GPL v3 or later)
#
#  Detects this PC's graphics-card memory and writes the companion mod's
#  settings file, so the in-game texture-streaming budget is set from real
#  hardware with no manual editing. Cross-vendor (NVIDIA / AMD / Intel) via the
#  vram module. This is the "true auto" half of 16x Map Fix: Lua cannot
#  read physical VRAM in-game, so the tool writes the value the mod then reads.
"""Detect VRAM and write the 16x Map Fix companion settings file."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr  # nosec B406 -- quoteattr ESCAPES our own generated attribute value for safe XML output; it never parses untrusted input

import vram

#: KEEP IN SYNC with the companion mod's settings filename in
#: companion-mod/FS25_16xMapFix/scripts/textureBudget.lua.
COMPANION_MOD_NAME = "FS25_16xMapFix"

#: Where FS25 keeps its user profile (and therefore ``modSettings/``). Documents
#: can be redirected (OneDrive) or localised, so several candidates are probed.
_PROFILE_CANDIDATES = (
    "Documents/My Games/FarmingSimulator2025",
    "OneDrive/Documents/My Games/FarmingSimulator2025",
    "OneDrive/Dokumente/My Games/FarmingSimulator2025",
    "Dokumente/My Games/FarmingSimulator2025",
)

_HELP_TEXT = (
    "Auto-written by 16x Map Fix from your detected graphics-card memory. "
    "vramGiB = how much VRAM FS25 may use for textures; delete this file to let "
    "the tool (or the mod's default) set it again."
)


def find_profile_dir() -> Path | None:
    """Locate the FS25 user-profile directory, or ``None`` if not present."""
    home = Path.home()
    for relative in _PROFILE_CANDIDATES:
        candidate = home / relative
        if candidate.is_dir():
            return candidate
    return None


def settings_path(profile_dir: Path) -> Path:
    """Path of the companion settings file inside a profile directory."""
    return profile_dir / "modSettings" / f"{COMPANION_MOD_NAME}.xml"


def write_settings(budget_gib: float, profile_dir: Path) -> Path:
    """Write the companion settings file and return its path.

    The structure matches what the mod's Lua reads: a root
    ``<textureStreamingBudget>`` element with a ``vramGiB`` attribute.
    """
    out = settings_path(profile_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n'
        f'<textureStreamingBudget vramGiB="{budget_gib:.1f}" '
        f"help={quoteattr(_HELP_TEXT)}/>\n",
        encoding="utf-8",
    )
    return out


def main() -> int:
    print("\n  16x Map Fix -- VRAM auto-config\n")

    raw = vram.detect_vram_bytes()
    if not raw:
        print("  Could not detect graphics-card memory automatically.")
        print("  The companion mod will fall back to its built-in default, or you")
        print(f"  can set vramGiB by hand in <FS25 profile>/modSettings/{COMPANION_MOD_NAME}.xml")
        return 1

    vram_gib = raw / 1024 ** 3
    budget_gib = vram.recommended_budget_gib(vram_gib)
    print(f"  Detected VRAM : {vram_gib:.2f} GiB")
    print(f"  Texture budget: {budget_gib:.1f} GiB  (VRAM minus 2 GiB headroom)")

    profile_dir = find_profile_dir()
    if profile_dir is None:
        print("\n  Could not find your FS25 profile folder under your user directory.")
        print("  Start Farming Simulator 25 once so it is created, then run this again.")
        return 1

    out = write_settings(budget_gib, profile_dir)
    print(f"\n  Wrote: {out}")
    print("  Install the companion mod and it will use this on the next game start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
