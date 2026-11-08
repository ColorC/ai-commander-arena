-- ai-commander-arena P0 probe, loaded by recorder.lua through run.lua `extra`. Records go to rec.jsonl (k = "p0_*").
--   P0.2 real-time gate: the job writes <out>\gate.txt = off | sleep | pause. "sleep" blocks the minute callback
--        until wall time catches up with game time (arena.sleep, rechecking gate.txt every 200 ms); "pause" calls
--        GameAPI.Pause() and writes <out>\gate-state.json { paused, resume_at_ms } for the job to post F9 at that time.
--   P0.3 when orders take effect: one move order issued during execution (in the minute callback) and one issued in
--        an Orders phase (job calls p0_orders(side) over telnet); both units are tracked for 10 game minutes.
--   P0.8 doctrine: GetUnitDoctrine / GetUnitEmissionStatus for one unit of every icon type (doctrine.json).
--   P0.10 deployment: SetUnitStatus location of one unit at load, read back (p0_deploy).
--   P0.5 end-of-game results: the job shoots the result pages of the information center (p0_shot).
local A, J, EMIT, OUT = ARENA, ARENA_JSON, ARENA_EMIT, ARENA_OUT
-- season 2 calibration (run.lua calibration = true): none of the test actions above that touch the game (P0.10 moved a
-- side-0 unit one hex at load and never back, P0.3 gave one an order at 120 s). freeze_side = 0 or 1: see freeze_minute.
local RUN = ARENA_RUN or {}
local CALIBRATION = RUN.calibration == true
local FREEZE = tonumber(RUN.freeze_side)
if FREEZE and FREEZE < 0 then FREEZE = nil end

local function readfile(p)
  local f = io.open(p, "r")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

local function writefile(p, s)
  local f = io.open(p, "w")
  if f then f:write(s); f:close() end
end

local function try(fn, ...)
  local ok, r = pcall(fn, ...)
  if ok then return r end
  return { error = tostring(r) }
end

local function units(side)
  local ok, names = pcall(GameAPI.GetUnitNames, side)
  local list = {}
  if ok and type(names) == "table" then for _, n in pairs(names) do list[#list + 1] = n end end
  table.sort(list)
  return list
end

local function shifted(loc, rows) -- "CCRR" hex number, moved by rows inside 1..16
  loc = tonumber(loc) or 0
  local col, row = loc // 100, loc % 100
  local r = row + rows
  if r < 1 or r > 16 then r = row - rows end
  return tostring(col * 100 + r)
end

-- P0.10 deployment: at load (phase "load") and again by the job in each side's setup phase ------------------
local function deploy_test(side, phase)
  for _, n in ipairs(units(side)) do
    local s = try(GameAPI.GetUnitStatus, n)
    if s.location and s.status ~= "Reinforcement" then
      local to = shifted(s.location, 1)
      local results = {}
      for _, v in ipairs({ to, tonumber(to) }) do -- string and integer forms
        results[#results + 1] = { arg = v, type = type(v), result = try(GameAPI.SetUnitStatus, n, { location = v }),
                                  readback = try(GameAPI.GetUnitStatus, n).location }
      end
      EMIT({ k = "p0_deploy", phase = phase, side = side, unit = n, from = s.location, to = to, tries = results })
      return n
    end
  end
end

function p0_deploy(side)
  local n = deploy_test(tonumber(side) or 0, "setup")
  ARENA_FLUSH()
  return n
end

-- job: click a control in one of the game's dialogs (hand-over "Pass control to <side> player", announcements,
-- "Secure Transmission" turn-resolution viewing choice, ...)
function p0_click(cls, text)
  local h, info = A.click(cls, text)
  EMIT({ k = "p0_click", cls = cls, text = text, hwnd = h, info = info, sec = last_sec, wall_ms = A.now() })
  return h, info
end

-- job: close a window that has no buttons (hot-seat "Turn Resolution Replay")
function p0_close(cls)
  local h, info = A.close(cls)
  EMIT({ k = "p0_close", cls = cls, hwnd = h, info = info, sec = last_sec, wall_ms = A.now() })
  return h, info
end

-- P0.8 doctrine ---------------------------------------------------------------------------------------
local function doctrine_dump()
  local out, forms, picked = { by_icon = {}, forms = {} }, {
    { "empty", {} }, { "current", { sop_choice = "current" } }, { "index0", { 0 } }, { "default", { sop_choice = 0 } },
  }, {}
  local first = units(0)[1]
  for _, f in ipairs(forms) do out.forms[f[1]] = try(GameAPI.GetUnitDoctrine, first, f[2]) end
  for side = 0, 1 do
    for _, n in ipairs(units(side)) do
      local s = try(GameAPI.GetUnitStatus, n)
      local key = side .. ":" .. tostring(s.icon_type)
      if not picked[key] then
        picked[key] = true
        out.by_icon[key] = { unit = n, doctrine = try(GameAPI.GetUnitDoctrine, n, {}),
                             emission = try(GameAPI.GetUnitEmissionStatus, n) }
      end
    end
  end
  writefile(OUT .. "doctrine.json", J(out) .. "\n")
  EMIT({ k = "p0_doctrine", icons = (function() local c = 0 for _ in pairs(out.by_icon) do c = c + 1 end return c end)() })
end

-- P0.3 orders timing ----------------------------------------------------------------------------------
local tracked = {} -- unit -> { tag, until_sec }
local exec_done, orders_done = false, {}

local function track(sec)
  for n, t in pairs(tracked) do
    if sec > t.until_sec then tracked[n] = nil else
      local s = try(GameAPI.GetUnitStatus, n)
      EMIT({ k = "p0_order_track", tag = t.tag, unit = n, sec = sec, location = s.location, status = s.status,
             orders = try(GameAPI.GetUnitOrders, n) })
    end
  end
end

local function issue(n, tag, sec)
  local s = try(GameAPI.GetUnitStatus, n)
  local to = shifted(s.location, 2)
  local before = try(GameAPI.GetUnitOrders, n)
  local r = try(GameAPI.SetUnitOrders, n, { { orders = "Move Hasty", destination_location = to } })
  EMIT({ k = "p0_order_issue", tag = tag, unit = n, sec = sec, from = s.location, to = to, result = r,
         before = before, after = try(GameAPI.GetUnitOrders, n), wall_ms = A and A.now() })
  tracked[n] = { tag = tag, until_sec = (sec or 0) + 600 }
end

local function pick(side, skip)
  for _, n in ipairs(units(side)) do
    local s = try(GameAPI.GetUnitStatus, n)
    if not skip[n] and s.status ~= "Reinforcement" and s.location and s.icon_type ~= "HQ" then return n end
  end
end

local used = {}
local last_sec = 0

-- called by the job over telnet while a side sits in its Orders phase
function p0_orders(side)
  side = tonumber(side) or 0
  if orders_done[side] then return "already" end
  local n = pick(side, used)
  if not n then return "no unit" end
  used[n] = true; orders_done[side] = true
  issue(n, "orders_phase_side" .. side, last_sec)
  ARENA_FLUSH()
  return n
end

-- P0.2 gate -------------------------------------------------------------------------------------------
local gate = { mode = "off" }

local function gate_mode()
  local s = readfile(OUT .. "gate.txt")
  return s and s:match("%a+") or "off"
end

local function gate_minute(sec)
  local mode = gate_mode()
  if mode ~= gate.mode then
    gate = { mode = mode, sec0 = sec, wall0 = A.now() }
    EMIT({ k = "p0_gate", event = "mode", mode = mode, sec = sec, wall_ms = gate.wall0 })
  end
  if mode == "off" then return end
  local target = gate.wall0 + (sec - gate.sec0) * 1000
  local now = A.now()
  if mode == "sleep" then
    while now < target do
      if gate_mode() ~= "sleep" then
        EMIT({ k = "p0_gate", event = "released", sec = sec, wall_ms = now })
        break
      end
      A.sleep(math.min(200, target - now))
      now = A.now()
    end
  elseif mode == "pause" and target - now > 300 then
    writefile(OUT .. "gate-state.json", J({ paused = true, resume_at_ms = target, sec = sec, at_ms = now }) .. "\n")
    gate.paused_sec = sec
    try(GameAPI.Pause)
  end
  EMIT({ k = "p0_gate", event = "tick", mode = mode, sec = sec, wall_ms = A.now(), target_ms = target })
end

-- job calls this (telnet) when resume_at_ms has passed: F9 resumes turn resolution
function p0_resume()
  writefile(OUT .. "gate-state.json", J({ paused = false, at_ms = A.now() }) .. "\n")
  if gate.paused_sec and last_sec > gate.paused_sec then -- Pause() did not hold: F9 would pause it instead
    EMIT({ k = "p0_gate", event = "not_paused", sec = last_sec, paused_sec = gate.paused_sec, wall_ms = A.now() })
    return nil, "not paused"
  end
  local h, err = A.key(0x78)
  EMIT({ k = "p0_gate", event = "resume_f9", sec = last_sec, wall_ms = A.now(), hwnd = h, err = err })
  return h, err
end

-- P0.5 end-of-game results ----------------------------------------------------------------------------
-- The end-of-game Scenario Information Center draws its pages itself (no window text): the job clicks each
-- result page and shoots it (<out>\<name>.bmp) for text recognition; p0_controls lists a window's controls.
local function save(name, v) writefile(OUT .. name .. ".json", J(v) .. "\n") end

function p0_controls(cls)
  local c, err = A.children(cls)
  save("controls-" .. cls, c or { error = err })
  return c and #c or err
end

function p0_shot(cls, name)
  local w, h = A.shot(cls, OUT .. name .. ".bmp")
  EMIT({ k = "p0_shot", cls = cls, name = name, w = w, h = h, sec = last_sec })
  return w, h
end

-- freeze (season 2 calibration "this side never gives an order", for the side the computer cannot be given alone):
-- in a computer-vs-computer game, every game minute each unit of the frozen side is compared with the orders it was
-- last allowed to have. Orders finishing in sequence (the list became its own tail) are accepted; anything else is
-- the side's AI giving a new order and is put back (SetUnitOrders with the last allowed list). A new order runs for
-- at most one game minute before it is undone. Every revert is a k = "freeze_revert" record.
local frozen = {}
local function okey(o)
  return table.concat({ tostring(o.orders), tostring(o.destination_location), tostring(o.target_location),
                        tostring(o.target_unit) }, "|")
end
local function okeys(list)
  local t = {}
  for i, o in ipairs(list) do t[i] = okey(o) end
  return t
end
local function is_tail(cur, prev)
  local off = #prev - #cur
  if off < 0 then return false end
  for i = 1, #cur do if cur[i] ~= prev[i + off] then return false end end
  return true
end
local function freeze_minute(sec)
  local reverts = 0
  for _, n in ipairs(units(FREEZE)) do
    local cur = try(GameAPI.GetUnitOrders, n)
    if type(cur) == "table" and not cur.error then
      local f, ck = frozen[n], okeys(cur)
      if not f or is_tail(ck, f.keys) then
        frozen[n] = { list = cur, keys = ck }
      else
        local ok = try(GameAPI.SetUnitOrders, n, f.list)
        reverts = reverts + 1
        EMIT({ k = "freeze_revert", sec = sec, unit = n, ai = ck, kept = f.keys, ok = ok })
      end
    end
  end
  return reverts
end

-- VP location probe (run.lua vp_probe = { hexes }, vp_probe_side = n): the scenario's VP hexes cannot be read
-- (GetMarkers is always empty, P2) and some briefings do not list them. At load, units of the other side standing on
-- a probed hex are moved off it, then one active ground unit of vp_probe_side is put on each probed hex
-- (SetUnitStatus with an integer hex, P0.10). After a few game minutes the end screen's VP Locations line of that
-- side is the sum of the values of the probed hexes that are VP locations. One k = "vp_probe" record per hex.
local NOT_GROUND = { Helo = true, Air = true, Aircraft = true, ["Fixed Wing"] = true }
local function vp_probe_setup(hexes, side)
  local want = {}
  for _, h in ipairs(hexes) do want[tonumber(h)] = true end
  local movers = {}
  for _, n in ipairs(units(side)) do
    local st = try(GameAPI.GetUnitStatus, n)
    if st.status == "Active" and not NOT_GROUND[st.icon_type or ""] and not want[tonumber(st.location)] then
      movers[#movers + 1] = n
    end
  end
  for _, n in ipairs(units(1 - side)) do
    local st = try(GameAPI.GetUnitStatus, n)
    local loc = tonumber(st.location)
    if loc and want[loc] then
      local to, k = loc, 0
      repeat k = k + 1; to = tonumber(shifted(loc, k + 2)) until not want[to] or k > 10
      local ok = try(GameAPI.SetUnitStatus, n, { location = to })
      EMIT({ k = "vp_probe_clear", unit = n, from = loc, to = to, ok = ok, readback = try(GameAPI.GetUnitStatus, n).location })
    end
  end
  for i, h in ipairs(hexes) do
    local n = movers[i]
    local ok = n and try(GameAPI.SetUnitStatus, n, { location = tonumber(h) })
    EMIT({ k = "vp_probe", hex = tonumber(h), side = side, unit = n, ok = ok,
           readback = n and try(GameAPI.GetUnitStatus, n).location })
  end
end

-- hooks -----------------------------------------------------------------------------------------------
local base_minute = GameAPI.OnGameMinuteElapsed
GameAPI.OnGameMinuteElapsed = function(sec)
  base_minute(sec)
  if type(sec) ~= "number" then return end
  last_sec = sec
  local ok, err = pcall(function()
    track(sec)
    if FREEZE then freeze_minute(sec) end
    if not exec_done and sec >= 120 and not CALIBRATION then
      exec_done = true
      local n = pick(0, used)
      if n then used[n] = true; issue(n, "execution", sec) end
    end
    if A then gate_minute(sec) end
  end)
  if not ok then EMIT({ k = "p0_error", sec = sec, err = tostring(err) }) end
end

if FREEZE then
  local okf, errf = pcall(freeze_minute, 0) -- the orders every unit of the frozen side starts with
  if not okf then EMIT({ k = "p0_error", where = "freeze", err = tostring(errf) }) end
end
if type(RUN.vp_probe) == "table" then
  local okv, errv = pcall(vp_probe_setup, RUN.vp_probe, tonumber(RUN.vp_probe_side) or 1)
  if not okv then EMIT({ k = "p0_error", where = "vp_probe", err = tostring(errv) }) end
end
if not CALIBRATION then
  local okd, errd = pcall(deploy_test, 0, "load")
  if not okd then EMIT({ k = "p0_error", where = "deploy", err = tostring(errd) }) end
end
local okt, errt = pcall(doctrine_dump)
if not okt then EMIT({ k = "p0_error", where = "doctrine", err = tostring(errt) }) end
EMIT({ k = "p0_loaded", arena = A ~= nil, calibration = CALIBRATION, freeze_side = FREEZE })
ARENA_FLUSH()
