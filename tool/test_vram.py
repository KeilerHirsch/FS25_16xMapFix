#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  Tests for vram + configure_companion -- part of BigMap Optimizer.
#  Copyright (C) 2026  KeilerHirsch. Licensed under the GNU GPL v3 or later.
#
#  Pure-logic tests: no registry, no GPU and no game install required.
#  Run with:  python -m unittest test_vram   (from the tool/ directory)

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import configure_companion as cc
import vram


class CoerceIntTests(unittest.TestCase):
    def test_positive_int_passes_through(self):
        self.assertEqual(vram._coerce_positive_int(8585740288), 8585740288)

    def test_zero_and_negative_are_unknown(self):
        self.assertIsNone(vram._coerce_positive_int(0))
        self.assertIsNone(vram._coerce_positive_int(-5))

    def test_reg_binary_little_endian_bytes(self):
        # The exact blob an integrated GPU reported: 0x40000000 = 1 GiB.
        self.assertEqual(vram._coerce_positive_int(b"\x00\x00\x00\x40"), 1073741824)

    def test_empty_bytes_are_unknown(self):
        self.assertIsNone(vram._coerce_positive_int(b""))

    def test_other_types_are_unknown(self):
        self.assertIsNone(vram._coerce_positive_int("8 GB"))
        self.assertIsNone(vram._coerce_positive_int(None))


class BudgetTests(unittest.TestCase):
    def test_eight_gb_card_yields_six(self):
        # 8 GB cards report ~7.99 GiB; rounding then -2 headroom must give 6.
        self.assertEqual(vram.recommended_budget_gib(7.996), 6.0)

    def test_large_card(self):
        self.assertEqual(vram.recommended_budget_gib(16.0), 14.0)

    def test_floor_never_below_two(self):
        self.assertEqual(vram.recommended_budget_gib(4.0), 2.0)
        self.assertEqual(vram.recommended_budget_gib(3.0), 2.0)  # round3-2=1 -> clamp 2
        self.assertEqual(vram.recommended_budget_gib(2.0), 2.0)


class NvidiaSmiParseTests(unittest.TestCase):
    def _run(self, returncode, stdout):
        result = mock.Mock(returncode=returncode, stdout=stdout)
        with mock.patch.object(vram.subprocess, "run", return_value=result):
            return vram.nvidia_smi_vram_bytes()

    def test_picks_largest_and_converts_mib(self):
        self.assertEqual(self._run(0, "8192\n4096\n"), 8192 * 1024 * 1024)

    def test_nonzero_return_is_zero(self):
        self.assertEqual(self._run(9, "8192\n"), 0)

    def test_missing_binary_is_zero(self):
        with mock.patch.object(vram.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(vram.nvidia_smi_vram_bytes(), 0)


class WriteSettingsTests(unittest.TestCase):
    def test_written_xml_matches_the_mod_contract(self):
        # The mod's Lua reads getXMLFloat("textureStreamingBudget#vramGiB"),
        # i.e. root element <textureStreamingBudget vramGiB="...">.
        with tempfile.TemporaryDirectory() as d:
            profile = Path(d)
            out = cc.write_settings(6.0, profile)
            self.assertEqual(out, profile / "modSettings" / f"{cc.COMPANION_MOD_NAME}.xml")
            self.assertTrue(out.is_file())
            root = ET.parse(out).getroot()
            self.assertEqual(root.tag, "textureStreamingBudget")
            self.assertEqual(root.attrib["vramGiB"], "6.0")

    def test_creates_modsettings_dir(self):
        with tempfile.TemporaryDirectory() as d:
            profile = Path(d) / "fresh"
            out = cc.write_settings(4.0, profile)
            self.assertTrue(out.parent.is_dir())


class FindProfileTests(unittest.TestCase):
    def test_finds_default_documents_path(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / "Documents" / "My Games" / "FarmingSimulator2025").mkdir(parents=True)
            with mock.patch.object(cc.Path, "home", return_value=home):
                self.assertIsNotNone(cc.find_profile_dir())

    def test_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(cc.Path, "home", return_value=Path(d)):
                self.assertIsNone(cc.find_profile_dir())


if __name__ == "__main__":
    unittest.main()
