-- Offline check of arena-bridge.lua (loaded by recorder.lua) against a fake GameAPI:
--   lua test-bridge.lua <recorder.lua> <arena-bridge.lua> <out dir with trailing slash>
-- Checks: state.json written at load / start / every minute with both sides' units; a release batch applies every
-- kind of command, reads it back and writes release-<batch_id>.json; a batch is loaded as data only.
local recorder, bridge, out = arg[1], arg[2], arg[3]
local units = {
  ["TF Alamo"] = { side = 0, location = 2118, status = "Active" },
  ["9 MR"] = { side = 1, location = 5120, status = "Active" },
}
local orders, calls = {}, {}
GameAPI = {
  GetScenario = function() return { title = "Alamo at Soest", path = "x.scn" } end,
  GetUnitNames = function(side)
    local r = {}
    for n, u in pairs(units) do if u.side == side then r[#r + 1] = n end end
    return r
  end,
  GetUnitStatus = function(n) local u = units[n]; return { name = n, side = u.side, location = u.location, status = u.status } end,
  GetUnitOrders = function(n) return orders[n] or {} end,
  GetUnitSubunits = function(n) return { { index = 1, name = "M1A1", status = "Runner" } } end,
  SetUnitOrders = function(n, o) orders[n] = o; return true end,
  SetUnitStatus = function(n, st) if math.type(st.location) ~= "integer" then return false end units[n].location = st.location; return true end,
  GetUnitDoctrine = function(n, choice) return { rules_of_engagement = calls.roe or "default" } end,
  SetUnitDoctrine = function(n, choice, sop) calls.roe = sop.rules_of_engagement; return true end,
  GetUnitEmissionStatus = function(n) return { radar = calls.radar or "on" } end,
  SetUnitEmissionStatus = function(n, st) calls.radar = st.radar; return true end,
  IssueFireSupportRequest = function(side, req) calls.fire = { side = side, req = req }; return true end,
}
-- fake arena.dll: a clock that only moves when the bridge sleeps; on_sleep plays the relay
local clock_ms, sleeps, on_sleep = 1000000, 0, nil
package.preload.arena = function()
  return { now = function() return clock_ms end,
           sleep = function(ms)    -- like arena.dll (luaL_checkinteger): a fractional ms is an error
             ms = math.tointeger(ms) or error("bad argument #1 to 'sleep' (number has no integer representation)")
             clock_ms = clock_ms + ms; sleeps = sleeps + 1; if on_sleep then on_sleep() end
           end,
           windows = function() return {} end }
end
ARENA_CFG = { out_dir = out, keyframe_seconds = 1800, extra = bridge }
dofile(recorder)

local function read(name)
  local f = assert(io.open(out .. name, "r"), "missing " .. name)
  local s = f:read("a")
  f:close()
  return s
end
local function check(cond, what) print((cond and "ok   " or "FAIL ") .. what); if not cond then FAILED = true end end

check(read("state.json"):find('"phase_hint":"load"', 1, true), "state.json at load")
GameAPI.OnScenarioStart("Alamo at Soest")
GameAPI.OnGameMinuteElapsed(60)
local st = read("state.json")
check(st:find('"sec":60', 1, true) and st:find('"TF Alamo"', 1, true) and st:find('"9 MR"', 1, true), "state.json at minute 60 has both sides")

local batch = out .. "release-s0-0001.lua"
local f = io.open(batch, "w")
f:write([[return { batch_id = "s0-0001", side = 0, cmds = {
  { cmd_id = "c1", kind = "orders", unit = "TF Alamo", orders = { { orders = "Move Hasty", destination_location = 2220 } } },
  { cmd_id = "c2", kind = "doctrine", unit = "TF Alamo", sop_choice = {}, sop = { rules_of_engagement = "hold" } },
  { cmd_id = "c3", kind = "emission", unit = "TF Alamo", emission = { radar = "off" } },
  { cmd_id = "c4", kind = "fire_support", request = { request_type = "static_target", location = 3010 } },
  { cmd_id = "c5", kind = "deploy", unit = "TF Alamo", location = 2119 },
  { cmd_id = "c6", kind = "kill", unit = "9 MR" },
} }]])
f:close()
check(arena_release(batch) == 6, "arena_release applied 6 commands")
local res = read("release-s0-0001.json")
check(res:find('"cmd_id":"c1","error"', 1, true) == nil and res:find('"destination_location":2220', 1, true), "orders read back")
check(calls.roe == "hold" and calls.radar == "off" and calls.fire.side == 0 and units["TF Alamo"].location == 2119, "doctrine / emission / fire support / deploy applied")
check(res:find('"cmd_id":"c6","error":"unknown kind"', 1, true) ~= nil and units["9 MR"] ~= nil, "unknown kind refused")

calls.fire = nil
check(arena_release(batch) == 6 and calls.fire == nil, "a batch sent again is not applied twice")

f = io.open(out .. "release-evil.lua", "w")
f:write([[os.remove("x"); return { batch_id = "evil", cmds = {} }]])
f:close()
local n, err = arena_release(out .. "release-evil.lua")
check(n == nil and tostring(err):find("bad batch", 1, true), "batch with code is refused (no globals): " .. tostring(err))
check(arena_state_now() > 0, "arena_state_now")

-- lockstep: minute 120 waits for the referee's ack (written by the "relay" after 5 sleeps, with gate sleep)
local function gate(text) local g = io.open(out .. "gate.txt", "w"); g:write(text); g:close() end
gate("off 60")
local t120, ack_sleeps = clock_ms, nil
sleeps, on_sleep = 0, function() if sleeps == 5 then gate("sleep 120"); ack_sleeps = sleeps end end
GameAPI.OnGameMinuteElapsed(120)
check(ack_sleeps == 5, "minute 120 waited for the ack (" .. tostring(ack_sleeps) .. " sleeps)")
-- the anchor minute itself is held half a minute (the fake game computes in no time), so game time is rounded
check(clock_ms - t120 >= 30000 and clock_ms - t120 < 30500, "anchor minute 120 held half a minute (" .. (clock_ms - t120) .. " ms)")
-- minute 180: no ack comes (the relay is gone): gives up after 3 s, then the gate holds until 60 s after the anchor
local t_anchor = clock_ms
sleeps, on_sleep = 0, nil
GameAPI.OnGameMinuteElapsed(180)
check(clock_ms - t_anchor >= 60000 and clock_ms - t_anchor < 64000, "gate held minute 180 to wall time (" .. (clock_ms - t_anchor) .. " ms)")
ARENA_FLUSH()
local rec = read("rec.jsonl")
check(rec:find('"event":"step_timeout"', 1, true) and rec:find('"mode":"sleep","sec":120', 1, true), "step timeout and gate anchor recorded")
-- a vanished gate file keeps the last reading (no false "off")
os.remove(out .. "gate.txt")
sleeps, on_sleep = 0, function() if sleeps == 3 then gate("sleep 240") end end
GameAPI.OnGameMinuteElapsed(240)
ARENA_FLUSH()
check(not read("rec.jsonl"):find('"mode":"off","sec":240', 1, true), "missing gate file is not read as off")
-- released mid-hold (the agent ended its turn), then "sleep" again a minute later: a new anchor, not the old one
gate("sleep 300")
sleeps, on_sleep = 0, function() if sleeps == 5 then gate("off 300") end end
GameAPI.OnGameMinuteElapsed(300)
gate("sleep 360")
sleeps, on_sleep = 0, nil
GameAPI.OnGameMinuteElapsed(360)
local t360 = clock_ms
GameAPI.OnGameMinuteElapsed(420)
ARENA_FLUSH()
rec = read("rec.jsonl")
check(rec:find('"event":"released","k":"gate","sec":300', 1, true) and rec:find('"mode":"sleep","sec":360', 1, true),
      "release mid-hold, then a new anchor at 360")
check(clock_ms - t360 >= 59000 and clock_ms - t360 <= 60500, "minute 420 held 60 s after the new anchor (" .. (clock_ms - t360) .. " ms)")
-- a game that takes a few ms per minute makes the compute-time mean fractional (P3 rehearsal 6: 31 sleep errors)
local err_before = select(2, read("rec.jsonl"):gsub('"k":"bridge_error"', ""))
for m = 480, 720, 60 do
  clock_ms = clock_ms + 7
  GameAPI.OnGameMinuteElapsed(m)
end
ARENA_FLUSH()
local err_after = select(2, read("rec.jsonl"):gsub('"k":"bridge_error"', ""))
check(err_after == err_before, "fractional compute time: no sleep error (" .. (err_after - err_before) .. " new bridge errors)")
if FAILED then os.exit(1) end
