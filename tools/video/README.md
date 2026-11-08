# 模拟沙盘与演出管线（tools/video）

three.js 网页沙盘 + 确定性逐帧出片，外加把对局做成解说节目的整条演出链：分段节拍表、镜头、台词、配音、混音、背景音乐、章节、预览页。

所有素材和产物都在 `$ARENA_DATA/video/` 下（默认 `<仓库>/data/video/`，见仓库 README「数据目录」），本目录只有代码。

## 一条链怎么走

```
录制 rec.jsonl + 裁判日志 ──tools/s2_battle_maps.py──▶ 对局 JSON（单位轨迹、事件、目标格易手、份额）
地形 TSV ───────────────tools/s2_battle_maps.py --terrain──▶ 地形 JSON（格网、道路、河流、VP 格）
GLO-30 DEM ─────────────dem_to_heightmap.mjs（geo_align.mjs 先找锚点）──▶ heightmap.bin/.json
                         build_data.py ──▶ data-<局>.js（window.ARENA = {terrain, game, hm}）
节拍表 segments/*.json ──make_segment.py──▶ shot-*.js（镜头表）+ 时间表 md + shot-*.voice.json（台词轨）
                         render.py（Playwright + 无头 Chromium，逐帧 renderFrame/encodeFrame，WebCodecs 编码）──▶ mp4 + 关键帧 PNG
台词 ──voice/synth_lines.py（Qwen3-TTS 克隆）→ voice/asr_check.py（Whisper 回听校对）→ voice/speed_lines.py（去长停顿、提速）
       ──voice/mix.py（按字幕起点放人声、统一响度）──voice/assemble.py（按节目顺序拼接、章节）──voice/music.py（铺曲、人声压低）
       ──staging/build_preview.py（集中预览页）
```

节目解说的「演出规范」（台词说到哪、画面演到哪）见 `staging/STANDARD.md`：每场一份 staging JSON → `staging/staging_check.py` 校验 → `staging/compile_staging.py` 编成节拍表与镜头表 → `staging/render_all.py` 出关键帧 / 分段视频 → `staging/build_review.py` 逐镜头审阅页。

## 第一次跑

```
python -I -X utf8 tools/video/fetch_vendor.py                 # three.js / milsymbol / mp4-muxer / GSAP（固定版本）
python -I -X utf8 tools/video/fetch_fonts.py tools/video/sandbox.js tools/video/theme-*.js <你的 shot / 台词文件> tools/video/vendor/fonts.css
python -I -m pip install playwright && python -I -m playwright install chromium
cd tools/video
python -I -X utf8 make_segment.py examples/segment-example.json       # 两拍示例 -> examples/shot-example.js + 时间表
python -I -X utf8 render.py --data <data-*.js> --shot examples/shot-example.js --out-dir <输出目录> --stills-only
```

`examples/segment-example.json` 和 `examples/shot-example-base.js` 是自己写的格式示例，不含游戏数据；渲染需要一份由你自己的对局生成的 `data-*.js`。

## 文件

| 文件 | 作用 |
|---|---|
| `sandbox.html` / `sandbox.js` | 渲染器页面与主体：真实 DEM 地形网格、地表贴图、台面、树林城镇实例化、VP 标记与易手特效、兵棋棋子与单位牌、尾迹与命令箭头、开火 / 命中 / 击毁特效、近景模型、相机、移轴后处理、HTML/CSS 叠加层（时钟、份额条、兵力曲线、战报卡、人物卡、字幕、示波器）、WebCodecs 编码。页面参数 `?data=<data.js>&video=<素材根>` |
| `fpc-symbology.js` | 可选图层：用 Flashpoint 原版的地图符号（棋子、接触、路径、标记、火力线、面板）画在沙盘上；素材从你自己的游戏安装转出 |
| `theme-default.js`、`theme-hoi4*.js` | 界面皮肤：颜色、字体、九宫格边框、底纹、`{{img:...}}` 内联位图（HOI4 皮肤的位图由 `hoi4/ui_extract.py` 转出） |
| `models.js`、`models-hoi4.js` | 模型套件：加载、归一化、拆件、按阵营调色、骨骼动画采样（`loadKit` / `makeVehicle` / `makeSoldier`） |
| `hoi4/pdx2glb.py`、`hoi4/ui_extract.py`、`hoi4/preview.*` | Paradox `.mesh/.anim/.dds` → glb；HOI4 界面 DDS → PNG；glb 对照表 |
| `build_data.py`、`dem_to_heightmap.mjs`、`geo_align.mjs` | 数据打包；DEM → 高度图；找六角格地图在真实地形上的位置（逐格高程相关） |
| `make_segment.py` | 节拍表 → 镜头表模块 + 时间表 md + 台词轨（文件头有全部字段说明） |
| `render.py` | Playwright 驱动逐帧渲染与编码，记录并核对浏览器 PID；`--stills-only` 只出关键帧，`--range` 出片段 |
| `voice_cache.py`、`voice/` | 配音缓存与整条声音链（选角、合成、回听、提速、混音、拼接、音乐） |
| `staging/` | 演出规范、校验、编译、批量出图、审阅页、指挥官原话卡（让说话的模型自己用中文复述）、集中预览页 |
| `raw_footage/` | 游戏原版回放画面（`fpc/remote/job-vcr-capture.ps1` 截的帧）拼成快进片段，旁边合成脱敏后的指挥官控制台文字轨；片段表在 `$ARENA_DATA/video/program/raw-footage/clips.json`（格式见 `examples/raw-footage-clips.example.json`） |
| `transcript_turns.py`、`hotspot.py`、`make_site.py`、`fpc_symbology_data.py`、`fpc_fx_atlas.py`、`fpc_off_check.py` | 转录按回合重组、热点格查找、单段审阅页、原版符号层侧数据、特效图集、关闭图层时像素不变的检查 |
| `vendor/savepoint/` | RPG 存档点特效（ics-creative 原版 MIT，改写说明见文件头） |

## 做法要点

- 确定性：每帧是帧号的纯函数（游戏时间 = 镜头表 `time` 的分段线性映射），不用 `requestAnimationFrame`、不用实时钟；GSAP 动画摘掉自身时钟，每帧 `gsap.updateRoot(视频秒)`。需要逐帧平滑的东西依赖顺序渲染，出片总是从第 0 帧顺序跑。
- 叠加层用 HTML/CSS 写，每帧经 SVG `foreignObject` 栅格化（字体内嵌为 data: URL），和 WebGL 画面合成到一块 2D 画布，再交给 `VideoEncoder`（H.264 High，硬件优先），mp4-muxer 封装。不截图、不用 ffmpeg。
- 交火：录像只记“谁在哪格打了几发”和“谁在哪格掉了几个子单位”，没有射击目标；渲染时把同一分钟的损失配给最近的开火方，配不上的按炮击散布画。
- 字幕和卡片里的三四位数会被当成格号改写成 (列,行)；比分之类写成 `1,711`、`102.0`。
