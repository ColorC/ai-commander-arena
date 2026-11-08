-- ai-commander-arena P2: how to read VP locations and their owners through GetMarkers (GetMarkers({}) and
-- GetMarkers({location = <VP hex>}) returned {} at load). Loaded by recorder.lua through run.lua `extra`.
-- Tries several argument forms (single hex, whole-map rectangle location1/location2) at load and again at the first
-- game minute; writes <out>\markers-load.json and <out>\markers-minute.json.
local J, OUT, cfg = ARENA_JSON, ARENA_OUT, ARENA_RUN

local function try(fn, ...)
  local ok, r = pcall(fn, ...)
  if ok then return r end
  return { error = tostring(r) }
end

local hex = cfg.probe_hex or 2118
local last = (cfg.map_cols or 53) * 100 + (cfg.map_rows or 34)
local forms = {
  table_int = { location = hex },
  rect_int = { location1 = 101, location2 = last },
  rect_str = { location1 = "101", location2 = tostring(last) },
  rect_hex = { location1 = hex, location2 = hex },
  rect_side0 = { location1 = 101, location2 = last, side = 0 },
}

local function probe(name)
  local out = { probe_hex = hex, forms = {} }
  for k, a in pairs(forms) do out.forms[k] = try(GameAPI.GetMarkers, a) end
  local fh = io.open(OUT .. name, "w")
  if fh then fh:write(J(out), "\n"); fh:close() end
end

probe("markers-load.json")
local base, done = GameAPI.OnGameMinuteElapsed, false
GameAPI.OnGameMinuteElapsed = function(sec)
  base(sec)
  if not done then done = true; pcall(probe, "markers-minute.json") end
end
