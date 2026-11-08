# AI 陆战军事演习（ai-commander-arena）

让两个大模型当指挥官，在兵棋 **Flashpoint Campaigns: Cold War** 里打陆地作战演习的一次尝试：**DeepSeek** 和 **Kimi** 各指挥一方，通过工具读战场、下命令、推进回合；赛后用自制的 3D 沙盘把对局重演成解说节目，由 **Claude** 和 **GPT** 担任解说（Claude 另兼裁判一角）。

本仓公开两块代码：

1. **指挥设施**：裁判、模型代理 / 官方 CLI 驱动、Lua 沙盒、游戏内 Lua 录制器与桥接、启动与推进游戏的作业脚本，以及对局、计分、复盘工具。
2. **模拟沙盘与演出管线**：three.js 沙盘回放、确定性逐帧出片，以及把对局做成解说节目的分段、镜头、台词、配音、混音、背景音乐、章节、预览页。

> English summary: an experiment in letting two LLMs (DeepSeek and Kimi) command opposing sides of a land battle in *Flashpoint Campaigns: Cold War* through a referee that exposes the battlefield as tools, plus a deterministic three.js sand-table renderer and a production pipeline (beats, cameras, lines, TTS, mixing, music, chapters) that turns the recorded games into a commentated show (Claude and GPT as commentators). Code only: no game files, scenario data, recordings, model outputs, voices, music or other assets are included. MIT licensed.

## 结构

```
游戏机（Windows）                                      裁判机（可以是同一台）
┌────────────────────────────────────┐   HTTP     ┌──────────────────────────────────────────────┐
│ FlashpointCampaigns.exe             │  /relay/*  │ arena/referee   局面状态机、迷雾视图、事件与唤醒、 │
│  ├ fpc/lua/recorder.lua  录制 rec.jsonl│◀────────▶│                 命令校验与放行、稽核、计分、日志   │
│  ├ fpc/lua/arena-bridge.lua 态势/闸/放行│           │      ▲ /agent/*（每方一个 token）              │
│  └ lua\arena.dll  列窗口、投递 F9      │           │      │                                        │
│ fpc/remote/relay.ps1  起游戏、同步、F9、│           │ arena/agent   OpenAI 兼容接口直连模型           │
│   点对话框、终局截图、按 PID 收尾       │           │ arena/harness 官方 CLI（Kimi Code / opencode）  │
└────────────────────────────────────┘           │               经 ACP 驱动，裁判工具经 MCP 暴露     │
        ▲ tools/gamehost.py（或 ARENA_GAMEHOST）       │ arena/sandbox 模型写的反射脚本 / 计算任务的       │
        │ push / spawn / wait / pull / ps           │               Lua 5.4 沙盒（限指令、内存、时间）   │
        └──────── tools/*.sh、tools/*_director.py ──┴──────────────────────────────────────────────┘
```

模型只拿到一个工具 `arena`（命令行式：`arena unit list`、`arena order add 1/A move 2118`、`arena turn end` …，解析在 `arena/agent/cli.py`），看到的是迷雾下的本方视角；裁判按游戏自己的下令周期唤醒它，在本方下令阶段把排队的命令转成 Lua 数据字面量放行给游戏，并逐条核对游戏日志。完整的协议、端点、规则见 [`arena/DESIGN.md`](arena/DESIGN.md)。

### 指挥设施

| 目录 | 内容 |
|---|---|
| `arena/referee/` | 裁判：`core.py` 状态机，`world.py` 想定与态势，`views.py` 迷雾视图与迷雾断言，`events.py` / `wake.py` 事件与唤醒，`orders.py` 命令队列与校验，`audit.py` 稽核，`scripts.py` 反射脚本，`fires.py` 炮击回报，`scoring.py` 计分视图，`training.py` 训练期裁判，`server.py` HTTP，`rules.py` 全部数值 |
| `arena/agent/` | 直连模型的代理（`arena_agent.py`）、`arena` 命令行（`cli.py`）、模型路线（`routes.py`）、系统提示（`prompts/`） |
| `arena/harness/` | 官方 CLI 驱动：ACP 客户端、Kimi Code / opencode 的锁定配置（内置工具全关，只留裁判 MCP 工具）、MCP 服务、玩家持久会话、开局队列与体检、记分卡 |
| `arena/sandbox/` | Lua 沙盒宿主（`host.lua`）与 Windows 作业对象限额的运行器（`runner.py`） |
| `arena/library/` | 发给两方的基础文档 `basics.md`；`fetch.py` 从你自己的游戏 `Documents` 目录把手册 PDF 转成文本（不进 Git） |
| `arena/tests/` | 单元与离线整局测试（假游戏、假 relay、假 ACP CLI） |
| `fpc/` | 游戏侧接入件，见 [`fpc/README.md`](fpc/README.md) |
| `tools/` | 对局：`p2_smoke.sh`、`p3_rehearsal.sh`、`p5_match.sh`、`p4_training.py`（训练期导演）、`s2_director.py`（赛季导演：队列、体检、重开、停表）、`s2_calibrate.sh` / `p1_calibrate.sh`（电脑对电脑定标）、`gamehost.py`（游戏机适配器）；计分与复盘：`p6_score.py`、`s2_match_score.py`、`fpc_endscreen.py`（终局页 OCR）、`p6_review_*.py`、`s2_battle_report.py`、`s2_battle_maps.py/.js`、`program_facts.py`、`rec_summary.py`、`fog_check.py`；想定普查：`fpc_briefs.py`、`scenario_screen.py`、`s2_scenario_data.py`、`s2_vp_table.py`、`fpc_map.py`、`fpc_objectives.py`、`fpc_vp_locations.py`；模型探针：`model_probe.py`、`s2_session_probe.py`、`harness_check.py` |

### 模拟沙盘与演出管线

`tools/video/`，见 [`tools/video/README.md`](tools/video/README.md)：数据打包（地形、对局、DEM 高度图）→ 节拍表 → 镜头表 → 无头 Chromium 逐帧渲染与 WebCodecs 编码 → 配音（Qwen3-TTS 克隆、Whisper 回听校对、提速）→ 混音 → 按节目顺序拼接与章节 → 背景音乐铺设与人声压低 → 预览页 / 审阅页。

### 内置 skill

`.claude/skills/` 下两份可照做的步骤说明（Claude Code 会自动发现）：

- [`wargame-ai-commander-arena`](.claude/skills/wargame-ai-commander-arena/SKILL.md)：为一款兵棋 / 策略游戏建立 AI 指挥官对抗设施和 3D 沙盘；
- [`battle-replay-show`](.claude/skills/battle-replay-show/SKILL.md)：把对局做成解说演出（分段、镜头、台词、配音、混音、章节、出片）。

## 运行前提

- **游戏**：Flashpoint Campaigns: Cold War（Windows）。需要游戏本身提供的 Lua / Telnet 接口，请按发行商条款使用对应版本。本仓不含任何游戏文件。
- **Windows + PowerShell 5.1 或更新**：游戏侧作业脚本、Lua 沙盒的作业对象限额。
- **MinGW-w64 gcc** + 官方 Lua 5.4.3 源码：`sh fpc/native/build.sh <lua-5.4.3 目录> <输出目录>`，产出 `lua54_64.dll`、`lua\arena.dll`、`lua.exe`（`lua.exe` 放到 `arena/sandbox/bin/` 或用 `ARENA_LUA` 指定）。
- **Python 3.11+**（脚本都按 `python -I -X utf8 ...` 写）；按用到的部分装：`playwright`（渲染）、`numpy`、`soundfile`、`Pillow`、`PyMuPDF`（手册转文本）、`markdown`、`rapidocr-onnxruntime`（终局页 OCR）、`faster-whisper`（配音回听）、`torch` + `qwen-tts`（配音合成，模型权重另下）、`librosa`。
- **Node.js 18+**：`dem_to_heightmap.mjs` / `geo_align.mjs` 需要 npm `geotiff@2.1.3`（装在仓库外，用 `--modules` 指过去）。
- **Chromium**：`python -m playwright install chromium`；渲染走 GPU 上的 WebGL2 和 WebCodecs。
- **ffmpeg / ffprobe**：在 `PATH` 上，或用 `FFMPEG` / `FFPROBE` 指定。
- **模型**：Kimi 走 Kimi Code CLI（本机已登录），DeepSeek 走 opencode 或任意 OpenAI 兼容端点（`ARENA_PROXY_*`）；离线测试不需要任何模型。

## 数据目录与环境变量

一切数据、素材和产物都放在数据根 `ARENA_DATA`（默认 `<仓库>/data`，已在 `.gitignore` 里）。约定的布局：

```
$ARENA_DATA/
  scenarios/<想定>/      start.json、units.json、vp-table.json、terrain-*.tsv、official.json、benchmarks.json、unit-names-zh.json
                         （由 tools/s2_scenario_data.py、s2_vp_table.py 等从你自己的游戏普查生成）
  human/                 人类对局参考分（tools/s2_human_scores.py）
  recordings/            录制样本（离线测试 test_referee_game 用的定标局放 recordings/p1-cal-als2/）
  runs/<run>/            每次对局 / 训练 / 定标的目录：裁判日志、代理转录、relay 拉回的文件夹
  fpc-docs/Documents/    游戏 Documents 目录的副本（arena/library/fetch.py 的输入）
  gamehost/              tools/gamehost.py 的作业状态与输出
  game-machine.lock      导演 / 定标脚本占用游戏机的锁
  video/                 沙盘素材与节目产物：models/、hoi4-assets/、assets-v9/、assets-staging/、fpc-original/、
                         fpc-symbology/、aigc/、voice/、music/（tracks/、plan.json）、program/（staging/、
                         live-commentary/、raw-footage/、v1108-voiced/ …）
  tts-lab/               TTS 模型与虚拟环境（可用 TTS_LAB 另指）
```

| 变量 | 作用 | 默认 |
|---|---|---|
| `ARENA_DATA` | 数据根 | `<仓库>/data` |
| `FPC_GAME_ROOT` | 游戏机上的游戏目录（真跑对局必填） | 无；作业脚本里退回到作业的当前目录 |
| `ARENA_GAME_NODE` | 游戏机的名字，原样传给适配器 | `local` |
| `ARENA_GAMEHOST` | 游戏机适配器命令，参数形状同 `tools/gamehost.py`（push / spawn / wait / kill / pull / ps / run / occupancy） | `python -I -X utf8 tools/gamehost.py`（游戏在本机） |
| `ARENA_REFEREE_HOST` | 游戏机连裁判用的地址 | `127.0.0.1` |
| `ARENA_PROXY_BASE_URL` / `ARENA_PROXY_API_KEY` / `ARENA_PROXY_MODEL` | `deepseek-proxy` 路线与 opencode 的 `proxy` 提供方：任意 OpenAI 兼容端点 | 模型默认 `deepseek-v4.1-flash` |
| `DASHSCOPE_TOKEN_PLAN_API_KEY` | `deepseek-1017-dashscope` 路线 | — |
| `ARENA_OPENCODE_EXE`、`ARENA_KIMI_MAIN`、`ARENA_KIMI_THINKING` | CLI 位置、Kimi 思考档位 | 见 `arena/harness/clis.py` |
| `ARENA_DROP_ENV` | 逗号分隔，启动 CLI 时额外去掉的环境变量 | — |
| `ARENA_LUA` | 沙盒用的 `lua.exe` | `arena/sandbox/bin/lua.exe` |
| `FFMPEG` / `FFPROBE` | ffmpeg / ffprobe 可执行文件 | `ffmpeg` / `ffprobe` |
| `TTS_LAB`、`TORCH_LIB` | TTS 模型目录；faster-whisper 需要的 CUDA DLL 目录 | `$ARENA_DATA/tts-lab`；— |
| `ARENA_FONT_CJK`、`ARENA_FONT_CJK_BOLD`、`ARENA_FONT_MONO`、`ARENA_FONT_MONO_BOLD` | 原版画面拼片用的字体 | 系统字体 `msyh.ttc`、`msyhbd.ttc`、`consola.ttf`、`consolab.ttf` |
| `ARENA_CLIPS` | 原版画面片段表 | `$ARENA_DATA/video/program/raw-footage/clips.json` |

## 怎么跑

```
# 单元与离线测试（不需要游戏和模型）；没有 $ARENA_DATA/scenarios（想定数据）或手册文本时，依赖它们的测试会跳过
python -I -X utf8 -m unittest discover -s arena/tests -t .

# 离线整套演练：假 relay 在本机打几分钟假局，脚本化假玩家
python -I -X utf8 tools/p4_training.py --run data/runs/p4-dry --dry-run
python -I -X utf8 tools/s2_director.py --run data/runs/s2-dry --dry-run

# 真跑：按 fpc/README.md 把录制器、桥接、DLL 放进游戏目录，准备好 $ARENA_DATA/scenarios/<想定>/，然后
export FPC_GAME_ROOT='<游戏目录>'
bash tools/p2_smoke.sh data/runs/p2-smoke 30                         # 双人模式冒烟，写死的简单代理
ARENA_HARNESS=cli bash tools/p3_rehearsal.sh data/runs/p3 kimi opencode   # 模型对模型整局
```

各脚本的参数和产物写在它们的文件头里。

## 不包含的东西

- 游戏想定、地形、兵力、VP 表、手册文本等一切游戏数据（原仓的 `fpc/data/`），以及由游戏导出的界面位图、图标、特效、字体；
- 录制样本、对局日志、裁判计分结果、模型转录与输出；
- 节目的镜头表（`shot-*.js`）、节拍表（`segments/*.json`）、台词轨（`*.voice.json`）、解说台本、演出 staging 文件、片段表、铺曲表、片尾致谢；
- 配音、音乐、立绘、封面、图片、模型（glb）、地表贴图、DEM、字体子集，以及第三方浏览器库（运行 `tools/video/fetch_vendor.py` 下载）；
- 项目过程资料（代码注释里提到的 `materials/…`、「N 号资料」只是说明某条做法的来由，读代码不需要）。

第三方代码与素材的来源和许可见 [`NOTICE.md`](NOTICE.md)。

## 许可

MIT，见 [`LICENSE`](LICENSE)。本项目与 Flashpoint Campaigns 的开发商 / 发行商、各模型提供方均无关联。
