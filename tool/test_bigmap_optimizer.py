#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Tests for bigmap_optimizer -- part of BigMap Optimizer.
# Copyright (C) 2026  KeilerHirsch. Licensed under the GNU GPL v3 or later.
#
# Pure-logic tests run without grleconvert; the end-to-end test self-skips
# unless the bundled grleconvert binary is present next to the tool.
#
# Run with:  python -m unittest test_fixer   (from the tool/ directory)

from __future__ import annotations

import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

import bigmap_optimizer as fx


def _make_gdm_header(
    *,
    versioned: bool,
    dim_log2: int,
    chunk_log2: int,
    channels: int,
    ranges: int,
    boundary: int = 0,
    type_index_channels: int = 0,
) -> bytes:
    """Build a synthetic GDM header for parser tests.

    Real GDM files always carry data blocks after the header, so we pad to a
    realistic minimum length rather than emitting a header-only stub that the
    parser's sanity check would (correctly) reject.
    """
    min_len = 64
    if versioned:
        head = bytearray(max(min_len, 0x10 + 3 * type_index_channels + max(0, ranges - 1)))
        head[0:4] = b'"MDF'
        head[0x08] = dim_log2
        head[0x09] = chunk_log2
        head[0x0B] = channels
        head[0x0C] = ranges
        head[0x0D] = type_index_channels
        if ranges > 1:
            head[0x10 + 3 * type_index_channels] = boundary
    else:
        head = bytearray(max(min_len, 0x09 + max(0, ranges - 1)))
        head[0:4] = b"!MDF"
        head[0x04] = dim_log2
        head[0x05] = chunk_log2
        head[0x07] = channels
        head[0x08] = ranges
        if ranges > 1:
            head[0x09] = boundary
    return bytes(head)


class GdmHeaderTests(unittest.TestCase):
    def _read(self, raw: bytes) -> fx.GdmHeader:
        with tempfile.NamedTemporaryFile(suffix=".gdm", delete=False) as fh:
            fh.write(raw)
            path = Path(fh.name)
        try:
            return fx.GdmHeader.read(path)
        finally:
            path.unlink(missing_ok=True)

    def test_versioned_edge_and_channels(self):
        # dim_log2 9 + chunk_log2 5 = 14 -> 16384, like Thueringen's fruits map.
        raw = _make_gdm_header(versioned=True, dim_log2=9, chunk_log2=5,
                               channels=14, ranges=2, boundary=8)
        h = self._read(raw)
        self.assertEqual(h.edge, 16384)
        self.assertEqual(h.num_channels, 14)
        self.assertEqual(h.compress_at, 8)

    def test_single_range_has_no_compress_at(self):
        raw = _make_gdm_header(versioned=True, dim_log2=8, chunk_log2=5,
                               channels=3, ranges=1)
        h = self._read(raw)
        self.assertEqual(h.edge, 8192)
        self.assertIsNone(h.compress_at)

    def test_type_index_channels_shift_boundary(self):
        # With type_index_channels > 0 the boundary byte moves; a naive 0x10
        # read would return the wrong value (this is regression cover for M1).
        raw = _make_gdm_header(versioned=True, dim_log2=9, chunk_log2=5,
                               channels=12, ranges=2, boundary=7,
                               type_index_channels=2)
        h = self._read(raw)
        self.assertEqual(h.compress_at, 7)

    def test_legacy_magic(self):
        raw = _make_gdm_header(versioned=False, dim_log2=9, chunk_log2=5,
                               channels=11, ranges=2, boundary=5)
        h = self._read(raw)
        self.assertEqual(h.edge, 16384)
        self.assertEqual(h.num_channels, 11)
        self.assertEqual(h.compress_at, 5)

    def test_rejects_non_gdm(self):
        with self.assertRaises(fx.FixerError):
            self._read(b"not a gdm file at all, really")


class SafeExtractTests(unittest.TestCase):
    def _zip_with_member(self, name: str) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(name, b"payload")
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.write(buf.getvalue())
        tmp.close()
        return Path(tmp.name)

    def test_rejects_path_traversal(self):
        archive = self._zip_with_member("../escape.txt")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(fx.FixerError):
                fx._safe_extract(archive, Path(d))
        archive.unlink(missing_ok=True)

    def test_accepts_normal_member(self):
        archive = self._zip_with_member("maps/data/ok.txt")
        with tempfile.TemporaryDirectory() as d:
            fx._safe_extract(archive, Path(d))
            self.assertTrue((Path(d) / "maps/data/ok.txt").is_file())
        archive.unlink(missing_ok=True)


class DownscalePngTests(unittest.TestCase):
    def _png(self, size, mode="RGB") -> Path:
        img = Image.new(mode, size, color=1)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name)
        tmp.close()
        return Path(tmp.name)

    def test_small_image_unchanged(self):
        png = self._png((16, 16))
        self.assertFalse(fx._downscale_png_file(png))
        png.unlink(missing_ok=True)

    def test_oversized_is_downscaled_preserving_mode(self):
        png = self._png((16, 16), mode="P")
        with mock.patch.object(fx, "SAFE_SIZE", 8):
            self.assertTrue(fx._downscale_png_file(png))
        with Image.open(png) as out:
            self.assertEqual(out.size, (8, 8))
            self.assertEqual(out.mode, "P")  # packed layout must survive
        png.unlink(missing_ok=True)

    def test_non_square_rejected(self):
        png = self._png((16, 8))
        with self.assertRaises(fx.FixerError):
            fx._downscale_png_file(png)
        png.unlink(missing_ok=True)

    def test_edge_ceiling_enforced(self):
        png = self._png((16, 16))
        with mock.patch.object(fx, "MAX_EDGE", 8):
            with self.assertRaises(fx.FixerError):
                fx._downscale_png_file(png)
        png.unlink(missing_ok=True)

    def test_heightmap_non_power_of_two_is_skipped(self):
        # A DEM / heightmap is a 2^n+1 vertex grid (e.g. 8193). It is larger
        # than SAFE_SIZE yet must NEVER be resampled -- shrinking it to a power
        # of two corrupts the terrain geometry. Only power-of-two tiled density
        # layers overflow the registry and get downscaled.
        png = self._png((9, 9))  # 9 > 8 (mocked SAFE_SIZE) and not a power of two
        with mock.patch.object(fx, "SAFE_SIZE", 8):
            self.assertFalse(fx._downscale_png_file(png))
        png.unlink(missing_ok=True)


class EndToEndTests(unittest.TestCase):
    def test_fix_map_smoke(self):
        try:
            fx._find_grleconvert()
        except fx.FixerError:
            self.skipTest("grleconvert binary not bundled; skipping end-to-end test")

        # A tiny map archive with one oversized PNG density layer, downscaled
        # against a small SAFE_SIZE so the test stays fast.
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            data = work / "maps" / "data"
            data.mkdir(parents=True)
            Image.new("L", (16, 16), 1).save(data / "densityMap_ground.png")
            map_zip = work / "tiny.zip"
            with zipfile.ZipFile(map_zip, "w") as zf:
                zf.write(data / "densityMap_ground.png", "maps/data/densityMap_ground.png")

            with mock.patch.object(fx, "SAFE_SIZE", 8):
                out = fx.fix_map(map_zip, work / "tiny_fixed.zip")
            with zipfile.ZipFile(out) as zf:
                with zf.open("maps/data/densityMap_ground.png") as fh:
                    head = fh.read(24)
            width, height = struct.unpack(">II", head[16:24])
            self.assertEqual((width, height), (8, 8))


if __name__ == "__main__":
    unittest.main()
