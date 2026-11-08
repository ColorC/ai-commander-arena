// HOI4 skin for the programme opening (shot-opening.js): the v9 skin plus
// - person cards (as narrow as the name / role allow, centred on x; `small` = scaled to 0.8 around the bottom edge (11-06: 0.62 read too small)),
//   with an oscilloscope under the role plate (shot.scope) that swings while the card's voice speaks:
//   tiled_window panel, the portrait (156x210) in the politics-view leader frame (pol_leader_frame, native
//   172x258) with the name plate under it, name and role on header_bg strips beside it (same build as the v9 order card);
// - the title card: the diplomacy pop-up header plate (diplo_popup_header_bg_large, 584x87 at native size) on a
//   tiled_window panel, the programme name under it;
// - subtitles (.narr): one line per sentence, speaker tag on the left. Style and position live only here (the
//   --sub-* variables and the .narr block), so a later voiced version can swap them without touching the shot.
// Bitmaps (not in the repo): $ARENA_DATA/video/assets-v9/hoi4-ui/ (README "节目开场": DDS -> PNG).
import v9 from './theme-hoi4-v9.js';
// asset root: ?video=<file URL> on sandbox.html (render.py passes $ARENA_DATA/video/), default <repo>/data/video/
const VIDEO = globalThis.ARENA_VIDEO || new URL('../../data/video/', import.meta.url).href;

const UI = VIDEO + 'hoi4-assets/ui/';
const V9 = VIDEO + 'assets-v9/hoi4-ui/';
const I = p => `{{img:${p}}}`;
const STRIP = I(UI + 'panel/header_bg.png');                   // 543x41, borderSize x 61
const PLATE = I(V9 + 'diplo_popup_header_bg_large.png');       // 584x87, native size
const MINI = I(UI + 'panel/tiled_mini_dialog.png');            // 26x26, borderSize 12

export default {
  ...v9,
  name: 'hoi4-opening',
  vars: { ...v9.vars, '--sub-bottom': '40px', '--sub-width': '1760px', '--sub-size': '52px' },   // 11-08 phone pass: 30 -> 52 px, wider
  css: v9.css + `
/* no game HUD in the opening: no top strip, share bar, force panel, feed or legend */
.nohud .top,.nohud .share,.nohud .side,.nohud .feed,.nohud .legend{display:none}
.dim{position:absolute;left:0;top:0;width:1920px;height:1080px;background:#000;z-index:4}
/* person cards */
.pc{position:absolute;height:298px;box-sizing:border-box;padding:0 44px 0 226px;z-index:5;transform:translateX(-50%);white-space:nowrap}
.pc.sm{transform:translateX(-50%) translateY(58px) scale(.8);transform-origin:50% 100%}   /* 11-08: its bottom just above the larger subtitle, clear of the (1.25x) boards */
.pc .pt{position:absolute;left:30px;top:26px;width:156px;height:210px;background-size:156px 210px;background-repeat:no-repeat}
.pc .pt:after{content:"";position:absolute;left:-8px;top:-10px;width:172px;height:258px;background:${I(V9 + 'pol_leader_frame.png')} 0 0 no-repeat}
.pc .pn{position:absolute;left:-8px;width:172px;top:213px;height:32px;line-height:32px;text-align:center;font-family:var(--font-num);font-size:21px;font-weight:600;color:#efebdd;text-shadow:0 1px 2px #000;z-index:1}
.pc .pb{padding-top:58px}
.pc .nm{font-size:46px;line-height:56px;font-weight:900;color:#f3efe2;font-family:var(--font-num);letter-spacing:1px;text-shadow:0 2px 4px #000}
.pc .rl{display:inline-block;margin-top:16px;height:41px;line-height:41px;padding:0 30px;border:0 solid transparent;border-width:0 30px;border-image:${STRIP} 0 61 fill / 0 30px stretch;font-size:27px;font-weight:700;color:#e9c35a;white-space:nowrap}
.pc .nt{margin-top:18px;font-size:24px;line-height:34px;color:#cfcab8}
/* the speaker is shown by the oscilloscope only (no outline around the card) */
/* oscilloscope under the role plate (shot.scope): dark phosphor glass, graticule, bezel; the trace colour is the voice's */
.pc .scope{position:relative;margin-top:14px;width:236px;height:54px;border-radius:7px;overflow:hidden;
  background:radial-gradient(ellipse at 50% 45%,#13261c 0%,#0a150f 62%,#050806 100%);
  box-shadow:inset 0 0 14px rgba(0,0,0,.95),0 0 0 2px #2c2b25,0 0 0 3px #090908,0 2px 4px rgba(0,0,0,.6)}
.pc .scope svg{position:absolute;left:0;top:0}
.pc .scope:after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,transparent 1px 3px)}
/* boards (shot.boards): game-window panel with a header strip; code blocks in a monospace face; timeline, flow, figures */
.board{position:absolute;z-index:5;box-sizing:border-box;padding:22px 30px 26px;color:#ece6d3;font-size:24px;line-height:1.55}
/* highlight frame on a full-screen still (board cls "mark"): just an outline + optional label, no window art */
.board.mark{padding:0;background:none!important;border:3px solid #f2c94c!important;border-image:none!important;border-radius:4px;box-shadow:0 0 0 2px rgba(0,0,0,.55),0 0 22px rgba(242,201,76,.55)!important}
.board.mark.r .lb{left:auto;right:-3px}
.board.mark .lb{position:absolute;left:-3px;top:-52px;background:#f2c94c;color:#1b1a14;font-size:30px;font-weight:800;line-height:1.5;padding:2px 12px;white-space:nowrap}
.board.osmc{border:none!important;box-shadow:none!important}
.board .osm{font-size:13px;line-height:20px;color:#333;background:rgba(255,255,255,.72);padding:0 6px;text-align:right}
.board .bt{display:inline-block;height:41px;line-height:41px;padding:0 30px;border:0 solid transparent;border-width:0 30px;border-image:${STRIP} 0 61 fill / 0 30px stretch;font-size:22px;font-weight:800;color:#e9c35a;margin:0 0 14px -6px}
.board .note{font-size:18px;color:#a9a48f;margin-top:10px}
.board .code{font-family:"JetBrains Mono","Noto Sans SC",monospace;font-size:19px;line-height:1.5;background:rgba(5,8,6,.94);color:#d3e8cb;padding:14px 18px;border:1px solid #34332c;box-shadow:inset 0 0 12px rgba(0,0,0,.8);white-space:pre;overflow:hidden}
.board .code .p{color:#e9c35a}.board .code .c{color:#7d8f78}.board .code .k{color:#8fc0ff}
.board table.t{border-collapse:collapse;font-size:20px;width:100%}
.board table.t th{color:#e9c35a;font-weight:700;text-align:left;padding:4px 10px;border-bottom:1px solid #4a4a42}
.board table.t td{padding:5px 10px;border-bottom:1px solid #2c2b25;font-family:"JetBrains Mono","Noto Sans SC",monospace;font-size:19px}
.board table.t td.no{color:#7d7a6c}
.board .row{display:flex;align-items:center;margin:10px 0}
.board .row .lab{width:150px;flex:none;font-weight:700}
.board .row .bar{flex:1;display:flex;height:30px;background:#14130f;border:1px solid #3a3a33}
.board .row .seg{height:100%;box-sizing:border-box;border-right:3px solid #0b0b0a;display:flex;align-items:center;justify-content:center;font-size:16px;color:#101010;font-weight:700}
.board .row .seg.b{background:#7fa6e6}.board .row .seg.r{background:#e58c7e}.board .row .seg.d{background:repeating-linear-gradient(45deg,#e9c35a 0 8px,#b8962f 8px 16px)}.board .row .seg.x{background:#3b3a33;color:#cfc9b4}
.board .flow{display:flex;align-items:center;gap:12px;flex-wrap:nowrap}
.board .flow .st{padding:12px 18px;border:2px solid #6b675a;background:rgba(20,19,15,.9);font-weight:700;white-space:nowrap}
.board .flow .st.on{border-color:#e9c35a;color:#f3e2a8}
.board .flow .ar{color:#e9c35a;font-size:28px}
.board .fx{font-size:30px;font-weight:800;text-align:center;margin:8px 0 6px}.board .fx b{color:#e9c35a}
.board .eqs{display:flex;gap:18px}
.board .eqs figure{margin:0;flex:1;background:#0b0b0a;border:1px solid #3a3a33}
.board .eqs img{display:block;width:100%;height:220px;object-fit:cover}
.board .eqs figcaption{padding:8px 12px;font-size:20px;font-weight:700}.board .eqs figcaption span{display:block;font-size:16px;font-weight:500;color:#a9a48f}
.board .hdr{display:block;margin:0 auto;width:100%}
/* title card */
.tc{position:absolute;left:50%;top:372px;width:960px;margin-left:-480px;padding:30px 0 38px;text-align:center;z-index:5}
.tc .k{display:block;width:584px;height:87px;margin:0 auto;line-height:87px;background:${PLATE} 0 0 no-repeat;font-family:var(--font-num);font-size:28px;font-weight:700;letter-spacing:5px;color:#e9c35a;text-shadow:0 2px 3px #000}
.tc .ti{margin-top:18px;font-size:84px;line-height:110px;font-weight:900;letter-spacing:12px;padding-left:12px;color:#f3efe2;text-shadow:0 3px 6px #000;white-space:nowrap}
.tc .su{margin-top:12px;font-family:var(--font-num);font-size:30px;letter-spacing:6px;color:#b5b2a0}
/* subtitles: one sentence, speaker on the left */
.narr{bottom:var(--sub-bottom);width:var(--sub-width);margin-left:calc(var(--sub-width) / -2);z-index:6}
.narr span{font-size:var(--sub-size);line-height:68px;font-weight:700;color:#f6f2e4;border:0;border-radius:0;border-image:${MINI} 12 fill / 12px round;background:none;padding:12px 32px;box-shadow:0 3px 10px rgba(0,0,0,.5)}
.narr span .sp{display:inline-block;margin-right:18px;padding:0 14px;border:0;border-image:none;box-shadow:none;background:#e9c35a;color:#1b1a16;font-size:36px;line-height:54px;vertical-align:5px}
`,
};
