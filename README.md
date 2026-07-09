# FS25 16x Map Crash Fix

**Diagnosis and a verified fix for the "freezes at 100% compiling shaders" / dedicated server crash that hits large-scale (16x) Farming Simulator 25 maps.**

> [!IMPORTANT]
> This repo does **not** redistribute any mods or the map. Everything below links to the original author's official page (ModHub/GitHub). What's here is the diagnosis, the exact config changes, and the before/after proof that it works — apply it to your own legally-owned copies of the mods/map.

> [!WARNING]
> **Status: partially solved, not a guaranteed fix.** This is one operator's specific case (dedicated server + a 16GB-RAM laptop client, both running the same 16x map). Steps 1–5 below are all individually verified, legitimate fixes for the specific bugs they target. But the DirectX-12 `allocReg` overflow **came back on a 3rd server restart with the identical DX11 config that had produced 0 errors twice before** — meaning the DX11 switch is not a deterministic, guaranteed fix, just a strong mitigation. The client-side crash during full map rendering (as opposed to the server's data-sync-only path) remains unresolved as of this writing.

## TL;DR

Loading a 16x-scale map (16x the area of a normal FS25 map — density maps up to 32768×32768px) under **DirectX 12** causes the GIANTS Engine's tile-registration system to overflow, producing thousands of `Error in allocReg` / `TiledBitmapOperationCompiler failed` log entries and a client freeze at "100% compiling shaders" (sometimes a hard crash instead). Switching the renderer to **DirectX 11** greatly reduces this — **0 errors on 2 of 3 identical dedicated-server restarts** — but is not 100% reliable on its own. The client (which actually renders the terrain, unlike the server) hit the same error class at a much larger scale (800K+ occurrences) even with DX11 active, and a follow-up fix to the map's own stale GPU-memory-budget declaration (see [step 6](#6-fix-the-maps-stale-memory-budget-declaration-unconfirmed)) did not resolve it either. Treat everything here as a set of legitimate, verified-individually mitigations, not a complete solved-it writeup.

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

### 6. Fix the map's stale memory-budget declaration (unconfirmed)

`maps/config/mapEU.xml` inside the map's own mod archive declares `textureMemoryUsage`, `vertexBufferMemoryUsage`, and `indexBufferMemoryUsage` — GIANTS Editor auto-calculates these normally, and they're used by the engine for GPU memory allocation planning. This particular 16x map still had the values from what looks like the pre-scaling (1x) map (`textureMemoryUsage` of ~408MB, absurdly low for 16384px+ density maps). Scaling them ×16 to match the map's actual area increase:

```diff
- <vertexBufferMemoryUsage>76170496</vertexBufferMemoryUsage>
- <indexBufferMemoryUsage>16780800</indexBufferMemoryUsage>
- <textureMemoryUsage>428408832</textureMemoryUsage>
+ <vertexBufferMemoryUsage>1218727936</vertexBufferMemoryUsage>
+ <indexBufferMemoryUsage>268492800</indexBufferMemoryUsage>
+ <textureMemoryUsage>6854541312</textureMemoryUsage>
```

**Result: did not resolve the client-side crash.** Reasonable theory, plausible mechanism, but tested and it didn't fix the underlying issue on its own. Documented here so nobody else burns time re-testing the same theory — if you find this *does* help in a different setup, please open an issue.

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

**Then, 3rd restart** (identical DX11 config, no changes made) — `allocReg` was back:

```
$ grep -c "Error in allocReg" server_log.txt
3014
```

Same count as the original DX12 run, on an unmodified DX11 config that had just produced 0 twice. Something non-deterministic is at play that we haven't isolated — possibly a timing/race condition in tile registration, possibly related to system state at connect time. **This is the main reason this repo is not claiming a solved problem.**

Meanwhile, the client log (which only exists on the machine actually rendering the world) showed **878,987** `Error in allocReg` occurrences in a single failed load attempt — two to three orders of magnitude worse than anything seen server-side, confirming the client's full-rendering path is a materially different (and worse) problem than the server's data-sync path.

## Mods used

This is a realism-focused setup. No mods are hosted here — official sources only:

- [Advanced Damage System](https://github.com/id577/FS25_AdvancedDamageSystem) — vehicle wear/breakdown/thermal/electrical overhaul
- [Enhanced Vehicle](https://www.farming-simulator.com/mod.php?title=fs2025) — extended vehicle control
- [Texture Streaming Budget Optimizer](https://www.farming-simulator.com/mod.php?mod_id=346266&title=fs2025) — see the bug + fix above
- Full list of ~260 mods available on request; not published here since most authors don't grant redistribution rights (see [Contributing](#contributing)).

## Sources

- [GIANTS Software Forum — "FS25 freezes at 100% compiling shaders on any 16x map"](https://forum.giants-software.com/viewtopic.php?t=217079)
- Community consensus: 16x maps need 20–32GB+ system memory for the first shader compile.

## Open problem

The client-side crash during actual map rendering (not just server-side data sync) is **not solved**. Symptoms that remain even with every fix above applied:
- Client hangs at "100% compiling shaders" / a specific loading percentage, non-responsive, RAM climbing
- Occasional hard crash in `nvwgf2umx.dll` (`0xC0000005`)
- `allocReg` overflow recurs unpredictably even server-side with an unchanged DX11 config

If you've solved this specific combination (16x map, full client render, consumer GPU) — or have a GIANTS Editor / SDK angle on why `allocReg`'s tile registry has a hard cap in the first place — please open an issue.

## Contributing

If you hit the same crash signature on a different 16x map, open an issue with your server log's `allocReg` count before/after switching to DX11, and whether it stayed at 0 across multiple restarts or came back like it did here — more data points make the root-cause case stronger for a potential GIANTS bug report.

## License

Documentation licensed under [CC BY 4.0](LICENSE). Maintained by **KeilerHirsch**.
