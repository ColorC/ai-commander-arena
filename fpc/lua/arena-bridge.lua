-- ai-commander-arena match bridge (arena/DESIGN.md section 4), loaded by recorder.lua through run.lua `extra`.
-- Every game minute: writes <out>\state.json (every unit of both sides: status, orders, subunits; the umpire's view,
-- read only by the referee through relay.ps1), waits until the referee has seen it (lockstep), then applies the
-- real-time gate from <out>\gate.txt ("sleep": hold the game thread until wall time catches up with game time,
-- anchored where the gate turned on and rounded to the half minute; "off": run free).
-- Telnet (relay.ps1): arena_release(path) applies one release batch (only while the game waits in an Orders phase:
-- telnet runs its Lua on its own thread, DESIGN 3.4), arena_state_now() writes state.json outside a minute tick
-- (setup). arena_click / arena_close / arena_shot are kept for the probe jobs; the relay posts those itself.
local A, J, EMIT, OUT = ARENA, ARENA_JSON, ARENA_EMIT, ARENA_OUT

local function readfile(p)
  local f = io.open(p, "r")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

local function writefile(p, s) -- whole file at once: write a temporary file, then rename over the target
  local renamable = os and os.rename and os.remove
  local tmp = renamable and (p .. ".tmp") or p
  local f = io.open(tmp, "w")
  if not f then return false end
  f:write(s)
  f:close()
  if not renamable then return true end
  os.remove(p)
  return os.rename(tmp, p)
end

local function try(fn, ...)
  local ok, r = pcall(fn, ...)
  if ok then return r end
  return { error = tostring(r) }
end

local function now_ms() return A and A.now() or os.time() * 1000 end

-- state -------------------------------------------------------------------------------------------------------
local seq, last_sec = 0, nil

local function units()
  local out = {}
  for side = 0, 1 do
    local ok, names = pcall(GameAPI.GetUnitNames, side)
    if ok and type(names) == "table" then
      for _, n in pairs(names) do
        out[n] = { s = try(GameAPI.GetUnitStatus, n), o = try(GameAPI.GetUnitOrders, n), u = try(GameAPI.GetUnitSubunits, n) }
      end
    end
  end
  return out
end

local gate = { mode = "off" }

local function write_state(sec, hint)
  seq = seq + 1
  writefile(OUT .. "state.json", J({ seq = seq, sec = sec, wall_ms = now_ms(), phase_hint = hint, units = units(),
                                     gate = { mode = gate.mode, late_ms = gate.late_ms } }) .. "\n")
end

function arena_state_now()
  write_state(last_sec, "telnet")
  return seq
end

-- real-time gate (P0.2 method 1) and lockstep -------------------------------------------------------------------
-- gate.txt is "<mode> [<ack sec>]", written by relay.ps1 after every answered sync; <ack sec> is the game second of
-- the newest state the referee has processed. Each minute first waits (up to STEP_WAIT_MS) until the referee has seen
-- this minute, so its wake-ups and gate changes take effect at the minute that caused them; then "sleep" holds the
-- game thread until wall time catches up with game time (anchored where the gate turned on).
local STEP_WAIT_MS = 3000
local LEAD_MS = 30000 -- "sleep" holds each minute this much longer than wall0 + (sec - sec0), less its compute time

local last_read = { mode = "off" }

local function gate_read() -- an unreadable file keeps the last good reading
  local s = readfile(OUT .. "gate.txt")
  local mode, ack = (s or ""):match("^%s*(%a+)%s*(%d*)")
  if mode then last_read = { mode = mode, ack = tonumber(ack) } end
  return last_read.mode, last_read.ack
end

local function gate_minute(sec)
  local arrived = now_ms()
  if gate.done_at and sec == gate.done_sec + 60 then -- how long the game took to compute the last minute
    local c = math.max(0, math.min(arrived - gate.done_at, LEAD_MS))
    gate.compute_ms = gate.compute_ms and (gate.compute_ms * 0.7 + c * 0.3) or c
  end
  local mode, ack = gate_read()
  if ack ~= nil and A then
    local t_end = now_ms() + STEP_WAIT_MS
    while (ack or -1) < sec and now_ms() < t_end do
      A.sleep(20)
      mode, ack = gate_read()
    end
    if (ack or -1) < sec then EMIT({ k = "gate", event = "step_timeout", sec = sec, ack = ack, wall_ms = now_ms() }) end
  end
  if mode ~= gate.mode then
    gate = { mode = mode, sec0 = sec, wall0 = now_ms(), compute_ms = gate.compute_ms }
    EMIT({ k = "gate", event = "mode", mode = mode, sec = sec, wall_ms = gate.wall0 })
  end
  if mode == "sleep" and A then
    -- The game shows minute sec+60 once this callback returns and the minute is computed (compute_ms). Holding to
    -- wall0 + (sec - sec0) put the game about half a minute ahead of wall time on average: the first minute of every
    -- active spell came at once (P3 rehearsal 3: 58 short spells, 4320 game s in 2686 wall s). Holding half a
    -- minute longer, less the compute time, rounds game time to wall time instead
    local lead = LEAD_MS - math.min(gate.compute_ms or 0, LEAD_MS)
    local target = gate.wall0 + (sec - gate.sec0) * 1000 + lead
    local now = now_ms()
    gate.late_ms = now - target -- > 0: the game is behind wall time (cannot speed it up), < 0: held below
    while now < target do
      local m = gate_read()
      if m ~= "sleep" then
        -- the next "sleep" anchors anew (P3 rehearsal 10-30: a stale anchor let the game run ahead of an agent's turn)
        gate = { mode = m, sec0 = sec, wall0 = now, compute_ms = gate.compute_ms }
        EMIT({ k = "gate", event = "released", sec = sec, wall_ms = now })
        break
      end
      -- compute_ms is a running mean, so target can be fractional; arena.sleep takes whole ms (P3 rehearsal 6: 31
      -- "number has no integer representation" errors, each one ending that minute's hold early)
      A.sleep(math.max(1, math.floor(math.min(200, target - now))))
      now = now_ms()
    end
  end
  gate.done_at, gate.done_sec = now_ms(), sec
end

-- release batches (DESIGN 3.2) ----------------------------------------------------------------------------------
-- The batch file is Lua data written by the referee: return { batch_id = "...", side = 0, cmds = { {cmd_id = "...",
-- kind = "orders" | "doctrine" | "emission" | "fire_support" | "deploy", unit = "<name>", orders = | sop = | emission = |
-- request = | location = } } }. It is loaded as
-- data only (empty environment), every command is applied in order and read back.
local APPLY = {
  orders = function(c) return GameAPI.SetUnitOrders(c.unit, c.orders), try(GameAPI.GetUnitOrders, c.unit) end,
  doctrine = function(c)
    return GameAPI.SetUnitDoctrine(c.unit, c.sop_choice or {}, c.sop), try(GameAPI.GetUnitDoctrine, c.unit, c.sop_choice or {})
  end,
  emission = function(c)
    return GameAPI.SetUnitEmissionStatus(c.unit, c.emission or c.status), try(GameAPI.GetUnitEmissionStatus, c.unit)
  end,
  fire_support = function(c, side) return GameAPI.IssueFireSupportRequest(side, c.request), nil end,
  deploy = function(c)
    return GameAPI.SetUnitStatus(c.unit, { location = math.tointeger(c.location) }), try(GameAPI.GetUnitStatus, c.unit).location
  end,
}

local applied = {} -- batch_id -> number of commands: a batch sent again (the referee retries) is not applied twice

function arena_release(path)
  local text = readfile(path)
  if not text then return nil, "no file " .. tostring(path) end
  local chunk, err = load(text, "release", "t", {})
  if not chunk then return nil, err end
  local ok, batch = pcall(chunk)
  if not ok or type(batch) ~= "table" then return nil, "bad batch: " .. tostring(batch) end
  if batch.batch_id ~= nil and applied[batch.batch_id] then
    EMIT({ k = "release_repeat", batch_id = batch.batch_id, sec = last_sec })
    return applied[batch.batch_id]
  end
  local results = {}
  for i, c in ipairs(batch.cmds or {}) do
    local fn = APPLY[c.kind]
    local r = { cmd_id = c.cmd_id, kind = c.kind, unit = c.unit }
    if not fn then
      r.ok, r.error = false, "unknown kind"
    else
      local okc, ret, readback = pcall(fn, c, batch.side)
      r.ok, r.ret, r.readback = okc and ret == true, okc and ret or nil, readback
      if not okc then r.error = tostring(ret) end
    end
    results[i] = r
  end
  local out = { batch_id = batch.batch_id, side = batch.side, sec = last_sec, wall_ms = now_ms(), results = results }
  writefile(OUT .. "release-" .. tostring(batch.batch_id) .. ".json", J(out) .. "\n")
  EMIT({ k = "release", batch_id = batch.batch_id, side = batch.side, sec = last_sec, results = results })
  ARENA_FLUSH()
  if batch.batch_id ~= nil then applied[batch.batch_id] = #results end
  return #results
end

-- dialogs (relay.ps1) -------------------------------------------------------------------------------------------
function arena_click(cls, text)
  local h, info = A.click(cls, text)
  EMIT({ k = "click", cls = cls, text = text, hwnd = h, info = info, sec = last_sec, wall_ms = now_ms() })
  return h, info
end

function arena_close(cls)
  local h, info = A.close(cls)
  EMIT({ k = "close", cls = cls, hwnd = h, info = info, sec = last_sec, wall_ms = now_ms() })
  return h, info
end

function arena_shot(cls, name)
  local w, h = A.shot(cls, OUT .. name .. ".bmp")
  EMIT({ k = "shot", cls = cls, name = name, w = w, h = h, sec = last_sec })
  return w, h
end

-- hooks ---------------------------------------------------------------------------------------------------------
local base_minute = GameAPI.OnGameMinuteElapsed
GameAPI.OnGameMinuteElapsed = function(sec)
  base_minute(sec)
  if type(sec) ~= "number" then return end
  last_sec = sec
  local ok, err = pcall(function()
    write_state(sec, "minute")
    gate_minute(sec)
  end)
  if not ok then EMIT({ k = "bridge_error", sec = sec, err = tostring(err) }) end
end

for _, cb in ipairs({ "OnScenarioStart", "OnScenarioResume" }) do
  local base = GameAPI[cb]
  GameAPI[cb] = function(name, sec)
    base(name, sec)
    last_sec = (cb == "OnScenarioStart") and 0 or sec
    pcall(write_state, last_sec, cb)
  end
end

pcall(write_state, nil, "load")
EMIT({ k = "bridge_loaded", arena = A ~= nil })
ARENA_FLUSH()
