#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Tests for mapfix -- part of 16x Map Fix.
# Copyright (C) 2026  KeilerHirsch. Licensed under the GNU GPL v3 or later.
#
# Pure-logic tests run without grleconvert; the compiled-layer round-trip is
# exercised with a stub grleconvert, and the real end-to-end test self-skips
# unless the bundled grleconvert binary is present next to the tool.
#
# Run with:  python -m pytest test_mapfix.py   (from the tool/ dir)

from __future__ import annotations

import io
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

import mapfix as fx


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
        head = bytearray(
            max(min_len, 0x10 + 3 * type_index_channels + max(0, ranges - 1))
        )
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
        raw = _make_gdm_header(
            versioned=True, dim_log2=9, chunk_log2=5, channels=14, ranges=2, boundary=8
        )
        h = self._read(raw)
        self.assertEqual(h.edge, 16384)
        self.assertEqual(h.num_channels, 14)
        self.assertEqual(h.compress_at, 8)

    def test_single_range_has_no_compress_at(self):
        raw = _make_gdm_header(
            versioned=True, dim_log2=8, chunk_log2=5, channels=3, ranges=1
        )
        h = self._read(raw)
        self.assertEqual(h.edge, 8192)
        self.assertIsNone(h.compress_at)

    def test_type_index_channels_shift_boundary(self):
        # With type_index_channels > 0 the boundary byte moves; a naive 0x10
        # read would return the wrong value (this is regression cover for M1).
        raw = _make_gdm_header(
            versioned=True,
            dim_log2=9,
            chunk_log2=5,
            channels=12,
            ranges=2,
            boundary=7,
            type_index_channels=2,
        )
        h = self._read(raw)
        self.assertEqual(h.compress_at, 7)

    def test_legacy_magic(self):
        raw = _make_gdm_header(
            versioned=False, dim_log2=9, chunk_log2=5, channels=11, ranges=2, boundary=5
        )
        h = self._read(raw)
        self.assertEqual(h.edge, 16384)
        self.assertEqual(h.num_channels, 11)
        self.assertEqual(h.compress_at, 5)

    def test_rejects_non_gdm(self):
        with self.assertRaises(fx.FixerError):
            self._read(b"not a gdm file at all, really")

    def test_header_larger_than_expected_rejected(self):
        # A type-index table that pushes the boundary offset past the bytes we
        # read must be rejected, not indexed out of range.
        raw = _make_gdm_header(
            versioned=True,
            dim_log2=9,
            chunk_log2=5,
            channels=14,
            ranges=2,
            boundary=8,
            type_index_channels=40,
        )
        with self.assertRaises(fx.FixerError):
            self._read(raw)

    def test_implausible_dimensions_rejected(self):
        # dim_log2 + chunk_log2 far above a 64x map => corrupt/hostile header.
        raw = _make_gdm_header(
            versioned=True, dim_log2=200, chunk_log2=5, channels=4, ranges=1
        )
        with self.assertRaises(fx.FixerError):
            self._read(raw)


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

    def test_rejects_corrupt_archive(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.zip"
            bad.write_bytes(b"this is not a zip file")
            with self.assertRaises(fx.FixerError):
                fx._safe_extract(bad, Path(d))

    def test_refuses_when_disk_too_full(self):
        archive = self._zip_with_member("maps/data/ok.txt")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("mapfix.shutil.disk_usage") as du:
                du.return_value = mock.Mock(free=0)
                with self.assertRaises(fx.FixerError):
                    fx._safe_extract(archive, Path(d))
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

    def test_small_non_square_is_left_alone(self):
        # Regression for the HIGH finding: a small, non-square overlay/icon
        # under maps/data must be ignored, NOT abort the whole archive.
        png = self._png((16, 8))
        self.assertFalse(fx._downscale_png_file(png))
        png.unlink(missing_ok=True)

    def test_oversized_non_square_rejected(self):
        png = self._png((16, 8))
        with mock.patch.object(fx, "SAFE_SIZE", 4):
            with self.assertRaises(fx.FixerError):
                fx._downscale_png_file(png)
        png.unlink(missing_ok=True)

    def test_edge_ceiling_enforced(self):
        png = self._png((16, 16))
        with mock.patch.object(fx, "SAFE_SIZE", 8):
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

    def test_expected_edge_mismatch_warns_but_resizes(self):
        # When a GDM header edge disagrees with the decoded pixel size we trust
        # the pixels and warn -- but still resize the genuinely-oversized layer.
        png = self._png((16, 16), mode="P")
        with mock.patch.object(fx, "SAFE_SIZE", 8):
            with mock.patch.object(fx, "_warn") as warn:
                self.assertTrue(fx._downscale_png_file(png, expected_edge=32))
                warn.assert_called_once()
        png.unlink(missing_ok=True)

    def test_non_png_content_named_png_is_rejected(self):
        # security H1: a member merely NAMED .png but holding another format
        # must not be routed through a non-PNG decoder.
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "densityMap_x.png"
            Image.new("RGB", (16, 16), 1).save(fake, format="GIF")
            with self.assertRaises(fx.FixerError):
                fx.fix_density_layers(Path(d), Path("grleconvert"))


class FixDensityLayersTests(unittest.TestCase):
    def test_mixed_case_png_suffix_is_processed(self):
        # security/correctness H2: .PNG (upper-case) must not be silently skipped.
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            Image.new("L", (16, 16), 1).save(data / "densityMap_ground.PNG")
            with mock.patch.object(fx, "SAFE_SIZE", 8):
                changed = fx.fix_density_layers(data, Path("grleconvert"))
            self.assertEqual(changed, ["densityMap_ground.PNG"])

    def test_directories_and_other_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            (data / "subdir").mkdir()
            (data / "map.i3d").write_text("not a density layer")
            self.assertEqual(fx.fix_density_layers(data, Path("grleconvert")), [])


class CompiledLayerTests(unittest.TestCase):
    def test_small_gdm_skipped_without_decoding(self):
        header = fx.GdmHeader(edge=8192, num_channels=4, compress_at=None)
        with mock.patch.object(fx, "_run_grleconvert") as run:
            self.assertFalse(
                fx._resize_compiled_layer(Path("small.gdm"), Path("grle"), header)
            )
            run.assert_not_called()

    def test_gdm_roundtrip_reencodes_with_faithful_params(self):
        header = fx.GdmHeader(edge=16, num_channels=4, compress_at=2)
        calls: list[list[str]] = []

        def fake_run(_grle: Path, args: list[str]) -> str:
            calls.append(args)
            if args[1].endswith(".fixer.png"):  # decode step
                Image.new("P", (16, 16), 1).save(args[1])
            else:  # re-encode step: write the .gdm back
                Path(args[1]).write_bytes(b"reencoded")
            return ""

        with tempfile.TemporaryDirectory() as d:
            gdm = Path(d) / "densityMap_x.gdm"
            gdm.write_bytes(b"placeholder")
            with mock.patch.object(fx, "SAFE_SIZE", 8):
                with mock.patch.object(fx, "_run_grleconvert", side_effect=fake_run):
                    self.assertTrue(
                        fx._resize_compiled_layer(gdm, Path("grle"), header)
                    )
            # decode + re-encode both ran; channel/compression forwarded.
            self.assertEqual(len(calls), 2)
            self.assertIn("--channels", calls[1])
            self.assertIn("--compress-at", calls[1])
            # the intermediate .fixer.png is cleaned up.
            self.assertFalse((gdm.parent / (gdm.name + ".fixer.png")).exists())

    def test_grle_roundtrip_without_header(self):
        calls: list[list[str]] = []

        def fake_run(_grle: Path, args: list[str]) -> str:
            calls.append(args)
            if args[1].endswith(".fixer.png"):
                Image.new("L", (16, 16), 1).save(args[1])
            else:
                Path(args[1]).write_bytes(b"reencoded")
            return ""

        with tempfile.TemporaryDirectory() as d:
            grle = Path(d) / "infoLayer.grle"
            grle.write_bytes(b"placeholder")
            with mock.patch.object(fx, "SAFE_SIZE", 8):
                with mock.patch.object(fx, "_run_grleconvert", side_effect=fake_run):
                    self.assertTrue(fx._resize_compiled_layer(grle, Path("grle"), None))
            self.assertNotIn("--channels", calls[1])  # GRLE: no channel args


class RunGrleconvertTests(unittest.TestCase):
    def test_nonzero_exit_raises(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch("mapfix.subprocess.run", return_value=fake):
            with self.assertRaises(fx.FixerError):
                fx._run_grleconvert(Path("grle"), ["a", "b"])

    def test_timeout_raises_fixererror(self):
        with mock.patch(
            "mapfix.subprocess.run",
            side_effect=subprocess.TimeoutExpired("grle", 1),
        ):
            with self.assertRaises(fx.FixerError):
                fx._run_grleconvert(Path("grle"), ["a", "b"])


class FindGrleconvertTests(unittest.TestCase):
    def test_missing_binary_raises(self):
        with mock.patch.object(Path, "is_file", return_value=False):
            with self.assertRaises(fx.FixerError):
                fx._find_grleconvert()


class RepackTests(unittest.TestCase):
    def test_repack_is_deterministic_and_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "src"
            (root / "maps" / "data").mkdir(parents=True)
            (root / "b.txt").write_text("b")
            (root / "a.txt").write_text("a")
            (root / "maps" / "data" / "c.png").write_text("c")
            out1, out2 = Path(d) / "1.zip", Path(d) / "2.zip"
            fx._repack(root, out1)
            fx._repack(root, out2)
            with zipfile.ZipFile(out1) as z1, zipfile.ZipFile(out2) as z2:
                self.assertEqual(z1.namelist(), z2.namelist())
                self.assertEqual(z1.namelist(), sorted(z1.namelist()))


class FixMapTests(unittest.TestCase):
    def test_rejects_non_zip(self):
        with self.assertRaises(fx.FixerError):
            fx.fix_map(Path("definitely_not_here.txt"))

    def test_rejects_output_equal_to_input(self):
        try:
            fx._find_grleconvert()
        except fx.FixerError:
            self.skipTest("grleconvert binary not bundled; skipping")
        with tempfile.TemporaryDirectory() as d:
            z = Path(d) / "map.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("maps/data/x.txt", b"x")
            with self.assertRaises(fx.FixerError):
                fx.fix_map(z, z)

    def test_missing_maps_data_folder(self):
        try:
            fx._find_grleconvert()
        except fx.FixerError:
            self.skipTest("grleconvert binary not bundled; skipping")
        with tempfile.TemporaryDirectory() as d:
            z = Path(d) / "map.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("readme.txt", b"no data folder here")
            with self.assertRaises(fx.FixerError):
                fx.fix_map(z, Path(d) / "out.zip")


class MainTests(unittest.TestCase):
    def test_no_args_returns_usage_code(self):
        self.assertEqual(fx.main([]), 2)

    def test_bad_path_returns_error_code(self):
        self.assertEqual(fx.main(["definitely_not_here.zip"]), 1)


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
                zf.write(
                    data / "densityMap_ground.png", "maps/data/densityMap_ground.png"
                )

            with mock.patch.object(fx, "SAFE_SIZE", 8):
                out = fx.fix_map(map_zip, work / "tiny_fixed.zip")
            with zipfile.ZipFile(out) as zf:
                with zf.open("maps/data/densityMap_ground.png") as fh:
                    head = fh.read(24)
            width, height = struct.unpack(">II", head[16:24])
            self.assertEqual((width, height), (8, 8))


if __name__ == "__main__":
    unittest.main()
