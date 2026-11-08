# fpc：Flashpoint Campaigns: Cold War 接入件

跑在游戏那台 Windows 机器上的部分：游戏进程里的 Lua 录制器 / 桥接脚本、一个 Lua C 模块，以及启动游戏、推进回合、收集产物的 PowerShell 作业。

> **前提**：这些脚本依赖游戏本身提供的 Lua 脚本接口（`-luafile`）、Telnet 控制台（`-telnet`）和命令行开局参数。请按发行商条款使用提供这些接口的游戏版本；本仓不提供、也不说明任何解锁或绕过方式。

## 文件

| 文件 | 作用 |
|---|---|
| `lua/recorder.lua` | 结构化录制器。随想定加载，开局全量快照，每游戏分钟对双方全部单位做差分，写 `rec.jsonl`、`status.json`、`rec.log`；`ai-arena\in\run.lua` 给出输出目录和要额外加载的脚本（`extra`） |
| `lua/arena-bridge.lua` | 比赛桥接（录制器的 `extra`）：每游戏分钟写裁判视角的全量态势 `state.json`；实时闸（`gate.txt`）；`arena_release(path)` 只当数据读入命令批并逐条执行，结果写回 |
| `lua/p0-probe.lua`、`lua/markers-probe.lua` | 接口探针：命令、条令、火力请求、标记等 GameAPI 调用的实测 |
| `lua/test-*.lua` | 用假 `GameAPI` 离线跑上面的脚本：`lua test-recorder.lua recorder.lua <输出目录\>` |
| `native/arena.c`、`native/build.sh` | Lua C 模块 `lua\arena.dll`：在游戏自己的进程里列窗口、向本进程主窗口投递按键（F9 = 开始回合推进）、`sleep`。`build.sh` 用 MinGW-w64 从官方 Lua 5.4.3 源码编出 `lua54_64.dll`、`lua\arena.dll` 和裁判脚本沙盒用的 `lua.exe` |
| `arena.ini` / `arena-h2h.ini` | 配合 `-config`：电脑对电脑（`StyleOfPlay=2`）/ 双人热座（`StyleOfPlay=3`，模型对模型用） |
| `remote/relay.ps1` | 比赛中继：起游戏（可放在独立桌面上，不出现在使用者桌面）、每 300 ms 把游戏时钟 / 阶段 / 态势 / 调试日志新行 / 对话框同步给裁判，执行裁判的答复（闸、放行命令批、F9、点对话框、停止），终局截图后按 PID 关掉自己的游戏 |
| `remote/job-smoke.ps1` | 电脑对电脑冒烟：启动一局、等录制器加载、按阶段推进、到终局或超时收尾、收集日志和自动存档 |
| `remote/job-p0.ps1`、`job-p0-parallel.ps1`、`job-p1-briefs.ps1` | 接口探针作业、并行探针、逐想定开局简报普查 |
| `remote/job-desktop-probe.ps1`、`job-diag-windows.ps1` | 独立桌面可行性探针；导出本进程窗口与对话框文字的诊断作业 |
| `remote/job-vcr-capture.ps1` | 原版画面素材：逐个读入双人对局的自动存档，用游戏自带的回合回放（Replay Previous Turn Resolution）放出原始推进，PrintWindow 逐帧截主窗口；拼片见 `../tools/video/raw_footage/` |
| `remote/health.ps1`、`stop-game.ps1`、`telnet-session.ps1` | 开局前体检（只读：自己上一局还在不在、有没有别人的游戏进程、Telnet 端口被谁占、遗留的 relay token）；按 PID + 创建时间关自己的游戏进程；单次 Telnet 会话 |

## 游戏目录里要放的东西

```
lua54_64.dll            native/build.sh 产物（游戏找的就是这个文件名）
lua\arena.dll           native/build.sh 产物
arena.ini, arena-h2h.ini
ai-arena\recorder.lua   lua/recorder.lua
ai-arena\arena-bridge.lua
ai-arena\p0-probe.lua   （只有探针作业要）
```

录制器按环境变量 `FPC_ARENA_DIR` 找 `ai-arena\`（作业脚本启动游戏前会设好），没有时按游戏工作目录下的 `ai-arena\` 找。作业脚本的游戏目录取 `FPC_GAME_ROOT`，没有时取作业的当前目录。

## 启动参数

作业脚本拼的命令行：`-Scenario "<想定>" -StyleOfPlay <n> -Quiet 1 -TimedMessages 1 -luafile "<游戏目录>\ai-arena\recorder.lua" -telnet 127.0.0.1:47823 [-config "arena.ini"]`。

- 没有 `-StyleOfPlay` 会停在主菜单；
- 带 `-config` 时由配置文件定两方归谁：`arena.ini` 两方都是电脑，`arena-h2h.ini` 两方都是“人”（每个下令阶段等 F9，命令由裁判放行）；不带 `-config` 时，命令行 `StyleOfPlay` 0 / 1 / 3 都让游戏 AI 指挥 2 号玩家（对 AI 的演习就这样开）；
- 双人热座里另一方的回放、移交对话框、F9 时机都由 `relay.ps1` 按裁判答复处理，细节见 `../arena/DESIGN.md` 第 3 节。

## 在本机跑一局电脑对电脑

游戏和这些脚本在同一台机器时，用仓库自带的适配器 `tools/gamehost.py`（`local` 节点）：

```
set FPC_GAME_ROOT=<游戏目录>
python -I -X utf8 tools/gamehost.py push local fpc/lua/recorder.lua "%FPC_GAME_ROOT%\ai-arena"
python -I -X utf8 tools/gamehost.py spawn local smoke-1 --file fpc/remote/job-smoke.ps1 --args <参数.json> --cwd "%FPC_GAME_ROOT%" --deadline 55m
python -I -X utf8 tools/gamehost.py wait local smoke-1 --timeout 3480
```

`<参数.json>` 是作业脚本的参数（如 `{"Scenario": "Chance Encounter", "RunForMinutes": 30}`）。产物在 `<游戏目录>\ai-arena\out\rec-<时间>\`：`rec.jsonl`、`status.json`、`rec.log`、`job.log`、`driver.tsv`，`collected\` 下是游戏调试日志和每回合自动存档。游戏在另一台机器时，把 `ARENA_GAMEHOST` 设成同样参数形状的远程命令（见 `tools/gamehost.py` 文件头）。

`tools/rec_summary.py` 汇总 `rec.jsonl` 并做回放校验：首个快照加全部差分重建出来的状态，要与后面每个快照一致。
