-- Offline check of p0-probe.lua's VP location probe against a fake GameAPI:
--   lua test-vpprobe.lua <recorder.lua> <p0-probe.lua> <out dir with trailing slash>
-- Prints "FAIL ..." lines and exits 1 on any failure.
local recorder, probe, out = arg[1], arg[2], arg[3]
local units = {
  ["HQ 1 Bde"] = { side = 0, location = 1205, status = "Active", icon_type = "HQ" },
  ["1/A/1-35"] = { side = 0, location = 2223, status = "Active", icon_type = "Tank" },
  ["1.AK/MSR-7"] = { side = 1, location = 2823, status = "Active", icon_type = "Tank" },
  ["Hubschr 1"] = { side = 1, location = 3001, status = "Active", icon_type = "Helo" },
  ["2.AK/MSR-7"] = { side = 1, location = 2830, status = "Active", icon_type = "APC" },
  ["3.AK/MSR-7"] = { side = 1, location = 3330, status = "Reinforcement", icon_type = "APC" },
}
GameAPI = {
  GetScenario = function() return { title = "The Armored Fist", path = "x.scn" } end,
  GetUnitNames = function(side)
    local r = {}
    for n, u in pairs(units) do if u.side == side then r[#r + 1] = n end end
    return r
  end,
  GetUnitStatus = function(n) local u = units[n]; return { name = n, side = u.side, location = u.location, status = u.status, icon_type = u.icon_type } end,
  GetUnitOrders = function() return {} end,
  SetUnitStatus = function(n, st)
    if type(st.location) ~= "number" then return false end -- the game takes integer hexes only (P0.10)
    units[n].location = st.location
    return true
  end,
  SetUnitOrders = function() return true end,
  GetUnitDoctrine = function() return {} end,
  GetUnitEmissionStatus = function() return {} end,
  Pause = function() end,
  GetUnitSubunits = function() return {} end,
}
ARENA_CFG = { out_dir = out, keyframe_seconds = 600, extra = probe, calibration = true, vp_probe = { 2223, 2120 }, vp_probe_side = 1 }
dofile(recorder)

local failed = false
local function check(cond, what) if not cond then failed = true; print("FAIL " .. what) else print("ok   " .. what) end end
GameAPI.OnScenarioStart("The Armored Fist")
check(units["1/A/1-35"].location ~= 2223 and units["1/A/1-35"].location ~= 2120, "the blue unit on 2223 was moved off the probed hexes")
local on = {}
for n, u in pairs(units) do if u.side == 1 then on[u.location] = n end end
check(on[2223] and on[2120], "a red unit stands on each probed hex")
check(units["Hubschr 1"].location == 3001, "helicopters are not used")
check(units["3.AK/MSR-7"].location == 3330, "reinforcements not yet on the map are not used")
check(units["HQ 1 Bde"].location == 1205, "other blue units stay")
GameAPI.OnScenarioEnd(300)
os.exit(failed and 1 or 0)
