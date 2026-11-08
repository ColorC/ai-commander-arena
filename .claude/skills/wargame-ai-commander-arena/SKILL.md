---
name: wargame-ai-commander-arena
description: 为一款兵棋 / 策略游戏建立让大模型当指挥官对抗的设施和 3D 沙盘回放：游戏侧录制与快照、裁判（迷雾视图、命令校验与放行、推进、稽核、计分）、模型接入（直连 API 或官方 CLI 经 ACP + MCP）、离线测试，以及从录像生成沙盘数据。以本仓 Flashpoint Campaigns 的实现为例。用户要接一款新游戏、改裁判规则、加命令种类、排查对局卡住或迷雾泄露、从录像做沙盘时使用。
---

# 为兵棋游戏建立 AI 指挥官对抗设施和 3D 沙盘

本仓的做法：游戏进程里跑一个 Lua 录制器 / 桥接，游戏机上跑一个 PowerShell 中继，中继和裁判之间用 HTTP JSON；模型只和裁判说话，看到的永远是本方迷雾视图。权威说明是 `arena/DESIGN.md`，下面是照着做的顺序。

## 0. 先确认游戏给了什么接口

列出游戏能做到的四件事，缺哪件就决定了后面怎么补：

| 需要 | 本仓用的 | 没有时 |
|---|---|---|
| 读全量真值（双方单位位置、状态、命令、子单位） | 游戏内 Lua：`GetUnitStatus` / `GetUnitOrders` / `GetUnitSubunits` | 读存档文件、读内存都不稳；优先找游戏的脚本 / 模组接口 |
| 每游戏分钟的回调 | Lua 分钟回调 | 轮询游戏时钟 |
| 下命令 | `SetUnitOrders`、`SetUnitDoctrine`、`IssueFireSupportRequest`、`SetUnitStatus`（部署） | 模拟输入非常脆弱，不推荐 |
| 推进回合、读阶段 | 向本进程主窗口投递 F9（`fpc/native/arena.c`）；Telnet 控制台读状态栏 | 依游戏而定 |

使用这些接口要遵守发行商条款，选用提供这些接口的游戏版本。

先写**探针**把每个接口实测一遍（`fpc/lua/p0-probe.lua`、`fpc/remote/job-p0.ps1`）：返回值的真实含义常常和文档不同。本仓实测到的例子：未知命令名返回 true 但被悄悄改成 Screen（所以只收回读后名字不变的命令）；火力请求返回 true 只表示进了队列；VP 格归属根本读不到（裁判只好按单位经过自己推算，见 `arena/referee/world.py`）。每条实测结论写进 `arena/referee/rules.py` 或校验代码的注释里。

## 1. 录制：一局的结构化录像

`fpc/lua/recorder.lua`：

1. 开局写一条 `meta` 和一个全量 `snap`（双方所有单位，嵌套表）。
2. 每游戏分钟对每个单位做差分，只写变了的扁平路径（`diff`），加 `unit_add` / `unit_gone` / `minute` / `event` / `order_start` / `order_finish` / `f9` / `end`。
3. 每 `keyframe_seconds`（默认 1800）再写一个全量 `snap`，用来校验差分。
4. 每分钟重写 `status.json`（最后游戏秒、计数），作业脚本看它判断游戏还活着。

录制器先离线测：`lua fpc/lua/test-recorder.lua fpc/lua/recorder.lua <输出目录\>`，用假 `GameAPI`。真局录完用 `tools/rec_summary.py` 回放校验：首个快照加全部差分重建的状态必须与后面每个快照一致。录像是之后计分、复盘、沙盘的唯一真源，这一步不能省。

## 2. 桥接：态势、实时闸、放行

`fpc/lua/arena-bridge.lua`（作为录制器的 `extra` 加载）：

- 每游戏分钟写 `state.json`（裁判视角全量，先写临时文件再改名）。**只给裁判，绝不直接给模型。**
- 逐分钟对齐：写完 state 后等 `gate.txt` 里的 ack 追上本分钟（最多 3 秒），保证唤醒在引起它的那一分钟生效。
- 实时闸：`gate.txt` 为 `sleep` 时按墙钟拖住游戏线程（有模型在想就别让游戏跑），`off` 时全速跑。
- `arena_release(path)`：把裁判给的命令批**只当数据**读入（`load(text, name, "t", {})`，裁判侧由 `arena/referee/lua_literal.py` 生成只含表 / 字符串 / 整数 / 布尔的字面量），逐条执行，结果和回读写回文件；记住已执行的 batch_id，重发不重做。

坑：游戏的 Telnet 在另一条线程上跑 Lua，和游戏线程同时动一个 Lua 状态会写坏它。所以凡是会让游戏线程继续跑的动作（点对话框、F9、截图）都由中继用窗口消息从自己进程里做，Telnet 只在游戏停在下令阶段时发放行批（DESIGN 3.4）。

## 3. 中继：游戏机上的循环

`fpc/remote/relay.ps1` 每 300 ms `POST /relay/sync`：带游戏时钟、阶段、下令方、新的 state、调试日志新行、对话框、上次放行结果；按答复写闸、放行、按 F9、点对话框、停止。要点：

- 下令方只认调试日志的 `Player N Orders Phase` 行；是否真在等命令以状态栏为准，读早了要重读。
- 回放期间报 `hold: true`，裁判不放行、不按 F9（回放中的输入不进日志，没法稽核）。
- F9 前读两次状态栏确认还在下令阶段，结算中按 F9 会暂停结算。
- 看门狗：时钟 15 秒不动就主动读状态栏。
- 终局截图后按 PID + 创建时间只关自己起的游戏（`fpc/remote/stop-game.ps1`），开局前体检（`fpc/remote/health.ps1`）。

游戏机怎么到达由适配器决定：脚本只调 `ARENA_GAMEHOST`（默认 `tools/gamehost.py`，游戏在本机），参数形状固定为 push / spawn / wait / kill / pull / ps / run / occupancy；游戏在别的机器上就写一个同样参数的命令（SSH、WinRM、作业系统都行）。

## 4. 裁判

`arena/referee/`，按这个顺序搭：

1. **world**（`world.py`）：最新 / 上一分钟 state、VP 格、开局区、地形表，从 `$ARENA_DATA/scenarios/<想定>/` 读。想定数据用 `tools/s2_scenario_data.py`、`tools/s2_vp_table.py` 等从自己的游戏普查生成。
2. **接触与迷雾视图**（`contacts.py`、`views.py`）：敌情只来自本方接触表（编号、最后发现位置与时间、报告文本），不给敌方单位名。视图出门前做**迷雾断言**：每个敌方条目都要对应一条曾被发现的接触、不含真值字段；失败就丢掉视图、记 `fog_violation`。`tools/fog_check.py` 事后再查一遍日志。
3. **事件与唤醒**（`events.py`、`wake.py`）：每分钟比较前后 state 生成本方事件（下令阶段到、新接触、交火、伤亡、VP 易手、命令完成…）；订阅、过滤、30 游戏秒合并；回合有截止时间，到点强制结束。
4. **命令**（`orders.py`）：提交时校验（本方且在场、格号在图内、字段在白名单、部署格在开局区），连续非法记违规；`POST /agent/queue/lint` 只校验不入队。本方下令阶段才放行。放行被扣或游戏返回 false 要回告模型（`command_failed`），否则模型永远不知道命令没生效。
5. **稽核**（`audit.py`）：调试日志的 `LUA>` 行与放行记录逐条核对。
6. **计分**（`scoring.py`、`tools/p6_score.py`、`tools/s2_match_score.py`）：游戏自己的终局页最权威，`tools/fpc_endscreen.py` 对终局截图做 OCR 取官方结果，裁判推算值只作参考和核对。
7. **HTTP**（`server.py`）：`/relay/*`、`/agent/*`（每方一个 token；回合外除 `wait` 一律 409）、`/admin/*`。

所有数值进 `rules.py`，每类记录一个 jsonl（`log.py`），复盘全靠这些日志。

## 5. 接模型

两条路，共用同一个唤醒循环：

- **直连**：`arena/agent/arena_agent.py --route <route>`，路线在 `arena/agent/routes.py`（OpenAI 兼容 chat completions + tools；新路线照 `deepseek-proxy` 的写法从环境变量取地址和 key，不要把地址写进代码）。
- **官方 CLI**：`arena/harness/driver.py --cli kimi|opencode`，经 ACP 驱动 `kimi acp` / `opencode acp`，裁判工具由 `arena/harness/mcp_server.py` 经 MCP 暴露。CLI 的内置工具（读写文件、shell、联网）必须全关，只按本次进程传配置，不改用户的全局配置（`arena/harness/clis.py`）。

模型只拿一个工具 `arena`，参数是一行命令（`arena/agent/cli.py`）：比几十个 JSON 工具好用得多。单位名不分大小写、前缀即可，格号多种写法，拼错给“你是不是想说”，处处有 help 和例子。两方同一份系统提示（`arena/agent/prompts/system.md`）和基础文档（`arena/library/basics.md`）。

反射脚本和计算任务在 Lua 沙盒里跑（`arena/sandbox/host.lua` + `runner.py`）：空环境、无 io/os/load，指令 / 墙钟 / 内存限额，Windows 作业对象兜底，超限 `pcall` 也吞不掉。

## 6. 测试，从离线到实机

1. 单元与离线整局：`python -I -X utf8 -m unittest discover -s arena/tests -t .`。`arena/tests/fakegame.py` 造假游戏，`fake_acp_cli.py` 造假 CLI，`--route fake` 是脚本化假模型。需要想定数据的模块在没有 `$ARENA_DATA/scenarios` 时会跳过。
2. 导演离线演练：`tools/p4_training.py --dry-run`、`tools/s2_director.py --dry-run`（假 relay、假玩家，`--inject` 注入未加载 / 卡死 / 断线 / CLI 崩溃）。
3. 实机电脑对电脑定标：`tools/s2_calibrate.sh`、`tools/p1_calibrate.sh`（得到 AI 对 AI 的基准分）。
4. 实机冒烟：`tools/p2_smoke.sh`（双人模式、写死的简单代理，查放行、F9、闸、稽核）。
5. 彩排与正式：`tools/p3_rehearsal.sh`、`tools/p5_match.sh`，赛季由 `tools/s2_director.py` 排队、体检、重开、停表。

每一层都通过了再上下一层；实机每次只改一项。

## 7. 从录像生成 3D 沙盘数据

1. 地形：`python -I -X utf8 tools/s2_battle_maps.py --terrain <terrain-*.tsv> <terrain.json>`（六角格、道路、河流、VP 格）。六角格约定（平顶、偶数列下沉半格、边从北顺时针编号）是用数据反推出来的：换游戏时照 `--geometry` 的做法，把所有约定都试一遍，选让道路和水系两侧互相吻合的那个。
2. 对局：`python -I -X utf8 tools/s2_battle_maps.py <对局目录> <game.json>`（单位轨迹、事件、目标格易手、份额；字段见文件头）。
3. 真实地形：`tools/video/geo_align.mjs` 用逐格高程相关找出六角格地图在真实世界的位置，`tools/video/dem_to_heightmap.mjs` 从 Copernicus GLO-30 DEM 切出高度图。
4. 打包：`python -I -X utf8 tools/video/build_data.py --terrain ... --game ... --heightmap ... --out data-<局>.js`。
5. 看一眼：`python -I -X utf8 tools/video/render.py --data data-<局>.js --shot <镜头表> --out-dir <目录> --stills-only`。镜头表和出片见 `battle-replay-show` skill。

## 常见坑

- 接口“返回成功”不等于生效：每种命令都要回读，或从下一分钟的 state / 调试日志确认。
- 阶段判断别只靠一个信号（对话框、日志行、状态栏各有例外），DESIGN 3.2 列了本仓踩过的每一种。
- 中继和游戏的竞态：只在游戏停住时经 Telnet 跑 Lua。
- 迷雾：任何给模型的东西（视图、脚本的 `view`、事件、错误信息）都要过同一道断言。
- 长跑的 bash 脚本先复制一份再跑（bash 边跑边按偏移读脚本，改原文件会让正在跑的局读到半截命令，见 `tools/p3_rehearsal.sh` 开头）。
- 只关自己起的进程：按 PID + 创建时间，不按进程名。
- 所有路径走 `ARENA_DATA`、`FPC_GAME_ROOT`，不要写死盘符。
