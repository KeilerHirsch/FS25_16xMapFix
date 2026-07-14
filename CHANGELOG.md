# Changelog

All notable changes to the **BigMap Optimizer** tool are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.3] - 2026-07-14

### Security
- ECC audit pass (ruff clean, bandit 7 → 0): annotated the benign findings as
  `nosec` with rationale — `quoteattr` escapes our own generated XML output (never
  parses untrusted input); `nvidia-smi` runs with a fixed list-form argv, no shell.
  No behaviour change; the 49-test suite stays green.

### Changed
- Repo now carries **GPLv3** at the root LICENSE. The tool code was already GPLv3
  in the file headers; this makes GitHub detect and display GPL-3.0 instead of the
  documentation's CC-BY, matching the KeilerHirsch default and removing the
  "why does it say CC-BY?" confusion. The root-cause writeup prose stays reusable
  under CC-BY-4.0 with attribution. No code change.

## [1.1.2] - 2026-07-12

### Fixed
- **Atomic repack:** the fixed `.zip` is now written to a temp file and moved into
  place only once complete, so an interrupted or disk-full run can no longer leave
  a truncated/corrupt output behind (this tool targets multi-GB maps).
- **Disk-space preflight** now measures the drive the fixed map is actually written
  to (extraction happens next to the output), not the system temp drive.
- **Silent skip made falsifiable:** an oversized layer that is neither a power of
  two nor a 2^n+1 heightmap is now warned about instead of quietly left unchanged.

Found by an independent code-review pass. No change to behaviour on a valid map —
all 49 tests still pass.

## [1.1.1] - 2026-07-12

### Changed
- Normalised the author signature to the canonical KeilerHirsch form across the
  tool scripts, batch files and the companion mod (rebuilt the companion zip).

### Added
- Ko-fi support callout in the README (FUNDING already present).

### Note
- Licensing is unchanged and intentional: documentation under CC BY 4.0, tool
  code under GPLv3, bundled `grleconvert` under its own MIT license. This repo
  stays open by design (community fix + reference), so the proprietary mod
  standard deliberately does not apply here.

## [1.1.0] - 2026-07-11

Security- and correctness-hardening pass. No change to the tool's behaviour on
a legitimate oversized map; every fix below closes a way a malformed or hostile
map archive could crash the tool, hang it, or make it silently ship an unfixed
layer. Driven by a full static-analysis battery (ruff, black, mypy --strict,
bandit) plus independent code- and security-review passes.

### Security
- Pin Pillow to the PNG decoder (`Image.open(..., formats=["PNG"])`) so an
  archive member merely *named* `.png` can no longer be routed through an
  unrelated image decoder (format-confusion / decoder-surface widening).
- Add a free-disk-space preflight before extraction, sized for the extracted
  tree plus the repacked copy, so a cheaply-compressible archive cannot fill
  the disk.
- Bound every `grleconvert` invocation with a timeout, so a corrupt layer that
  makes the native converter hang can no longer wedge the tool indefinitely.

### Fixed
- Match layer extensions case-insensitively: `.PNG` / `.GDM` / `.GRLE` layers
  are no longer silently skipped (which previously reported success while
  leaving the overflowing layer unfixed).
- A small, non-square overlay/icon under `maps/data` is now left alone instead
  of aborting the whole map.
- A corrupt or truncated `.zip` now fails with a clear error message instead of
  a raw Python traceback.
- Refuse an output path equal to the input, honouring the "input is never
  modified" guarantee.
- Reject implausible GDM header dimensions instead of computing an oversized
  shift; cross-check a decoded layer's real size against its header and warn on
  a mismatch (trusting the pixels).

### Changed
- Use the non-deprecated `Image.Resampling.NEAREST` spelling.
- Expanded the test suite from 13 to 34 tests; statement coverage 69% → 89%,
  including a stub-`grleconvert` round-trip so the compiled-layer path is
  covered without the bundled binary.

## [1.0.0] - 2026-07-11

### Added
- Initial release. Downscales every oversized (16x/32x) density/info layer of a
  Farming Simulator 25 map to the engine-safe 8192px so it loads and syncs in
  multiplayer, without deleting data or touching map geometry, scripts or
  gameplay. Heightmaps (2^n+1 DEM grids) are deliberately left untouched.
- Bundles `grleconvert` (Paint-a-Farm/grleconvert, MIT) for `.gdm`/`.grle` ↔ PNG
  conversion; zip-bomb and path-traversal guards on extraction.
- Drag-and-drop launcher (`Optimize-Map.bat`) and a documented SP-vs-MP root
  cause for the "Error in allocReg" crash.

[1.1.0]: https://github.com/KeilerHirsch/fs25-16x-map-fix/releases/tag/v1.1.0
[1.0.0]: https://github.com/KeilerHirsch/fs25-16x-map-fix/releases/tag/v1.0.0
