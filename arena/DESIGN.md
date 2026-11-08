# arena：表演赛裁判设施（P2）设计与接口约定

规则数值一律写在 `arena/referee/rules.py` 的常量里，改数值只改那里。本文只定实现结构和各部件之间的接口。文中提到的「N 号资料」「materials/…」「P0–P5」「第二届」等是项目过程中的内部资料与阶段名，未随本仓公开；引用它们的地方只是说明某条做法的来由，读代码不需要它们。

## 1. 部件与目录

| 部件 | 位置 | 跑在哪 | 作用 |
|---|---|---|---|
| arena-bridge | `fpc/lua/arena-bridge.lua` | 游戏进程内（recorder.lua 的 `extra`） | 每游戏分钟写全量态势；实时闸；放行命令批；对话框点击 |
| relay | `fpc/remote/relay.ps1` | 游戏机，隐藏窗口作业 | 起游戏；文件 ⇄ HTTP；Telnet 只用来放行（`arena_release`）和读状态栏；尾随调试日志；对话框、F9、终局截图由 relay 自己发窗口消息（3.4） |
| referee | `arena/referee/` | 本机 Python | 局面状态机、迷雾视图、事件与唤醒、思考用时、命令队列、稽核、日志；HTTP 服务 |
| agent | `arena/agent/` | 本机 Python，每方一个进程 | 调模型；工具全部走 referee 的 HTTP；子代理；对话与工具调用记录 |
| sandbox | `arena/sandbox/` + `fpc/native/build.sh` 的 `lua.exe` | 本机 | 反射脚本和计算任务的 Lua 5.4 沙盒宿主（Windows 作业对象限内存） |
| library | `arena/library/` | 本机 | 手册文本（FM01–FM07、FAQ、快捷键），只读；不进 Git（游戏版权材料），由 `arena/library/fetch.py` 从游戏 `Documents` 目录的 PDF 生成，`manifest.json` 记每个文件的 sha256 |

通信只有两条：relay → referee（游戏机主动连裁判所在机器的 41010 端口；游戏与裁判同机时就是 `127.0.0.1:41010`，异机时 relay 的 `-Referee` 写裁判机的地址（`ARENA_REFEREE_HOST`）），agent → referee（`127.0.0.1:41010`）。referee 不主动连任何地方。

## 2. 方与身份

- `side` 取 0 / 1，与游戏一致：0 = Player 1 = 蓝方（北约），1 = Player 2 = 红方。
- 每方一个随机 token（referee 启动时生成，写进 `<run>/secrets/side<N>.token`，只交给该方 agent 进程）。agent 端点都要带 `Authorization: Bearer <token>`，referee 按 token 定方，请求里自报的方一律忽略。
- 一方 agent 的工作区 `<run>/workspace/side<N>/`（笔记、脚本源码、订阅）只有该方能读写；referee 不把一方的任何内容给另一方。

## 3. relay ⇄ referee 协议（HTTP JSON）

### 3.1 `POST /relay/sync`（relay 每 300 ms 一次）

请求：
```json
{
  "seq": 123,                       // relay 自增
  "wall_ms": 1793333023553,
  "game": {"sec": 3600, "phase": "Executing|Orders|Setup|Pregame|Ended|Unknown", "orders_side": 0|1|null,
           "elapsed": 3600, "total": 18000, "alive": true,
           "hold": false},                // true：回放进行中，referee 这次不放行、不按 F9（3.2）
  "state": {...} | null,            // bridge 写的 state.json，有新的 minute 才带（见 4.2）
  "lines": ["<调试日志新行>", ...],  // UpdateSpottedStepsAndReport()、Player N Orders Phase、LUA>、ChangeSideNow 等；同步失败时缓存到下次
  "dialogs": [{"cls": "...", "title": "...", "texts": ["..."]}],
  "release_result": {...} | null    // 上一次放行的结果（见 3.2）
}
```
响应：
```json
{
  "gate": "off|sleep",              // 写进 bridge 的 gate.txt
  "release": {"batch_id": "s0-0007", "side": 0, "lua": "return {...}"} | null,
  "f9": true|false,                 // 放行完成后按 F9 继续（relay 看状态栏把关，见 3.2）
  "orders_done": true|false,        // 本下令阶段已放行完：relay 这时才点移交对话框（3.2）
  "click": [{"cls": "...", "text": "&Proceed"}],
  "stop": false
}
```

### 3.2 放行

- `orders_side` 只认调试日志的 `Player N Orders Phase` 行。双人模式里“Pass control to X player”对话框也用于让另一方看回放，不能当下令方（P2 冒烟 10-30 实测，按它判断会在对方阶段放行）。
- 游戏是否真在等命令（`phase == "Orders"`）以 Telnet 状态栏为准：双人局里不是每条下令行都停下等 F9（P2 冒烟 07:14、07:29 两次自行走过）。下令行之后状态栏可能还没变，relay 每 2 秒重读，直到状态栏显示 Orders，或调试日志的游戏时钟已经走过这一分钟（P2 冒烟 smoke-150257：只读一次，读早了，卡在真正的下令阶段 36 分钟）。
- 开局（Setup/Pregame）一次放行两方的批（先 0 后 1），再按 F9：双人模式打完 Player 1 的部署画面就直接开打，没有 Player 2 的部署画面（P2 冒烟实测）；部署是 `SetUnitStatus`，任何一方的画面下都生效。
- 回放期间（从移交对话框到 “Game Resolution Replay Concluded”）游戏不把 Lua 输入写进调试日志，relay 报 `hold: true`，referee 这段时间不放行、不按 F9，保证每条输入都能稽核（P2 冒烟实测漏记了一次 F9）。
- 双人局的下令阶段有两种（P2 冒烟 10-30 实测，回放窗口开着不关也一样）：
  - 带回放的（07:14 美军、07:29 波军）：移交对话框一点，接着放回放，回放完游戏自己开始结算，不等 F9；能下令的只有下令行到移交对话框之间几秒；
  - 不带回放的（07:28 美军）：点了移交对话框后停在下令阶段，等 F9；
  - 带回放、回放完又回到下令阶段等 F9 的（P3 彩排 10-30 08:45 美军）：回放中状态栏显示 “PlayVCR phase”，relay 报 Executing，回放结束后若不再读状态栏就会一直卡住（这次卡了 75 分钟）。所以 relay 看到 “Game Resolution Replay Concluded” 后按下令行的办法重读状态栏；状态栏是 Orders 而当前没有下令方时，沿用最近一条下令行的一方（F9 按成功后清掉）。referee 把同一方、同一游戏秒的下令阶段当成同一个阶段继续：不再发 `orders_phase` 事件、不再跑脚本、已放行的批不重放，只重新开始要 F9。
- relay 另有看门狗：闸为 off、不在 hold 中，而游戏秒和调试日志时钟 15 秒都没动，就每 2 秒读一次状态栏，不管有没有提示（游戏在等什么时 Telnet 能答）。
- 所以下令行之后出现的移交对话框，relay 先不点，等 referee 的 `orders_done: true`（本阶段的批已放行并收到结果，或没有可放的），最多等 `PassDeferSec`（20 秒）；点了才开始 hold。
- F9 由 relay 自己把关：referee 说 `f9` 后，relay 等到没有移交对话框、hold 已过（回放后的移交对话框点完另停 3 秒），读两次 Telnet 状态栏（只连接、不发 Lua，间隔 1 秒），两次都显示 Orders 或开局阶段、游戏时间也一样才向游戏主窗口发 F9 键消息，否则跳过（结算中按 F9 会暂停结算）。referee 在同一阶段里每 15 秒再要一次，最多 8 次，仍不动就记一条 `f9_no_effect`。
- 万一还是暂停了：状态栏显示 Pause、又没有对话框时，relay 自己按 F9 恢复，最多 10 秒一次（P3 彩排 5：回放结束后的 F9 撞上结算开始，停在 Pause phase，手动 F9 一次就恢复）。
- 下令阶段（`phase == "Orders"` 且 `orders_side` 已知）referee 给出该方的放行批：队列里该方所有待放行命令，转成 Lua 数据字面量（`arena/referee/lua_literal.py`，只含表、字符串、整数、布尔）。
- relay 把 `lua` 写进 `ai-arena\in\release-<batch_id>.lua`，Telnet 调 `arena_release([[<path>]])`；bridge 用 `load(text, name, "t", {})` 只当数据读，逐条执行，结果写 `ai-arena\out\<run>\release-<batch_id>.json`：`{"batch_id":..., "results":[{"cmd_id":..., "ok":true|false, "ret":..., "readback":...}]}`。
- relay 在下一次 sync 带上 `release_result`；referee 记放行记录（稽核用），回 `f9: true`；relay 发 F9（上一条）。放行结果文件出现之前 relay 不发 F9、不点对话框。
- 没收到结果时 referee 隔 10 秒重发同一批；bridge 记住已执行的 batch_id，重发的批不再执行，直接返回第一次的结果。
- 没有待放行命令时 referee 直接回 `f9: true`（游戏只停放行所需的几秒，10 号资料 2.5）。

命令种类（`kind`）与游戏接口：

| kind | 游戏接口 | 何时可放行 |
|---|---|---|
| `orders` | `SetUnitOrders(unit, orders[])`；命令名白名单 13 个（`rules.ORDER_TYPES`），10-31 加 Counter Battery、Hunt（Hunt 须带 destination_location）。游戏机探针（10-31，16 号资料第 3 节）：游戏对不认识的命令名也返回 true，但悄悄改成 Screen，所以只收回读名字不变的；Barrage 须有瞄准点（`target_location`，或接触换成的 `target_unit`）加 `barrage_type`（HE / ICM）、`barrage_rounds`、`barrage_duration_mins`（都 ≥ 1），用 destination_location 就被拒；`barrage_timing` 只开 asap（程序里另有 fixed，未测），`barrage_area_fire` 布尔值裁判转成字符串（游戏只读字符串），这些都在提交时校验 | 本方下令阶段 |
| `doctrine` | `SetUnitDoctrine(unit, sop)`，字段白名单见 `arena/referee/doctrine_whitelist.json`（P0.8 的 16 个字段） | 本方下令阶段 |
| `emission` | `SetUnitEmissionStatus(unit, {...})` | 本方下令阶段 |
| `fire_support` | `IssueFireSupportRequest(side, request)`；request 键：`request_type`（static_target / enemy_target / obscuration / cb_scan / direct_support / sead / minefield）、`location`、`enemy_unit`、`spotter_unit`、`requester_unit`、`target_type`、`projectile_type`、`duration_mins`、`priority`、`delay`（游戏程序字符串里查到）。游戏机探针（10-31，15 号资料第 4 节）：`target_type` 只认小写 soft / hard / hq / obscuration / illumination，static_target 不带就被拒，裁判提交时就校验；其余字段游戏不校验；sead、direct_support 怎么写都被拒；返回 true 只表示进了 FSCC 队列；代理打敌方单位只给接触编号，接触在视时裁判换成 `enemy_unit` | 本方下令阶段 |
| `deploy` | `SetUnitStatus(unit, {location = <整数格号>})` | 只在开局部署（Setup/Pregame），目标格须在本方开局区内 |

### 3.3 实时闸

- referee 按 10 号资料 2.5 决定 `gate`：任一方有代理活跃（含观察模式）→ `sleep`；否则 `off`。
- `gate.txt` 内容是 `<mode> <ack 秒>`，relay 每次同步成功后整份替换（先写临时文件再 `File.Replace`）；ack 是 referee 已处理的最新 state 的游戏秒。
- **逐分钟对齐**：bridge 在每个 minute 回调里先写 state，再等到 ack ≥ 本分钟（最多 3 秒，超时记 `step_timeout`）才往下走。这样唤醒和闸都在引起它的那一分钟生效；不对齐时游戏空闲全速跑（约每秒 2.5 游戏分钟），P2 冒烟实测代理醒来时游戏已多跑 1–4 分钟。代价是每游戏分钟约 0.5 秒，整局 5 小时约多 3 分钟。
- 然后 `sleep` 时以切到 sleep 的那一分钟为锚，用 `arena.sleep` 分段等到墙钟追上游戏钟（每 200 ms 重读一次 gate.txt，变成 `off` 立刻放行）。这就是 P0.2 验证过的做法。读不到 gate.txt 时沿用上次读到的值，不当成 off。
- Executing 阶段 relay 只在调试日志刚报下令阶段时读 Telnet 横幅，其余靠调试日志和自己按的 F9 判断阶段。

### 3.4 Telnet 的 Lua 跑在另一条线程上

- 游戏的 Telnet 服务在自己的连接线程里执行收到的 Lua，不等游戏线程（2026-10-30 实测：分钟回调里 `arena.sleep(4000)` 占住游戏线程、置 `in_cb = true`，同时 Telnet 读到 `in_cb=true`，回答不延迟）。两条线程同时用一个 Lua 状态会写坏它：P3 第 4 次彩排 07:30，relay 点“Secure Transmission”的 Lua 还没跑完，游戏线程已进分钟回调，随后 `Access violation ... ntdll.dll`，bridge 读 gate.txt 的句柄留着没关（之后 relay 改不了闸），09:42 弹出点不掉的 Error 框，整局作废。
- 所以凡是会让游戏线程接着跑的动作（点对话框、关回放窗、F9）和终局截图，都由 relay 用窗口消息从自己进程里做：`[Wn]::Click`（BM_CLICK）、`Close`（WM_CLOSE）、`Key`（WM_KEYDOWN/UP）、`Shot`（PrintWindow），和 arena.dll 同名函数一样，只是不经 Lua。只发消息、读窗口标题，不往游戏进程里写东西。
- 剩下唯一的 Telnet Lua 是放行批：只在下令阶段（游戏线程停着等命令，没有游戏自己的 Lua 在跑）发，等结果文件写出来才做下一步。状态栏横幅只连接不发 Lua。

## 4. bridge（游戏侧）

### 4.1 bridge 的全局函数

| 函数 | 作用 |
|---|---|
| `arena_release(path)` | 3.2，比赛里唯一经 Telnet 调的 |
| `arena_f9()` | recorder 已有；比赛里不用（3.4），P0 探针作业还在用 |
| `arena_click(cls, text)`、`arena_close(cls)` | 同 `p0_click` / `p0_close`；比赛里不用（3.4） |
| `arena_shot(cls, name)` | 同 `p0_shot`；比赛里不用（3.4） |

VP 格归属游戏接口拿不到（2026-10-30 实测：`GetMarkers` 以单格、整图矩形、字符串格号等写法，在开局和第 1 游戏分钟都返回空表；调试日志和录制事件里也没有易手记录），由 referee 按手册规则推算，见 5.1 `world.py`。

### 4.2 `state.json`（每游戏分钟覆盖写，先写临时文件再改名）

```json
{"seq": 61, "sec": 3600, "wall_ms": ..., "phase_hint": "minute",
 "units": {"<unit name>": {"s": <GetUnitStatus>, "o": <GetUnitOrders>, "u": <GetUnitSubunits>}},
 "gate": {"mode": "sleep", "late_ms": 12}}
```
这是裁判视角（双方全部真值），只给 referee，绝不直接给 agent。

## 5. referee

### 5.1 模块

| 模块 | 内容 |
|---|---|
| `rules.py` | 全部数值常量（唤醒、时限、脚本限额、违规阈值） |
| `world.py` | 最新 state、上一分钟 state、VP 格、开局区、地形表加载（`$ARENA_DATA/scenarios/<想定>/`）。VP 格归属推算：开局归属取 `start.json`；之后一方地面单位（不含 Helo / Air）落在 VP 格上，或上一分钟到本分钟的位置连线（六角格逐格插值）经过 VP 格，就归这一方；同一分钟两方都经过则保持原归属。视图里注明“裁判按单位经过推算”，P3 用终局 Victory Status 页核对 |
| `contacts.py` | 解析 `UpdateSpottedStepsAndReport()` 行：被发现单位名 → 状态枚举（0–7，见 11 号资料 P0.4）、报告文本、首次/最后发现游戏秒、最后发现位置（发现时刻真实位置，状态 ∈ {1,2,3,5,6,7} 时更新）、对外编号 = 行里的 Contact 号 |
| `views.py` | 本方视角：本方单位全量；敌情只来自本方接触表（给 Contact 号、最后发现位置与时间、报告文本、当前是否在视），不给敌方单位名；VP 格归属；本方兵力百分比与损失。**迷雾断言**：视图里出现的每个敌方条目都必须对应接触表里一条“曾被发现”的记录，且不含单位名等真值字段；断言失败就丢掉该视图、记 `fog_violation` 并报警 |
| `events.py` | 每分钟对比前后 state 生成本方事件：下令阶段到、新接触、交火或伤亡、单位出局、兵力下降、VP 格易手、命令完成、脚本报警、定时、接触进入半径。另有 `command_failed`（core 发，不可订阅、不唤醒，进事件流和下一次摘要）：命令放行时被裁判扣下（单位不在图上、接触不在视），或放行后游戏返回 false（P3 彩排 6：波军 38 次火力请求被拒，选手一直不知道） |
| `wake.py` | 订阅（含默认订阅）、过滤、30 游戏秒合并、到点强制结束回合（兜底） |
| `thinking.py` | 思考用时（2026-11-04 起替代能量账）：每方按真实秒累计指挥官、观察模式、子代理的回合时间，赛前部署另记，再记醒来次数；只供显示和战报，没有预算、不影响唤醒。代理侧每条工具结果末尾一行“本回合已用、本局已思考”，唤醒开头一行本局累计，不再显示倒计时 |
| `orders.py` | 命令队列：校验（单位属本方且在场、格号在图内、条令字段在白名单、部署格在开局区）、连续 3 次非法记违规并开始直接丢弃（丢弃不再记违规，但回答照样带原因；本方单位不在场时报出游戏状态，如增援未到的 Reinforcement）、放行批生成、放行结果回填 |
| `audit.py` | 收 `LUA>` 行，与放行记录逐条核对，对不上记 `audit_mismatch` |
| `scripts.py` | 反射脚本登记与调度（每分钟、下令阶段、订阅事件），调 sandbox。脚本的 `view` 是唤醒摘要加 `units`（本方全部单位，同 list_units）和 `contacts`（同 get_contacts），整体过迷雾检查（10-31 起；之前只有摘要，没有全量单位表，P4 考核里 Kimi 的增援脚本 107 次运行都没触发） |
| `fires.py` | 炮击回报（第二届）：按本方炮兵弹药变化每游戏分钟汇总实际开火（单位、命令、目标、发数、弹种），本方事件 `fire_report`；下了炮击命令超过 `rules.FIRE_PENDING_SEC` 还没开火发一次 `fire_pending`。只看本方 |
| `scoring.py` | 计分视图（第二届）：VP 格归属与分值、本方兵力与损失、简报里的胜利条件；份额公式未定，回答带 `formula_known: false` |
| `server.py` | HTTP：`/relay/*`、`/agent/*`、`/admin/*`（裁判用：状态、暂停、结束） |
| `log.py` | 每类记录一个 jsonl：`events`、`wakes`、`queue`、`releases`、`audit`、`fog`、`clock`（真实时间 ↔ 游戏时间）、`scripts` |

### 5.2 agent 端点（都带 token）

| 端点 | 作用 |
|---|---|
| `GET /agent/wait?timeout=60` | 长轮询：空闲时等唤醒；返回唤醒摘要（≤ 6k token，内容见 10 号资料 2.6）和回合编号 `turn_id`、回合截止时刻 |
| `POST /agent/turn/begin` `{agent: "commander"|"<sub name>"}` | 子代理开始一项任务（指挥官的回合由唤醒开始） |
| `POST /agent/turn/end` `{turn_id, subscriptions?}` | `end_turn`；可同时改订阅 |
| `GET /agent/view/<what>` | `summary`、`units`、`unit/<name>`、`contacts`、`vp`、`terrain?hexes=...|box=...`、`briefing`、`queue`、`queue/<cmd_id>`（一条本方命令原样）、`score`、`fires`、`thinking`、`events?since=` |
| `POST /agent/queue` | 提交命令（3.2 的 kind），返回 `cmd_id` 与校验结果；结果带 `warnings`（如盲射：炮击目标格 `rules.FIRE_NEAR_HEXES` 格内没有在视接触），警告不拦命令 |
| `POST /agent/queue/lint` | 同样的校验，只回答 `errors` / `warnings`，不入队、不记违规 |
| `DELETE /agent/queue/<cmd_id>` | 撤回未放行的命令 |
| `POST /agent/observe` `{on: true|false}` | 观察模式 |
| `PUT /agent/scripts/<name>`、`DELETE ...`、`GET /agent/scripts` | 反射脚本 |
| `POST /agent/scripts/test` `{name | source, trigger?}` | 已登记的脚本或给出的源码，按当前态势空跑一次：命令只校验不入队，报警不唤醒，记忆不保留（没有 sandbox 时 501） |
| `POST /agent/compute` `{code, input}` | 计算任务 |
| `GET/PUT /agent/notes/<path>` | 工作区笔记 |

回合外（空闲）调用除 `wait` 以外的端点返回 409。活跃时长以 referee 墙钟为准：唤醒时刻起算，到 `turn/end` 或时限为止。

## 6. agent

- `python -I arena/agent/arena_agent.py --side N --route <route> --referee http://127.0.0.1:41010 --token-file <path> --run <run dir>`。
- 路线沿用 `tools/model_probe.py` 的 `ROUTES`（OpenAI 兼容 chat completions + tools），抽到 `arena/agent/routes.py`；Kimi 固定 `k3.1-pro`、思考 `high`；token 过期按 `model_probe.kimi_token` 的提示处理（不自己用 refresh token 续期）。
- 训练期（P4）的回合用另一份提示 `arena/agent/prompts/training.md`（训练裁判的简报带 `sides` 时选它）：讲正式赛两局换边与计分、训练期 12 小时、工作区带进演习和正式赛、唤醒只有三种、没排演习就不再被唤醒。演习裁判带 `--exercise`（导演传），简报多一段 `exercise`（不计分、节奏、对手、小实验单位），比赛提示的对手那一行据此写成“训练期的演习”，回合制时注明正式赛节奏不适用（10-31 起；之前训练和演习都用比赛提示，玩家 1 会被叫成“红方指挥官”，对 AI 演习会被说成“考核局”）。
- 两方同一份系统提示 `arena/agent/prompts/system.md`。第二届起模型只有一个工具 `arena`（参数 `cmd` 必填、`input` 可选多行），解析在 `arena/agent/cli.py`：名词 help、doc、brief、score、map、unit、order、fire、contact、event、script、note、exercise、agent、turn；单位名不分大小写、唯一前缀或子串即可，有歧义时列候选；格号 `2118` / `21,18` / `21 18`；拼错给“你是不是想说”；处处有 help 和例子；表格是纯文本，`--json` 给原始回答。基础文档 `arena/library/basics.md`（`arena doc show basics`）。第一届的 26 个工具（`arena/agent/tools.py` 的 SPECS）不再给模型，只留给 `cli.legacy()` 把脚本化测试里的旧调用翻成命令行。
- 子代理：同一进程内的另一条模型对话，同一个 `arena` 工具，但 `agent spawn` / `agent check` 会被拒；同时最多 2 个；每项任务 10 分钟上限。
- 记录：`<run>/agents/side<N>/transcript.jsonl`（每条请求与回复、工具调用与结果、token 用量、供应方回报的模型名）。
- `--route fake`：离线测试用的假模型，按脚本发工具调用，不联网。

## 6a. CLI harness（`arena/harness/`）

- 选手也可以由官方 harness 来当：蓝方 Kimi Code CLI（`kimi acp`），红方 opencode（`opencode acp`）。驱动程序 `python -I arena/harness/driver.py --side N --cli kimi|opencode [--model M] --referee http://127.0.0.1:41010 --token-file <path> --run <run dir>`；`tools/p3_rehearsal.sh` 设 `ARENA_HARNESS=cli` 时用它（路线参数变成 CLI 名，默认 kimi / opencode；`ARENA_HARNESS_MODEL0/1` 选模型），不设时行为不变。
- 驱动程序和 arena-agent 共用同一个循环（继承 `arena_agent.Agent`）：`/agent/wait` 长等；唤醒文字相同（`[唤醒 #n｜回合 id｜…｜本回合最多 N 秒]` + 子代理报告 + 态势）；作为一条 ACP `session/prompt` 发给本方指挥官会话；模型执行 `arena turn end` 结束；到裁判给的截止时间就 `session/cancel`，再按 `force_end` 关回合；下一次唤醒发进同一个会话。没调工具的回复按 arena-agent 的提示语催两次，第三次结束回合（`no_tool_call`）。
- 裁判工具经 MCP 暴露：每个会话一个 `arena/harness/mcp_server.py`（stdio），只列一个工具 `arena`（`cli.mcp_tool()`，和 arena-agent 同一份解析）；子代理执行 `agent spawn` / `agent check` 被拒。`turn end`、`agent spawn`、`agent check` 经驱动程序的本机控制端口（独立 token，协议不变，op 仍是 end_turn / spawn_subagent / check_subagents）处理，其他命令结果末尾照样加 `[本回合剩余 N 秒]`。
- 子代理：最多 2 个；派出时驱动程序开新会话（kimi：同一进程里的新会话；opencode：每个代理一个进程），先 `turn/begin`，首条与 arena-agent 相同；子代理 `arena turn end` 带的报告（`input` 或 `--report`）进收件箱，`arena agent check` 和下一次唤醒取走。
- 系统提示：`arena/agent/prompts/system.md` 按 arena-agent 的方法渲染，两边相同。kimi 放在会话工作目录的 `.kimi-code/agents/agent.md`（`override: true`，覆盖默认配置文件 agent，工具白名单只有 `mcp__arena__*`）；opencode 放在 `OPENCODE_CONFIG_DIR` 指向的本次配置目录里的 primary agent `arena`。CLI 自己还会加的几行：kimi 一条“自动权限模式”提醒；opencode 模型名、日期、工作目录几行。
- 内置工具全关，只按本次进程传，不改 `~/.kimi-code`、`~/.config/opencode`，不复制凭据：kimi 靠上面的白名单（wire 记录的工具快照只有 `mcp__arena__*`，第二届起只有 `mcp__arena__arena` 一个）；opencode 靠 `OPENCODE_CONFIG_DIR` 换掉全局配置目录，MCP 服务器写成 v2 形状 `mcp.servers.arena`、`codemode: false`（旧写法 `mcp.arena` 会丢掉这个开关，工具被放到 code mode 的 `execute` 后面），内置工具逐个 deny（`"*": "deny"` 会连 MCP 工具一起去掉）。两个 CLI 都以 `env -u KIMI_API_KEY` 的环境启动；kimi 关自动更新。
- 模型：kimi `kimi-code/k3.1-pro`、思考 `high`（`session/set_config_option` 按会话设）；opencode 默认 `opencode-go/deepseek-v4.1-flash`，备用 `proxy/deepseek-v4.1-flash`（任意 OpenAI 兼容端点，地址和 key 由环境变量 `ARENA_PROXY_BASE_URL` / `ARENA_PROXY_API_KEY` 按引用传入）。
- 记录：`<run>/agents/side<N>/transcript.jsonl`（驱动事件 + 每条 ACP 消息，含全部 `session/update`）、`mcp.jsonl`（每次工具调用：参数、结果、HTTP 状态、耗时）、`usage.jsonl`（供应方 token 用量，每次模型调用一行：kimi 读会话 wire 文件的 `usage.record`，opencode 只读打开它的 `opencode.db`）、`<cli>.stderr`；CLI 工作目录 `<run>/cli/side<N>/`。结束时按 PID 关 CLI（先关 stdin，再只结束该 PID），并记录子进程是否都已退出。
- 测试：`arena/tests/test_harness.py`（MCP 服务对真裁判：initialize、tools/list、tools/call、回合外 409；驱动对假 CLI `arena/tests/fake_acp_cli.py`：两次唤醒、子代理报告、截止取消、三次不调工具）；联调 `tools/harness_check.py <run>`（真 kimi + 真 opencode，FakeGame 按录像推进到第 30 游戏分钟，另做内置工具三项检查）。

## 7. sandbox

- `fpc/native/build.sh` 加了 `lua.exe` 目标（同一份 Lua 5.4.3 源码，静态链接，不依赖 DLL），放到 `arena/sandbox/bin/`（不进 Git）。
- `arena/sandbox/host.lua`：一行一个请求（runner 写的 Lua 数据字面量，空环境加载），一行一个 JSON 回复。代码在每次新建的环境里跑，只有 `string table math utf8` 的副本和安全基础函数，没有 `io os package debug load require coroutine`；共享的 `string` 库去掉 `dump`，`string.rep` 限 16 MB；`getmetatable` 只对表有效，`setmetatable` 拒绝 `__gc`。计数钩子每 1000 条指令查一次指令数、墙钟、Lua 堆（限额的 75%），超限后 `pcall` 也吞不掉。
- 脚本能用：`view`、`events`、`trigger`、`memory`（持久表，经 JSON 往返）、`submit(body)`（同 `/agent/queue`，每次最多 100 条）、`alarm(text)`、`print(...)`。计算任务读 `input`，`return` 结果（JSON ≤ 256 KB）。
- `arena/sandbox/runner.py`：`ScriptHost` 每方一个常驻宿主，作业对象限进程内存（64 MB）、只许 1 个进程、作业关闭即结束；每次调用墙钟看门狗 = 限额 + 1 秒，卡在 C 函数里不返回就结束进程，下次调用自动重启。`compute()` 每个任务新起一个宿主，作业对象限 CPU 时间（10 秒）和内存（256 MB）。
- 测试：`arena/tests/test_sandbox.py`（可达性、指令/时间/内存三种超限、pcall 吞不掉、卡死重启、`__gc`、字符串往返、计算任务限额）。

## 8. 测试

| 层 | 做法 | 通过条件 |
|---|---|---|
| 单元 | `python -I -m unittest discover arena/tests` | 全过 |
| 离线整局 | `arena/tests/test_offline_game.py`：用 `$ARENA_DATA/scenarios/alamo-at-soest` 的开局数据造一个假游戏（单位按脚本移动、交火、发现、下令阶段轮流），假 relay 调 referee，两个 `--route fake` 的 agent | 思考用时与回合时长对得上；迷雾断言零失败；命令只在本方下令阶段放行；脚本超限按规则作废；整局跑完 |
| 实机冒烟 | 游戏机双人模式 30 游戏分钟，两个写死的简单代理 | 下令阶段放行、F9 推进、实时闸比值、`LUA>` 稽核全对上 |

## 9. P4 训练期（10 号资料 P4、2.10）

### 9.1 演习选项（`python -I arena/referee ...`，正式比赛不带这些参数，行为不变）

| 参数 | 作用 |
|---|---|
| `--pacing formal|turn` | `turn`：回合制演习。只在本方下令阶段唤醒（其他事件只进事件流）；没有实时闸；每个本方下令阶段一个回合，限时 `TURN_ORDERS_SEC`（180 s，结束原因 `turn_time`）；回合结束或到点才放行、按 F9 |
| `--agents 0,1` | 由模型下令的方；另一方交游戏 AI（`StyleOfPlay` 由导演定）。非玩家方不唤醒、不放行、端点一律 403 |
| `--prematch-sec` | 部署时限；演习用 `EXERCISE_PREMATCH_SEC`（600 s） |
| `--units0/--units1` | 小实验：本方只许指挥这些单位（名单来自 `$ARENA_DATA/scenarios/<想定>/units.json`，最多 `EXERCISE_SMALL_UNITS` 个；`arrives_later` 的后到单位可以列，只是不一定在实验时间内到场）。其余单位在部署批和每个本方下令阶段由 referee 下 Hold；对名单外单位下令 400 `unit not in this experiment`，不计违规 |
| `--workspace0/--workspace1` | 一方工作区目录（笔记与脚本）；训练、演习、正式比赛用同一个目录。脚本存成 `scripts/<name>.json`，referee 启动和每次加载时重新登记；笔记不能写到 `scripts/` 下 |
| `--batch-prefix l<N>-` | 放行批号前缀；两条演习线共用游戏机的 `ai-arena\in`，批号不能撞 |

演习结束时 `<run>/final.json` 之外还写 `<run>/exercise-report.json`：想定、节奏、玩家方、实验单位、`ended_by`（`game_end` / `stopped` / `game_gone`）、游戏秒与时刻、墙钟、两方损失（每单位开局与当前子单位数、状态）、VP 格归属（0 / 1 / null，referee 按单位经过推算）、`official`（导演从终局截图填，读不到为 null）；不是游戏终局时 `note` 写明“按停表时的局面”。

### 9.2 训练裁判（`--mode training`）

- 不连游戏、没有 relay。两个玩家用 `side0.token` / `side1.token`（玩家 0 = A、1 = B）。只有三种事件：`training_start`（首次 wait）、`exercise_done`（结果或失败回来）唤醒；`exercise_refused` 只进事件流（拒绝原因在 409 回答里）。每回合 `TRAINING_TURN_SEC`（20 分钟）。
- 回合内能用：`view/briefing`（赛用想定两方简报与两方单位名单）、`view/terrain`、`view/summary`；笔记、脚本（只存不跑，每回合开始重新加载）、计算任务（整个训练期合计 `PREMATCH_COMPUTE_CPU_S` CPU）。
- `POST /agent/exercise` `{type: full|small, opponent: ai|self, play_side, pacing?, units?}`：校验错 400（对 AI 只能 `play_side: 0`，游戏只能让内置 AI 指挥红方）；配额或已有排队 / 进行中的演习 409（同时记 `exercise_refused`）。受理后回合结束（回答带 `turn_over: true`），编号 `p<玩家>-<序号>`。`GET /agent/exercises`：本人的全部演习与剩余配额。
- 配额（每人）：全套 `EXERCISE_FULL_MAX` 次（失败的不算）、小实验合计 `EXERCISE_SMALL_WALL_SEC` 墙钟（按导演报的每次墙钟累计），剩余不足 `EXERCISE_SMALL_MIN_LEFT_SEC`（15 分钟）不再受理小实验。
- 导演端点：`GET /admin/exercises`（全部演习、配额、是否结束、两玩家的回合数 / 是否在回合中 / 是否有待发唤醒）、`POST /admin/exercise_update` `{id, status: running|done|failed, report?, wall_sec?, error?}`、`POST /admin/stop`（训练结束：排队的记失败，之后的结果只记录不唤醒）。`<run>/exercises.json` 每次变化重写，是训练期的总记录。
- agent 工具：`request_exercise`、`list_exercises`（`arena/agent/tools.py`，官方 CLI 的 MCP 也用这两个定义）。

### 9.3 导演 `tools/p4_training.py`

- `python -I -X utf8 tools/p4_training.py --run $ARENA_DATA/runs/p4-<时间> --route0 kimi-code --route1 deepseek-proxy --hours 12`。确定性程序，不经模型；每 15 s 读一次 `/admin/exercises`。
- 训练裁判端口 41020，工作区 `<run>/workspace/player<N>`。玩家进程由 `start_player(side_or_player, route, run_dir, referee_url, token_file, mode)` 起：`kimi-code`、`deepseek-proxy` 两条路线走官方 CLI（`arena/harness/driver.py --cli kimi|opencode`，同正式赛），其他路线（dry run 的 fake）走 `arena_agent.py`；训练回合 `--turn-limit 1200`。
- 一个玩家一条线、两条线并行：玩家 0 用演习裁判 41011、Telnet 47823，玩家 1 用 41012、47824。每次演习的目录 `<run>/ex/<id>/`：演习裁判（9.1 的参数，自对弈两方都指向该玩家的工作区）→ 玩家进程（自对弈两个）→ 按 `tools/p3_rehearsal.sh` 的顺序在游戏机上跑：relay token 换成本次演习的文件名推过去、经游戏机适配器（`tools/gamehost.py` 或 `ARENA_GAMEHOST`）spawn relay、`p2_stopper`（全套：打到终局、90 分钟上限；小实验：60 游戏分钟、上限 = 该玩家剩余小实验时间）、wait、pull、删远端 token、按 PID 停本机进程 → 有终局截图就跑 `tools/fpc_endscreen.py` 填 `official` → `exercise_update`。
- relay 参数：`StyleOfPlay` 自对弈 3（带 `-config arena-h2h.ini`）；对 AI 不带 `-config`、`StyleOfPlay` 0（游戏机10-30 实测：不带 `-config` 时命令行 0 / 1 / 3 都让游戏 AI 指挥 2 号玩家，带任何 `-config` 两方都是人）；回合制 `PassDeferSec` = `TURN_ORDERS_SEC` + 20（交接对话框等玩家下完令），formal 20。
- 游戏启动串行：relay 的 `ai-arena\in\run.lua` 加载时只读一次、调试日志按开局分钟命名（P0.7），所以下一次启动要等上一局发来第一份态势且距上次启动 ≥ 180 s；300 s 没有态势记失败。
- 两个玩家都结束了回合、手上没有排队 / 进行中的演习、也没有待发唤醒（连续两次轮询）时训练提前结束（`players_finished`）：这时谁也不会再被唤醒。
- 到 `--hours` 或提前结束：先停训练裁判（不再唤醒），再停进行中的演习（照常出报告），最后按 PID 停玩家和训练裁判。每一步一行 `<run>/director.jsonl`。
- `--dry-run`：不连游戏机，假 relay 在本机打三分钟假局，脚本化假玩家各要一次演习（玩家 0 对 AI 小实验、玩家 1 自对弈全套），两局都完就结束；`arena/tests/test_director.py` 跑它。
