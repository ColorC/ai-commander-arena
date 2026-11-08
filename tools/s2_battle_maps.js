/* Battle maps for the Flashpoint Campaigns "Eyes Ears and Teeth" battle report. Vanilla JS, SVG, no libraries, no network.
 * Data comes from tools/s2_battle_maps.py (terrain JSON once, one JSON per game). Inline them in one script element
 * (const TERRAIN = {...}; const GAME_F1 = {...};), then this file in the next one. Never write a closing script tag in here.
 *
 *   BattleMap.replay(el, terrain, game, opts)    map + time slider (07:00-11:00, 1-minute steps), play/pause, speed, legend
 *   BattleMap.snapshot(el, terrain, game, tSec, opts)   the same drawing at one moment, no controls (small multiples)
 *   BattleMap.chart(el, game, opts)              US share % and both sides' force % over time, VP changes of hands
 *   BattleMap.legend(el, opts)                   stand-alone legend (symbols, terrain, roads) for a grid of snapshots
 *
 * replay opts: start (sec, 0), speed (game sec per real sec, 300), trail (sec of movement trail, 1200), flash (sec a combat
 *   burst stays, 300), hexLabels (true), startAreas (true), labels (false: unit names), legend (true), width (css px;
 *   default: the container), sym (symbol size in px), crop ([col0, col1, row0, row1]: zoom to a block of hexes), highlight,
 *   reinforcements ('show' (default): units that have not arrived yet are drawn faint with a dashed frame on the hex they
 *   enter at; 'hide': not drawn at all).
 * snapshot opts: the same plus caption (text under the map; default the clock), clock (true: clock in the corner),
 *   legend (false), highlight: [2327, 'EET TF', {hex: 1322, label: 'HQ'}, {unit: '4/EET TF', label: 'recon'}].
 * chart opts: height (260), width (css px; default the container), title, table (true: details with the numbers).
 * BattleMap.lang = 'zh' switches every visible string to Chinese (default 'en'); set it before creating the views.
 * All three return a handle {el, ...}. Colours are CSS variables (--bm-us, --bm-ru, ...) on the .bm wrapper; the defaults follow
 * prefers-color-scheme and the page's data-theme="light|dark" on <html>.
 */
(function (global) {
  'use strict';

  var A = 10;                       // hex circumradius in map units; flat-top hexes, columns vertical
  var H = A * Math.sqrt(3);         // hex height (flat to flat), 500 m on the real map
  var TMAX = 14400;
  // ---------------------------------------------------------------- strings (BattleMap.lang = 'en' | 'zh')
  var I18N = {
    en: {
      play: 'Play', pause: 'Pause', speedAria: 'Playback speed', speed: '{0} min/s', timeAria: 'Game time', names: 'unit names',
      side0: 'US', side1: 'Soviet', side2: 'nobody',
      'type.Recce': 'Reconnaissance', 'type.Tank': 'Tank', 'type.Inf': 'Mech / motor-rifle infantry', 'type.HQ': 'Headquarters',
      'type.SPArty': 'Self-propelled artillery', 'type.AD': 'Air defence', 'type.Helo': 'Helicopter', 'type.Depot': 'Depot / supply',
      'type.Eng': 'Engineer',
      'size.': 'squad', 'size..': 'section', 'size...': 'platoon', 'size.I': 'company', 'size.II': 'battalion', 'size.III': 'regiment',
      partOf: 'part of {0}', elemOp: '{0}/{1} elements operational', ko: 'Knocked out (0/{0} elements)', hexAt: 'hex {0}',
      offTip: 'off the map (fire support)', order: 'order: {0}', orderTo: ' to {0}', sameHex: 'same hex: {0}',
      reserve: 'Reserve: not on the map yet ({0} elements)', enters: 'enters at hex {0}', enterAt: ' at about {0}',
      vpTitle: 'Victory-point cell, hex {0}', vpHeld: '{0} VP · held by {1}', since: 'since {0}',
      unclaimedStart: 'unclaimed at the start', heldStart: 'held since the start',
      offMap: 'off-map:', startArea0: 'US start area', startArea1: 'Soviet start area',
      status: '{0} {1}/{2} elements operational', later: '{0} more {1} elements arrive later', mapAria: 'Battle map at {0}',
      lgTank: 'tank', lgRecon: 'recon', lgInf: 'mech / motor-rifle infantry', lgArty: 'artillery', lgAD: 'air defence',
      lgHelo: 'helicopter', lgHQ: 'headquarters', lgDepot: 'depot', lgEng: 'engineer', lgKO: 'knocked out',
      lgLoss: 'fainter + bar = elements lost', lgRes: 'dashed = reserve, not arrived yet',
      lgVp0: 'VP cell held by US', lgVp1: 'VP cell held by Soviet', lgVp2: 'VP cell, unclaimed',
      lgCombat: 'combat in the last 5 min (−n = lost)', lgTrail: '20-min trail', lgStart: 'start area',
      lgOpen: 'open / fields', lgLight: 'light woods / mixed cover', lgHeavy: 'heavy woods', lgTown: 'town', lgWater: 'water / marsh',
      lgMain: 'main road', lgMinor: 'minor road', lgRail: 'railway', lgRiver: 'river', lgStream: 'stream',
      serShare: 'US share of the score (50% = even)', serF0: 'US force remaining', serF1: 'Soviet force remaining',
      shShare: 'US share', shF0: 'US force', shF1: 'Soviet force', vpLegend: 'VP cell changes hands (colour = new owner)',
      chartAria: 'US share of the score and force remaining of both sides, 07:00 to 11:00', noVp: 'no VP cell changed hands',
      vpHeldTip: 'VP held: US {0} · Soviet {1}', cell: 'cell {0}', takenBy: 'taken by {0}', nowNobody: 'now held by nobody',
      tableView: 'Table view', thTime: 'Time', thShare: 'US share %', thF0: 'US force %', thF1: 'Soviet force %',
      thVp0: 'VP held US', thVp1: 'VP held Soviet', thChange: 'VP cell changes hands', thCell: 'Cell', thNew: 'New owner'
    },
    zh: {
      play: '播放', pause: '暂停', speedAria: '播放速度', speed: '{0} 分钟/秒', timeAria: '游戏时间', names: '显示单位名',
      side0: '美军', side1: '苏军', side2: '无人',
      'type.Recce': '侦察', 'type.Tank': '坦克', 'type.Inf': '摩步/机步', 'type.HQ': '指挥部',
      'type.SPArty': '自行火炮', 'type.AD': '防空', 'type.Helo': '直升机', 'type.Depot': '补给点', 'type.Eng': '工兵',
      'size.': '班', 'size..': '小队', 'size...': '排', 'size.I': '连', 'size.II': '营', 'size.III': '团',
      partOf: '隶属 {0}', elemOp: '{0}/{1} 个单元可用', ko: '被打掉（0/{0} 个单元）', hexAt: '格 {0}',
      offTip: '地图外（火力支援）', order: '命令：{0}', orderTo: ' → {0}', sameHex: '同格：{0}',
      reserve: '预备队：尚未入场（{0} 个单元）', enters: '将从格 {0} 入场', enterAt: '，约 {0}',
      vpTitle: 'VP 格（格 {0}）', vpHeld: '{0} VP · {1}控制', since: '自 {0} 起',
      unclaimedStart: '开局无人占领', heldStart: '开局即由其控制',
      offMap: '地图外：', startArea0: '美军部署区', startArea1: '苏军部署区',
      status: '{0} {1}/{2} 个单元可用', later: '另有 {0} 个{1}单元稍后入场', mapAria: '战场地图 {0}',
      lgTank: '坦克', lgRecon: '侦察', lgInf: '摩步/机步', lgArty: '炮兵', lgAD: '防空',
      lgHelo: '直升机', lgHQ: '指挥部', lgDepot: '补给点', lgEng: '工兵', lgKO: '被打掉',
      lgLoss: '变淡+血条 = 单元减员', lgRes: '虚线 = 尚未入场的预备队',
      lgVp0: 'VP 格（美军）', lgVp1: 'VP 格（苏军）', lgVp2: 'VP 格（无人）',
      lgCombat: '近 5 分钟交战（−n = 损失单元）', lgTrail: '近 20 分钟轨迹', lgStart: '部署区',
      lgOpen: '开阔地/农田', lgLight: '疏林/混合掩蔽', lgHeavy: '密林', lgTown: '城镇', lgWater: '水面/沼泽',
      lgMain: '主干道', lgMinor: '次要道路', lgRail: '铁路', lgRiver: '河流', lgStream: '溪流',
      serShare: '美军份额（50% = 势均力敌）', serF0: '美军剩余兵力', serF1: '苏军剩余兵力',
      shShare: '美军份额', shF0: '美军兵力', shF1: '苏军兵力', vpLegend: 'VP 格易手（颜色 = 新主人）',
      chartAria: '美军份额与双方剩余兵力，07:00–11:00', noVp: '没有 VP 格易手',
      vpHeldTip: '控制 VP：美军 {0} · 苏军 {1}', cell: '格 {0}', takenBy: '被{0}占领', nowNobody: '现无人控制',
      tableView: '表格视图', thTime: '时间', thShare: '美军份额 %', thF0: '美军兵力 %', thF1: '苏军兵力 %',
      thVp0: '美军控制 VP', thVp1: '苏军控制 VP', thChange: 'VP 格易手', thCell: '格', thNew: '新主人'
    }
  };
  var ORDER_ZH = {'Screen': '警戒', 'On Call': '待召火力', 'Hold': '坚守', 'Move Hasty': '急速机动', 'Move Deliberate': '稳步机动',
    'Withdraw': '撤退', 'Barrage': '炮击', 'Assault': '突击', 'Scoot': '射后转移', 'Resupply': '补给'};
  function tr(k) {
    var lang = (global.BattleMap && global.BattleMap.lang) || 'en', d = I18N[lang] || I18N.en;
    var s = d[k] != null ? d[k] : (I18N.en[k] != null ? I18N.en[k] : k);
    for (var i = 1; i < arguments.length; i++) s = s.split('{' + (i - 1) + '}').join(arguments[i]);
    return s;
  }
  function sideName(i) { return tr('side' + i); }
  function typeName(icon) { var k = 'type.' + icon, v = tr(k); return v === k ? icon : v; }
  function sizeName(sz) { var k = 'size.' + sz, v = tr(k); return v === k ? '' : v; }
  function orderName(n) {
    var zh = (global.BattleMap && global.BattleMap.lang) === 'zh' && ORDER_ZH[n];
    return zh ? zh + ' (' + n + ')' : n;
  }
  var FONT = 'system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif';

  // ---------------------------------------------------------------- styles
  var LIGHT = '--bm-us:#2a78d6;--bm-ru:#e34948;--bm-none:#8b8b86;--bm-surface:#fcfcfb;--bm-ink:#0b0b0b;--bm-ink2:#52514e;' +
    '--bm-muted:#898781;--bm-line:#e1e0d9;--bm-axis:#c3c2b7;--bm-halo:rgba(252,252,251,.92);--bm-sym:rgba(252,252,251,.94);' +
    '--bm-hit:#f0a30a;--bm-hl:#7b3fa8;--bm-c0:#f4f2e8;--bm-c1:#e2e9d2;--bm-c2:#c8d9b6;--bm-c3:#e2d3cb;--bm-c4:#cbe0ec;' +
    '--bm-elev:#8c6a35;--bm-grid:rgba(120,108,85,.17);--bm-road1:#a98650;--bm-road2:#b8aa92;--bm-casing:#fcfcfb;' +
    '--bm-rail:#66645f;--bm-water:#78acd0;--bm-river:#4f90c4;--bm-slope:#8a6a3a;--bm-bar:rgba(11,11,11,.18);';
  var DARK = '--bm-us:#3987e5;--bm-ru:#e66767;--bm-none:#8b8b86;--bm-surface:#1a1a19;--bm-ink:#f0efec;--bm-ink2:#c3c2b7;' +
    '--bm-muted:#898781;--bm-line:#2c2c2a;--bm-axis:#383835;--bm-halo:rgba(26,26,25,.92);--bm-sym:rgba(26,26,25,.92);' +
    '--bm-hit:#f0a30a;--bm-hl:#c79bf0;--bm-c0:#2c2d28;--bm-c1:#31382b;--bm-c2:#293a28;--bm-c3:#3b3633;--bm-c4:#253746;' +
    '--bm-elev:#d8c9a8;--bm-grid:rgba(255,255,255,.07);--bm-road1:#8d7954;--bm-road2:#6f6757;--bm-casing:#1a1a19;' +
    '--bm-rail:#a9a7a1;--bm-water:#3f6f93;--bm-river:#4e86b3;--bm-slope:#b79d6b;--bm-bar:rgba(240,239,236,.25);';
  var CSS = '.bm{' + LIGHT + 'color-scheme:light;box-sizing:border-box;position:relative;background:var(--bm-surface);' +
    'color:var(--bm-ink);border:1px solid var(--bm-line);border-radius:8px;padding:8px;' +
    'font:12px/1.35 ' + FONT + '}' +
    '@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .bm{' + DARK + 'color-scheme:dark}}' +
    ':root[data-theme="dark"] .bm{' + DARK + 'color-scheme:dark}' +
    '.bm *{box-sizing:border-box}.bm svg{display:block;width:100%;height:auto;overflow:hidden}' +
    '.bm-bar{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin:0 0 6px}' +
    '.bm-btn,.bm select{font:inherit;color:var(--bm-ink);background:var(--bm-surface);border:1px solid var(--bm-axis);' +
    'border-radius:6px;padding:3px 10px;cursor:pointer}.bm-btn{min-width:64px;font-weight:600}' +
    '.bm-btn:focus-visible,.bm select:focus-visible,.bm input:focus-visible{outline:2px solid var(--bm-us);outline-offset:2px}' +
    '.bm-slider{flex:1 1 220px;min-width:160px;accent-color:var(--bm-us)}' +
    '.bm-clock{font-size:20px;font-weight:650;min-width:58px;text-align:right;font-variant-numeric:tabular-nums}' +
    '.bm-chk{display:inline-flex;gap:4px;align-items:center;color:var(--bm-ink2);cursor:pointer}' +
    '.bm-status{margin:6px 0 0;color:var(--bm-ink2)}.bm-status b{color:var(--bm-ink);font-weight:650}' +
    '.bm-cap{margin:4px 2px 0;color:var(--bm-ink2);text-align:center}' +
    '.bm-wrap{position:relative}.bm-tip{position:absolute;z-index:5;pointer-events:none;display:none;max-width:280px;' +
    'background:var(--bm-surface);color:var(--bm-ink);border:1px solid var(--bm-axis);border-radius:6px;padding:6px 8px;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.18)}.bm-tip b{font-weight:650}.bm-tip .m{color:var(--bm-ink2)}' +
    '.bm-tip .k{display:inline-block;width:14px;height:0;border-top:2px solid;vertical-align:middle;margin-right:6px}' +
    '.bm-legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin:8px 0 0;color:var(--bm-ink2)}' +
    '.bm-legend span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}.bm-legend svg{width:auto;overflow:visible}' +
    '.bm-u{cursor:default}.bm-u:hover .bm-fr{stroke-width:2.6px}.bm-fr.bm-res{stroke-dasharray:3 2.5;fill-opacity:.35}' +
    '.bm-flash{animation:bm-blink 1s ease-in-out infinite}@keyframes bm-blink{50%{opacity:.45}}' +
    '@media (prefers-reduced-motion:reduce){.bm-flash{animation:none}}' +
    '.bm text{font-family:' + FONT + '}' +
    '.bm-halo{paint-order:stroke;stroke:var(--bm-halo);stroke-linejoin:round}' +
    '.bm-chart-legend{display:flex;flex-wrap:wrap;gap:4px 16px;margin:0 0 4px;color:var(--bm-ink2)}' +
    '.bm-chart-legend span{display:inline-flex;align-items:center;gap:6px}.bm-chart-legend svg{width:auto;flex:none}' +
    '.bm details{margin:6px 0 0;color:var(--bm-ink2)}.bm summary{cursor:pointer}' +
    '.bm table{border-collapse:collapse;margin:6px 0 0;font-variant-numeric:tabular-nums}' +
    '.bm th,.bm td{padding:2px 10px;text-align:right;border-bottom:1px solid var(--bm-line)}' +
    '.bm th:first-child,.bm td:first-child{text-align:left}';

  function injectStyle() {
    if (document.getElementById('bm-style')) return;
    var s = document.createElement('style');
    s.id = 'bm-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ---------------------------------------------------------------- small helpers
  function f1(n) { return Math.round(n * 10) / 10; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function clock(sec) { return pad(7 + Math.floor(sec / 3600)) + ':' + pad(Math.floor(sec % 3600 / 60)); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function ownerVar(o) { return o === 0 ? 'var(--bm-us)' : o === 1 ? 'var(--bm-ru)' : 'var(--bm-none)'; }

  // last index whose first element <= t (rows sorted by that first element), 0 when t is before the first row
  function rowAt(rows, t) {
    var lo = 0, hi = rows.length - 1, ans = 0;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (rows[mid][0] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }

  // ---------------------------------------------------------------- hex geometry
  // flat-top hexes; hex number = column * 100 + row; rows grow southwards; EVEN columns sit half a hex lower;
  // hex side k (1..6) = edge k-1 counted clockwise from north (N, NE, SE, S, SW, NW).
  function colX(c) { return A * (1 + 1.5 * (c - 1)); }
  function rowY(c, r) { return H * (r - 0.5 + (c % 2 === 0 ? 0.5 : 0)); }
  function hexXY(hex) { var c = Math.floor(hex / 100); return [colX(c), rowY(c, hex % 100)]; }
  var CORNER = [], MID = [];
  (function () {
    for (var i = 0; i < 6; i++) CORNER.push([A * Math.cos(i * Math.PI / 3), A * Math.sin(i * Math.PI / 3)]);   // E SE SW W NW NE
    for (var k = 0; k < 6; k++) {
      var a = (-90 + 60 * k) * Math.PI / 180;
      MID.push([H / 2 * Math.cos(a), H / 2 * Math.sin(a)]);
    }
  })();
  function hexPath(x, y) {
    var d = '';
    for (var i = 0; i < 6; i++) d += (i ? 'L' : 'M') + f1(x + CORNER[i][0]) + ' ' + f1(y + CORNER[i][1]);
    return d + 'Z';
  }
  function edgePath(x, y, k) {
    var a = CORNER[(4 + k) % 6], b = CORNER[(5 + k) % 6];
    return 'M' + f1(x + a[0]) + ' ' + f1(y + a[1]) + 'L' + f1(x + b[0]) + ' ' + f1(y + b[1]);
  }
  function roadPath(list) {
    var d = '';
    list.forEach(function (p) {
      var xy = hexXY(p[0]);
      for (var k = 0; k < 6; k++) if (p[1] >> k & 1) d += 'M' + f1(xy[0]) + ' ' + f1(xy[1]) + 'L' + f1(xy[0] + MID[k][0]) + ' ' + f1(xy[1] + MID[k][1]);
    });
    return d;
  }
  function edgesPath(list) {
    var d = '';
    list.forEach(function (p) {
      var xy = hexXY(p[0]);
      for (var k = 0; k < 6; k++) if (p[1] >> k & 1) d += edgePath(xy[0], xy[1], k);
    });
    return d;
  }

  // terrain drawing that does not depend on the scale: cached on the terrain object
  function terrainBase(T) {
    if (T._bm) return T._bm;
    var cls = [[], [], [], [], []], bands = [[], [], [], [], [], []], R = T.rows, i, c, r;
    var emin = Math.round(T.emin / 10), emax = Math.round(T.emax / 10), span = Math.max(1, emax - emin);
    for (c = 1; c <= T.cols; c++) for (r = 1; r <= R; r++) {
      i = (c - 1) * R + (r - 1);
      var p = hexPath(colX(c), rowY(c, r));
      cls[+T.t.charAt(i)].push(p);
      var b = Math.min(5, Math.floor((T.e[i] - emin) / span * 6));
      if (b > 0) bands[b].push(p);
    }
    var s = '';
    for (i = 0; i < 5; i++) s += '<path d="' + cls[i].join('') + '" fill="var(--bm-c' + i + ')" stroke="var(--bm-grid)" stroke-width=".5"/>';
    for (i = 1; i < 6; i++) if (bands[i].length) s += '<path d="' + bands[i].join('') + '" fill="var(--bm-elev)" opacity="' + f1(i * 0.045) + '"/>';
    s += '<path d="' + edgesPath(T.sl || []) + '" fill="none" stroke="var(--bm-slope)" stroke-width="1.4" stroke-dasharray="2.5 1.5" opacity=".7"/>';
    s += '<path d="' + edgesPath(T.st || []) + '" fill="none" stroke="var(--bm-water)" stroke-width="1.5" stroke-linecap="round"/>';
    s += '<path d="' + edgesPath(T.rv || []) + '" fill="none" stroke="var(--bm-river)" stroke-width="3" stroke-linecap="round"/>';
    var sr = roadPath(T.sr || []), pr = roadPath(T.pr || []), rl = roadPath(T.rl || []);
    s += '<path d="' + sr + '" fill="none" stroke="var(--bm-road2)" stroke-width="1.7" stroke-linecap="round"/>';
    s += '<path d="' + pr + '" fill="none" stroke="var(--bm-casing)" stroke-width="5.2" stroke-linecap="round" opacity=".75"/>';
    s += '<path d="' + pr + '" fill="none" stroke="var(--bm-road1)" stroke-width="3.2" stroke-linecap="round"/>';
    s += '<path d="' + rl + '" fill="none" stroke="var(--bm-rail)" stroke-width="2"/>';
    s += '<path d="' + rl + '" fill="none" stroke="var(--bm-casing)" stroke-width="1.1" stroke-dasharray="3.2 3.2"/>';
    T._bm = s;
    return s;
  }

  // ---------------------------------------------------------------- symbols (unit coordinates, 1 = symbol size S)
  function glyph(icon) {
    var o = 'fill="none" stroke="var(--bm-ink)" stroke-width="1.2" vector-effect="non-scaling-stroke" stroke-linecap="round"';
    switch (icon) {
      case 'Tank': return '<ellipse rx=".5" ry=".3" ' + o + '/>';
      case 'Recce': return '<path d="M-.5 .3L.5 -.3" ' + o + '/>';
      case 'Inf': return '<path d="M-.5 -.3L.5 .3M-.5 .3L.5 -.3" ' + o + '/><ellipse rx=".5" ry=".3" ' + o + '/>';
      case 'SPArty': return '<circle r=".2" fill="var(--bm-ink)"/>';
      case 'AD': return '<path d="M-.5 .3A.5 .55 0 0 1 .5 .3" ' + o + '/>';
      case 'Helo': return '<path d="M-.5 -.3L0 0L-.5 .3ZM.5 -.3L0 0L.5 .3Z" ' + o + '/>';
      case 'HQ': return '<path d="M-.55 -.32V.98" ' + o + '/>';
      case 'Depot': return '<text y=".26" font-size=".8" font-weight="700" text-anchor="middle" fill="var(--bm-ink)">S</text>';
      case 'Eng': return '<text y=".26" font-size=".8" font-weight="700" text-anchor="middle" fill="var(--bm-ink)">E</text>';
    }
    return '<text y=".26" font-size=".8" font-weight="700" text-anchor="middle" fill="var(--bm-ink)">?</text>';
  }
  // frame: US rectangle (blue), Soviet diamond (red); `cls` = extra class on the frame
  function frame(side, cls) {
    var col = side === 0 ? 'var(--bm-us)' : 'var(--bm-ru)';
    var a = 'class="bm-fr ' + (cls || '') + '" fill="var(--bm-sym)" stroke="' + col + '" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linejoin="round"';
    return side === 0 ? '<rect x="-.85" y="-.58" width="1.7" height="1.16" ' + a + '/>'
      : '<path d="M0 -1L1 0L0 1L-1 0Z" ' + a + '/>';
  }
  function symbol(side, icon, cls) { return frame(side, cls) + glyph(icon); }

  function star(cx, cy, R, n) {
    var d = '';
    for (var i = 0; i < n * 2; i++) {
      var rr = i % 2 ? R * 0.5 : R, a = -Math.PI / 2 + i * Math.PI / n;
      d += (i ? 'L' : 'M') + f1(cx + rr * Math.cos(a)) + ' ' + f1(cy + rr * Math.sin(a));
    }
    return d + 'Z';
  }

  // ---------------------------------------------------------------- per-game lookups
  function prep(T, G) {
    if (G._bm) return G._bm;
    var vp = G.vp || T.vp || [], n = G.u.length, offmap = [];
    for (var i = 0; i < n; i++) if (G.p[i][0][1] === 0) offmap.push(i);
    G._bm = {vp: vp, offmap: offmap, nsub: [0, 0]};
    return G._bm;
  }
  function unitAt(G, i, t) {
    var r = G.p[i][rowAt(G.p[i], t)];
    return {hex: r[1], alive: r[2], st: r[3], t: r[0]};
  }
  function orderAt(G, i, t) {
    var rows = G.o[i];
    if (!rows.length || rows[0][0] > t) return null;
    var r = rows[rowAt(rows, t)];
    var txt = orderName(G.on[r[1]]);
    if (r[2]) txt += tr('orderTo', r[2]);
    else if (r[3] >= 0) txt += ' ' + G.u[r[3]][0];
    return txt;
  }
  function cellOwner(G, cell, t) {      // [owner, since sec or -1]
    var o = cell[2], since = -1;
    (G.vc || []).forEach(function (v) { if (v[1] === cell[0] && v[0] <= t) { o = v[2]; since = v[0]; } });
    return [o, since];
  }

  // ---------------------------------------------------------------- the map view (replay and snapshot)
  function makeView(el, T, G, opts, interactive, t0) {
    injectStyle();
    opts = opts || {};
    var P = prep(T, G);
    var root = mk('div', 'bm'), view = {el: el, t: clamp(t0 || 0, 0, TMAX), selected: -1, labels: !!opts.labels, playing: false};
    el.innerHTML = '';
    el.appendChild(root);

    var bar, slider, clockEl, playBtn, speedSel, statusEl, capEl;
    if (interactive) {
      bar = mk('div', 'bm-bar');
      playBtn = mk('button', 'bm-btn', tr('play'));
      playBtn.type = 'button';
      speedSel = mk('select');
      speedSel.setAttribute('aria-label', tr('speedAria'));
      [60, 180, 300, 600, 1200].forEach(function (v) {
        var o = mk('option', null, tr('speed', v / 60)); o.value = v; speedSel.appendChild(o);
      });
      speedSel.value = String(opts.speed || 300);
      if (speedSel.value !== String(opts.speed || 300)) speedSel.value = '300';
      slider = mk('input', 'bm-slider');
      slider.type = 'range'; slider.min = 0; slider.max = TMAX / 60; slider.step = 1; slider.value = Math.floor(view.t / 60);
      slider.setAttribute('aria-label', tr('timeAria'));
      clockEl = mk('span', 'bm-clock', clock(view.t));
      var lab = mk('label', 'bm-chk'), chk = mk('input'); chk.type = 'checkbox'; chk.checked = view.labels;
      lab.appendChild(chk); lab.appendChild(document.createTextNode(tr('names')));
      bar.appendChild(playBtn); bar.appendChild(speedSel); bar.appendChild(slider); bar.appendChild(clockEl); bar.appendChild(lab);
      root.appendChild(bar);
      chk.addEventListener('change', function () { view.labels = chk.checked; draw(); });
    }
    var wrap = mk('div', 'bm-wrap'), tip = mk('div', 'bm-tip');
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('role', 'img');
    wrap.appendChild(svg); wrap.appendChild(tip); root.appendChild(wrap);
    if (interactive) { statusEl = mk('div', 'bm-status'); root.appendChild(statusEl); }
    if (!interactive) { capEl = mk('div', 'bm-cap'); root.appendChild(capEl); }
    if (opts.legend != null ? opts.legend : interactive) root.appendChild(legendEl());

    var L = null;   // layout, rebuilt when the width changes
    var layers = {};

    function layout() {
      var W = opts.width || svg.getBoundingClientRect().width || el.clientWidth || 900;
      if (L && Math.abs(L.W - W) < 2) return false;
      var crop = opts.crop || [1, T.cols, 1, T.rows], compact = W < 460;
      var x0 = colX(crop[0]) - A, x1 = colX(crop[1]) + A, y0 = rowY(1, crop[2]) - H / 2, y1 = rowY(2, crop[3]) + H / 2;
      var mw = x1 - x0, mh = y1 - y0;
      var mlpx = opts.hexLabels === false ? 4 : 24, mtpx = opts.hexLabels === false ? 4 : 16;
      var mbpx = (opts.hexLabels === false ? 4 : 16) + (P.offmap.length ? 22 : 0);
      var vbW = mw / (1 - (2 * mlpx) / W), k = vbW / W;
      L = {W: W, k: k, x0: x0, y0: y0, mw: mw, mh: mh, ml: mlpx * k, mt: mtpx * k, mb: mbpx * k, crop: crop,
        vbW: vbW, vbH: mh + (mtpx + mbpx) * k, scale: 1 / k, compact: compact, S: opts.sym || (W >= 800 ? 9.5 : W >= 500 ? 8 : 6.5)};
      svg.setAttribute('viewBox', '0 0 ' + f1(L.vbW) + ' ' + f1(L.vbH));
      svg.style.width = opts.width ? opts.width + 'px' : '100%';
      buildStatic();
      return true;
    }

    function buildStatic() {
      var k = L.k, crop = L.crop, s = '';
      s += '<rect width="' + f1(L.vbW) + '" height="' + f1(L.vbH) + '" fill="var(--bm-surface)"/>';
      s += '<svg x="' + f1(L.ml) + '" y="' + f1(L.mt) + '" width="' + f1(L.mw) + '" height="' + f1(L.mh) + '" viewBox="' +
        f1(L.x0) + ' ' + f1(L.y0) + ' ' + f1(L.mw) + ' ' + f1(L.mh) + '" style="overflow:hidden">';
      s += terrainBase(T);
      if (opts.startAreas !== false && T.start) {
        var capt = !L.compact;
        T.start.forEach(function (a, side) {
          var xa = colX(a[0]) - A, xb = colX(a[1]) + A, ya = rowY(1, a[2]) - H / 2, yb = rowY(2, a[3]) + H / 2;
          s += '<rect x="' + f1(xa) + '" y="' + f1(ya) + '" width="' + f1(xb - xa) + '" height="' + f1(yb - ya) + '" fill="none" stroke="' +
            ownerVar(side) + '" stroke-width="1.5" stroke-dasharray="7 5" vector-effect="non-scaling-stroke" opacity=".7"/>';
          if (capt) s += '<text class="bm-halo" x="' + f1(xa + 4 * k) + '" y="' + f1(ya + 11 * k) + '" font-size="' + f1(10.5 * k) + '" font-weight="600" fill="var(--bm-ink2)" style="stroke-width:' +
            f1(3 * k) + 'px">' + tr('startArea' + (side ? 1 : 0)) + '</text>';
        });
      }
      s += '<g data-l="vp"></g><g data-l="trail"></g><g data-l="fx"></g><g data-l="units"></g><g data-l="hl"></g></svg>';
      // hex ticks on the four edges
      if (opts.hexLabels !== false) {
        var fs = 9 * k, cStep = [1, 2, 5, 10].filter(function (n) { return n * 1.5 * A / k >= 15; })[0] || 10;
        var rStep = [1, 2, 5, 10].filter(function (n) { return n * H / k >= 12; })[0] || 10, c, r;
        for (c = crop[0]; c <= crop[1]; c++) if (c % cStep === 0 || c === crop[0] && cStep === 1) {
          var tx = L.ml + colX(c) - L.x0;
          s += '<text x="' + f1(tx) + '" y="' + f1(L.mt - 4 * k) + '" font-size="' + f1(fs) + '" text-anchor="middle" fill="var(--bm-muted)">' + c + '</text>';
          s += '<text x="' + f1(tx) + '" y="' + f1(L.mt + L.mh + 11 * k) + '" font-size="' + f1(fs) + '" text-anchor="middle" fill="var(--bm-muted)">' + c + '</text>';
        }
        for (r = crop[2]; r <= crop[3]; r++) if (r % rStep === 0 || r === crop[2] && rStep === 1) {
          var ty = L.mt + rowY(1, r) - L.y0 + 3 * k;
          s += '<text x="' + f1(L.ml - 4 * k) + '" y="' + f1(ty) + '" font-size="' + f1(fs) + '" text-anchor="end" fill="var(--bm-muted)">' + r + '</text>';
          s += '<text x="' + f1(L.ml + L.mw + 4 * k) + '" y="' + f1(ty) + '" font-size="' + f1(fs) + '" text-anchor="start" fill="var(--bm-muted)">' + r + '</text>';
        }
      }
      s += '<g data-l="offmap"></g><g data-l="clk"></g>';
      svg.innerHTML = s;
      ['vp', 'trail', 'fx', 'units', 'hl', 'offmap', 'clk'].forEach(function (n) { layers[n] = svg.querySelector('[data-l="' + n + '"]'); });
    }

    // ---------------------------------------------------------------- dynamic drawing
    function unitMarkup(i, u, x, y, k) {
      var d = G.u[i], S = L.S * k, frac = d[5] ? u.alive / d[5] : 0, out = '', dead = u.st === 2 || u.alive === 0;
      var col = d[1] === 0 ? 'var(--bm-us)' : 'var(--bm-ru)';
      out += '<g class="bm-u" data-u="' + i + '" transform="translate(' + f1(x) + ' ' + f1(y) + ')">';
      if (dead) {
        out += '<g transform="scale(' + f1(S) + ')" opacity=".5"><rect x="-.9" y="-.9" width="1.8" height="1.8" fill="transparent"/>' +
          '<path d="M-.65 -.65L.65 .65M-.65 .65L.65 -.65" stroke="' + col + '" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linecap="round"/></g>';
      } else {
        var res = u.st === 0, sc = res ? 0.9 : 0.8 + 0.2 * frac, op = res ? 0.6 : 0.45 + 0.55 * frac;
        out += '<g transform="scale(' + f1(S * sc) + ')" opacity="' + f1(op) + '">' + symbol(d[1], d[2], res ? 'bm-res' : '') + '</g>';
        if (frac < 1 && !res) {
          var bw = 1.5 * S, by = (d[1] === 0 ? 0.74 : 1.1) * S * sc + 1.5 * k;
          out += '<rect x="' + f1(-bw / 2) + '" y="' + f1(by) + '" width="' + f1(bw) + '" height="' + f1(2.2 * k) + '" fill="var(--bm-bar)"/>' +
            '<rect x="' + f1(-bw / 2) + '" y="' + f1(by) + '" width="' + f1(bw * frac) + '" height="' + f1(2.2 * k) + '" fill="' + col + '"/>';
        }
      }
      if (view.labels || view.hlUnits[i]) {
        out += '<text class="bm-halo" y="' + f1(S * (d[1] === 0 ? 1.15 : 1.55) + 9 * k) + '" font-size="' + f1(9 * k) + '" text-anchor="middle" fill="var(--bm-ink)" style="stroke-width:' +
          f1(3 * k) + 'px">' + esc(view.hlUnits[i] && view.hlUnits[i] !== true ? view.hlUnits[i] : d[0]) + '</text>';
      }
      if (view.hlUnits[i]) out += '<circle r="' + f1(S * 1.5) + '" fill="none" stroke="var(--bm-hl)" stroke-width="2.2" vector-effect="non-scaling-stroke"/>';
      if (view.selected === i) out += '<circle r="' + f1(S * 1.35) + '" fill="none" stroke="var(--bm-ink)" stroke-width="1.6" stroke-dasharray="3 2" vector-effect="non-scaling-stroke"/>';
      return out + '</g>';
    }

    function drawUnits(t) {
      var k = L.k, S = L.S * k, byHex = {}, off = [], i;
      var us = [0, 0], tot = [0, 0], pend = [0, 0], onmap = [0, 0];
      view.hlUnits = view.hlUnits || {};
      for (i = 0; i < G.u.length; i++) {
        var u = unitAt(G, i, t), d = G.u[i];
        if (u.st === 0) {
          pend[d[1]] += d[5];
          if (opts.reinforcements !== 'hide' && u.hex) (byHex[u.hex] = byHex[u.hex] || []).push([i, u]);
          continue;
        }
        tot[d[1]] += d[5]; us[d[1]] += u.alive; onmap[d[1]]++;
        if (u.hex === 0) { off.push([i, u]); continue; }
        (byHex[u.hex] = byHex[u.hex] || []).push([i, u]);
      }
      var s = '';
      Object.keys(byHex).forEach(function (h) {
        var list = byHex[h].sort(function (a, b) { return G.u[a[0]][1] - G.u[b[0]][1] || a[0] - b[0]; });
        var xy = hexXY(+h), n = list.length, shown = list.slice(0, 8);
        var step = Math.min(S * 1.05, S * 3.2 / Math.max(1, shown.length - 1));
        shown.forEach(function (e, j) {
          var dx = (j - (shown.length - 1) / 2) * step, dy = shown.length > 1 ? (j % 2 ? 0.32 : -0.32) * S : 0;
          s += unitMarkup(e[0], e[1], xy[0] + dx, xy[1] + dy, k);
        });
        if (n > shown.length) s += '<text class="bm-halo" x="' + f1(xy[0] + S * 2.2) + '" y="' + f1(xy[1] + 3 * k) + '" font-size="' + f1(9 * k) + '" font-weight="700" fill="var(--bm-ink)" style="stroke-width:' + f1(3 * k) + 'px">+' + (n - shown.length) + '</text>';
      });
      layers.units.innerHTML = s;
      // off-map units (artillery without a hex) sit under the map
      var om = '';
      if (P.offmap.length) {
        var oy = L.mt + L.mh + L.mb - 11 * k, ox = L.ml + 46 * k;
        om += '<text x="' + f1(L.ml) + '" y="' + f1(oy + 3 * k) + '" font-size="' + f1(9.5 * k) + '" fill="var(--bm-ink2)">' + esc(tr('offMap')) + '</text>';
        off.forEach(function (e, j) { om += unitMarkup(e[0], e[1], ox + j * S * 2.4, oy, k); });
      }
      layers.offmap.innerHTML = om;
      // the off-map group lives in outer coordinates; its units use the same markup
      return {us: us, tot: tot, pend: pend, onmap: onmap};
    }

    function drawVp(t) {
      var k = L.k, R = 11 * k, s = '';
      P.vp.forEach(function (cell) {
        var xy = hexXY(cell[0]), ow = cellOwner(G, cell, t)[0];
        s += '<g class="bm-u" data-vp="' + cell[0] + '"><path d="' + star(xy[0], xy[1], R, 5) + '" fill="' + ownerVar(ow) +
          '" stroke="var(--bm-surface)" stroke-width="1.6" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>' +
          '<text class="bm-halo" x="' + f1(xy[0]) + '" y="' + f1(xy[1] - R - 3 * k) + '" font-size="' + f1(10 * k) + '" font-weight="650" text-anchor="middle" fill="var(--bm-ink)" style="stroke-width:' +
          f1(3 * k) + 'px">' + cell[1] + '</text></g>';
      });
      layers.vp.innerHTML = s;
    }

    function drawTrails(t) {
      var win = opts.trail == null ? 1200 : opts.trail, s = '', k = L.k;
      if (win > 0) {
        for (var i = 0; i < G.u.length; i++) {
          var rows = G.p[i], a = rowAt(rows, Math.max(0, t - win)), b = rowAt(rows, t), pts = [], last = -1;
          if (rows[b][3] !== 1) continue;
          for (var j = a; j <= b; j++) {
            if (rows[j][3] !== 1 || rows[j][1] === 0 || rows[j][1] === last) continue;
            last = rows[j][1]; pts.push(hexXY(last));
          }
          if (pts.length < 2) continue;
          s += '<polyline points="' + pts.map(function (p) { return f1(p[0]) + ',' + f1(p[1]); }).join(' ') + '" fill="none" stroke="' +
            ownerVar(G.u[i][1]) + '" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" opacity=".55" vector-effect="non-scaling-stroke"/>';
          s += '<circle cx="' + f1(pts[0][0]) + '" cy="' + f1(pts[0][1]) + '" r="' + f1(2 * k) + '" fill="' + ownerVar(G.u[i][1]) + '" opacity=".5"/>';
        }
      }
      if (view.selected >= 0 && G.p[view.selected]) {          // whole path of the clicked unit
        var rows2 = G.p[view.selected], pp = [], l2 = -1;
        for (var q = 0; q < rows2.length && rows2[q][0] <= t; q++) if (rows2[q][3] === 1 && rows2[q][1] && rows2[q][1] !== l2) { l2 = rows2[q][1]; pp.push(hexXY(l2)); }
        if (pp.length > 1) s += '<polyline points="' + pp.map(function (p) { return f1(p[0]) + ',' + f1(p[1]); }).join(' ') + '" fill="none" stroke="var(--bm-ink)" stroke-width="1.8" stroke-dasharray="4 3" opacity=".8" vector-effect="non-scaling-stroke"/>';
      }
      layers.trail.innerHTML = s;
    }

    function drawFx(t) {
      var win = opts.flash == null ? 300 : opts.flash, k = L.k, byHex = {}, s = '';
      if (win > 0) {
        var ev = G.ev, lo = 0, hi = ev.length - 1;
        while (lo <= hi) { var mid = (lo + hi) >> 1; if (ev[mid][0] < t - win) lo = mid + 1; else hi = mid - 1; }
        for (var i = lo; i < ev.length && ev[i][0] <= t; i++) {
          var e = ev[i]; if (!e[1]) continue;
          var b = byHex[e[1]] = byHex[e[1]] || {n: 0, loss: 0, newest: 0};
          b.n += e[3] === 2 ? 2 : 1; if (e[3] === 2) b.loss += e[4]; b.newest = Math.max(b.newest, e[0]);
        }
      }
      Object.keys(byHex).forEach(function (h) {
        var b = byHex[h], xy = hexXY(+h), age = (t - b.newest) / win, R = (10 + Math.min(10, Math.sqrt(b.n) * 3)) * k;
        var fresh = age < 0.35;
        s += '<g' + (fresh ? ' class="bm-flash"' : '') + ' opacity="' + f1(1 - 0.6 * age) + '"><path d="' + star(xy[0], xy[1], R, 8) +
          '" fill="var(--bm-hit)" stroke="var(--bm-ink)" stroke-width="' + (b.loss ? 1.6 : 0.8) + '" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
        if (b.loss) s += '<text class="bm-halo" x="' + f1(xy[0] + R * 0.9) + '" y="' + f1(xy[1] - R * 0.7) + '" font-size="' + f1(10 * k) + '" font-weight="700" fill="var(--bm-ink)" style="stroke-width:' + f1(3 * k) + 'px">−' + b.loss + '</text>';
        s += '</g>';
      });
      layers.fx.innerHTML = s;
    }

    function drawHighlights() {
      var k = L.k, s = '';
      (view.hlHex || []).forEach(function (h) {
        var xy = hexXY(h.hex);
        s += '<path d="' + hexPath(xy[0], xy[1]) + '" fill="none" stroke="var(--bm-hl)" stroke-width="2.6" vector-effect="non-scaling-stroke"/>';
        if (h.label) s += '<text class="bm-halo" x="' + f1(xy[0]) + '" y="' + f1(xy[1] + H / 2 + 10 * k) + '" font-size="' + f1(10 * k) + '" font-weight="650" text-anchor="middle" fill="var(--bm-ink)" style="stroke-width:' + f1(3 * k) + 'px">' + esc(h.label) + '</text>';
      });
      layers.hl.innerHTML = s;
    }

    function draw() {
      layout();
      var t = Math.floor(view.t / 60) * 60;
      drawVp(t); drawTrails(t); drawFx(t);
      var st = drawUnits(t);
      drawHighlights();
      if (!interactive && (opts.clock != null ? opts.clock : opts.caption == null)) {
        layers.clk.innerHTML = '<text class="bm-halo" x="' + f1(L.ml + 8 * L.k) + '" y="' + f1(L.mt + L.mh - 8 * L.k) + '" font-size="' + f1(15 * L.k) + '" font-weight="700" fill="var(--bm-ink)" style="stroke-width:' + f1(4 * L.k) + 'px">' + clock(t) + '</text>';
      }
      if (interactive) {
        clockEl.textContent = clock(t);
        if (+slider.value !== t / 60) slider.value = t / 60;
        var parts = [0, 1].map(function (sd) {
          return esc(tr('status', '@@', st.us[sd], st.tot[sd])).replace('@@', '<b style="color:inherit">' + esc(sideName(sd)) + '</b>');
        });
        statusEl.innerHTML = parts.join(' &middot; ') + [0, 1].map(function (sd) { return st.pend[sd] ? ' &middot; ' + esc(tr('later', st.pend[sd], sideName(sd))) : ''; }).join('');
      } else if (capEl) {
        capEl.textContent = opts.caption != null ? opts.caption : clock(t);
        capEl.style.display = capEl.textContent ? '' : 'none';
      }
      svg.setAttribute('aria-label', tr('mapAria', clock(t)));
    }

    // ---------------------------------------------------------------- highlight parsing
    view.hlUnits = {};
    view.hlHex = [];
    (opts.highlight || []).forEach(function (h) {
      var o = typeof h === 'object' ? h : (typeof h === 'number' ? {hex: h} : {unit: h});
      if (o.hex) view.hlHex.push({hex: o.hex, label: o.label});
      if (o.unit) G.u.forEach(function (d, i) { if (d[0] === o.unit) view.hlUnits[i] = o.label || true; });
    });

    // ---------------------------------------------------------------- tooltip + selection
    function tipFor(target, t) {
      var lines = [];
      var ud = target.closest('[data-u]'), vd = target.closest('[data-vp]');
      if (ud) {
        var i = +ud.getAttribute('data-u'), d = G.u[i], u = unitAt(G, i, t);
        lines.push(['b', d[0]]);
        lines.push(['m', sideName(d[1]) + ' · ' + typeName(d[2]) + (sizeName(d[3]) ? ' · ' + sizeName(d[3]) : '')]);
        if (d[4] >= 0) lines.push(['m', tr('partOf', G.pa[d[4]])]);
        if (u.st === 0) {
          var arr = G.p[i].filter(function (r) { return r[3] === 1 && r[0] > t; })[0];
          lines.push([null, tr('reserve', d[5])]);
          lines.push(['m', tr('enters', u.hex) + (arr ? tr('enterAt', clock(arr[0])) : '')]);
        } else {
          lines.push([null, u.st === 2 || u.alive === 0 ? tr('ko', d[5]) : tr('elemOp', u.alive, d[5])]);
          lines.push(['m', u.hex ? tr('hexAt', u.hex) : tr('offTip')]);
        }
        var o = orderAt(G, i, t);
        if (o) lines.push(['m', tr('order', o)]);
        var same = [];
        for (var j = 0; j < G.u.length; j++) if (j !== i && u.hex && unitAt(G, j, t).hex === u.hex && (unitAt(G, j, t).st === 0) === (u.st === 0) && unitAt(G, j, t).st !== 2) same.push(G.u[j][0]);
        if (same.length) lines.push(['m', tr('sameHex', same.slice(0, 6).join(', ') + (same.length > 6 ? ' +' + (same.length - 6) : ''))]);
      } else if (vd) {
        var cell = P.vp.filter(function (c) { return c[0] === +vd.getAttribute('data-vp'); })[0], ow = cellOwner(G, cell, t);
        lines.push(['b', tr('vpTitle', cell[0])]);
        lines.push([null, tr('vpHeld', cell[1], sideName(ow[0]))]);
        lines.push(['m', ow[1] >= 0 ? tr('since', clock(ow[1])) : tr(ow[0] === 2 ? 'unclaimedStart' : 'heldStart')]);
      }
      return lines;
    }
    function showTip(ev) {
      var lines = tipFor(ev.target, Math.floor(view.t / 60) * 60);
      if (!lines.length) { tip.style.display = 'none'; return; }
      tip.innerHTML = '';
      lines.forEach(function (l) {
        var row = mk('div');
        if (l[0] === 'b') row.appendChild(mk('b', null, l[1]));
        else row.appendChild(mk('span', l[0] === 'm' ? 'm' : null, l[1]));
        tip.appendChild(row);
      });
      tip.style.display = 'block';
      var r = wrap.getBoundingClientRect(), x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
      if (x + tip.offsetWidth > r.width) x = Math.max(0, ev.clientX - r.left - tip.offsetWidth - 14);
      if (y + tip.offsetHeight > r.height) y = Math.max(0, ev.clientY - r.top - tip.offsetHeight - 14);
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    }
    svg.addEventListener('pointermove', showTip);
    svg.addEventListener('pointerleave', function () { tip.style.display = 'none'; });
    svg.addEventListener('click', function (ev) {
      var ud = ev.target.closest('[data-u]'), i = ud ? +ud.getAttribute('data-u') : -1;
      view.selected = i === view.selected ? -1 : i;
      draw();
    });

    // ---------------------------------------------------------------- playback
    var raf = 0, last = 0;
    function tick(ts) {
      if (!view.playing) return;
      var dt = last ? (ts - last) / 1000 : 0; last = ts;
      var nt = view.t + dt * (+speedSel.value);
      if (nt >= TMAX) { view.t = TMAX; draw(); view.pause(); return; }
      var moved = Math.floor(nt / 60) !== Math.floor(view.t / 60);
      view.t = nt;
      if (moved) draw();
      raf = requestAnimationFrame(tick);
    }
    view.setTime = function (sec) { view.t = clamp(sec, 0, TMAX); draw(); };
    view.play = function () {
      if (!interactive || view.playing) return;
      if (view.t >= TMAX) view.t = 0;
      view.playing = true; last = 0; playBtn.textContent = tr('pause');
      raf = requestAnimationFrame(tick);
    };
    view.pause = function () {
      view.playing = false; cancelAnimationFrame(raf);
      if (playBtn) playBtn.textContent = tr('play');
    };
    view.redraw = function () { L = null; draw(); };
    if (interactive) {
      playBtn.addEventListener('click', function () { view.playing ? view.pause() : view.play(); });
      slider.addEventListener('input', function () { view.t = +slider.value * 60; draw(); });
    }
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () { if (L && Math.abs((opts.width || svg.getBoundingClientRect().width) - L.W) >= 2) draw(); }).observe(wrap);
    }
    draw();
    return view;
  }

  // ---------------------------------------------------------------- legend
  function legendEl() {
    var d = mk('div', 'bm-legend'), items = [];
    function item(svgInner, w, text) { items.push('<span><svg width="' + w + '" height="18" viewBox="0 0 ' + w + ' 18">' + svgInner + '</svg>' + esc(text) + '</span>'); }
    function sym(side, icon, w) { return '<g transform="translate(' + (w / 2) + ' 9) scale(7)">' + symbol(side, icon) + '</g>'; }
    item(sym(0, 'Tank', 24), 24, sideName(0));
    item(sym(1, 'Tank', 24), 24, sideName(1));
    [['Tank', 'lgTank'], ['Recce', 'lgRecon'], ['Inf', 'lgInf'], ['SPArty', 'lgArty'], ['AD', 'lgAD'],
      ['Helo', 'lgHelo'], ['HQ', 'lgHQ'], ['Depot', 'lgDepot'], ['Eng', 'lgEng']].forEach(function (g) { item(sym(0, g[0], 24), 24, tr(g[1])); });
    item('<g transform="translate(12 9) scale(7)" opacity=".5"><path d="M-.65 -.65L.65 .65M-.65 .65L.65 -.65" stroke="var(--bm-ru)" stroke-width="2" vector-effect="non-scaling-stroke"/></g>', 24, tr('lgKO'));
    item('<g transform="translate(12 8) scale(6)" opacity=".6">' + symbol(0, 'Recce') + '</g><rect x="3" y="14" width="18" height="2.2" fill="var(--bm-bar)"/><rect x="3" y="14" width="9" height="2.2" fill="var(--bm-us)"/>', 24, tr('lgLoss'));
    item('<g transform="translate(12 9) scale(7)" opacity=".6">' + symbol(1, 'Tank', 'bm-res') + '</g>', 24, tr('lgRes'));
    [0, 1, 2].forEach(function (o) { item('<path d="' + star(12, 10, 8, 5) + '" fill="' + ownerVar(o) + '"/>', 24, tr('lgVp' + o)); });
    item('<path d="' + star(12, 9, 9, 8) + '" fill="var(--bm-hit)" stroke="var(--bm-ink)" stroke-width=".8"/>', 24, tr('lgCombat'));
    item('<path d="M2 14L10 5L22 9" fill="none" stroke="var(--bm-us)" stroke-width="1.6" opacity=".6"/>', 24, tr('lgTrail'));
    item('<rect x="2" y="3" width="20" height="12" fill="none" stroke="var(--bm-ink2)" stroke-dasharray="4 3"/>', 24, tr('lgStart'));
    [['c0', 'lgOpen'], ['c1', 'lgLight'], ['c2', 'lgHeavy'], ['c3', 'lgTown'], ['c4', 'lgWater']].forEach(function (c) {
      item('<rect x="3" y="3" width="18" height="12" fill="var(--bm-' + c[0] + ')" stroke="var(--bm-grid)"/>', 24, tr(c[1]));
    });
    item('<path d="M1 9H23" stroke="var(--bm-road1)" stroke-width="3.2"/>', 24, tr('lgMain'));
    item('<path d="M1 9H23" stroke="var(--bm-road2)" stroke-width="1.7"/>', 24, tr('lgMinor'));
    item('<path d="M1 9H23" stroke="var(--bm-rail)" stroke-width="2"/><path d="M1 9H23" stroke="var(--bm-casing)" stroke-width="1.1" stroke-dasharray="3 3"/>', 24, tr('lgRail'));
    item('<path d="M1 9H23" stroke="var(--bm-river)" stroke-width="3"/>', 24, tr('lgRiver'));
    item('<path d="M1 9H23" stroke="var(--bm-water)" stroke-width="1.5"/>', 24, tr('lgStream'));
    d.innerHTML = items.join('');
    return d;
  }

  // ---------------------------------------------------------------- chart
  function chart(el, G, opts) {
    injectStyle();
    opts = opts || {};
    var root = mk('div', 'bm'), tipEl = mk('div', 'bm-tip'), host = mk('div', 'bm-wrap');
    el.innerHTML = '';
    el.appendChild(root);
    if (opts.title) { var h = mk('div', null, opts.title); h.style.cssText = 'font-weight:650;font-size:14px;margin:0 0 4px'; root.appendChild(h); }
    var leg = mk('div', 'bm-chart-legend');
    var SER = [
      {key: 'share', i: 3, name: tr('serShare'), short: tr('shShare'), col: 'var(--bm-us)', dash: ''},
      {key: 'f0', i: 1, name: tr('serF0'), short: tr('shF0'), col: 'var(--bm-us)', dash: '6 4'},
      {key: 'f1', i: 2, name: tr('serF1'), short: tr('shF1'), col: 'var(--bm-ru)', dash: '6 4'}];
    SER.forEach(function (s) {
      var sp = mk('span');
      sp.innerHTML = '<svg width="26" height="10" viewBox="0 0 26 10"><path d="M0 5H26" stroke="' + s.col + '" stroke-width="2" stroke-linecap="round"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : '') + '/></svg>';
      sp.appendChild(document.createTextNode(s.name));
      leg.appendChild(sp);
    });
    var vsp = mk('span');
    vsp.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="var(--bm-ink2)" stroke="var(--bm-surface)" stroke-width="2"/></svg>';
    vsp.appendChild(document.createTextNode(tr('vpLegend')));
    leg.appendChild(vsp);
    root.appendChild(leg);
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', tr('chartAria'));
    host.appendChild(svg); host.appendChild(tipEl); root.appendChild(host);

    var sc = G.sc || [], cells = G.vp || [];
    var W = 0, geo = null;
    var vcs = (G.vc || []).map(function (v) {
      var c = cells.filter(function (x) { return x[0] === v[1]; })[0];
      return {t: v[0], hex: v[1], owner: v[2], vp: c ? c[1] : 0};
    });

    function render() {
      W = opts.width || svg.getBoundingClientRect().width || el.clientWidth || 720;
      var Ht = opts.height || 260, laneEnd = [];
      var pwEst = W - 38 - Math.min(124, W * 0.2);
      vcs.forEach(function (v) {
        var x = pwEst * v.t / TMAX, ln = 0;
        while (laneEnd[ln] !== undefined && laneEnd[ln] > x - 7) ln++;
        laneEnd[ln] = x + 40; v.lane = ln;
      });
      var strip = 22 + Math.max(1, laneEnd.length) * 15;
      var m = {l: 38, r: Math.min(124, W * 0.2), t: 10, b: 24}, ph = Ht - m.t - m.b, pw = W - m.l - m.r;
      var totalH = Ht + strip;
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + totalH);
      svg.style.width = opts.width ? W + 'px' : '100%';
      svg.style.height = opts.width ? totalH + 'px' : 'auto';
      var X = function (t) { return m.l + pw * t / TMAX; }, Y = function (v) { return m.t + ph * (1 - v / 100); };
      geo = {m: m, pw: pw, ph: ph, X: X, Y: Y};
      var s = '';
      [0, 25, 50, 75, 100].forEach(function (v) {
        s += '<path d="M' + m.l + ' ' + f1(Y(v)) + 'H' + (m.l + pw) + '" stroke="' + (v === 0 ? 'var(--bm-axis)' : 'var(--bm-line)') + '" stroke-width="1"/>';
        s += '<text x="' + (m.l - 6) + '" y="' + f1(Y(v) + 3.5) + '" font-size="11" text-anchor="end" fill="var(--bm-muted)">' + v + '%</text>';
      });
      var every = pw > 520 ? 1800 : 3600;
      for (var t = 0; t <= TMAX; t += every) {
        s += '<path d="M' + f1(X(t)) + ' ' + (m.t + ph) + 'v4" stroke="var(--bm-axis)"/>';
        s += '<text x="' + f1(X(t)) + '" y="' + (m.t + ph + 17) + '" font-size="11" text-anchor="middle" fill="var(--bm-muted)">' + clock(t) + '</text>';
      }
      // VP changes: hairline up the plot, marker + hex in the strip below
      var sy = m.t + ph + 36;
      vcs.forEach(function (v, n) {
        var x = X(v.t);
        s += '<path d="M' + f1(x) + ' ' + m.t + 'V' + (m.t + ph) + '" stroke="' + ownerVar(v.owner) + '" stroke-width="1" opacity=".35"/>';
      });
      var linePaths = '';
      SER.forEach(function (ser) {
        if (!sc.length) return;
        var d = sc.map(function (r, j) { return (j ? 'L' : 'M') + f1(X(r[0])) + ' ' + f1(Y(r[ser.i])); }).join('');
        linePaths += '<path d="' + d + '" fill="none" stroke="' + ser.col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"' + (ser.dash ? ' stroke-dasharray="' + ser.dash + '"' : '') + '/>';
      });
      s += linePaths;
      // end labels with leader lines, spread so they do not collide
      if (sc.length) {
        var lastRow = sc[sc.length - 1], labs = SER.map(function (ser) { return {ser: ser, y: Y(lastRow[ser.i]), v: lastRow[ser.i]}; }).sort(function (a, b) { return a.y - b.y; });
        var ly = labs.map(function (l) { return l.y; });
        for (var q = 1; q < ly.length; q++) if (ly[q] - ly[q - 1] < 14) ly[q] = ly[q - 1] + 14;
        var over = ly[ly.length - 1] - (m.t + ph); if (over > 0) for (q = 0; q < ly.length; q++) ly[q] -= over;
        var ex = X(lastRow[0]);
        labs.forEach(function (l, q) {
          s += '<path d="M' + f1(ex) + ' ' + f1(l.y) + 'L' + f1(ex + 8) + ' ' + f1(ly[q]) + 'H' + f1(ex + 12) + '" fill="none" stroke="' + l.ser.col + '" stroke-width="1" opacity=".7"/>';
          s += '<text x="' + f1(ex + 16) + '" y="' + f1(ly[q] + 4) + '" font-size="11.5" fill="var(--bm-ink)"><tspan font-weight="650">' + f1(l.v) + '%</tspan><tspan fill="var(--bm-ink2)"> ' + l.ser.short + '</tspan></text>';
        });
      }
      // strip with markers
      s += '<path d="M' + m.l + ' ' + (sy - 2) + 'H' + (m.l + pw) + '" stroke="var(--bm-line)"/>';
      vcs.forEach(function (v, n) {
        var x = X(v.t), cy = sy + v.lane * 15;
        s += '<g data-vc="' + n + '"><circle cx="' + f1(x) + '" cy="' + cy + '" r="5" fill="' + ownerVar(v.owner) + '" stroke="var(--bm-surface)" stroke-width="2"/>' +
          '<circle cx="' + f1(x) + '" cy="' + cy + '" r="11" fill="transparent"/>' +
          '<text x="' + f1(x + 9) + '" y="' + (cy + 3.5) + '" font-size="10" fill="var(--bm-ink2)">' + v.hex + '</text></g>';
      });
      if (!vcs.length) s += '<text x="' + (m.l + 4) + '" y="' + (sy + 4) + '" font-size="11" fill="var(--bm-muted)">' + esc(tr('noVp')) + '</text>';
      s += '<g id="xh" style="display:none"><path id="xl" d="" stroke="var(--bm-muted)" stroke-width="1"/><circle id="d0" r="4.5" fill="' + SER[0].col + '" stroke="var(--bm-surface)" stroke-width="2"/><circle id="d1" r="4.5" fill="' + SER[1].col + '" stroke="var(--bm-surface)" stroke-width="2"/><circle id="d2" r="4.5" fill="' + SER[2].col + '" stroke="var(--bm-surface)" stroke-width="2"/></g>';
      s += '<rect id="hit" x="' + m.l + '" y="' + m.t + '" width="' + pw + '" height="' + ph + '" fill="transparent"/>';
      svg.innerHTML = s;
      tipEl.style.display = 'none';
    }

    function showAt(ev) {
      if (!sc.length) return;
      var r = svg.getBoundingClientRect(), k = W / r.width, x = (ev.clientX - r.left) * k, tt = clamp((x - geo.m.l) / geo.pw * TMAX, 0, TMAX);
      var j = rowAt(sc, tt); if (j + 1 < sc.length && Math.abs(sc[j + 1][0] - tt) < Math.abs(sc[j][0] - tt)) j++;
      var row = sc[j], xs = geo.X(row[0]);
      var xh = svg.querySelector('#xh'); xh.style.display = '';
      svg.querySelector('#xl').setAttribute('d', 'M' + f1(xs) + ' ' + geo.m.t + 'V' + (geo.m.t + geo.ph));
      SER.forEach(function (ser, q) { var c = svg.querySelector('#d' + q); c.setAttribute('cx', f1(xs)); c.setAttribute('cy', f1(geo.Y(row[ser.i]))); });
      tipEl.innerHTML = '';
      var head = mk('div', null, clock(row[0])); head.style.fontWeight = '650'; tipEl.appendChild(head);
      SER.forEach(function (ser) {
        var line = mk('div'), key = mk('span', 'k'); key.style.borderTopColor = ser.col; if (ser.dash) key.style.borderTopStyle = 'dashed';
        var val = mk('b', null, f1(row[ser.i]) + '%');
        line.appendChild(key); line.appendChild(val); line.appendChild(mk('span', 'm', ' ' + ser.name));
        tipEl.appendChild(line);
      });
      tipEl.appendChild(mk('div', 'm', tr('vpHeldTip', row[4], row[5])));
      tipEl.style.display = 'block';
      var hr = host.getBoundingClientRect(), px = ev.clientX - hr.left + 14, py = ev.clientY - hr.top + 14;
      if (px + tipEl.offsetWidth > hr.width) px = ev.clientX - hr.left - tipEl.offsetWidth - 14;
      tipEl.style.left = Math.max(0, px) + 'px'; tipEl.style.top = Math.max(0, py) + 'px';
    }
    function hideAt() { var xh = svg.querySelector('#xh'); if (xh) xh.style.display = 'none'; tipEl.style.display = 'none'; }
    function showVc(ev) {
      var g = ev.target.closest('[data-vc]'); if (!g) return false;
      var v = vcs[+g.getAttribute('data-vc')];
      tipEl.innerHTML = '';
      var head = mk('div', null, clock(v.t) + ' · ' + tr('cell', v.hex)); head.style.fontWeight = '650'; tipEl.appendChild(head);
      tipEl.appendChild(mk('div', 'm', (v.vp ? v.vp + ' VP · ' : '') + (v.owner === 2 ? tr('nowNobody') : tr('takenBy', sideName(v.owner)))));
      tipEl.style.display = 'block';
      var hr = host.getBoundingClientRect(), px = ev.clientX - hr.left + 14, py = ev.clientY - hr.top + 14;
      if (px + tipEl.offsetWidth > hr.width) px = ev.clientX - hr.left - tipEl.offsetWidth - 14;
      tipEl.style.left = Math.max(0, px) + 'px'; tipEl.style.top = Math.max(0, py) + 'px';
      return true;
    }
    svg.addEventListener('pointermove', function (ev) { if (ev.target.id === 'hit') { showAt(ev); } else { hideAt(); showVc(ev); } });
    svg.addEventListener('pointerleave', hideAt);

    if (opts.table !== false && sc.length) {
      var det = mk('details'), sum = mk('summary', null, tr('tableView')), tb = mk('table');
      det.appendChild(sum);
      var th = mk('tr');
      ['thTime', 'thShare', 'thF0', 'thF1', 'thVp0', 'thVp1'].map(function (k) { return tr(k); }).forEach(function (c) { th.appendChild(mk('th', null, c)); });
      tb.appendChild(th);
      sc.forEach(function (r) {
        if (r[0] % 900 !== 0 && r !== sc[sc.length - 1]) return;
        var tr = mk('tr');
        [clock(r[0]), f1(r[3]), f1(r[1]), f1(r[2]), r[4], r[5]].forEach(function (c) { tr.appendChild(mk('td', null, String(c))); });
        tb.appendChild(tr);
      });
      det.appendChild(tb);
      if (vcs.length) {
        var tb2 = mk('table'), h2 = mk('tr');
        ['thChange', 'thCell', 'VP', 'thNew'].map(function (k) { return k === 'VP' ? k : tr(k); }).forEach(function (c) { h2.appendChild(mk('th', null, c)); });
        tb2.appendChild(h2);
        vcs.forEach(function (v) {
          var tr = mk('tr');
          [clock(v.t), v.hex, v.vp, sideName(v.owner)].forEach(function (c) { tr.appendChild(mk('td', null, String(c))); });
          tb2.appendChild(tr);
        });
        det.appendChild(tb2);
      }
      root.appendChild(det);
    }
    render();
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () { var w = opts.width || svg.getBoundingClientRect().width; if (Math.abs(w - W) >= 2) render(); }).observe(host);
    }
    return {el: el, render: render};
  }

  // ---------------------------------------------------------------- public API
  global.BattleMap = {
    version: 1,
    lang: 'en',
    replay: function (el, terrain, game, opts) { return makeView(el, terrain, game, opts, true, (opts && opts.start) || 0); },
    snapshot: function (el, terrain, game, tSec, opts) { return makeView(el, terrain, game, opts, false, tSec); },
    chart: chart,
    legend: function (el) { injectStyle(); var r = mk('div', 'bm'); r.appendChild(legendEl()); el.innerHTML = ''; el.appendChild(r); return {el: el}; },
    clock: clock
  };
})(typeof window !== 'undefined' ? window : this);
