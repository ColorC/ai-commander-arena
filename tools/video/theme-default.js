// UI theme ("skin") for the sandbox overlay. Everything the HUD looks like that is not layout lives here, so a different
// look (e.g. a HOI4-style skin supplied later as textures) is a new theme file, not code changes:
//   vars     CSS custom properties used by the overlay CSS (colours, panel gradient, fonts)
//   fonts    @font-face stylesheet (inlined into the SVG foreignObject; fonts must be data: URLs, see fetch_fonts.py)
//   frames   9-slice frame images per element class -> CSS border-image; { src, slice, width, repeat, fill }
//            src: path or file:// URL (read at init and inlined as a data: URL)
//   textures background images per element class -> CSS background; { src, size, repeat, blend }
//   css      extra CSS appended last (overrides anything)
// Pick a theme with  SHOT.theme = './theme-xxx.js'  or  render.py --theme <file>.
export default {
  name: 'default',
  vars: {
    '--font-ui': '"Noto Sans SC", sans-serif',
    '--font-num': '"Oswald", "Noto Sans SC", sans-serif',
    '--text': '#efe6cf',
    '--text-hi': '#f3e6c2',
    '--muted': '#bfae86',
    '--accent': '#e8c14d',
    '--edge': '#6d5a36',
    '--rule': '#4d4130',
    '--panel-top': 'rgba(42,40,33,.93)',
    '--panel-bot': 'rgba(25,24,20,.93)',
    '--blue': '#4f86d6', '--blue-dk': '#2b5598', '--blue-lt': '#9cc3f2',
    '--red': '#d65a4c', '--red-dk': '#8f2f27', '--red-lt': '#f2a497',
  },
  fonts: './vendor/fonts.css',
  frames: {
    // panel: { src: '$ARENA_DATA/video/skins/hoi4/panel_frame.png', slice: 24, width: 12, repeat: 'stretch', fill: true },
    // order: { ... }, card: { ... }, cap: { ... }
  },
  textures: {
    // panel: { src: '$ARENA_DATA/video/skins/hoi4/panel_bg.png', size: 'auto', repeat: 'repeat' },
  },
  css: '',
};
