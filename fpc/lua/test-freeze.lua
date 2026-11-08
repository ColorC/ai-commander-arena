-- Offline check of p0-probe.lua's calibration switch and side freeze against a fake GameAPI:
--   lua test-freeze.lua <recorder.lua> <p0-probe.lua> <out dir with trailing slash>
-- Prints "FAIL ..." lines and exits 1 on any failure.
local recorder, probe, out = arg[1], arg[2], arg[3]
local units = {
  ["1/A/1-35"] = { side = 0, location = 412 },
  ["1.AK/MSR-7"] = { side = 1, location = 2823 },
  ["2.AK/MSR-7"] = { side = 1, location = 2830 },
}
local orders = {
  ["1/A/1-35"] = { { orders = "Screen", start_location = "412" } },
  ["1.AK/MSR-7"] = { { orders = "Move Deliberate", destination_location = "2423" },
                     { orders = "Move Deliberate", destination_location = "2220" },
                     { orders = "Screen", start_location = "2220" } },
  ["2.AK/MSR-7"] = { { orders = "Hold", start_location = "2830" } },
}
local sets = {}
local function copy(list)
  local r = {}
  for i, o in ipairs(list) do local c = {}; for k, v in pairs(o) do c[k] = v end; r[i] = c end
  return r
end
GameAPI = {
  GetScenario = function() return { title = "The Armored Fist", path = "x.scn" } end,
  GetUnitNames = function(side)
    local r = {}
    for n, u in pairs(units) do if u.side == side then r[#r + 1] = n end end
    return r
  end,
  GetUnitStatus = function(n) local u = units[n]; return { name = n, side = u.side, location = u.location, status = "Active" } end,
  GetUnitOrders = function(n) return copy(orders[n]) end,
  SetUnitStatus = function(n, st) if st.location then units[n].location = tonumber(st.location) end return true end,
  SetUnitOrders = function(n, o) sets[#sets + 1] = n; orders[n] = copy(o); return true end,
  GetUnitDoctrine = function() return {} end,
  GetUnitEmissionStatus = function() return {} end,
  Pause = function() end,
  GetUnitSubunits = function() return {} end,
}
ARENA_CFG = { out_dir = out, keyframe_seconds = 600, extra = probe, calibration = true, freeze_side = 1 }
dofile(recorder)

local failed = false
local function check(cond, what) if not cond then failed = true; print("FAIL " .. what) else print("ok   " .. what) end end

GameAPI.OnScenarioStart("The Armored Fist")
check(units["1/A/1-35"].location == 412, "calibration: no unit moved at load")
GameAPI.OnGameMinuteElapsed(60)
check(#sets == 0, "nothing reverted while no order changed")
-- the first move finished: the list became its own tail
table.remove(orders["1.AK/MSR-7"], 1)
-- red's AI gives 2.AK a new order
orders["2.AK/MSR-7"] = { { orders = "Assault", target_location = "2425" } }
GameAPI.OnGameMinuteElapsed(120)
check(#sets == 1 and sets[1] == "2.AK/MSR-7", "the AI's new order is reverted, the finished move is not")
check(orders["2.AK/MSR-7"][1].orders == "Hold", "2.AK is back on Hold")
check(#orders["1.AK/MSR-7"] == 2, "1.AK keeps its remaining preset orders")
check(#sets == 1 or sets[2] ~= "1/A/1-35", "calibration: no test order for side 0 at 120 s")
-- the AI replaces 1.AK's remaining plan
orders["1.AK/MSR-7"] = { { orders = "Move Hasty", destination_location = "2020" } }
GameAPI.OnGameMinuteElapsed(180)
check(#sets == 2 and orders["1.AK/MSR-7"][1].destination_location == "2220", "1.AK goes back to its remaining preset plan")
-- side 0 is never touched
orders["1/A/1-35"] = { { orders = "Move Hasty", destination_location = "420" } }
GameAPI.OnGameMinuteElapsed(240)
check(#sets == 2, "side 0 orders are not frozen")
GameAPI.OnScenarioEnd(300)
os.exit(failed and 1 or 0)
