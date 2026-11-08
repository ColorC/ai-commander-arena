-- ai-commander-arena recorder: structured record of an FPC game through the built-in Lua GameAPI.
-- ai-arena\in\run.lua (written by the job script) returns { out_dir = "<dir>\\", extra = "<lua file>" (optional) }.
-- Files written to out_dir:
--   rec.jsonl    one JSON object per line, field "k" is the record kind:
--                meta, snap (all units, nested), diff (one unit: changed flat paths), unit_add, unit_gone,
--                minute (clock tick + cost), event, order_start, order_finish, f9, end
--   status.json  last game second and counters, rewritten every game minute (the job script reads it)
--   rec.log      load messages and errors
-- GetUnitStatus / GetUnitOrders / GetUnitSubunits are not limited by fog of war: this is the umpire's view.

-- ai-arena\ folder: FPC_ARENA_DIR (set by the job scripts before they start the game), else relative to the game's
-- working directory (the job scripts start the game in its own folder)
local ROOT = (os.getenv and os.getenv("FPC_ARENA_DIR")) or "ai-arena\\"
if ROOT:sub(-1) ~= "\\" and ROOT:sub(-1) ~= "/" then ROOT = ROOT .. "\\" end
local cfg = ARENA_CFG or {} -- ARENA_CFG: set by offline tests
if not ARENA_CFG then
  local ok, c = pcall(dofile, ROOT .. "in\\run.lua")
  if ok and type(c) == "table" then cfg = c end
end
local OUT = cfg.out_dir or (ROOT .. "out\\")
local KEYFRAME_SECONDS = cfg.keyframe_seconds or 1800

local function logline(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  local f = io.open(OUT .. "rec.log", "a")
  if f then
    f:write(os.date("%Y-%m-%d %H:%M:%S "), table.concat(parts, " "), "\n")
    f:close()
  end
end

-- JSON ---------------------------------------------------------------------------------------------
local ESC = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
local function jstr(s)
  s = s:gsub('[%c"\\]', function(c) return ESC[c] or string.format("\\u%04x", c:byte()) end)
  if not utf8.len(s) then -- not UTF-8: treat high bytes as Latin-1
    s = s:gsub("[\128-\255]", function(c) return string.format("\\u%04x", c:byte()) end)
  end
  return '"' .. s .. '"'
end

local function is_array(t)
  local n = #t
  if n == 0 then return false end
  local c = 0
  for _ in pairs(t) do c = c + 1 end
  return c == n
end

local function json(v)
  local t = type(v)
  if t == "string" then return jstr(v) end
  if t == "boolean" then return tostring(v) end
  if t == "number" then
    if math.type(v) == "integer" then return tostring(v) end
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.10g", v)
  end
  if t ~= "table" then return v == nil and "null" or jstr(tostring(v)) end
  local parts = {}
  if is_array(v) then
    for i = 1, #v do parts[i] = json(v[i]) end
    return "[" .. table.concat(parts, ",") .. "]"
  end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = k end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  for _, k in ipairs(keys) do parts[#parts + 1] = jstr(tostring(k)) .. ":" .. json(v[k]) end
  return "{" .. table.concat(parts, ",") .. "}"
end

-- output -------------------------------------------------------------------------------------------
local REC = io.open(OUT .. "rec.jsonl", "a")
local counts = { records = 0, diffs = 0, events = 0, orders = 0, errors = 0 }
local last_sec, ended = nil, false

local function emit(rec)
  counts.records = counts.records + 1
  if REC then REC:write(json(rec), "\n") end
end

-- shared with the probe / bridge scripts loaded through cfg.extra
ARENA_JSON, ARENA_EMIT, ARENA_OUT, ARENA_RUN = json, emit, OUT, cfg
function ARENA_FLUSH() if REC then REC:flush() end end

local function write_status(extra)
  local f = io.open(OUT .. "status.json", "w")
  if not f then return end
  local s = { sec = last_sec, wall = os.time(), ended = ended }
  for k, v in pairs(counts) do s[k] = v end
  for k, v in pairs(extra or {}) do s[k] = v end
  f:write(json(s), "\n")
  f:close()
end

local function guarded(name, fn)
  return function(...)
    local ok, err = xpcall(fn, debug.traceback, ...)
    if not ok then
      counts.errors = counts.errors + 1
      if counts.errors <= 50 then logline("ERR in", name, err) end
    end
  end
end

-- unit state ---------------------------------------------------------------------------------------
local function call(fn, ...)
  local ok, r = pcall(fn, ...)
  if ok then return r end
  return { error = tostring(r) }
end

local function capture(name)
  return {
    s = call(GameAPI.GetUnitStatus, name),
    o = call(GameAPI.GetUnitOrders, name),
    u = call(GameAPI.GetUnitSubunits, name),
  }
end

local function flatten(v, prefix, out)
  if type(v) ~= "table" then
    out[prefix] = v
    return out
  end
  for k, x in pairs(v) do flatten(x, prefix == "" and tostring(k) or (prefix .. "." .. tostring(k)), out) end
  return out
end

local function all_units()
  local list = {}
  for side = 0, 1 do
    local ok, names = pcall(GameAPI.GetUnitNames, side)
    if ok and type(names) == "table" then
      for _, n in pairs(names) do list[#list + 1] = n end
    end
  end
  table.sort(list)
  return list
end

local prev = nil       -- unit name -> flat state as last recorded (nil until the first snapshot)
local last_key = nil   -- game second of the last keyframe

-- Emit diff / unit_add / unit_gone records for everything that changed since the last record.
-- phase tags changes not made at a minute tick ("orders": issued during the Orders phase, "end": final).
local function diff_all(sec, phase)
  local seen, changed = {}, 0
  for _, n in ipairs(all_units()) do
    seen[n] = true
    local c = capture(n)
    local flat = flatten(c, "", {})
    local old = prev[n]
    if not old then
      emit({ k = "unit_add", sec = sec, phase = phase, unit = n, state = c })
    else
      local set, del, any = {}, {}, false
      for p, v in pairs(flat) do
        if old[p] ~= v then set[p] = v; any = true end
      end
      for p in pairs(old) do
        if flat[p] == nil then del[#del + 1] = p; any = true end
      end
      if any then
        table.sort(del)
        emit({ k = "diff", sec = sec, phase = phase, unit = n, set = set, del = (#del > 0) and del or nil })
        changed = changed + 1
      end
    end
    prev[n] = flat
  end
  for n in pairs(prev) do
    if not seen[n] then
      emit({ k = "unit_gone", sec = sec, phase = phase, unit = n })
      prev[n] = nil
    end
  end
  counts.diffs = counts.diffs + changed
  return changed
end

-- Full state of every unit. Changes since the last record are emitted as diffs first, so replaying
-- the first snapshot plus all diffs reproduces every later snapshot.
local function snapshot(sec, reason, phase)
  if prev then diff_all(sec, phase or reason) end
  local units = {}
  prev = {}
  for _, n in ipairs(all_units()) do
    local c = capture(n)
    units[n] = c
    prev[n] = flatten(c, "", {})
  end
  emit({ k = "snap", sec = sec, wall = os.time(), reason = reason, units = units,
         markers = call(GameAPI.GetMarkers, {}) }) -- objective / VP markers; returned {} in Chance Encounter
  last_key = sec
  if REC then REC:flush() end
end

local function tick(sec)
  if not prev then return snapshot(sec, "first_tick") end
  local t0 = os.clock()
  local changed = diff_all(sec, nil)
  emit({ k = "minute", sec = sec, wall = os.time(), changed = changed, cpu_ms = math.floor((os.clock() - t0) * 1000) })
  if REC then REC:flush() end
end

-- callbacks ----------------------------------------------------------------------------------------
GameAPI.OnScenarioStart = guarded("OnScenarioStart", function(name)
  logline("OnScenarioStart", name)
  last_sec = 0
  snapshot(0, "scenario_start")
  write_status()
end)

GameAPI.OnScenarioResume = guarded("OnScenarioResume", function(name, sec)
  logline("OnScenarioResume", name, sec)
  last_sec = sec
  snapshot(sec, "scenario_resume")
  write_status()
end)

GameAPI.OnGameMinuteElapsed = guarded("OnGameMinuteElapsed", function(sec)
  last_sec = sec
  tick(sec)
  if last_key == nil or sec - last_key >= KEYFRAME_SECONDS then snapshot(sec, "keyframe") end
  write_status()
end)

GameAPI.OnGameEventElapsed = guarded("OnGameEventElapsed", function(ev, sec)
  counts.events = counts.events + 1
  emit({ k = "event", sec = sec, event = ev })
end)

GameAPI.OnTokenOrderStart = guarded("OnTokenOrderStart", function(tok, sec)
  counts.orders = counts.orders + 1
  emit({ k = "order_start", sec = sec, token = tok })
end)

GameAPI.OnTokenOrderFinish = guarded("OnTokenOrderFinish", function(tok, sec)
  emit({ k = "order_finish", sec = sec, token = tok })
end)

GameAPI.OnScenarioEnd = guarded("OnScenarioEnd", function(sec)
  logline("OnScenarioEnd", sec)
  last_sec = sec
  ended = true
  snapshot(sec, "scenario_end", "end")
  emit({ k = "end", sec = sec, wall = os.time() })
  if REC then REC:flush() end
  write_status()
end)

-- telnet helpers (the job script calls these) ------------------------------------------------------
local okA, A = pcall(require, "arena")
ARENA = okA and A or nil
if not okA then logline("arena module not loaded:", A) end

-- Called by the job script when the game sits in the Orders phase: first record the orders the
-- side AIs issued during this phase, then post F9 (Start Turn Resolution) to the game's main window.
function arena_f9()
  if not ARENA then return nil, "arena module not loaded" end
  if prev then diff_all(last_sec, "orders") end
  local h, err = ARENA.key(0x78)
  emit({ k = "f9", sec = last_sec, wall = os.time(), hwnd = h, err = err })
  if REC then REC:flush() end
  return h, err
end

function arena_windows()
  if not ARENA then return "arena module not loaded" end
  local lines = {}
  for _, w in ipairs(ARENA.windows()) do
    lines[#lines + 1] = string.format("%x %s visible=%s %q", w.hwnd, w.class, tostring(w.visible), w.title)
  end
  return table.concat(lines, "\n")
end

function arena_snapshot(reason)
  snapshot(last_sec, reason or "manual")
  write_status()
  return counts.records
end

-- load ---------------------------------------------------------------------------------------------
emit({ k = "meta", wall = os.time(), lua = _VERSION, out = OUT, scenario = call(GameAPI.GetScenario),
       arena = okA, keyframe_seconds = KEYFRAME_SECONDS })
snapshot(nil, "load")
write_status()
logline("recorder loaded", _VERSION, "out=" .. OUT, "units=" .. tostring(#all_units()), "arena=" .. tostring(okA))
logline("windows:\n" .. arena_windows())

-- optional extra script (run.lua: extra = [[<path>]]), loaded after the recorder's callbacks are set
if cfg.extra then
  local ok, err = pcall(dofile, cfg.extra)
  logline("extra", cfg.extra, ok and "loaded" or ("failed: " .. tostring(err)))
end
