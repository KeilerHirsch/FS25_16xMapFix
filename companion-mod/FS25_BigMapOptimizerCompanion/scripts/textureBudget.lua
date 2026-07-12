-- ============================================================================
--  BigMap Optimizer Companion  --  Texture-Streaming Budget
--  "The Man, The Mythos, The Legend : KeilerHirsch"
--
--  Farming Simulator 25 caps high-resolution texture streaming at ~4 GB of
--  VRAM by default. On large (16x / 32x) maps that starves the terrain and
--  causes texture flicker / pop-in. This raises the cap to a value you set
--  once in a plain settings file -- no code editing -- surviving mod updates.
--
--  It is a purely client-side rendering setting: it changes no gameplay, no
--  savegame and no multiplayer state, so it is fully MP-safe and needs no
--  server/client hash matching for its effect.
--
--  The engine cannot report the card's physical VRAM to Lua (verified against
--  the GIANTS engine Lua source: no VRAM query exists, and the engine does NOT
--  treat setTextureStreamingMemoryBudget(-1) as "use all VRAM" -- it falls
--  back to a small default). So the amount is read from the settings file
--  rather than guessed; the fixer TOOL can pre-fill it from a VRAM probe later.
--
--  Copyright (C) 2026  KeilerHirsch.  GNU GPL v3 or later.
-- ============================================================================

TextureBudgetCompanion = {}

local MOD = "BigMap Optimizer Companion"
local BYTES_PER_GIB = 1073741824
local DEFAULT_GIB = 6    -- safe for an 8 GB card: (VRAM in GB) minus 2 headroom
local MIN_GIB = 2        -- never drop below the engine's own fallback
local MAX_GIB = 64       -- sanity ceiling against a fat-fingered config value

local function log(msg)
    if Logging ~= nil and Logging.info ~= nil then
        Logging.info("[%s] %s", MOD, msg)
    else
        print(("[%s] %s"):format(MOD, msg))
    end
end

local function settingsPath()
    return getUserProfileAppPath() .. "modSettings/FS25_BigMapOptimizerCompanion.xml"
end

--- Return the configured budget in GiB, creating a default settings file on
--- the first run so the player edits one clear number, not Lua code.
local function readConfiguredGiB()
    local path = settingsPath()

    if not fileExists(path) then
        createFolder(getUserProfileAppPath() .. "modSettings")
        local xml = createXMLFile("tbc", path, "textureStreamingBudget")
        setXMLFloat(xml, "textureStreamingBudget#vramGiB", DEFAULT_GIB)
        setXMLString(xml, "textureStreamingBudget#help",
            "vramGiB = how much graphics-card memory FS25 may use for textures. "
            .. "Rule of thumb: your VRAM in GB minus 2. Only raise above 4 if "
            .. "your card actually has more than 4 GB.")
        saveXMLFile(xml)
        delete(xml)
        log(string.format("created settings %s (default %d GiB)", path, DEFAULT_GIB))
        return DEFAULT_GIB
    end

    local xml = loadXMLFile("tbc", path)
    if xml == nil or xml == 0 then
        log("settings file could not be read; using default")
        return DEFAULT_GIB
    end
    local gib = getXMLFloat(xml, "textureStreamingBudget#vramGiB")
    delete(xml)
    if gib == nil then
        log("settings present but vramGiB missing; using default")
        return DEFAULT_GIB
    end
    return gib
end

function TextureBudgetCompanion.apply()
    if setTextureStreamingMemoryBudget == nil then
        log("this engine build has no setTextureStreamingMemoryBudget(); nothing to do.")
        return
    end

    local gib = readConfiguredGiB()
    if gib < MIN_GIB then gib = MIN_GIB end
    if gib > MAX_GIB then gib = MAX_GIB end

    local bytes = math.floor(gib * BYTES_PER_GIB)
    setTextureStreamingMemoryBudget(bytes)
    log(string.format("texture streaming budget set to %.1f GiB (%d bytes).", gib, bytes))
end

-- Runs once when the mod is loaded (via <extraSourceFiles> in modDesc.xml),
-- exactly like the original community mod it supersedes.
TextureBudgetCompanion.apply()
