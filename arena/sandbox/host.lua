-- Lua sandbox host for arena reflex scripts and compute tasks (arena/DESIGN.md section 7), started by runner.py
-- (lua.exe -E host.lua) inside a Windows job object.
-- stdin: one request per line, a Lua data literal written by runner.py and loaded with an empty environment:
--   { mode = "script" | "task" | "check", name =, source =, input =, events =, trigger =, persist =, max_instr =, max_ms =, mem_mb = }
--   ("check" only compiles the source and answers ok or the syntax error; nothing of it runs)
-- stdout: one JSON reply per line:
--   { ok, limit = false | "instructions" | "time" | "memory", error, instructions, ms, log,
--     commands, alarm, report, persist (script) | result (task) }
-- The code runs in a fresh environment per call that holds only copies of string / table / math / utf8 and the safe
-- base functions; a count hook stops it at its instruction, wall-time and Lua heap limits, and a pcall inside the code
-- cannot swallow that stop. What a script sees:
--   view, events, trigger      its side's view (same fog as the commander), the events that fired it, "minute" |
--                              "orders_phase" | "event" | "wake"
--   memory                     its own table, kept between calls (passed through JSON: string keys, arrays)
--   submit(body)               queue a command, same body as the commander's submit_command
--   alarm(text)                wake the commander
--   report(...)                text for the commander's wake (trigger "wake"): every call adds a line; the whole is
--                              cut at MAX_REPORT bytes without a word (the referee uses it only for wake scripts)
--   print(...)                 a log line in the referee's script log
-- A task sees `input` and returns its result.
local STEP = 1000
local MAX_COMMANDS, MAX_LOG, MAX_RESULT, MAX_REPORT = 100, 50, 256 * 1024, 64 * 1024

local sethook, clock, gccount = debug.sethook, os.clock, collectgarbage
local real_pcall, real_getmt, real_setmt, real_type, real_tostring = pcall, getmetatable, setmetatable, type, tostring
local rawget, rawlen, next, concat, fmt = rawget, rawlen, next, table.concat, string.format

-- strings reach the real string library through their metatable, so the shared table itself is patched
string.dump = nil
local rep = string.rep
string.rep = function(s, n, sep)
  local k = math.tointeger(n) or 0
  if k > 0 and #real_tostring(s) * k + #real_tostring(sep or "") * (k - 1) > 16 * 1024 * 1024 then
    error("string.rep result over 16 MB", 2)
  end
  return rep(s, n, sep)
end

-- json (raw access only: never calls a metamethod the code set) ---------------------------------------------------
local function jstr(s)
  return '"' .. s:gsub('[%c"\\\127]', function(c)
    if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" end
    return fmt("\\u%04x", c:byte())
  end) .. '"'
end

local function json(v, seen, depth)
  local t = real_type(v)
  if t == "string" then return jstr(v) end
  if t == "boolean" then return v and "true" or "false" end
  if t == "number" then
    if math.type(v) == "integer" then return fmt("%d", v) end
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return fmt("%.17g", v)
  end
  if t == "nil" then return "null" end
  if t ~= "table" then error("cannot encode a " .. t, 0) end
  if seen[v] then error("table refers to itself", 0) end
  if depth > 64 then error("tables nested deeper than 64", 0) end
  seen[v] = true
  local parts, n = {}, rawlen(v)
  local count = 0
  for _ in next, v do count = count + 1 end
  if count == n and n > 0 then
    local dense = true
    for i = 1, n do if rawget(v, i) == nil then dense = false; break end end
    if dense then
      for i = 1, n do parts[i] = json(rawget(v, i), seen, depth + 1) end
      seen[v] = nil
      return "[" .. concat(parts, ",") .. "]"
    end
  end
  if count == 0 then seen[v] = nil; return "[]" end
  for k, x in next, v do
    local kt = real_type(k)
    if kt ~= "string" and kt ~= "number" then error("table key of type " .. kt, 0) end
    parts[#parts + 1] = jstr(kt == "string" and k or json(k, seen, depth)) .. ":" .. json(x, seen, depth + 1)
  end
  seen[v] = nil
  return "{" .. concat(parts, ",") .. "}"
end

local function encode(v) return json(v, {}, 0) end

-- limits ----------------------------------------------------------------------------------------------------------
local state                 -- per call: instr, max_instr, t0, max_s, mem_kb, over
local OVER = setmetatable({}, { __tostring = function() return "over the limit" end })

local function over(what)
  state.over = state.over or what
  error(OVER, 0)
end

local function hook()
  local s = state
  s.instr = s.instr + STEP
  if s.over then error(OVER, 0) end
  if s.max_instr and s.instr > s.max_instr then over("instructions") end
  if clock() - s.t0 > s.max_s then over("time") end
  if gccount("count") > s.mem_kb then over("memory") end
end

local function safe_pcall(f, ...)
  local r = table.pack(real_pcall(f, ...))
  if not r[1] and r[2] == "not enough memory" then state.over = state.over or "memory" end
  if state.over then error(OVER, 0) end
  return table.unpack(r, 1, r.n)
end

local function safe_getmt(v)
  if real_type(v) == "table" then return real_getmt(v) end
  return nil
end

local function safe_setmt(t, mt)
  if real_type(mt) == "table" and rawget(mt, "__gc") ~= nil then error("__gc is not allowed", 2) end
  return real_setmt(t, mt)
end

-- environment -----------------------------------------------------------------------------------------------------
local BASE = { "assert", "error", "ipairs", "next", "pairs", "rawequal", "rawget", "rawlen", "rawset", "select",
               "tonumber", "tostring", "type" }

local function copy(t)
  local r = {}
  for k, v in next, t do r[k] = v end
  return r
end

local function make_env(extra)
  local env = { _VERSION = _VERSION, pcall = safe_pcall, getmetatable = safe_getmt, setmetatable = safe_setmt,
                string = copy(string), table = copy(table), math = copy(math), utf8 = copy(utf8) }
  for _, n in ipairs(BASE) do env[n] = _G[n] end
  for k, v in next, extra do env[k] = v end
  env._G = env
  return env
end

local function message(e)
  if real_type(e) == "string" then return e end
  return "error object of type " .. real_type(e)
end

-- one call --------------------------------------------------------------------------------------------------------
local function run(req)
  local reply = { ok = false, limit = false, instructions = 0 }
  local logs, commands, alarm, report, report_n = {}, {}, nil, {}, 0
  local function words(...)
    local n, parts = select("#", ...), {}
    for i = 1, n do
      local v = select(i, ...)
      parts[i] = real_type(v) == "string" and v or (real_type(v) == "number" or real_type(v) == "boolean" or v == nil)
                 and real_tostring(v) or real_type(v)
    end
    return concat(parts, " ")
  end
  local function print_(...)
    if #logs >= MAX_LOG then return end
    logs[#logs + 1] = words(...):sub(1, 300)
  end
  local extra = { print = print_ }
  if req.mode == "script" then
    extra.view, extra.events, extra.trigger, extra.memory = req.input, req.events or {}, req.trigger, req.persist or {}
    extra.submit = function(body)
      if real_type(body) ~= "table" then error("submit needs a table", 2) end
      if #commands >= MAX_COMMANDS then error("at most " .. MAX_COMMANDS .. " commands per call", 2) end
      commands[#commands + 1] = body
    end
    extra.alarm = function(text)
      alarm = (real_type(text) == "string" or real_type(text) == "number") and real_tostring(text):sub(1, 300) or "alarm"
    end
    extra.report = function(...)
      if report_n >= MAX_REPORT then return end
      local line = words(...):sub(1, MAX_REPORT - report_n)
      report[#report + 1] = line
      report_n = report_n + #line + 1
    end
  else
    extra.input = req.input
  end
  local env = make_env(extra)
  local chunk, err = load(req.source or "", "=" .. (req.name or "task"), "t", env)
  reply.log = logs
  if not chunk then reply.error = err; return reply end
  if req.mode == "check" then reply.ok = true; return reply end

  local t0 = clock()
  state = { instr = 0, max_instr = req.max_instr, t0 = t0, max_s = (req.max_ms or 200) / 1000,
            mem_kb = (req.mem_mb or 64) * 768, over = false }
  sethook(hook, "", STEP)
  local ok, res = real_pcall(chunk)
  sethook()
  reply.instructions, reply.ms = state.instr, (clock() - t0) * 1000
  if not ok and res == "not enough memory" then state.over = state.over or "memory" end
  if state.over then
    reply.limit, reply.error = state.over, "over the " .. state.over .. " limit"
    return reply
  end
  if not ok then reply.error = message(res); return reply end

  local enc_ok, enc = real_pcall(function()
    if req.mode == "script" then
      if real_type(env.memory) ~= "table" then error("memory must stay a table", 0) end
      return { commands = encode(commands), persist = encode(env.memory) }
    end
    local r = encode(res)
    if #r > MAX_RESULT then error("result over " .. MAX_RESULT .. " bytes of JSON", 0) end
    return { result = r }
  end)
  if not enc_ok then reply.error = "cannot return the result: " .. message(enc); return reply end
  reply.ok, reply.alarm = true, alarm
  if #report > 0 then reply.report = concat(report, "\n") end
  reply.raw = enc
  return reply
end

-- reply: the encoded parts are spliced in as raw JSON
local function write_reply(reply)
  local raw = reply.raw
  reply.raw = nil
  local s = encode(reply)
  if raw then
    local extra = {}
    for k, v in pairs(raw) do extra[#extra + 1] = jstr(k) .. ":" .. v end
    s = s:sub(1, -2) .. "," .. concat(extra, ",") .. "}"
  end
  io.write(s, "\n")
  io.flush()
end

for line in io.lines() do
  local reply
  local f, err = load("return " .. line, "=request", "t", {})
  local ok, req = false, err
  if f then ok, req = real_pcall(f) end
  if not ok or real_type(req) ~= "table" then
    reply = { ok = false, limit = false, error = "bad request: " .. message(req) }
  else
    local ok2, r = real_pcall(run, req)
    reply = ok2 and r or { ok = false, limit = false, error = "host error: " .. message(r) }
  end
  state = nil
  write_reply(reply)
  gccount("collect")
end
