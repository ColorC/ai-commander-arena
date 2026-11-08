// Minimal hand-written base shot for examples/segment-example.json (format reference only; no game data).
// A base shot carries what every cut of one map shares: frame size and rate, skin (theme), model kit, unit-name table,
// the match card. make_segment.py writes the per-cut fields (time, camera, lines, panels ...) on top of it.
// Times: video seconds; game time in seconds since the scenario's H-hour. Positions: hex numbers (col * 100 + row) or
// [x, z] map km. Camera keys: { t, at, dist (km), pitch (deg below horizontal), yaw (deg, 0 = north), focus, blur }.
export default {
  id: 'example-base',
  fps: 30, width: 1920, height: 1080,
  match: { title: '示例对局', blue: 'Blue AI', red: 'Red AI' },
  // theme: './theme-default.js',          // UI skin module (relative to sandbox.html)
  // names: '<file URL of a unit-name table>', // { "<unit name>": { "zh": "...", "short": "..." } }
  moments: [], roles: [], given: [], focus: [], steps: [], orders: [], captions: [], labels: [], panels: [], fog: [],
  keyframes: [],
};
