#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# ============================================================================
#  BigMap Optimizer  --  oversized-map density downscaler
#  "The MAN, The MYTH, The LEGEND; KeilerHirsch"
# ============================================================================
#
#  Makes oversized Farming Simulator 25 maps (16x / 32x, density maps larger
#  than the engine's safe 8192px tile-registry ceiling) load and sync in
#  multiplayer on modest hardware, by downscaling every oversized density map
#  to 8192px in place -- fruit data included, with the field state preserved.
#
#  It never deletes data and never touches map geometry, scripts or gameplay:
#  it only re-samples the density/info layers that overflow the GIANTS engine's
#  C++ tile-registration table (the root cause of the "Error in allocReg" /
#  "TiledBitmapOperationCompiler failed" crash on large maps).
#
#  Copyright (C) 2026  KeilerHirsch
#
#  This program is free software: you can redistribute it and/or modify it
#  under the terms of the GNU General Public License as published by the Free
#  Software Foundation, either version 3 of the License, or (at your option)
#  any later version.
#
#  This program is distributed in the hope that it will be useful, but WITHOUT
#  ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
#  FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
#  more details.  You should have received a copy of the GNU General Public
#  License along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#  Bundles `grleconvert` (Paint-a-Farm/grleconvert, MIT License) for GDM/GRLE
#  <-> PNG conversion. GPLv3 is compatible with bundled MIT-licensed tools.
#
#  A note on the map's memory declarations
#  ---------------------------------------
#  A map's config XML declares textureMemoryUsage / vertexBufferMemoryUsage /
#  indexBufferMemoryUsage for GPU allocation planning. We deliberately leave
#  these untouched: the vertex/index buffers describe the terrain MESH, which
#  this tool never changes, and only the texture budget tracks the density
#  layers. After shrinking the layers the original values become conservative
#  OVER-estimates, which is safe; rewriting them risks an UNDER-estimate we
#  cannot verify per subsystem. Correctness beats a cosmetic edit.
# ============================================================================
"""BigMap Optimizer -- FS25 oversized-map density downscaler.

Usage:
    python bigmap_optimizer.py <map.zip> [output.zip]

Drop a map .zip on the accompanying launcher, or pass its path on the command
line. A fixed copy is written next to the original as ``<name>_fixed.zip`` --
the input is never modified.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# --- Configuration ----------------------------------------------------------

#: The engine's safe density-map edge length. 4x maps ship at this size and
#: load fine in multiplayer; anything larger overflows the tile registry.
SAFE_SIZE = 8192

#: Hard ceiling on any density-map edge we will decode. Comfortably above a
#: 64x map (65536px) yet bounded, so a malicious archive cannot make Pillow
#: allocate an unbounded image (decompression-bomb defence -- see M2).
MAX_EDGE = 70_000

#: Density/info layers live here inside a map archive.
DATA_SUBDIR = "maps/data"

GDM_EXT = ".gdm"
GRLE_EXT = ".grle"
PNG_EXT = ".png"

#: Guardrails against hostile archives (zip bombs / path traversal).
MAX_ARCHIVE_MEMBERS = 100_000
MAX_TOTAL_UNCOMPRESSED = 8 * 1024 ** 3  # 8 GiB is well above any real FS25 map.

# Bound Pillow's own decompression-bomb guard to our ceiling rather than
# disabling it. MAX_EDGE**2 still admits legitimate 64x maps.
Image.MAX_IMAGE_PIXELS = MAX_EDGE * MAX_EDGE

BANNER = r"""
  ============================================================
   BigMap Optimizer  --  oversized-map density downscaler
   "The MAN, The MYTH, The LEGEND; KeilerHirsch"
  ============================================================
"""


class FixerError(Exception):
    """Raised for any recoverable, user-facing failure."""


@dataclass(frozen=True)
class GdmHeader:
    """The subset of the GDM header we need to inspect and re-encode faithfully.

    Reference: Paint-a-Farm/grleconvert docs/GDM_FORMAT.md. Two magics exist:

    * ``!MDF`` (legacy): dim_log2@0x04, chunk_log2@0x05, num_channels@0x07,
      num_compression_ranges@0x08; boundary bytes follow at 0x09. No type-index
      mappings.
    * ``"MDF`` (versioned): dim_log2@0x08, chunk_log2@0x09, num_channels@0x0B,
      num_compression_ranges@0x0C, type_index_channels@0x0D, 2 reserved bytes,
      then ``3 * type_index_channels`` optional mapping bytes, then the boundary
      bytes.

    Edge length is ``2 ** (dim_log2 + chunk_log2)``.
    """

    edge: int
    num_channels: int
    compress_at: int | None  # first channel of the second compression range

    @classmethod
    def read(cls, path: Path) -> "GdmHeader":
        # 128 bytes covers the fixed header plus any realistic type-index table.
        with path.open("rb") as fh:
            head = fh.read(128)
        if len(head) < 16 or head[0:3] not in (b"!MD", b'"MD') or head[3] != 0x46:
            raise FixerError(f"{path.name}: not a recognised GDM file")

        if head[0] == 0x21:  # '!' -> legacy !MDF layout
            dim_log2, chunk_log2 = head[0x04], head[0x05]
            num_channels, num_ranges = head[0x07], head[0x08]
            boundaries_off = 0x09
        else:  # '"' -> versioned "MDF layout
            dim_log2, chunk_log2 = head[0x08], head[0x09]
            num_channels, num_ranges = head[0x0B], head[0x0C]
            type_index_channels = head[0x0D]
            # Fixed header ends at 0x10; optional type-index mappings precede
            # the compression boundaries.
            boundaries_off = 0x10 + 3 * type_index_channels

        edge = 1 << (dim_log2 + chunk_log2)
        if num_ranges > 1:
            if boundaries_off >= len(head):
                raise FixerError(f"{path.name}: GDM header larger than expected")
            compress_at: int | None = head[boundaries_off]
        else:
            compress_at = None
        return cls(edge=edge, num_channels=num_channels, compress_at=compress_at)


# --- Density-layer processing -----------------------------------------------


def _run_grleconvert(grleconvert: Path, args: list[str]) -> str:
    """Run grleconvert and return its stdout, raising on failure."""
    result = subprocess.run(
        [str(grleconvert), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FixerError(
            f"grleconvert failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _downscale_png_file(png: Path) -> bool:
    """Downscale a square PNG in place with nearest-neighbour.

    Returns True if the image was oversized and therefore changed.

    Density pixels are packed bit fields (fruit type, growth stage, ...), not
    colours -- averaging them would corrupt the data, so NEAREST is mandatory.
    """
    with Image.open(png) as img:
        width, height = img.size
        if width > MAX_EDGE or height > MAX_EDGE:
            raise FixerError(f"{png.name}: {width}x{height} exceeds the {MAX_EDGE}px ceiling")
        if width != height:
            raise FixerError(f"{png.name}: non-square density map {width}x{height} is unsupported")
        if width <= SAFE_SIZE:
            return False
        if width & (width - 1) != 0:
            # A power-of-two edge (8192, 16384, 32768) marks a tiled density/
            # info layer -- exactly what overflows the engine's tile registry.
            # A non-power-of-two edge (8193, 4097, 2049, ...) marks a HEIGHTMAP
            # / DEM: a 2^n+1 vertex grid loaded outside the tile registry, which
            # is NOT the overflow source. Resampling it to 2^n would corrupt the
            # terrain geometry, so heightmaps are left exactly as they are.
            return False
        mode = img.mode
        resized = img.resize((SAFE_SIZE, SAFE_SIZE), Image.NEAREST)
    if resized.mode != mode:  # the packed layout must survive the resize
        raise FixerError(f"{png.name}: mode changed on resize ({mode} -> {resized.mode})")
    resized.save(png)
    return True


def _resize_compiled_layer(path: Path, grleconvert: Path, header: GdmHeader | None) -> bool:
    """Downscale a compiled .gdm/.grle layer in place via a PNG round-trip.

    ``header`` is the parsed GDM header for .gdm files (used to re-encode with
    the exact channel/compression layout) or ``None`` for .grle files, where
    the format is inferred from the .grle output extension.

    For .gdm the header already tells us the edge length, so oversized maps are
    detected before the (costly) decode. Returns True if the layer was changed.
    """
    if header is not None and header.edge <= SAFE_SIZE:
        return False  # small GDM: skip without decoding at all

    png = path.with_name(path.name + ".fixer.png")
    _run_grleconvert(grleconvert, [str(path), str(png)])
    try:
        if not _downscale_png_file(png):
            return False  # GRLE (or GDM) that turned out to be small enough
        if header is not None:  # GDM: re-encode with faithful parameters
            args = [str(png), str(path), "--channels", str(header.num_channels)]
            if header.compress_at is not None:
                args += ["--compress-at", str(header.compress_at)]
        else:  # GRLE: format inferred from the .grle output extension
            args = [str(png), str(path)]
        _run_grleconvert(grleconvert, args)
        return True
    finally:
        png.unlink(missing_ok=True)


def fix_density_layers(data_dir: Path, grleconvert: Path) -> list[str]:
    """Resize every oversized density layer under ``data_dir`` in place.

    Returns the names of the layers that were changed, for the report.
    """
    changed: list[str] = []
    for path in sorted(data_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            if path.suffix == PNG_EXT:
                if _downscale_png_file(path):
                    changed.append(path.name)
            elif path.suffix == GDM_EXT:
                if _resize_compiled_layer(path, grleconvert, GdmHeader.read(path)):
                    changed.append(path.name)
            elif path.suffix == GRLE_EXT:
                if _resize_compiled_layer(path, grleconvert, None):
                    changed.append(path.name)
        except FixerError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any layer failure clearly
            raise FixerError(f"failed to process {path.name}: {exc}") from exc
    return changed


# --- Archive handling -------------------------------------------------------


def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, refusing traversal and zip bombs."""
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise FixerError("archive has too many members; refusing to extract")
        total = 0
        dest_root = dest.resolve()
        for member in members:
            total += member.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise FixerError("archive is unreasonably large; refusing to extract")
            target = (dest / member.filename).resolve()
            if dest_root != target and dest_root not in target.parents:
                raise FixerError(f"unsafe path in archive: {member.filename}")
        zf.extractall(dest)


def _repack(src_dir: Path, out_zip: Path) -> None:
    """Repack a directory tree into a zip, deterministically ordered."""
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(src_dir).as_posix())


def _find_grleconvert() -> Path:
    """Locate the bundled grleconvert executable next to this script."""
    here = Path(__file__).resolve().parent
    name = "grleconvert.exe" if sys.platform.startswith("win") else "grleconvert"
    for candidate in (here / name, here / "bin" / name):
        if candidate.is_file():
            return candidate
    raise FixerError(
        "grleconvert not found next to the fixer. Place the grleconvert "
        "binary (or bin/grleconvert) in the tool directory."
    )


# --- Orchestration ----------------------------------------------------------


def fix_map(map_zip: Path, out_zip: Path | None = None) -> Path:
    """Fix a single map archive, returning the path of the fixed copy."""
    if not map_zip.is_file() or map_zip.suffix.lower() != ".zip":
        raise FixerError(f"not a .zip map archive: {map_zip}")
    grleconvert = _find_grleconvert()
    out_zip = out_zip or map_zip.with_name(f"{map_zip.stem}_fixed.zip")

    with tempfile.TemporaryDirectory(prefix="fs25fixer_") as tmp:
        work = Path(tmp)
        _safe_extract(map_zip, work)

        data_dir = work / DATA_SUBDIR
        if not data_dir.is_dir():
            raise FixerError(
                f"no '{DATA_SUBDIR}' folder inside the archive -- is this an FS25 map?"
            )

        print(f"  Scanning density layers in {DATA_SUBDIR} ...")
        changed = fix_density_layers(data_dir, grleconvert)
        if not changed:
            print("  Nothing oversized found -- this map is already engine-safe.")
        else:
            print(f"  Resized {len(changed)} oversized layer(s) to {SAFE_SIZE}px:")
            for name in changed:
                print(f"    - {name}")

        print(f"  Repacking -> {out_zip.name}")
        _repack(work, out_zip)
    return out_zip


def main(argv: list[str]) -> int:
    print(BANNER)
    if not argv:
        print("Usage: python bigmap_optimizer.py <map.zip> [output.zip]")
        return 2
    try:
        out = fix_map(Path(argv[0]), Path(argv[1]) if len(argv) > 1 else None)
    except FixerError as exc:
        print(f"\n  ERROR: {exc}")
        return 1
    print(f"\n  Done. Fixed map written to:\n    {out}\n")
    print("  Apply it to YOUR legally-owned copy of the map. Happy farming.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
