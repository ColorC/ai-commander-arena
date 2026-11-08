// HOI4 skin, v9: the interface frames are the game's own bitmaps again, drawn at their native pixel size. Windows use the
// game's 9-slice sprites (corneredTileSpriteType) the way the engine does: corners and edges at native size (borderSize from
// interface/*.gfx), edges and centre tiled, never the whole bitmap enlarged. Layout follows interface/topbar.gui (top strip,
// flag box at the left, resource strip, date box at the right), tiled_window panels, the land-combat progress bar and its
// chevron fills, the decision-row strip for the feed, the politics-view leader frame (pol_leader_frame) around the portraits,
// header_bg title strips, tiled_mini_dialog captions and the game's buttons. Chinese text stays in Noto Sans SC / Oswald
// (the game's bitmap fonts have no CJK glyphs).
// Bitmaps (not in the repo): $ARENA_DATA/video/hoi4-assets/ui/ (ui_extract.py) and
// $ARENA_DATA/video/assets-v9/hoi4-ui/ (README v9: extra DDS -> PNG, the 1920 px top strip cut from
// background_extended at 0-1100 + 1526-2346, the dark frame of decision_item_bg).
// asset root: ?video=<file URL> on sandbox.html (render.py passes $ARENA_DATA/video/), default <repo>/data/video/
const VIDEO = globalThis.ARENA_VIDEO || new URL('../../data/video/', import.meta.url).href;
const UI = VIDEO + 'hoi4-assets/ui/';
const V9 = VIDEO + 'assets-v9/hoi4-ui/';
const I = p => `{{img:${p}}}`;
const WIN = I(UI + 'panel/tiled_window.png');                 // 192x192, borderSize 64
const SMALL = I(V9 + 'tiled_window_small_small.png');          // 48x48, borderSize 16
const STRIP = I(UI + 'panel/header_bg.png');                   // 543x41, borderSize x 61
const MINI = I(UI + 'panel/tiled_mini_dialog.png');            // 26x26, borderSize 12
const HDR = I(UI + 'panel/tiled_header_1.png');                // 77x38, borderSize 8
export default {
  name: 'hoi4-v9',
  vars: {
    '--font-ui': '"Noto Sans SC", sans-serif',
    '--font-num': '"Oswald", "Noto Sans SC", sans-serif',
    '--text': '#dcd8cb', '--text-hi': '#f3efe2', '--muted': '#a5a290', '--accent': '#e9c35a',
    '--edge': '#0d0d0c', '--rule': '#3e3e38',
    '--panel-top': 'rgba(0,0,0,0)', '--panel-bot': 'rgba(0,0,0,0)',
    '--blue': '#4d7fd0', '--blue-dk': '#2a4f8f', '--blue-lt': '#a9c8f4',
    '--red': '#cc4b3e', '--red-dk': '#86281f', '--red-lt': '#f2a99c',
  },
  fonts: './vendor/fonts.css',
  frames: {},
  textures: {},
  engageIcon: UI + 'combat/mapicon_land_combat.png',
  arrow: { src: UI + 'maparrows/blitz_pattern.png', bodyU: 0.12, bodyV: [0.35, 0.65], headU: [0.47, 0.985], headV: [0.0, 1.0],
    headAspect: 1.16, bodyRatio: 0.30, overlap: 0.14, fill: ['#4f86e0', '#e0473a'], grey: 0.55 },
  css: `
/* windows: tiled_window 9-slice at native 64 px corners, centre tiled */
.panel{border:6px solid transparent;border-radius:0;border-image:${WIN} 64 fill / 64px round;background:none;box-shadow:0 6px 18px rgba(0,0,0,.5);}
/* top bar: the game's top strip at native size (87 px), flag box with both sides' colour blocks (blue left, red right), title in the resource strip; the intro banner sits low so the
   place names stay readable */
.top{left:0;top:0;width:1920px;height:87px;margin-left:0;border:none;border-image:none;border-radius:0;box-shadow:none;background:${I(V9 + 'topbar_1920.png')} 0 0 no-repeat;}
.top:before{content:"";position:absolute;left:9px;top:14px;width:88px;height:55px;background:linear-gradient(180deg,rgba(255,255,255,.22),rgba(255,255,255,0) 38%,rgba(0,0,0,0) 62%,rgba(0,0,0,.32)),linear-gradient(90deg,#3f74c4 0,#3f74c4 50%,#0d0d0c 50%,#0d0d0c calc(50% + 2px),#c0453b calc(50% + 2px),#c0453b 100%);box-shadow:inset 0 0 0 1px rgba(0,0,0,.6);}
.top .title{left:116px;right:auto;top:5px;width:590px;text-align:left;font-family:var(--font-ui);font-size:17px;line-height:24px;font-weight:700;letter-spacing:1px;color:#e6e2d4;text-shadow:0 1px 2px #000;white-space:nowrap;}
.top .title .b{color:#a9c8f4}.top .title .r{color:#f2a99c}.top .title .dot{color:#e9c35a;margin:0 8px}
.top .chip{top:2px;width:88px;height:32px;line-height:31px;font-size:16px;letter-spacing:3px;border:none;border-radius:0;box-shadow:none;text-shadow:0 1px 2px #000;background:${I(UI + 'button/standard_button_88.png')} 0 0 no-repeat;}
.top .chip.b{left:738px;right:auto;color:#b7d2f7}.top .chip.r{left:832px;right:auto;color:#f6b4a8}
.top .lbl{top:8px;font-size:14px;letter-spacing:2px;color:#c3c0b0;text-shadow:0 1px 2px #000;}
.top .lbl.l{left:auto;right:250px;top:9px}
.top .lbl.r{left:auto;right:300px;top:4px;width:206px;height:28px;line-height:28px;padding-left:44px;box-sizing:border-box;color:#e9c35a;font-family:var(--font-num);font-size:16px;background:${I(UI + 'topbar/date_pause_button_bg.png')} 0 0 no-repeat;}
.top .lbl.l{right:520px}
.top .clock{left:auto;right:12px;top:0;width:228px;height:42px;margin-left:0;line-height:42px;font-size:28px;font-weight:600;color:#f3efe0;letter-spacing:2px;text-align:center;background:${I(UI + 'topbar/ingameclock_bg.png')} 0 0 no-repeat;text-shadow:0 1px 2px #000;}
/* share bar: the game's tiled progress bar (190x27, 3-slice 14 px ends) with the land-combat chevron fills at native 14 px */
.share{left:104px;top:42px;width:574px;margin-left:0;height:27px;}
.share .bar{left:122px;right:122px;top:0;height:14px;border-style:solid;border-width:6px 8px 7px;border-color:transparent;border-radius:0;box-shadow:none;background:none;
  border-image:${I(UI + 'combat/tiled_progress_bar.png')} 13 14 fill / 13px 14px repeat;}
.share .bar .bf{background:${I(UI + 'combat/landcombat_defender_progress.png')} left center/auto 14px repeat-x;}
.share .bar .rf{background:${I(UI + 'combat/landcombat_attacker_progress.png')} right center/auto 14px repeat-x;}
.share .tick{top:-6px;height:26px;width:2px;background:#e9c35a;box-shadow:0 0 0 1px #000;}
.share .t{top:2px;font-family:var(--font-num);font-size:16px;font-weight:600;color:#efebdd;text-shadow:0 1px 2px #000;white-space:nowrap;}
.share .t.l{left:4px;width:116px;text-align:left}.share .t.r{right:4px;width:116px;text-align:right}.share .t.c{display:none}
/* title strips (header_bg, 61 px ends, native height 41) */
.card .ti,.step .ti{margin:4px -4px 2px;padding:0 18px;height:41px;line-height:41px;border:0;border-image:${STRIP} 0 61 fill / 0 61px round;background:none;white-space:nowrap;overflow:hidden;}
.card{top:104px;right:18px;padding:10px 14px 14px;}
.card .k,.step .k{color:#e9c35a;letter-spacing:2px;}
.card .ti{font-size:24px;color:#f3efe2;}
.card .row{border-top:none;padding-top:10px;background:${I(UI + 'panel/divider.png')} left top/100% 9px no-repeat;}
.card .loss div{border:2px solid transparent;border-radius:0;border-image:${SMALL} 16 fill / 16px round;background:none;position:relative;}
.card .loss .lb .n{color:#b7d2f7}.card .loss .lr .n{color:#f6b4a8}
.card .loss .lb:after,.card .loss .lr:after{content:"";position:absolute;right:8px;bottom:8px;width:42px;height:32px;background:${I(UI + 'combat/unitview_entry_combat_icon.png')} 0 0/84px 32px no-repeat;}
.card .loss .lb:after{background-position:-42px 0}
.card .res{height:32px;line-height:32px;padding:0 12px;font-size:18px;border:0;border-radius:0;border-image:${I(UI + 'button/button_type_1.png')} 0 16 fill / 0 16px round;background:none;color:#f3efe2;text-shadow:0 1px 2px #000;}
.card .res.b{color:#b7d2f7}.card .res.r{color:#f6b4a8}
/* force panel */
.side{top:104px;left:18px;padding:8px 14px 12px;}
.side .hd{color:#e9c35a;font-size:16px;letter-spacing:2px;border-bottom:none;padding:4px 0 8px;background:${I(UI + 'panel/divider.png')} left bottom/100% 9px no-repeat;}
.force .f .n{font-family:var(--font-num);font-size:40px;}
.vp{border:2px solid transparent;border-radius:0;border-image:${SMALL} 16 fill / 16px round;background:none;box-shadow:none;}
.vp.o0 .h{color:#b7d2f7}.vp.o1 .h{color:#f6b4a8}
.vp.hot{outline:2px solid #e9c35a}
/* kill feed: the decision-row strip (512x40 frame, 12 px ends), a side-colour tick */
.feed{right:18px;bottom:70px;width:420px;}
.feed div{position:relative;height:40px;line-height:40px;margin-top:2px;padding:0 16px 0 26px;border:0;border-image:${I(V9 + 'decision_item_dark.png')} 0 12 fill / 0 12px repeat;background:none;font-size:16px;}
.feed div:before{content:"";position:absolute;left:12px;top:11px;bottom:11px;width:4px;background:#888;}
.feed div.s0:before{background:#5d8ee0}.feed div.s1:before{background:#d85a4b}
/* commander's order: politics-view leader frame (172x258) around the 156x210 portrait, name on the frame's plate */
.order{top:auto;bottom:96px;left:18px;width:540px;padding:14px 18px 14px 16px;gap:16px;}
.order.s0,.order.s1{border-left-width:6px}
.order .pt{position:relative;width:156px;height:210px;margin:10px 8px 38px 8px;border:0;box-shadow:none;background-size:156px 210px;background-position:0 0;}
.vs .pt:after{content:"";position:absolute;left:-8px;top:-10px;width:172px;height:258px;background:${I(V9 + 'pol_leader_frame.png')} 0 0 no-repeat;}
.vs .pn{position:absolute;left:-8px;width:172px;top:213px;height:32px;line-height:32px;text-align:center;font-family:var(--font-num);font-size:16px;font-weight:600;color:#efebdd;text-shadow:0 1px 2px #000;z-index:1;}
.order .pt:after{content:"";position:absolute;left:-8px;top:-10px;width:172px;height:258px;background:${I(V9 + 'pol_leader_frame.png')} 0 0 no-repeat;}
.order .pn{position:absolute;left:-8px;width:172px;top:213px;height:32px;line-height:32px;text-align:center;font-family:var(--font-num);font-size:16px;font-weight:600;color:#efebdd;text-shadow:0 1px 2px #000;z-index:1;}
.order .q{font-size:19px;line-height:30px;}
/* orders reveal, explain */
.step,.explain{top:104px;left:18px;}
/* intro banner: header tab + tiled window */
.banner{top:auto;bottom:96px;}
.banner .ti,.banner .su{display:table !important;margin-left:auto;margin-right:auto;}
.banner .k{display:inline-block;padding:6px 22px;border:0;border-image:${HDR} 8 fill / 8px round;background:none;color:#e9c35a;letter-spacing:6px;font-size:18px;}
.banner .ti{display:inline-block;margin-top:8px;padding:12px 56px 14px;font-size:44px;color:#f3efe2;border:6px solid transparent;border-image:${WIN} 64 fill / 64px round;background:none;text-shadow:0 2px 4px #000;}
.banner .su{display:inline-block;margin-top:6px;padding:6px 20px;font-size:20px;color:#ece8da;border:0;border-image:${MINI} 12 fill / 12px round;background:none;text-shadow:0 1px 2px #000;}
/* captions and narration: tiled_mini_dialog 9-slice (12 px) */
.cap{bottom:36px;}
.cap span,.narr span{border:0;border-radius:0;border-image:${MINI} 12 fill / 12px round;background:none;box-shadow:0 3px 10px rgba(0,0,0,.5);padding:10px 26px;font-size:21px;color:#f1eee2;}
/* map labels: tiled_mini_dialog too, side tick on the arrow labels */
.alab,.vlab{border:0;border-radius:0;border-image:${MINI} 12 fill / 12px round;background:none;box-shadow:0 2px 6px rgba(0,0,0,.5);padding:4px 12px 5px;}
.alab{padding-left:18px;}
.alab:before{content:"";position:absolute;left:7px;top:6px;bottom:6px;width:4px;background:#888;}
.alab.s0:before{background:#5d8ee0}.alab.s1:before{background:#d85a4b}
.alab.cur{outline:none;box-shadow:0 0 0 2px #e9c35a,0 2px 6px rgba(0,0,0,.5);}
/* unit names beside the tiny counters (shot.chipNames): light text on the dark mini-dialog tile, side stripe */
.ulab{position:absolute;transform:translate(-50%,-50%);white-space:nowrap;font-weight:700;line-height:1;color:#f3efe2;text-shadow:0 1px 2px #000;
  border:0;border-radius:0;border-image:${MINI} 12 fill / 8px round;background:none;padding:4px 9px 5px 14px;box-shadow:0 2px 5px rgba(0,0,0,.45);}
.ulab:before{content:"";position:absolute;left:5px;top:5px;bottom:5px;width:3px;background:#888}
.ulab.s0:before{background:#5d8ee0}.ulab.s1:before{background:#d85a4b}
.ulab .st{margin-left:6px;font-family:var(--font-num);font-weight:600;color:#e7b77f}
.vlab .vc.s0{color:#9cc3f2}.vlab .vc.s1{color:#f2a497}
/* objective label with its owner (shot.vpCapture): side stripe like the arrow labels; on a capture it flashes once (--fl 1 -> 0) */
.vlab.o0,.vlab.o1,.vlab.o2{padding-left:18px}
.vlab.o0:before,.vlab.o1:before,.vlab.o2:before{content:"";position:absolute;left:7px;top:6px;bottom:6px;width:4px;background:#d8d2c4}
.vlab.o0:before{background:#5d8ee0}.vlab.o1:before{background:#d85a4b}
.vlab.fl.o0{box-shadow:0 0 calc(var(--fl) * 22px) calc(var(--fl) * 4px) rgba(120,170,255,.85),0 2px 6px rgba(0,0,0,.5)}
.vlab.fl.o1{box-shadow:0 0 calc(var(--fl) * 22px) calc(var(--fl) * 4px) rgba(255,130,110,.85),0 2px 6px rgba(0,0,0,.5)}
/* staging (.stg2): real flags in the flag box (blue on top, red under it); the subtitle low and wide with the speaker in
   its own full-height plate beside it (header tab); commander cards and the kill feed stand above the subtitle; an order card
   opens with the header strip and the HOI4 order icon */
.top.fl:before{display:none}
.top .flags{position:absolute;left:9px;top:14px;width:88px;height:55px;display:flex;flex-direction:column;gap:2px;background:#0d0d0c;}
.top .flags i{flex:1;background:center/contain no-repeat;}
.stg2 .order{bottom:176px}
.stg2 .feed{bottom:176px}
.narr2{position:absolute;left:50%;transform:translateX(-50%);bottom:30px;display:flex;align-items:stretch;gap:6px;width:max-content;max-width:1680px;}
.narr2 .sp{flex:none;display:flex;align-items:center;justify-content:center;min-width:92px;padding:0 16px;border:0;border-image:${HDR} 8 fill / 8px round;font-size:22px;font-weight:700;color:#e9c35a;letter-spacing:1px;text-shadow:0 1px 2px #000;box-shadow:0 3px 10px rgba(0,0,0,.5);}
.narr2 .tx{flex:0 1 auto;border:0;border-image:${MINI} 12 fill / 12px round;padding:9px 26px 10px;font-size:25px;line-height:37px;font-weight:700;color:#f3efe2;text-shadow:0 1px 2px #000;box-shadow:0 3px 10px rgba(0,0,0,.5);}
.order .oh{display:flex;align-items:center;gap:6px;height:41px;margin:-2px -4px 6px;padding:0 18px 0 4px;border:0;border-image:${STRIP} 0 61 fill / 0 61px round;color:#e9c35a;font-size:18px;font-weight:700;letter-spacing:3px;}
.order .oh i{flex:none;width:35px;height:34px;background:${I(VIDEO + 'assets-staging/hoi4/order_draw_offence_btn.png')} 0 0 no-repeat;}
.order.ord .q:before,.order.ord .q:after{content:none}
.order.ord .q{color:#f3efe2}
/* end card */
.end .k{color:#e9c35a}
.end .grid .c{border:2px solid transparent;border-radius:0;border-image:${SMALL} 16 fill / 16px round;background:none;}
.attr{bottom:8px;font-size:12px;line-height:16px;color:rgba(239,230,207,.6);}
/* 11-08 phone pass ($ARENA_DATA/video/research/ui-scale-benchmark): viewed on a phone held sideways the
   1920 frame is ~410 pt wide, so the programme HUD is drawn 1.5x: the top strip and share bar scale together around the
   top-left corner (the strip's long thin bar runs off the right edge under the clock box, which stays at the right edge);
   the kill feed 1.5x and the commander card 1.3x, both anchored to their bottom corner above the subtitle; the subtitle
   text 52 px (was 25) on one row (sandbox.js cuts long lines into chunks) */
.stg2 .top{width:1280px;transform:scale(1.5);transform-origin:0 0;}
/* the scaled title would run into the "游戏时间" caption on long names (T2): the caption goes (the replay box and clock stay), the title
   gets the room up to the replay box and an ellipsis past it */
.stg2 .top .lbl.l{display:none}
.stg2 .top .title{width:640px;overflow:hidden;text-overflow:ellipsis}
.stg2 .share{left:156px;top:63px;transform:scale(1.5);transform-origin:0 0;}
.stg2 .feed{width:360px;bottom:196px;transform:scale(1.5);transform-origin:100% 100%;}
.stg2 .feed div{padding:0 12px 0 22px;}
.stg2 .order{bottom:196px;transform:scale(1.3);transform-origin:0 100%;}
.narr2{bottom:26px;max-width:1800px;gap:8px;}
.narr2 .sp{min-width:132px;padding:0 22px;font-size:38px;}
.narr2 .tx{padding:11px 32px 12px;font-size:52px;line-height:68px;}
`,
};
