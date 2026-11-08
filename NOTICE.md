# 第三方代码与素材说明（NOTICE）

本仓自写代码按 MIT 许可（见 `LICENSE`，版权人 ColorC）。下面列出本仓用到、但不属于本仓的东西：哪些随仓分发、哪些只写来源。

## 随仓分发的第三方代码

| 位置 | 来源 | 许可 |
|---|---|---|
| `tools/video/vendor/savepoint/savepoint.js` | 按 ics-creative/160304_threejs_save_point（https://github.com/ics-creative/160304_threejs_save_point ，2026-10 版 `src/`）的 TypeScript 源码逐行改写成 three.js r170 WebGL 版（改动见文件头） | MIT，原版权声明见同目录 `LICENSE` |

存档点特效用到的贴图（光柱、魔法阵等 PNG）不随仓分发，从上面仓库的 `src/img/` 取，放到 `$ARENA_DATA/video/assets-v9/savepoint/`（或镜头表 `vpCapture.base` 指定的目录）。

## 不随仓分发、运行时下载的第三方库

`python -I -X utf8 tools/video/fetch_vendor.py` 从 jsDelivr 下载到 `tools/video/vendor/`（`.gitignore` 已排除）：

| 库 | 版本 | 许可 |
|---|---|---|
| three.js（`build/three.module.js`，`examples/jsm` 下 GLTFLoader、SkeletonUtils、BufferGeometryUtils） | 0.170.0 | MIT |
| milsymbol | 2.2.0 | MIT |
| mp4-muxer | 5.1.3 | MIT |
| GSAP | 3.15.0 | GreenSock Standard "No Charge" License（https://gsap.com/standard-license） |

`tools/video/fetch_fonts.py` 从 Google Fonts 生成 `vendor/fonts.css`（Noto Sans SC、Oswald、JetBrains Mono 的子集，均为 SIL OFL 1.1）。

Python / Node 依赖按各自许可自行安装：Playwright、numpy、soundfile、Pillow、PyMuPDF（AGPL，仅 `arena/library/fetch.py` 用）、markdown、
rapidocr-onnxruntime、faster-whisper、librosa、torch、qwen-tts（Qwen3-TTS 模型权重按其模型许可另行下载）、npm `geotiff@2.1.3`。

## 只写来源、不附带文件的素材

下列素材本仓一律不附带，路径约定见 README「数据目录」。使用前请自行确认各自的授权条款。

- **Flashpoint Campaigns: Cold War**（游戏本体、想定、地形、兵力、手册、界面位图、单位图标、特效动画、字体）：从你自己合法持有的游戏安装里读取或转换。本仓只含与游戏交互的脚本，不含任何游戏文件或由其导出的数据。
- **Hearts of Iron IV**（Paradox Interactive）界面位图、单位牌、模型与动画：`tools/video/hoi4/ui_extract.py`、`hoi4/pdx2glb.py` 从你自己的游戏安装转换。
- **HOI4 模组**（Steam 创意工坊）：Millennium Dawn: A Modern Day Mod（2777392649）、Ultimate Tech Tree & Expanded CN（3156943624）等的冷战模型，订阅后用 `hoi4/pdx2glb.py` 转换；模组内容的权利归各模组作者。
- **地表与特效**：Poly Haven 地表贴图（CC0）、Kenney 素材包（Minigolf Kit、Smoke Particles、Particle Pack，CC0）、Sinestesia「Free Explosion Animations」（CC0）；从各自官网下载。
- **地形高程**：Copernicus GLO-30 DEM（© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA），从 Copernicus / AWS Open Data 下载。
- **背景音乐**：节目用的是《蔚蓝档案》（Blue Archive）原声，版权归其权利人，本仓不附带任何音频；`tools/video/voice/music.py` 只读你自己放进 `$ARENA_DATA/video/music/tracks/` 的曲目。
- **立绘、封面、人物卡图片**：节目里的指挥官 / 解说立绘是 AIGC 生成的二创图，不随仓分发。
- **模型输出与配音**：对局转录、模型发言、合成配音都属于产物，不随仓分发。

## 商标

Flashpoint Campaigns、Hearts of Iron、Blue Archive、Kimi、DeepSeek、Claude、GPT 等名称归各自权利人所有。本项目与这些游戏的发行商、模型提供方均无关联。
