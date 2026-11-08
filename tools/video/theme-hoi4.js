// HOI4 skin for the sandbox overlay (v3). Textures are the game's own interface sprites, converted from DDS to PNG by
// tools/video/hoi4/ui_extract.py into $ARENA_DATA/video/hoi4-assets/ui (not in the repo; inlined as data:
// URLs at init). Layout follows the game's main screen: a metal top bar across the screen (date box at the right end),
// riveted dark window frames (tiled_window, 9-slice 64 px), the land-combat progress bars (attacker red / defender blue
// chevrons) for the share bar, combat icons on the loss boxes, decision-row strips for the kill feed, map counters
// (onmap_unit_counter plate + counters/divisions_large unit icons + green strength pips) and the battle-plan arrow
// (maparrows/blitz_pattern) for the key-moment pushes. Fonts: the game's bitmap fonts are not in the unpacked set, so
// Oswald (condensed, like hoi_*mbs) + Noto Sans SC stand in; sizes follow interface/*.gui (top bar 18, headers 30).
// asset root: ?video=<file URL> on sandbox.html (render.py passes $ARENA_DATA/video/), default <repo>/data/video/
const VIDEO = globalThis.ARENA_VIDEO || new URL('../../data/video/', import.meta.url).href;
const UI = VIDEO + 'hoi4-assets/ui/';
const I = p => `{{img:${UI}${p}}}`;
const ICON = n => UI + 'army_icons/' + n + '.png';
export default {
  name: 'hoi4',
  vars: {
    '--font-ui': '"Noto Sans SC", sans-serif',
    '--font-num': '"Oswald", "Noto Sans SC", sans-serif',
    '--text': '#dedbd0',
    '--text-hi': '#f4f1e6',
    '--muted': '#a7a493',
    '--accent': '#f2c84b',
    '--edge': '#121210',
    '--rule': '#3b3b36',
    '--panel-top': 'rgba(30,31,29,.94)',
    '--panel-bot': 'rgba(18,19,18,.94)',
    '--blue': '#4d7fd0', '--blue-dk': '#2a4f8f', '--blue-lt': '#a9c8f4',
    '--red': '#cc4b3e', '--red-dk': '#86281f', '--red-lt': '#f2a99c',
  },
  fonts: './vendor/fonts.css',
  frames: {
    panel: { src: UI + 'panel/tiled_window_1b_border.png', slice: 64, width: 18, repeat: 'stretch', fill: true },
  },
  textures: {},
  counter: {
    plate: UI + 'counter/onmap_unit_counter.png', plateFrames: 3,
    iconFrames: 2, iconFrame: 0,                      // frame 0 = the game's unit sprite, 1 = NATO symbol
    side: [{ plate: '#6f9be0' }, { plate: '#d9584a' }],
    bar: '#62b43c', barLost: '#3d2a24',
    icons: {
      Tank: ICON('unit_medium_tank_icon'), Recce: ICON('unit_recon_icon'), Inf: ICON('unit_infantry_icon'), APC: ICON('unit_mechanized_icon'),
      SPArty: ICON('unit_medium_tank_artillery_icon'), Arty: ICON('unit_art_icon'), AD: ICON('unit_anti_air_icon'), HQ: ICON('support_unit_hq_icon'),
      AT: ICON('unit_at_icon'), 'SP AT': ICON('unit_at_icon'), Eng: ICON('unit_engineer_icon'), Helo: ICON('unit_helicopter_brigade_icon'),
      Utility: ICON('unit_motorized_icon'), Depot: ICON('unit_motorized_icon'), WLR: ICON('unit_motorized_icon'),
    },
  },
  // blitz_pattern.png 400x178: body band rows 62-116 (v 0.35-0.65), double head from x 190 to 394 (u 0.47-0.985)
  arrow: { src: UI + 'maparrows/blitz_pattern.png', bodyU: 0.12, bodyV: [0.35, 0.65], headU: [0.47, 0.985], headV: [0.0, 1.0],
    headAspect: 1.16, bodyRatio: 0.30, overlap: 0.14, fill: ['#4f86e0', '#e0473a'], grey: 0.55 },
  css: `
.panel{box-shadow:0 4px 14px rgba(0,0,0,.55);border-radius:0;}
/* top bar: the game's main-screen strip across the screen, date box at the right end */
.top{left:0;top:0;width:1920px;height:87px;margin-left:0;border:none;border-image:none;box-shadow:none;background:${I('topbar/background_extended.png')} left top/auto 87px no-repeat;}
.top .title{left:640px;right:360px;top:2px;font-family:var(--font-num);font-size:19px;font-weight:600;letter-spacing:1.5px;color:#e9e5d6;text-shadow:0 1px 2px #000;}
.top .title .b{color:#a9c8f4}.top .title .r{color:#f2a99c}.top .title .dot{color:#f2c84b}
.top .chip{top:30px;width:118px;height:30px;line-height:30px;font-size:17px;letter-spacing:3px;border:none;border-radius:0;box-shadow:inset 0 0 0 2px rgba(0,0,0,.55);}
.top .chip.b{left:104px;right:auto;background:linear-gradient(180deg,#5a8bd8,#2c5394);}
.top .chip.r{left:236px;right:auto;background:linear-gradient(180deg,#d4584a,#8a2b22);}
.top .clock{left:auto;right:22px;top:28px;width:300px;margin-left:0;height:40px;line-height:40px;font-size:31px;font-weight:600;color:#f4f0e0;letter-spacing:2px;
  background:${I('topbar/date_pause_button_bg.png')} 0 0/100% 100% no-repeat;text-shadow:0 1px 2px #000;}
.top .lbl{top:38px;font-size:14px;letter-spacing:2px;color:#b9b6a6;text-shadow:0 1px 2px #000;}
.top .lbl.l{left:auto;right:334px}.top .lbl.r{left:368px;right:auto;color:#f2c84b}
/* share bar: land-combat progress frame with the attacker / defender chevrons */
.share{top:94px;width:760px;margin-left:-380px;}
.share .bar{height:16px;background:#151514;border:3px solid #0d0d0c;border-radius:2px;box-shadow:0 0 0 1px #57574f,0 3px 8px rgba(0,0,0,.6);}
.share .bar .bf{background:${I('combat/landcombat_defender_progress.png')} left center/auto 100% repeat-x;}
.share .bar .rf{background:${I('combat/landcombat_attacker_progress.png')} right center/auto 100% repeat-x;}
.share .tick{top:-7px;height:30px;width:3px;background:#f2c84b;box-shadow:0 0 0 1px #000;}
.share .t{top:24px;font-family:var(--font-num);font-size:17px;font-weight:600;color:#f1eee2;}
.share .t.c{font-family:var(--font-ui);font-size:13px;color:#b9b6a6;}
/* side panel, cards, order card: riveted window frames */
.side{top:122px;left:16px;padding:6px 12px 10px;}
.side .ref{color:#a7a493;border-bottom:none;padding:6px 10px;margin:-2px -4px 0;background:${I('panel/header_bg.png')} 0 0/100% 100% no-repeat;}
.side .ref b{color:#f4f1e6;}
.side .eye{border-color:#f2c84b}.side .eye i{background:#f2c84b}
.side .hd{color:#f2c84b;font-size:16px;letter-spacing:2px;border-bottom:none;padding:4px 0 2px;background:${I('panel/divider.png')} left bottom/100% 6px no-repeat;}
.force .f .n{font-family:var(--font-num);font-size:42px;}
.vp{border-radius:0;border:2px solid #0b0b0a;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);}
.vp.o0{background:linear-gradient(180deg,#5686d2,#284a86)}.vp.o1{background:linear-gradient(180deg,#cf5547,#7f261e)}
.vp.hot{outline:3px solid #f2c84b}
.card{top:122px;right:16px;padding:6px 14px 12px;}
.card .k{color:#f2c84b;font-family:var(--font-ui);letter-spacing:2px;}
.card .ti{margin:4px -6px 0;padding:4px 10px 6px;font-size:25px;color:#f4f1e6;background:${I('panel/header_bg.png')} 0 0/100% 100% no-repeat;}
.card .row{border-top:none;background:${I('panel/divider.png')} left top/100% 5px no-repeat;padding-top:9px;}
.card .loss div{border-radius:0;position:relative;}
.card .loss .lb{background:rgba(48,86,150,.32);border:2px solid #0d0d0c;box-shadow:inset 0 0 0 1px #4d7fd0;}
.card .loss .lr{background:rgba(150,46,36,.32);border:2px solid #0d0d0c;box-shadow:inset 0 0 0 1px #cc4b3e;}
.card .loss .lb:after,.card .loss .lr:after{content:"";position:absolute;right:8px;bottom:8px;width:42px;height:32px;background:${I('combat/unitview_entry_combat_icon.png')} 0 0/84px 32px no-repeat;}
.card .loss .lb:after{background-position:-42px 0}
.card .res{border-radius:0;border:2px solid #0b0b0a;box-shadow:inset 0 1px 0 rgba(255,255,255,.2);font-family:var(--font-ui);}
.card .res.n{background:${I('button/button_type_1.png')} 0 0/100% 100% no-repeat;}
.feed{right:16px;}
.feed div{background:${I('panel/decision_item_bg.png')} 0 0/100% 100% no-repeat;border-left-width:6px;line-height:30px;}
.order{top:640px;left:16px;padding:8px 12px;}
.order .pt{border:3px solid #0b0b0a;box-shadow:0 0 0 1px #57574f;}
.banner{top:200px;}
.banner .k{background:#f2c84b;color:#14130f;border:2px solid #0b0b0a;}
.banner .ti{display:inline-block;padding:8px 70px 12px;margin-top:10px;font-size:48px;background:${I('panel/header_bg.png')} 0 0/100% 100% no-repeat;text-shadow:0 2px 4px #000;}
.banner .su{font-size:21px;color:#e9e5d6;text-shadow:0 1px 3px #000,0 0 8px #000;}
.cap span{border-radius:0;font-size:20px;color:#f1eee2;border:none;padding:10px 24px;background:${I('panel/tiled_mini_dialog.png')} 0 0/100% 100% no-repeat;box-shadow:0 3px 10px rgba(0,0,0,.5);}
.narr span{border-radius:0;background:${I('panel/tiled_mini_dialog.png')} 0 0/100% 100% no-repeat;}
.legend{left:16px;bottom:16px;}
.end .k{color:#f2c84b}.end .ti{padding:4px 12px 8px;margin:4px -8px 0;background:${I('panel/header_bg.png')} 0 0/100% 100% no-repeat;}
.end .grid .c{border-radius:0;border:2px solid #0b0b0a;background:rgba(0,0,0,.35);box-shadow:inset 0 0 0 1px #3f3f39;}
`,
};
