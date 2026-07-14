#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# ============================================================================
#  16x Map Fix  --  oversized-map density downscaler
#  "The Man, The Mythos, The Legend : KeilerHirsch"
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
"""16x Map Fix -- FS25 oversized-map density downscaler.

Usage:
    python mapfix.py <map.zip> [output.zip]

Drop a map .zip on the accompanying launcher, or pass its path on the command
line. A fixed copy is written next to the original as ``<name>_fixed.zip`` --
the input is never modified.
"""

from __future__ import annotations

import shutil

# subprocess only ever runs our own bundled grleconvert (list-argv, no shell);
# see _run_grleconvert for the full rationale. bandit B404 acknowledged.
import subprocess  # nosec B404
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

#: Beyond this the sum (dim_log2 + chunk_log2) in a GDM header describes an
#: edge that dwarfs even a 64x map -- treat such a header as corrupt/hostile
#: rather than computing an astronomically large shift from it.
MAX_TOTAL_LOG2 = 24

#: Density/info layers live here inside a map archive.
DATA_SUBDIR = "maps/data"

GDM_EXT = ".gdm"
GRLE_EXT = ".grle"
PNG_EXT = ".png"

#: Guardrails against hostile archives (zip bombs / path traversal).
MAX_ARCHIVE_MEMBERS = 100_000
#: Upper bound on the summed *uncompressed* size we will extract. A 32x map is
#: genuinely multi-gigabyte (that is the whole reason this tool exists), so the
#: bound stays generous; the real disk-fill defence is the free-space preflight
#: in _safe_extract, which also accounts for the repacked copy.
MAX_TOTAL_UNCOMPRESSED = 8 * 1024**3  # 8 GiB covers a 32x map with headroom.

#: grleconvert is a third-party native binary; a corrupt payload could make it
#: hang. Bound every call so a hostile archive cannot wedge the tool forever
#: (mirrors the timeout vram.py already uses on its own subprocess call).
GRLECONVERT_TIMEOUT_S = 300

# Bound Pillow's own decompression-bomb guard to our ceiling rather than
# disabling it. MAX_EDGE**2 still admits legitimate 64x maps.
Image.MAX_IMAGE_PIXELS = MAX_EDGE * MAX_EDGE

BANNER = r"""
  ============================================================
   16x Map Fix  --  oversized-map density downscaler
   "The Man, The Mythos, The Legend : KeilerHirsch"
  ============================================================
"""


class FixerError(Exception):
    """Raised for any recoverable, user-facing failure."""


def _warn(msg: str) -> None:
    """Emit a non-fatal warning to stderr without aborting the run."""
    print(f"  WARNING: {msg}", file=sys.stderr)


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

    ``edge`` is derived solely from the header bytes and is used to skip the
    (costly) decode of a GDM that is already small enough. Every layer we do
    decode is cross-checked against its real pixel dimensions in
    ``_downscale_png_file`` (via ``expected_edge``), which surfaces any header
    mis-parse on the layers that actually matter.
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

        total_log2 = dim_log2 + chunk_log2
        if total_log2 > MAX_TOTAL_LOG2:
            raise FixerError(f"{path.name}: implausible GDM dimensions in header")
        edge = 1 << total_log2
        if num_ranges > 1:
            if boundaries_off >= len(head):
                raise FixerError(f"{path.name}: GDM header larger than expected")
            compress_at: int | None = head[boundaries_off]
        else:
            compress_at = None
        return cls(edge=edge, num_channels=num_channels, compress_at=compress_at)


# --- Density-layer processing -----------------------------------------------


def _run_grleconvert(grleconvert: Path, args: list[str]) -> str:
    """Run grleconvert and return its stdout, raising on failure.

    The executable is our own bundled binary (resolved next to this script,
    never via PATH or the extracted archive) and every argument is either an
    absolute path we built or a small integer, so there is no shell and no
    argument-injection surface -- bandit B603 is a non-issue here.
    """
    try:
        # trusted bundled exe, list-argv, no shell (see docstring) -> B603 non-issue
        result = subprocess.run(  # nosec B603
            [str(grleconvert), *args],
            capture_output=True,
            text=True,
            errors="replace",  # never let a non-UTF8 diagnostic mask the real error
            check=False,
            timeout=GRLECONVERT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise FixerError(
            f"grleconvert timed out after {GRLECONVERT_TIMEOUT_S}s ({' '.join(args)})"
        ) from exc
    if result.returncode != 0:
        raise FixerError(
            f"grleconvert failed ({' '.join(args)}):\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def _downscale_png_file(png: Path, expected_edge: int | None = None) -> bool:
    """Downscale a square, oversized PNG in place with nearest-neighbour.

    Returns True if the image was oversized and therefore changed, False if it
    was left untouched (small, or a heightmap). Density pixels are packed bit
    fields (fruit type, growth stage, ...), not colours -- averaging them would
    corrupt the data, so NEAREST is mandatory.

    ``formats=["PNG"]`` pins Pillow to the PNG decoder: the bytes come straight
    from an untrusted archive, and without this a member merely *named* ``.png``
    could be routed through any of Pillow's other plugins (see security H1).
    """
    with Image.open(png, formats=["PNG"]) as img:
        width, height = img.size
        # Not oversized: leave every small file alone -- including small,
        # non-square overlays/icons that legitimately live under maps/data and
        # must not abort the whole archive (finding H1/python).
        if width <= SAFE_SIZE:
            return False
        # A non-power-of-two edge (8193, 4097, 2049, ...) marks a HEIGHTMAP /
        # DEM: a 2^n+1 vertex grid loaded outside the tile registry, which is
        # NOT the overflow source. Resampling it to 2^n would corrupt the
        # terrain geometry, so heightmaps are left exactly as they are.
        if width & (width - 1) != 0:
            # A 2^n+1 edge (8193, 4097, ...) is a genuine heightmap/DEM and is
            # correctly left alone. Any OTHER non-power-of-two oversized layer is
            # unexpected -- warn so the "it's a heightmap" assumption is
            # falsifiable by the user instead of silently trusted.
            if (width - 1) & (width - 2) != 0:
                _warn(
                    f"{png.name}: oversized {width}px layer is neither a power of "
                    "two nor a 2^n+1 heightmap; leaving it unchanged. If the map "
                    "still overflows in multiplayer, this layer may be the cause."
                )
            return False
        # Genuinely oversized, power-of-two tiled density/info layer from here.
        if width > MAX_EDGE:
            raise FixerError(
                f"{png.name}: {width}x{height} exceeds the {MAX_EDGE}px ceiling"
            )
        if width != height:
            raise FixerError(
                f"{png.name}: oversized non-square density map {width}x{height} "
                "is unsupported"
            )
        if expected_edge is not None and width != expected_edge:
            _warn(
                f"{png.name}: GDM header claimed {expected_edge}px but the decoded "
                f"layer is {width}px; trusting the decoded size."
            )
        mode = img.mode
        resized = img.resize((SAFE_SIZE, SAFE_SIZE), Image.Resampling.NEAREST)
    if resized.mode != mode:  # the packed layout must survive the resize
        raise FixerError(
            f"{png.name}: mode changed on resize ({mode} -> {resized.mode})"
        )
    resized.save(png)
    return True


def _resize_compiled_layer(
    path: Path, grleconvert: Path, header: GdmHeader | None
) -> bool:
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
    try:
        _run_grleconvert(grleconvert, [str(path), str(png)])
        expected_edge = header.edge if header is not None else None
        if not _downscale_png_file(png, expected_edge=expected_edge):
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
        suffix = path.suffix.lower()  # archives may carry .PNG / .GDM (finding H2)
        try:
            if suffix == PNG_EXT:
                if _downscale_png_file(path):
                    changed.append(path.name)
            elif suffix == GDM_EXT:
                if _resize_compiled_layer(path, grleconvert, GdmHeader.read(path)):
                    changed.append(path.name)
            elif suffix == GRLE_EXT:
                if _resize_compiled_layer(path, grleconvert, None):
                    changed.append(path.name)
        except FixerError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any layer failure clearly
            raise FixerError(f"failed to process {path.name}: {exc}") from exc
    return changed


# --- Archive handling -------------------------------------------------------


def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, refusing traversal, bombs and corruption."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise FixerError(
            f"{archive.name}: not a readable .zip archive ({exc})"
        ) from exc
    with zf:
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
        # Refuse to start if the disk cannot hold the extracted tree plus the
        # repacked copy that follows (~twice the uncompressed size).
        free = shutil.disk_usage(dest).free
        if free < total * 2:
            raise FixerError(
                f"not enough free disk space: need ~{total * 2 // 1024**2} MiB, "
                f"have {free // 1024**2} MiB free"
            )
        try:
            zf.extractall(dest)
        except (zipfile.BadZipFile, OSError) as exc:
            raise FixerError(f"{archive.name}: archive is corrupt ({exc})") from exc


def _repack(src_dir: Path, out_zip: Path) -> None:
    """Repack a directory tree into a zip, deterministically ordered.

    Written to a sibling ``.tmp`` file and atomically moved into place only once
    the whole archive is complete, so an interruption or a disk-full error
    mid-write can never leave a truncated/corrupt ``_fixed.zip`` behind.
    """
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    tmp = out_zip.with_name(out_zip.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, path.relative_to(src_dir).as_posix())
        tmp.replace(out_zip)  # atomic on the same filesystem
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
    if out_zip is None:
        out_zip = map_zip.with_name(f"{map_zip.stem}_fixed.zip")
    if out_zip.resolve() == map_zip.resolve():
        raise FixerError(
            "output path must differ from the input; refusing to overwrite the original map"
        )

    # Extract next to the output so the free-space preflight in _safe_extract
    # measures the drive the fixed map is actually written to (not the system
    # temp drive), and so extraction + the repacked copy share one filesystem
    # (making the final move atomic).
    with tempfile.TemporaryDirectory(prefix="fs25fixer_", dir=out_zip.parent) as tmp:
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
    """CLI entry point: fix the map given as the first argument."""
    print(BANNER)
    if not argv:
        print("Usage: python mapfix.py <map.zip> [output.zip]")
        return 2
    try:
        out = fix_map(Path(argv[0]), Path(argv[1]) if len(argv) > 1 else None)
    except FixerError as exc:
        print(f"\n  ERROR: {exc}")
        return 1
    except (
        Exception
    ) as exc:  # noqa: BLE001 - last resort: a clean message, never a raw traceback
        print(f"\n  UNEXPECTED ERROR: {exc}")
        return 1
    print(f"\n  Done. Fixed map written to:\n    {out}\n")
    print("  Apply it to YOUR legally-owned copy of the map. Happy farming.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
