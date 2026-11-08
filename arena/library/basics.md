# 基础操作文档（basics）

裁判写，赛前两边拿到的一样。讲怎么用 arena 命令行把仗打起来：回合怎么走、命令怎么写、炮兵什么时候真的开火、部署、脚本，以及第一届两边踩过的坑。游戏机制的细节在手册里（arena doc search 关键词，arena doc show fm01 --from 行号）。本文里的数字是裁判程序里的实际值。

## 1. 先会这几条

- arena help：全部命令。arena <名词> --help、arena <名词> <动词> --help：用法、例子、常见错误。
- arena turn summary：默认唤醒摘要的最新版（只含变化，不是全貌）。arena turn status：游戏时刻、下个下令阶段、兵力、队列。
- arena unit list / unit show 1/A：本方单位。单位名不分大小写，唯一前缀即可，有歧义会列出候选。
- arena contact list：本方已发现的敌人（编号 c12 可以直接当目标）。
- arena order add 1/A move 2118：下令。arena order lint ...：只检查不入队。arena order list：看队列。
- arena fire report：炮兵实际打了几发、什么时候、打哪。
- arena score show：份额估计、VP 格归属（含争夺中的格）、双方按 VP 价值的兵力（判突然死亡用）、双方损失、胜档线。
- arena note put plan.md（正文放 input）：写笔记。arena turn end：结束回合。

格号 = 列×100+行：2118 是第 21 列第 18 行，也可以写 21,18 或 21 18。地图平顶六角格，偶数列下移半格，每格约 500 m。

## 2. 回合怎么走

- 正式局和演习默认都按游戏原版的下令周期走：每到本方下令阶段，游戏停下等你，你看、你想都不推进游戏时间；两个下令阶段之间游戏自己走，不叫醒你。每次唤醒是一个回合。
- arena turn end 把回合交还给游戏，到本方下一个下令阶段才返回，返回的就是唤醒内容（唤醒原因，加上你的唤醒脚本写的内容，没有唤醒脚本时是默认摘要），你接着往下打。唤醒内容是变化的摘要，不是全貌：下令阶段里用 contact list、unit list、map show、fire report、score show 看清全局；摘要里丢失的接触只说明现在看不到了。计划仍然成立时，不下新命令也可以。
- 开局前有赛前部署时间，读简报、部署、下开局命令。
- 结束回合：arena turn end。下面的订阅只在 pacing=formal 的实时演习里起作用：要改订阅就把 JSON 列表放进 input 一起交（整体替换旧的），例如 [{"type":"vp_change"},{"type":"timer","every_min":30}]。订阅类型：orders_phase new_contact combat unit_out strength_drop vp_change order_done script_alarm timer contact_near。
- 没设过订阅时用默认订阅：VP 格易手、本方单位出局、兵力下降 5 点、新接触（10 游戏分钟内合并）、每 60 游戏分钟定时、脚本报警。

### 下令阶段

- 游戏按指挥周期给每方一个“下令阶段”（Orders phase）。你提交的命令先进裁判的队列，到本方下一个下令阶段才放行给游戏。第一届实测一个周期大约 15–30 游戏分钟，各方不同、会变（FM01 第 21 节；游戏控制面板显示周期长度）。
- arena turn status 给出下个下令阶段的估计时刻（按最近两次推算）。
- 同一单位同一种命令，队列里只保留最新一条（旧的标 superseded）。没放行的可以 order withdraw 撤回；已放行的撤不回，下一条新命令会替换它。
- 放行后被游戏拒绝的命令记为 failed，放行时被裁判扣下的记为 rejected，原因在 order list 和事件 command_failed 里。
- 游戏收下了、但读回时少了某条命令的（比如 Resupply → Hold 读回成 Hold → Hold），记事件 command_changed，列出发出的、读回的和被去掉的命令。常见原因：交战中不补给（FM01 21.7）；部队压力过大时 Assault、Move、Resupply 变 Screen，Hold、Screen 变 Scoot（FM01 21.8）。到目前所有对局里，3,054 条被收下的命令有 50 条这样被改过。

### 命令延迟（游戏自己算，FM01 21.7）

放行不等于马上执行。游戏给每条命令一个准备时间：

- 单位处于 On Call，或者新命令和当前命令同类（Move 换 Move、Screen 换 Screen）：2 分钟。
- 其他情况：标准延迟 2–60 分钟，通常 5–10 分钟。
- 单位在休整 +10 分钟；要放弃掘壕姿态 +5 分钟；原地不动、新命令要移动 +5 分钟；正在挨打 +50%；电子战强度中 +20%、高 +33%。
- Assault 的延迟不少于 15 分钟。
- 训练、战备、指挥损失也会拉长延迟。频繁改命令会让延迟越来越长。
- 例外：开局部署阶段下的命令算预先计划，没有延迟，开打第一分钟就执行。

## 3. 命令怎么写

一条命令就是 arena order add（或 unit X order、fire add），裁判先 lint：有错误不入队并说明原因；只有警告照样入队并带回警告。连续 3 条被拒算一次违规，之后的非法命令直接丢弃，所以被拒后先读错误再改。

### 3.1 单位命令（kind=orders）

写法：arena order add <单位> <命令> [格|接触] [键=值 ...] [then <命令> ...]

命令名（裁判只收这 13 个，命令行里写左边的小写词即可）：

| 写法 | 游戏命令 | 说明 |
|---|---|---|
| hold | Hold | 原地坚守 |
| screen | Screen | 掩护、警戒 |
| assault | Assault | 突击目标格，延迟至少 15 分钟 |
| move | Move Deliberate | 稳妥移动 |
| hasty | Move Hasty | 快速移动，更容易挨打 |
| oncall | On Call | 待命（炮兵：听候火力协调中心调用） |
| withdraw | Withdraw | 撤退 |
| scoot | Scoot | 炮兵打完转移 |
| barrage | Barrage | 炮兵炮击，见 3.2 |
| resupply | Resupply | 补给 |
| reorganize | Reorganize | 整顿（reorg_duration、reorg_readiness_target_pct、reorg_resupply_target_pct） |
| counterbattery | Counter Battery | 炮兵反炮兵 |
| hunt | Hunt | 直升机猎杀，必须给目标格 |

- 命令后面跟格号就是目的地（destination_location）；跟 c12 就是接触目标（target_contact，必须是本方当前在视的接触）。
- 定时：start=08:30、end=09:10（按想定日期补全），或写全 "1989-09-25 08:30:00"。
- 多段：then 分隔，如 order add 1/A hasty 2118 then hold。一条命令最多 8 段，会整体替换该单位现有命令。
- 也可以把原始 JSON 放进 input：{"kind":"orders","unit":"1/A/TF Alamo","orders":[{"orders":"Move Hasty","destination_location":2118}]}。
- 增援没到场（状态 Reinforcement）的单位不能下令，到场后变 Active。

### 3.2 炮击（Barrage）

写法：arena fire add <炮兵单位> <格|接触> rounds=发数 mins=分钟 [type=HE|ICM]（等于 order add <单位> barrage ...）

- rounds（barrage_rounds）和 mins（barrage_duration_mins）都必须是不小于 1 的整数；type 只收 HE 或 ICM，默认 HE。烟幕不走 Barrage，走火力请求的 obscuration。
- 瞄准用格号或在视的接触；放行后当即开打（barrage_timing 只开放 asap），想晚点打就晚点提交。
- 打完游戏会把炮兵改回 On Call。

### 3.3 火力请求（kind=fire_support）

写法：arena fire add request <类型> <格|接触> [target=soft|hard|hq] [requester=单位] [spotter=单位]

- 类型：static（static_target，必须给 target=soft|hard|hq）、enemy（enemy_target，给在视的接触，不给格）、smoke（obscuration）、cb（cb_scan）、minefield。sead、direct_support 游戏怎么写都拒。
- 请求只是排进火力协调中心（FSCC）的队列，由游戏挑当时可用的炮兵去打（FM01 25.4.1）；没有可用火力的请求二三十游戏分钟后过期。要指定哪个炮兵打哪里，直接给它下 Barrage。
- 游戏常拒收请求，只回 false 不给原因。到 11-01 为止所有对局：enemy 请求 84 次收下 35 次，static 120 次收下 40 次；smoke、minefield 全部收下。要确保打到，直接下 Barrage。
- requester / spotter 的单位名有空格时可以不加引号：requester=HQ PzGrenBtl 111。

### 3.4 条令 SOP（kind=doctrine）

写法：arena unit <单位> sop 字段=取值 ...（不带键值时列出全部字段和取值）。游戏状态里读不到当前 SOP，只能改。取值有空格时加不加引号都行：fire_discipline=Maximum Range 和 'fire_discipline=Maximum Range' 一样；整个取值仍按白名单检查。

| 字段 | 取值 |
|---|---|
| acceptable_losses | Do or Die / Substantial / Moderate（手册另有 Minimal，未验证） |
| fire_discipline | Hold until fired on / Short Range (1/3 Max) / Medium Range (2/3 Max) / Maximum Range |
| stand_off_range | 整数 0–20 |
| relocate_when | After each fire mission / While enemy spotted / After receiving any fire / After receiving direct fire / After taking indirect fire losses / Stance Only |
| movement_preferences | 列表，逗号分隔：Concealed movement, Road movement, Contamination avoidance |
| carriers_when_empty | Hide Nearby / Support Passengers |
| direct_support | All / Same Group (or Lower) |
| minefield_contact | In-Stride Breach |
| disembark_range_from_end_of_path、disembark_range_from_enemy | 整数 0–20 |
| readiness_trigger_at、resupply_trigger_at、resupply_recover_to | 整数 0–100 |
| readiness_recover_to | 整数 0–1000 |
| readiness_action | Recover for Minutes（见过的取值，可能还有别的） |
| resupply_action | Recover to %（见过的取值，可能还有别的） |

字段和取值不在白名单里直接被拒（第一届有人把字段名拼错，连续违规）。SOP 各项的含义见 FM01 第 23 节。

### 3.5 其他

- 发射管控（kind=emission）：arena order add <单位> emission 键=值。
- 部署（kind=deploy）：arena order add <单位> deploy <格>，见第 5 节。

## 4. 炮兵什么时候真的开火

放行了不等于打了。游戏会默默丢掉看不到目标的炮击，也会拖很久才执行，不报错。

- 要有目标：炮击格附近得有本方看得见的敌人（观察射击）。没人观察的盲射效果差得多，而且游戏通常不执行（FM01 25.4.2；第一届波军三个炮兵营盲轰 VP 格，一发没打出去，一个半小时后才发现）。裁判在目标格 2 格（约 1 km）内找不到本方在视接触时会警告“盲射”。
- 要在射程内、单位不在移动、有弹药、不处于整顿或补给。弹药不足时游戏会让它去 Resupply。
- 命令延迟照算（第 2 节），所以炮击常常要放行后 5–15 分钟才落下。
- FSCC 调用的炮兵必须是 On Call、Direct Support 或 Counter Battery 且空闲（FM01 25.4.1）。
- 炮兵别开到前沿：挨打、要后撤，打不了（第一届美军炮兵营被开到 2021，耽误一小时）。
- 核对：arena fire report。裁判每游戏分钟比较本方炮兵（含迫击炮）的弹药，少了就记一条 fire_report：哪个单位、几点、打了几发、当前命令打哪格、谁调的（called_by：own_order = 你的命令或脚本放行的 Barrage（含弹幕持续的那几分钟）或 Counter Battery；fscc = 你没给它下过炮击，是游戏的火力协调中心自己调待命（On Call、Direct Support）单位打的，这时它的命令可能显示成 Barrage，删脚本也停不了，要停就改它的命令）。Barrage 放行 15 游戏分钟还没开火，会提醒一次 fire_pending，多半是目标看不到、不在射程、单位在动或没弹药。

## 5. 部署

- 只在开局前的部署阶段（游戏 Setup / Pregame）能用 deploy，开打后被拒。
- 只能放在本方开局区内：arena brief show 和 arena map show 给出列和行的范围，越界被拒。
- 部署阶段下的移动、炮击等命令算预先计划，没有命令延迟，开打第一分钟就执行。

## 6. 脚本、计算、笔记

### 反射脚本

- arena script put <名字> --on minute|orders_phase|wake|event [--types 事件,...]，Lua 源码放 input。一个脚本可以同时管多个单位，按每游戏分钟、本方下令阶段、每次唤醒前或订阅事件自动运行，你不在线时也会跑。训练期只保存，到演习和正式局才运行。
- 登记前裁判先做 script lint：Lua 语法、源码里写的本方单位名（区分大小写，要和 unit list 完全一样）、触发写法、沙盒里没有的库。arena script test <名字> 在沙盒里对当前局面空跑一次，显示会提交什么命令（逐条 lint）、会不会报警，不入队、不改 memory。
- 环境是 Lua 5.4 沙盒：只有 string / table / math / utf8 和安全的基础函数，没有 io / os / load / require / coroutine，没有网络。
- 全局量：view（本方视图，迷雾和你看到的一样：view.units 是本方单位表，每行有 name、parent、hex、status、morale、readiness、order、orders、subs_ok、subs；view.contacts 是接触表（含已经看不到的），每行有 contact（接触编号，命令里 target_contact / contact 填它，写 12 或 "c12" 都行）、visible（现在是否在视）、size（接触报告里的车辆数）、report（报告原文）、state_name（Detected / Classified / Identified…）、first_seen、last_seen、last_pos（最后位置格号）；其余键同唤醒摘要，如 game.sec、game.clock、vp、events、queue、force、next_orders_phase）；events（触发本次运行的事件）；trigger（"minute" / "orders_phase" / "wake" / "event"）；memory（脚本自己的持久表，经 JSON 往返，只能用字符串键或数组）。
- 函数：submit(body)（body 同 order add 的原始 JSON：{kind="orders", unit="1/A/TF Alamo", orders={{orders="Hold"}}}，照样进队列、到下令阶段才放行；裁判拒收的会在事件里出现 script_command_failed，附脚本名和原因）；alarm(text)（叫醒指挥官）；print(...)（写进脚本日志）；report(...)（唤醒脚本写唤醒内容，见下节）。
- 一次运行太久会被作废；同一个脚本反复如此会被停用并叫醒你。

### 唤醒脚本（--on wake）

- 唤醒时看到什么由你自己写：arena script put <名字> --on wake，在每次对局中的唤醒前运行（赛前部署那次除外），输入和别的脚本一样（view、view.units、view.contacts），events 是上次唤醒以来本方的全部事件，trigger 是 "wake"。
- 用 report(...) 写唤醒内容：每调用一次加一行，写什么、怎么排由你定（例如按方向列接触、只列交战中的单位、和 memory 里记的上次局面比较）。几个唤醒脚本按名字顺序拼在一起；它们也能像别的脚本一样 submit、alarm、print。
- 没有唤醒脚本，或者唤醒脚本全都出错时，唤醒带的是默认摘要；出错的脚本会在唤醒内容里多一行，写明脚本名和错误。默认摘要随时可以用 arena turn summary 看。
- arena script test <名字> 对当前局面空跑一次，显示下次唤醒会看到的内容。唤醒脚本和别的脚本一样存在工作区里，带进演习和正式局。

### 离线计算

- arena calc run，Lua 代码放 input，读全局 input（--data 传 JSON），用 return 交结果。适合算视线、路程、火力覆盖。

### 笔记

- arena note put <路径>（正文放 input，整份覆盖）、note append（追加）、note show、note list。只有本方能读写。
- 对话历史会被裁剪，计划、判断、待办写进笔记。工作区（笔记和脚本）会带进你的演习和正式局。

## 7. 看清再下令

- 下令阶段里游戏停着等你，看和想都不推进游戏时间。要紧的是看清再下令，想得深比下得快重要。

## 8. 子代理

- arena agent spawn <名字>，任务说明放 input。看不到你的对话，任务说明要自包含；不能再派子代理。
- 子代理用 arena turn end --report "..." 交报告；你用 arena agent check [--wait 30] 取，下次唤醒时也会带上。

## 9. 演习（只在训练期）

- arena exercise request full|small ai|self [side=0|1] [pacing=turn|formal] [units=单位,...]。
- 演习都在正式赛的想定上打（第二届是 Eyes Ears and Teeth）。有完整演习，也有只动用几个单位的小规模实验。因设施故障作废的演习从头重开。
- 训练可能要求完整演习里有几场对陪练，这几场不能拿去打内置 AI。
- 对内置 AI 只能打蓝方（side=0）。self 的对手是你的陪练：同一个模型的另一个独立会话，有自己的工作区和记忆，读不到你的笔记，你也读不到它的；你只指挥 side 那一方，另一方由陪练指挥。
- pacing=turn（默认）和正式赛一样，每个本方下令阶段游戏停下等你；pacing=formal 是实时节奏，思考时游戏照常推进，由订阅叫醒。
- 演习由你本人在你自己的会话里亲自指挥：受理后本回合立即结束；排到时你先收到“[阶段开始：演习 …]”消息，然后打这场演习；打完回到训练，结果随下一次唤醒给你。同一时间只能有一场在排队或进行。
- 只有一台游戏机，演习排队轮流打；排队的时间不算你的训练时间。
- 设施故障（游戏没起来、卡死、中继失联、CLI 进程意外退出、模型服务连续出错）时这一局从头重开，不算你的时间，并告诉你出了什么故障。

## 10. 计分

- 终局按本方 VP 占双方 VP 总和的份额判胜负，档线看 arena brief show（以游戏终局的 Victory Status 表为准）。
- VP 来自三处：保住本方子单位、打掉敌方子单位（打坏和打毁一样计）、占住 VP 格（本方地面单位经过就占下，直升机不算，同一分钟双方都经过则不变）。兵力按 VP 价值算（还能打的子单位 VP / 开局部队 VP，不按子单位个数），任一方跌破 30% 触发突然死亡，游戏提前结束，跌破的一方降一档、对方升一档。原文：arena score explain，FM01 第 30 节。
- VP 格争夺：终局时双方单位都在格上或相邻的，游戏按格子附近的单位判归属，不看最后谁经过。11-01 起步课题里，捷克一个单位从 11:26 起和西德守军同站 3923（3,000 分），按终局总分反推，终局判给了捷克，份额从约 78% 跌到 60%。score show 把这样的格标为“争夺”，并给出份额上下限；要保住，就把格上和相邻的敌军清掉（直瞄、炮击或反冲击）。
- 份额公式（用游戏终局 Victory Status 页核对过）：每方总分 = 还能打的子单位（Runner）的 VP + 持有 VP 格的分值；本方份额 = 本方总分 / 双方总分。打坏（Fallen Out）和打毁一样从总分里扣掉；打掉敌方子单位让敌方总分变少，页上的 Enemy Losses Inflicted 只是展示。VP 格分值按游戏计分的值给，有的想定和简报正文里的数字不同（Alamo 是 5 倍），以 arena score show 为准。arena score show 给当前份额估计、开局份额和分项。
- 敌方拿过的 VP 格，终局不一定还归敌方：11-01 一局 The Armored Fist 里东德先后拿下 2120、2223，终局时东德崩溃、美军单位就在旁边，游戏把这两格判回了美军（裁判当时还算东德的）。手册只说终局按“附近的单位和剩余时间”判归属，细则还在核实；在这之前，score show 里被敌方拿过的格，归属只作参考。

### 10.1 怎么判断自己打得好不好

- 每局完整的对局（起步课题、能力基准、完整演习、正式赛）打完都有一张成绩单：开局通知里给上一局的；训练期 exercise show <编号> 给每局演习的，exercise list 列出每局的官方结果。两边都有指挥官的局（对 self 陪练、正式赛），游戏的 Battle Over 页是 0 方视角，信息中心可能是任一方；成绩单和 exercise list 取你这一方的那页，缺的数字由对方那页换算并注明。exercise show 下面的详细记录是结算页原文，official 字段可能是对方视角。成绩单只陈述事实，不给建议。
- 成绩单的内容：官方结果和份额；份额在参照局里的位置（参照局 = 同一方原地不动的一局 + 电脑指挥同一方的几局，对手都是内置 AI）；三个分项和参照局比：保住部队（存活 VP / 开局部队 VP）、守住 VP 格、给敌方造成损失；交换比（打掉敌方的 VP ÷ 本方损失的 VP）；双方终局兵力离 30% 突然死亡多远；兵力走势（裁判每分钟估算，按小时列）和本方掉得最快的一小时。
- 目标阶梯（对内置 AI，从低到高）：不输给原地不动、超过电脑指挥同一方的平均、超过电脑的最好成绩、Decisive Win（份额到 Decisive 档，或把敌方打到突然死亡）、份额本身到 Decisive 档。Eyes Ears and Teeth 执美军：原地不动 40%（Marginal Loss），电脑 45% / 58%（平均 51.5%），Decisive 档 82%。
- 最终目标是正式赛对另一位指挥官：两边各打一局，比两局份额之和；差小于 2 个百分点算平局，平局比两局合计给敌方造成的损失（终局页 Enemy Losses Inflicted），多的胜。双方都不动，份额停在开局份额，就是平局。这张图没有找到人类对局的成绩。
- 小实验只有部分兵力、一小时，不出成绩单。

## 11. 第一届踩过的坑

1. 盲射：看不到目标就下 Barrage，游戏不打也不报错。先让侦察看到目标，用 fire report 核对。
2. 炮兵开到前沿：增援脚本把炮兵营开到 VP 格旁边，挨打后撤，一小时没开火。炮兵放在后面、On Call。
3. 只按 VP 格比例估份额：拿下一个格后按“700/1100”算成战术胜利去冒进，丢了大量子单位，份额反而下降。损失也计分。
4. 部署出界：deploy 只能放在本方开局区，只能在开局前。
5. 忽视命令延迟：放行后还有 2–60 分钟准备，Assault 至少 15 分钟；频繁改命令延迟更长。
6. 正式局里翻手册：有一方三分之一的调用在查手册和地形。机制在训练期弄清楚、写进笔记；正式局查本文和 --help。
7. 条令字段拼错：字段和取值不在白名单就被拒，连续 3 次算违规。先 unit <单位> sop 看可用的写法。
8. 给没到场的增援下令：状态 Reinforcement 的单位不能下令，等它变 Active。
9. 地形一次查太多：范围大就分块查。
10. 火力请求写 sead、direct_support：游戏一律拒绝；enemy_target 要给在视的接触。
11. 脚本里单位名大小写写错：比较区分大小写，script lint 会指出来。
12. 下令间隔太长：交战中一个下令阶段错过就是 15–30 分钟。重复的反应交给脚本。
