// HOI4 skin, v4 (broadcast layout). Rule: game bitmaps are only used at their native size or smaller. Window frames are the
// game's tiled_window 9-slice (64 px slices drawn at 18 px, edges tiled with `round`, the centre not stretched: the panel body
// is a CSS gradient); the share bar uses the land-combat progress strips at their native 14 px height; the loss boxes the
// 84x32 combat icon; the engagement marker the 38x37 land-combat button. Everything else (top bar, clock box, header strips,
// buttons, banner, captions) is drawn with CSS in the game's look: gunmetal gradients, dark bevels, rivet rows, gold
// accents, condensed numerals. Bitmaps live in $ARENA_DATA/video/hoi4-assets/ui (not in the repo).
// asset root: ?video=<file URL> on sandbox.html (render.py passes $ARENA_DATA/video/), default <repo>/data/video/
const VIDEO = globalThis.ARENA_VIDEO || new URL('../../data/video/', import.meta.url).href;
const UI = VIDEO + 'hoi4-assets/ui/';
const I = p => `{{img:${UI}${p}}}`;
const RIVETS = 'radial-gradient(circle,#a3a196 0 1.6px,#2a2a26 2.2px,#0e0e0d 2.8px,transparent 3.3px)';
const METAL = 'linear-gradient(180deg,#55564f 0%,#3a3b35 38%,#272824 70%,#1c1d1a 100%)';
export default {
  name: 'hoi4-v4',
  vars: {
    '--font-ui': '"Noto Sans SC", sans-serif',
    '--font-num': '"Oswald", "Noto Sans SC", sans-serif',
    '--text': '#dedbd0', '--text-hi': '#f6f3e8', '--muted': '#a9a694', '--accent': '#f2c84b',
    '--edge': '#0d0d0c', '--rule': '#3e3e38',
    '--panel-top': 'rgba(40,41,37,.95)', '--panel-bot': 'rgba(20,21,19,.95)',
    '--blue': '#4d7fd0', '--blue-dk': '#2a4f8f', '--blue-lt': '#a9c8f4',
    '--red': '#cc4b3e', '--red-dk': '#86281f', '--red-lt': '#f2a99c',
  },
  fonts: './vendor/fonts.css',
  frames: { panel: { src: UI + 'panel/tiled_window_1b_border.png', slice: 64, width: 18, repeat: 'round', fill: false } },
  textures: {},
  engageIcon: UI + 'combat/mapicon_land_combat.png',
  // blitz_pattern.png 400x178: body band rows 62-116 (v 0.35-0.65), double head from x 190 to 394 (u 0.47-0.985)
  arrow: { src: UI + 'maparrows/blitz_pattern.png', bodyU: 0.12, bodyV: [0.35, 0.65], headU: [0.47, 0.985], headV: [0.0, 1.0],
    headAspect: 1.16, bodyRatio: 0.30, overlap: 0.14, fill: ['#4f86e0', '#e0473a'], grey: 0.55 },
  css: `
.panel{border-radius:0;background:linear-gradient(180deg,var(--panel-top),var(--panel-bot)) padding-box;box-shadow:0 6px 16px rgba(0,0,0,.55);}
/* top bar: full width, opaque gunmetal strip with rivet rows; title + share bar in the middle, clock box at the right */
.top{left:0;top:0;width:1920px;height:70px;margin-left:0;border:none;border-image:none;border-radius:0;background:${METAL};
  box-shadow:inset 0 1px 0 rgba(255,255,255,.22),inset 0 -1px 0 rgba(0,0,0,.8),0 2px 0 #0b0b0a,0 3px 0 #8a7536,0 8px 16px rgba(0,0,0,.55);}
.top:before,.top:after{content:"";position:absolute;left:10px;right:10px;height:6px;background:${RIVETS} 0 0/46px 6px repeat-x;opacity:.75;}
.top:before{top:3px}.top:after{bottom:3px}
.top .title{left:520px;right:520px;top:7px;font-family:var(--font-ui);font-size:20px;font-weight:900;letter-spacing:2px;color:#ece8da;text-shadow:0 1px 2px #000;}
.top .title .b{color:#a9c8f4}.top .title .r{color:#f2a99c}.top .title .dot{color:#f2c84b;margin:0 10px}
.top .chip{top:15px;width:112px;height:38px;line-height:38px;font-size:18px;letter-spacing:4px;border:2px solid #0b0b0a;border-radius:0;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.35),inset 0 -2px 0 rgba(0,0,0,.35),0 0 0 1px #6b6a5f;}
.top .chip.b{left:22px;right:auto;background:linear-gradient(180deg,#6a9be6,#2f5aa0 55%,#24477f);}
.top .chip.r{left:144px;right:auto;background:linear-gradient(180deg,#e2695a,#a3392d 55%,#7d2a22);}
.top .lbl{top:24px;font-size:15px;letter-spacing:2px;color:#c9c6b6;text-shadow:0 1px 2px #000;}
.top .lbl.l{left:auto;right:258px}.top .lbl.r{left:274px;right:auto;color:#f2c84b;font-family:var(--font-num);font-size:17px}
.top .clock{left:auto;right:20px;top:11px;width:222px;margin-left:0;height:44px;line-height:44px;font-size:32px;font-weight:600;color:#f6f2e2;letter-spacing:3px;
  background:linear-gradient(180deg,#0c0c0b,#20201d);border:2px solid #000;box-shadow:inset 0 2px 5px #000,0 0 0 1px #77756a,0 0 0 3px #1a1a18;text-shadow:0 0 6px rgba(242,200,75,.25);}
.share{top:36px;width:720px;margin-left:-360px;height:22px;}
.share .bar{height:14px;top:0;background:#151514;border:2px solid #0b0b0a;border-radius:0;box-shadow:0 0 0 1px #6b6a5f,inset 0 1px 3px #000;}
.share .bar .bf{background:${I('combat/landcombat_defender_progress.png')} left center/auto 14px repeat-x;}
.share .bar .rf{background:${I('combat/landcombat_attacker_progress.png')} right center/auto 14px repeat-x;}
.share .tick{top:-6px;height:26px;width:3px;background:#f2c84b;box-shadow:0 0 0 1px #000;}
.share .t{top:-6px;font-family:var(--font-num);font-size:18px;font-weight:600;color:#f1eee2;text-shadow:0 1px 2px #000;}
.share .t.l{left:-176px;width:166px;text-align:right}.share .t.r{right:-176px;width:166px;text-align:left}.share .t.c{display:none}
/* pop-up panels */
.side{top:96px;left:22px;padding:4px 10px 8px;}
.side .ref{color:#a9a694;border-bottom:1px solid #3e3e38;}
.side .ref b{color:#f6f3e8;}
.side .eye{border-color:#f2c84b}.side .eye i{background:#f2c84b}
.side .hd{color:#f2c84b;font-size:16px;letter-spacing:2px;border-bottom:1px solid #3e3e38;padding:4px 0 3px;}
.force .f .n{font-family:var(--font-num);font-size:42px;}
.vp{border-radius:0;border:2px solid #0b0b0a;box-shadow:inset 0 1px 0 rgba(255,255,255,.22);}
.vp.o0{background:linear-gradient(180deg,#5686d2,#284a86)}.vp.o1{background:linear-gradient(180deg,#cf5547,#7f261e)}
.vp.hot{outline:3px solid #f2c84b}
.card{top:96px;right:22px;padding:4px 12px 10px;}
.card .k{color:#f2c84b;letter-spacing:2px;}
.card .ti{margin:4px -4px 0;padding:4px 10px 6px;font-size:26px;color:#f6f3e8;background:linear-gradient(180deg,#3d3f38,#262722);border:1px solid #000;box-shadow:inset 0 1px 0 rgba(255,255,255,.15);}
.card .row{border-top:1px solid #3e3e38;}
.card .loss div{border-radius:0;position:relative;}
.card .loss .lb{background:rgba(48,86,150,.32);border:2px solid #0d0d0c;box-shadow:inset 0 0 0 1px #4d7fd0;}
.card .loss .lr{background:rgba(150,46,36,.32);border:2px solid #0d0d0c;box-shadow:inset 0 0 0 1px #cc4b3e;}
.card .loss .lb:after,.card .loss .lr:after{content:"";position:absolute;right:8px;bottom:8px;width:42px;height:32px;background:${I('combat/unitview_entry_combat_icon.png')} 0 0/84px 32px no-repeat;}
.card .loss .lb:after{background-position:-42px 0}
.card .res{border-radius:0;border:2px solid #0b0b0a;box-shadow:inset 0 1px 0 rgba(255,255,255,.22),inset 0 -2px 0 rgba(0,0,0,.3);}
.card .res.n{background:linear-gradient(180deg,#5b5a50,#3a3933);}
.feed{right:22px;bottom:70px;width:400px;}
.feed div{background:linear-gradient(90deg,rgba(18,18,16,.9),rgba(30,31,28,.82));border:1px solid #000;border-left-width:6px;line-height:28px;font-size:16px;}
.feed div.s0{border-left-color:#4d7fd0}.feed div.s1{border-left-color:#cc4b3e}
.order{top:auto;bottom:150px;left:22px;padding:8px 12px;}
.order .pt{border:2px solid #0b0b0a;box-shadow:0 0 0 1px #6b6a5f;}
.step,.explain{top:96px;}
.banner{top:180px;}
.banner .k{background:#f2c84b;color:#14130f;border:2px solid #0b0b0a;letter-spacing:6px;}
.banner .ti{display:inline-block;padding:6px 60px 10px;margin-top:10px;font-size:48px;color:#f6f3e8;background:linear-gradient(180deg,rgba(64,65,58,.94),rgba(28,29,26,.94));
  border:2px solid #0b0b0a;box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 0 0 1px #77756a,0 6px 18px rgba(0,0,0,.6);text-shadow:0 2px 4px #000;}
.banner .su{font-size:21px;color:#ece8da;text-shadow:0 1px 3px #000,0 0 8px #000;margin-top:8px;}
.cap{bottom:40px;}
.cap span{border-radius:0;font-size:21px;color:#f3f0e4;padding:9px 24px;background:linear-gradient(180deg,rgba(44,45,40,.93),rgba(22,23,20,.93));border:2px solid #0b0b0a;box-shadow:0 0 0 1px #6b6a5f,0 4px 12px rgba(0,0,0,.55);}
.narr span{border-radius:0;background:linear-gradient(180deg,rgba(44,45,40,.93),rgba(22,23,20,.93));border:2px solid #0b0b0a;}
.attr{bottom:8px;font-size:12px;line-height:16px;color:rgba(239,230,207,.6);}
.end .k{color:#f2c84b}.end .ti{padding:4px 12px 8px;margin:4px -6px 0;background:linear-gradient(180deg,#3d3f38,#262722);border:1px solid #000;}
.end .grid .c{border-radius:0;border:2px solid #0b0b0a;background:rgba(0,0,0,.35);box-shadow:inset 0 0 0 1px #3f3f39;}
`,
};
