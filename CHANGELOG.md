# Changelog

All notable changes to the **16x Map Fix** tool are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.4] - 2026-07-14

### Changed
- **Rebrand to "16x Map Fix"** — one consistent name across the whole project. The
  product was previously labelled "BigMap Optimizer" while the repo and releases said
  "16x Map Fix", so the download names did not match the repository name. Now unified:
  repo `FS25_16xMapFix`, tool module `mapfix.py`, companion mod `FS25_16xMapFix`,
  logo wordmark "16× MAP FIX". **No functional change** — the tool and companion
  behavior are unchanged; the 49-test suite remained green at release time.
- The companion settings file is now `modSettings/FS25_16xMapFix.xml` (was
  `FS25_BigMapOptimizerCompanion.xml`). Delete the old file after updating; the mod
  recreates the new one on first run.

## [1.1.3] - 2026-07-14

### Security
- ECC audit pass (ruff clean, bandit 7 → 0): annotated the benign findings as
  `nosec` with rationale — `quoteattr` escapes generated XML output; `nvidia-smi`
  runs with a fixed list-form argv and no shell. No behavior change; the 49-test
  suite was green for this release.

### Changed
- Repo now carries **GPLv3** at the root LICENSE. The tool code was already GPLv3
  in the file headers; this makes GitHub detect and display GPL-3.0 instead of the
  documentation's CC-BY. The technical write-up prose remains reusable under
  CC-BY-4.0 with attribution. No code change.

## [1.1.2] - 2026-07-12

### Fixed
- **Atomic repack:** the fixed `.zip` is now written to a temp file and moved into
  place only once complete, reducing the risk that an interrupted or disk-full run
  leaves a truncated output archive behind.
- **Disk-space preflight** now measures the drive the fixed map is actually written
  to (extraction happens next to the output), not the system temp drive.
- **Silent skip made observable:** an oversized layer that is neither a power of
  two nor a 2^n+1 heightmap candidate is now warned about instead of quietly left
  unchanged.

Found by an independent code-review pass. No intended behavior change on the
validated map fixtures; all 49 tests passed for this release.

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

Security- and correctness-hardening pass. The changes below address malformed or
hostile archive behavior and failure observability. Driven by static analysis
(ruff, black, mypy --strict, bandit) plus independent code- and security-review
passes.

### Security
- Pin Pillow to the PNG decoder (`Image.open(..., formats=["PNG"])`) so an
  archive member merely *named* `.png` is not routed through an unrelated image
  decoder.
- Add a free-disk-space preflight before extraction, sized for the extracted
  tree plus the repacked copy.
- Bound every `grleconvert` invocation with a timeout so a converter hang does
  not block the tool indefinitely.

### Fixed
- Match layer extensions case-insensitively: `.PNG` / `.GDM` / `.GRLE` layers
  are no longer silently skipped by the extension check.
- A small, non-square overlay/icon under `maps/data` is left alone instead of
  aborting the whole map.
- A corrupt or truncated `.zip` fails with a clearer error message instead of
  exposing a raw Python traceback.
- Refuse an output path equal to the input, preserving the tool's design that
  source archives are not overwritten in place.
- Reject implausible GDM header dimensions instead of computing an oversized
  shift; cross-check a decoded layer's real size against its header and warn on
  a mismatch.

### Changed
- Use the non-deprecated `Image.Resampling.NEAREST` spelling.
- Expanded the test suite from 13 to 34 tests; statement coverage 69% → 89%,
  including a stub-`grleconvert` round-trip so the compiled-layer path is
  covered without the bundled binary.

## [1.0.0] - 2026-07-11

### Added
- Initial release. Downscales supported oversized (16x/32x) density/info layers
  of a Farming Simulator 25 map to an 8192px operational target derived from the
  validated field case. The tool does not rewrite terrain geometry, scripts or
  gameplay code, and leaves 2^n+1 heightmap/DEM candidates untouched.
- Bundles `grleconvert` (Paint-a-Farm/grleconvert, MIT) for `.gdm`/`.grle` ↔ PNG
  conversion; zip-bomb and path-traversal guards on extraction.
- Drag-and-drop launcher (`Optimize-Map.bat`) and a write-up documenting the
  observed SP-vs-MP failure pattern and the mitigation hypothesis. The exact
  proprietary engine-internal root cause is not claimed as independently proven.

[1.1.0]: https://github.com/KeilerHirsch/fs25-16x-map-fix/releases/tag/v1.1.0
[1.0.0]: https://github.com/KeilerHirsch/fs25-16x-map-fix/releases/tag/v1.0.0
