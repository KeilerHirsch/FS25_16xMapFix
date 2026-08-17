#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# ============================================================================
#  16x Map Fix  --  oversized-map density downscaler
#  "The Man, The Myth, The Legend : Keilerhirsch"
# ============================================================================
#
#  Makes oversized Farming Simulator 25 maps (16x / 32x) more manageable by
#  downscaling oversized density/info layers to 8192px in place -- fruit data
#  included, with field-state encoding preserved through nearest-neighbour
#  resampling.
#
#  In the documented field case, reducing those oversized layers removed the
#  observed allocReg / TiledBitmapOperationCompiler failure pattern. That is a
#  measured mitigation result; the exact proprietary engine-internal root cause
#  is not claimed here as independently proven.
#
#  The tool does not modify terrain geometry, scripts, or gameplay code. It
#  rewrites only supported density/info layers that exceed the configured
#  8192px processing target. The input archive itself is never modified; a new
#  fixed archive is written alongside it.
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
#  these untouched: the vertex/index buffers describe terrain mesh data that
#  this tool does not rewrite, while the practical meaning of the declared
#  budgets is engine-dependent. Recomputing them without a validated per-
#  subsystem model would create a new unsupported assumption.
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

#: Processing target used by this tool for oversized density/info layers. The
#: documented failing maps became stable after oversized layers were reduced to
#: this edge length; this constant is therefore an operational mitigation
#: target, not a claim about an official GIANTS engine limit.
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
            errors="replace",
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
        if width <= SAFE_SIZE:
            return False
        # 2^n+1 dimensions are commonly used for heightmap/DEM grids. Those
        # layers are outside this tool's intended density/info rewrite scope and
        # are therefore left untouched rather than being classified as the
        # cause of a particular engine failure.
        if width & (width - 1) != 0:
            if (width - 1) & (width - 2) != 0:
                _warn(
                    f"{png.name}: oversized {width}px layer is neither a power of "
                    "two nor a 2^n+1 heightmap candidate; leaving it unchanged. "
                    "Inspect it manually if the map remains unstable."
                )
            return False
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
    if resized.mode != mode:
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
        return False

    png = path.with_name(path.name + ".fixer.png")
    try:
        _run_grleconvert(grleconvert, [str(path), str(png)])
        expected_edge = header.edge if header is not None else None
        if not _downscale_png_file(png, expected_edge=expected_edge):
            return False
        if header is not None:
            args = [str(png), str(path), "--channels", str(header.num_channels)]
            if header.compress_at is not None:
                args += ["--compress-at", str(header.compress_at)]
        else:
            args = [str(png), str(path)]
        _run_grleconvert(grleconvert, args)
        return True
    finally:
        png.unlink(missing_ok=True)


def fix_density_layers(data_dir: Path, grleconvert: Path) -> list[str]:
    """Resize every oversized supported density layer under ``data_dir``.

    Returns the names of the layers that were changed, for the report.
    """
    changed: list[str] = []
    for path in sorted(data_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
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
    except (OSError, zipfile.BadZipFile) as exc:
        raise FixerError(f"cannot open archive: {exc}") from exc

    with zf:
        members = zf.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise FixerError(
                f"archive contains {len(members):,} members; limit is "
                f"{MAX_ARCHIVE_MEMBERS:,}"
            )

        total = sum(m.file_size for m in members)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise FixerError(
                f"archive expands to {total / 1024**3:.1f} GiB; limit is "
                f"{MAX_TOTAL_UNCOMPRESSED / 1024**3:.1f} GiB"
            )

        free = shutil.disk_usage(dest).free
        required = max(total * 2, total + 2 * 1024**3)
        if free < required:
            raise FixerError(
                f"not enough free space: need roughly {required / 1024**3:.1f} GiB, "
                f"have {free / 1024**3:.1f} GiB"
            )

        root = dest.resolve()
        for member in members:
            target = (dest / member.filename).resolve()
            if target != root and root not in target.parents:
                raise FixerError(f"archive contains unsafe path: {member.filename!r}")

        try:
            zf.extractall(dest)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise FixerError(f"failed to extract archive: {exc}") from exc


def _find_data_dir(root: Path) -> Path:
    candidates = [p for p in root.rglob(DATA_SUBDIR) if p.is_dir()]
    if not candidates:
        raise FixerError(f"archive does not contain {DATA_SUBDIR}/")
    if len(candidates) > 1:
        raise FixerError(
            f"archive contains multiple {DATA_SUBDIR}/ directories; refusing to guess"
        )
    return candidates[0]


def _write_archive(root: Path, output: Path) -> None:
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(root))
        tmp.replace(output)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def fix_archive(archive: Path, output: Path) -> list[str]:
    """Fix an archive and return the changed layer names."""
    if archive.resolve() == output.resolve():
        raise FixerError("output path must differ from input; source archives are immutable")

    grleconvert = Path(__file__).with_name("grleconvert.exe")
    if not grleconvert.is_file():
        raise FixerError(f"bundled converter not found: {grleconvert}")

    with tempfile.TemporaryDirectory(prefix="fs25-mapfix-") as tmpdir:
        root = Path(tmpdir)
        _safe_extract(archive, root)
        data_dir = _find_data_dir(root)
        changed = fix_density_layers(data_dir, grleconvert)
        _write_archive(root, output)
        return changed


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    print(BANNER)

    if not argv:
        print("Usage: mapfix.py <map.zip> [output.zip]", file=sys.stderr)
        return 2

    archive = Path(argv[0]).expanduser()
    if not archive.is_file():
        print(f"ERROR: input archive not found: {archive}", file=sys.stderr)
        return 2

    output = (
        Path(argv[1]).expanduser()
        if len(argv) > 1
        else archive.with_name(f"{archive.stem}_fixed{archive.suffix}")
    )

    try:
        changed = fix_archive(archive, output)
    except FixerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - final defensive boundary
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        return 1

    if changed:
        print(f"Fixed {len(changed)} oversized density layer(s):")
        for name in changed:
            print(f"  - {name}")
    else:
        print("No supported oversized density layers required changes.")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())