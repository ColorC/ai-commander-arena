-- Offline check of recorder.lua against a fake GameAPI:
--   lua test-recorder.lua <recorder.lua> <out dir with trailing slash> [extra script, e.g. p0-probe.lua]
local recorder, out, extra = arg[1], arg[2], arg[3]
local units = {
  ["HQ 9/12 L"] = { side = 0, location = 412, status = "Active", morale = 90 },
  ["HQ 7 IGRB"] = { side = 1, location = 5120, status = "Reinforcement", morale = 85 },
}
local rounds = 80
GameAPI = {
  GetScenario = function() return { title = "Chance Encounter", path = "x.scn" } end,
  GetUnitNames = function(side)
    local r = {}
    for n, u in pairs(units) do if u.side == side then r[#r + 1] = n end end
    return r
  end,
  GetUnitStatus = function(n) local u = units[n]; return { name = n, side = u.side, location = u.location, status = u.status, morale = u.morale } end,
  GetUnitOrders = function(n) return { { orders = "Screen", start_location = tostring(units[n].location) } } end,
  SetUnitStatus = function(n, st) if st.location then units[n].location = tonumber(st.location) end return true end,
  SetUnitOrders = function(n, o) return true end,
  GetUnitDoctrine = function(n, choice) return { sop = "test" } end,
  GetUnitEmissionStatus = function(n) return { radar = "off" } end,
  Pause = function() end,
  GetUnitSubunits = function(n) return { { index = 1, name = "Sultan", status = "Runner", ammo = { { ammo_id = "AAA7", rounds = tostring(rounds), max_rounds = "80" } } } } end,
}
ARENA_CFG = { out_dir = out, keyframe_seconds = 120, extra = extra }
dofile(recorder)
GameAPI.OnScenarioStart("Chance Encounter")
GameAPI.OnGameMinuteElapsed(60)
units["HQ 9/12 L"].location = 413; rounds = 75
GameAPI.OnGameMinuteElapsed(120)
GameAPI.OnGameEventElapsed({ type = "fire", text = "caf\233 \"q\"" }, 125)
GameAPI.OnTokenOrderStart({ token_index = 1 }, 130)
units["HQ 7 IGRB"] = nil
GameAPI.OnGameMinuteElapsed(180)
GameAPI.OnGameMinuteElapsed(nil) -- bad input must be logged, not raised
GameAPI.OnScenarioEnd(240)
if p0_orders then print("p0_orders:", p0_orders(0)) end
print("f9:", arena_f9())
print("snapshot records:", arena_snapshot("test"))
