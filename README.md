# FS25 16x Map Crash Fix

**Diagnosis and a verified fix for the "freezes at 100% compiling shaders" / dedicated server crash that hits large-scale (16x) Farming Simulator 25 maps.**

> [!IMPORTANT]
> This repo does **not** redistribute any mods or the map. Everything below links to the original author's official page (ModHub/GitHub). What's here is the diagnosis, the exact config changes, and the before/after proof that it works — apply it to your own legally-owned copies of the mods/map.

> [!WARNING]
> This is one operator's specific case (dedicated server + a 16GB-RAM laptop client, both running the same 16x map). The root cause (DirectX 12 texture-tile registration limit) is generic and well documented for 16x maps in general — see [Sources](#sources) — but your exact numbers (RAM, VRAM, pagefile size) will differ.

## TL;DR

Loading a 16x-scale map (16x the area of a normal FS25 map — density maps up to 32768×32768px) under **DirectX 12** causes the GIANTS Engine's tile-registration system to overflow, producing thousands of `Error in allocReg` / `TiledBitmapOperationCompiler failed` log entries and a client freeze at "100% compiling shaders" (sometimes a hard crash instead). Switching the renderer to **DirectX 11** eliminates the error entirely — confirmed **3014 → 0** errors across two otherwise-identical server restarts on the same map, same mod list.

## The symptom

- Client: freezes at "100% compiling shaders", or crashes with `nvwgf2umx.dll` / `0xC0000005` (access violation in the NVIDIA D3D driver)
- Dedicated server log: thousands of repeating `Error in allocReg` lines right after the density-map (`DM syncer`) load step, followed by `Error: TiledBitmapOperationCompiler failed`
- Community reports of the same signature on other 16x maps (Kansas, Juotca, etc.) — see [Sources](#sources)

## Root cause

A 16x map's density maps are enormous — in this case:

```
densityMap_ground.png            16384 x 16384, 11 bpp
TerrainDisplacementMap           32768 x 32768, 7 bpp
densityMap_height.png            16384 x 16384, 14 bpp
densityMap_fruits.png            16384 x 16384, 14 bpp
+ 6 more infoLayer/density maps at 16384x16384
```

At a 16×16px tile size, the displacement map alone is ~4.2 million tiles. Under DirectX 12, the engine's tile-registration table overflows well before that, throwing `Error in allocReg` for every tile past its capacity. This is a DirectX-12-path-specific engine limit, not a RAM shortage — the dedicated server in this case had 262GB of RAM available and still threw the same errors.

## The fix

Applied in this order. None of it requires buying more RAM or reducing map/mod content.

### 1. DirectX 12 → DirectX 11

In `game.xml` (both the client's `Documents/My Games/FarmingSimulator2025/game.xml` **and** the dedicated server's `profile/game.xml`):

```diff
- <renderer>D3D_12</renderer>
+ <renderer>D3D_11</renderer>
```

This is the fix that actually resolves the `allocReg` errors — DX11 uses a different, non-overflowing tile path.

### 2. Clear shader cache

Delete the contents of `shader_cache/` and `jim_cache/` (client and server) after changing the renderer, so nothing tries to reuse DX12-compiled shaders under DX11.

### 3. Pagefile

Not the root cause, but cheap insurance for the one-time heavy first load after a cache wipe: set a fixed pagefile of physical-RAM × 3 or more (`Set-CimInstance -Query "SELECT * FROM Win32_PageFileSetting" -Property @{InitialSize=X; MaximumSize=X}`, reboot required).

### 4. Reduce view/LOD distance coefficients

Optional, only needed if you're also fighting VRAM limits on the client GPU. In `game.xml`:

```xml
<performanceClass>Medium</performanceClass>
<viewDistanceCoeff>0.75</viewDistanceCoeff>
<lodDistanceCoeff>0.75</lodDistanceCoeff>
<terrainLODDistanceCoeff>0.75</terrainLODDistanceCoeff>
<foliageViewDistanceCoeff>0.75</foliageViewDistanceCoeff>
```

### 5. Fix the "Texture Streaming Budget Optimizer" mod's `-1` bug

By default FS25 caps texture-streaming VRAM usage at 4GB regardless of your actual GPU's memory. A community mod, **[Texture Streaming Budget Optimizer](https://www.farming-simulator.com/mod.php?mod_id=346266&title=fs2025)** by *Helfer B.*, is supposed to raise that limit — but as shipped it calls the engine function with `-1`, presumably intending "auto/unlimited". In practice the engine does **not** treat `-1` as "use everything available" — it falls back to a fixed default (`2147483648` bytes = 2GB), confirmed via the server log. The mod is not doing what its description promises.

**This is not a redistribution of the mod** — download it from the official ModHub link above, then edit `textureStreamingBudget.lua` yourself:

```diff
- local newBudget = -1
+ local newBudget = 6442450944 -- 6 GB, explicit value — set to (your VRAM in GB - 2) * 1024^3
```

Reported to the original author; not yet merged upstream as of this writing.

## Proof

**Before** (DX12, first restart) — excerpt from the dedicated server log, `2026-07-09 12:54:23–24`:

```
DM syncer[10] : .../mods/FS25_Thueringen_2_0_16x/maps/data/densityMap_weed.png (16384 x 16384, 4 bpp) tile size 32 x 32 subdivs: 8 VT: false
DM syncer[11] : .../mods/FS25_Thueringen_2_0_16x/maps/data/densityMap_fruits.png (16384 x 16384, 14 bpp) tile size 32 x 32 subdivs: 8 VT: false
DM syncer : 26112 KB per connection
Error in allocReg
Error in allocReg
Error in allocReg
[... 3014 total occurrences this session ...]
```

**After** (DX11, next restart, same map, same mod list, same server):

```
$ grep -c "Error in allocReg" server_log.txt
0
$ grep "joined the game" server_log.txt
2026-07-09 14:33:19.260 KeilerHirsch joined the game
```

Clean load, ~6 minutes server-restart-to-join, zero allocation errors, stable session afterward.

## Mods used

This is a realism-focused setup. No mods are hosted here — official sources only:

- [Advanced Damage System](https://github.com/id577/FS25_AdvancedDamageSystem) — vehicle wear/breakdown/thermal/electrical overhaul
- [Enhanced Vehicle](https://www.farming-simulator.com/mod.php?title=fs2025) — extended vehicle control
- [Texture Streaming Budget Optimizer](https://www.farming-simulator.com/mod.php?mod_id=346266&title=fs2025) — see the bug + fix above
- Full list of ~260 mods available on request; not published here since most authors don't grant redistribution rights (see [Contributing](#contributing)).

## Sources

- [GIANTS Software Forum — "FS25 freezes at 100% compiling shaders on any 16x map"](https://forum.giants-software.com/viewtopic.php?t=217079)
- Community consensus: 16x maps need 20–32GB+ system memory for the first shader compile.

## Contributing

If you hit the same crash signature on a different 16x map, open an issue with your server log's `allocReg` count before/after switching to DX11 — more data points make the root-cause case stronger for a potential GIANTS bug report.

## License

Documentation licensed under [CC BY 4.0](LICENSE). Maintained by **KeilerHirsch**.
