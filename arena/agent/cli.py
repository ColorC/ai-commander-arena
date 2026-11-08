"""The players' command line `arena` (materials/18 2.1): one tool with `cmd` and `input` instead of 26 verb_noun tools.

Grammar: `<noun> <verb> [arguments] [--options]`, e.g. `unit show 1/A`, `order add 1/A move 2118`; `input` carries the
multi-line text of a command (script source, note text, compute code, a subagent's task, raw JSON of a command or a
subscription list). Both ways a model reaches the referee use this module and so behave the same: arena-agent's own
loop (arena/agent/arena_agent.py) and the MCP server the official CLIs talk to (arena/harness/mcp_server.py).
Cli.run(cmd, input) -> Out: the text for the model (plain-text tables; `--json` gives the raw data), the HTTP status
of the referee call, and what the caller must do itself: `turn end`, `agent spawn`, `agent check` come back as
Out.driver = (op, args) with the op names of the old tools (end_turn, spawn_subagent, check_subagents), because the
caller owns the turn ids and the subagents; `turn observe` sets Out.observe; `note put/append` sets Out.note (the
notes digest arena-agent keeps across trimming).
Many ways in to the same thing: unit names in any case, by unique prefix, path segments or substring (an ambiguous name
lists the candidates and guesses nothing); hexes as 2118, 21,18 or 21 18; `unit X order ...` = `order add X ...` =
`fire add X ...` for a Barrage; ls / rm / cat style verb synonyms; a noun alone runs its default verb; a misspelt noun
or verb gets "did you mean". Every noun and verb has help (NOUNS: use, usage, arguments, examples, common mistakes).
Order checks are the referee's own (POST /agent/queue/lint, orders.Queue.lint); script lint runs here: the Lua syntax
in the sandbox's compile-only mode (arena/sandbox/runner.check_syntax) and the unit names against the side's units
(arena/referee/scripts.lint_source). legacy() turns an old tool call into a command (scripted fake players still
written with the old names).
"""
import difflib
import json
import os
import re
import shlex
import unicodedata
import urllib.parse
from dataclasses import dataclass

from arena.agent import tools
from arena.harness import scorecard
from arena.referee import rules as R
from arena.referee.scripts import ScriptError, check_trigger, lint_source
from arena.referee.world import hex_dist, hex_line

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BASICS = os.path.join(ROOT, "arena", "library", "basics.md")
WHITELIST = os.path.join(ROOT, "arena", "referee", "doctrine_whitelist.json")
TOOL_NAME = "arena"
MAX_TEXT = tools.MAX_RESULT_CHARS
TOOL_DESC = ("裁判命令行：名词在前、动词在后，cmd 例：\"help\"、\"unit list\"、\"unit show 1/A\"、\"order add 1/A move 2118\"、"
             "\"fire report\"、\"turn end\"。多行内容（脚本源码、笔记正文、计算代码、子代理任务、原始 JSON）放 input。"
             "不知道怎么写就 arena help，或 arena <名词> --help；基础操作文档 arena doc show basics。")


def schema():
    """The one tool, OpenAI function shape (arena-agent)."""
    return {"type": "function", "function": {"name": TOOL_NAME, "description": TOOL_DESC, "parameters": {
        "type": "object", "required": ["cmd"], "properties": {
            "cmd": {"type": "string", "description": "命令行，如 unit show 1/A；不用写开头的 arena"},
            "input": {"type": "string", "description": "可选的多行正文：脚本源码、笔记正文、计算代码、子代理任务、原始 JSON 命令或订阅列表"}}}}}


def mcp_tool():
    f = schema()["function"]
    return {"name": f["name"], "description": f["description"], "inputSchema": f["parameters"]}


@dataclass
class Out:
    text: str = ""
    status: int = None
    error: bool = False
    turn_over: bool = False
    observe: bool = None
    driver: tuple = None
    note: tuple = None


class CliError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.hint = hint


# ---------------------------------------------------------------------------------------------------------------------
# help registry: noun -> {what, default, verbs: {verb: {use, usage, args, ex, err}}}
# ---------------------------------------------------------------------------------------------------------------------
def V(use, usage, ex, err, args=()):
    return {"use": use, "usage": usage, "args": list(args), "ex": list(ex), "err": list(err)}


NOUNS = {
    "help": {"what": "命令总览；每个名词、动词都有帮助", "default": None, "verbs": {}, "usage": "help [名词 [动词]]",
             "ex": ["help", "help order", "help order add", "order --help", "fire add --help"],
             "err": ["名词在前、动词在后：写 unit list，不是 list units（后者也认，但会提示）"]},
    "doc": {"what": "基础操作文档（basics）和游戏手册 FM01–FM07、FAQ、快捷键", "default": "list", "verbs": {
        "list": V("列出所有文档和行数", "doc list", ["doc list"], ["文档名可以只写一部分，如 fm01、faq"]),
        "show": V("按行读文档；basics 默认整份，手册默认 100 行", "doc show <文档> [--from 行号] [--lines 行数]",
                  ["doc show basics", "doc show fm01 --from 6978 --lines 60"],
                  ["文档名有歧义（如 fm03 对应三份）会列出候选", "行号来自 doc search 的结果"],
                  [("文档", "basics、fm01、faq…（唯一的部分名即可）"), ("--from", "起始行，默认 1"), ("--lines", "行数")]),
        "search": V("按关键词找段落（空格分隔，不分大小写），命中关键词多的排前面", "doc search <关键词...> [--max N]",
                    ["doc search barrage spotter", "doc search victory points --max 5"],
                    ["手册是英文，用英文关键词", "结果给文件名和行号，再用 doc show 读全文"],
                    [("关键词", "空格分隔"), ("--max", "返回几段，默认 8")])}},
    "brief": {"what": "任务简报：胜利档线、VP 格、开局区、本方指挥部", "default": "show", "verbs": {
        "show": V("任务简报（训练期给双方的简报和单位表）", "brief show [--json]", ["brief show", "brief show --json"],
                  ["胜负按游戏终局的 Victory Status 表判，不按简报正文里的数字"])}},
    "score": {"what": "当前份额估计、VP 分项、VP 格归属；explain 讲计分原则", "default": "show", "verbs": {
        "show": V("当前份额估计与分项（存活兵力 VP + VP 格）、开局份额、VP 格归属、双方损失、胜利档线", "score show [--json]",
                  ["score show"], ["VP 格归属是裁判按地面单位经过推算的，和游戏地图上的旗标一致；终局以游戏为准",
                                   "份额按游戏 Victory Status 页的算法：本方总分 / 双方总分，总分 = 存活子单位 VP + 持有 VP 格分值"]),
        "explain": V("手册里的计分原则（FM01 第 30 节）", "score explain", ["score explain"],
                     ["只按 VP 格比例估份额会算错：损失子单位也计分（第一届有人因此冒进）"])}},
    "map": {"what": "地形：概览、查格、两格间直线经过的格", "default": "show", "verbs": {
        "show": V("不带参数：地图大小、开局区、VP 格；带范围：地形表",
                  "map show [<列0>-<列1> <行0>-<行1> | <格> --radius N | --box 列0,列1,行0,行1]",
                  ["map show", "map show 20-24 15-20", "map show 2118 --radius 2"],
                  ["范围大就分块查", "格号是 列×100+行：2118 = 第 21 列第 18 行"]),
        "hex": V("查一个或几个格的地形：高程、地表、道路、河流", "map hex <格> [<格> ...]", ["map hex 2118 2321", "map hex 21,18"],
                 ["格号写 2118、21,18 或 21 18 都行"]),
        "path": V("两格之间的直线经过哪些格及其地形（几何直线，不是寻路，也不是通视）", "map path <起点格> <终点格>",
                  ["map path 2118 2530"], ["这不是行军路线：游戏自己按条令和地形选路"])}},
    "unit": {"what": "本方单位：列表、详情、直接下令、SOP", "default": "list", "verbs": {
        "list": V("本方全部单位一览：位置、状态、兵力、当前命令", "unit list [--json]", ["unit list"],
                  ["状态 Reinforcement 是增援还没到，不能下令；到场后变 Active"]),
        "show": V("单个单位详情：状态、士气、战备、全部命令、逐车子单位和弹药", "unit show <单位>", ["unit show 1/A", "unit 1/A"],
                  ["单位名不分大小写，唯一前缀即可；有歧义会列出候选"], [("单位", "名字、唯一前缀或唯一片段，如 1/a、mtr、tf alamo")]),
        "order": V("给单位下命令（同 order add）", "unit <单位> order <命令> [格|接触] [键=值...] [then <命令> ...]",
                   ["unit 1/A order move 2118", "unit Mtr order barrage 2118 rounds=20 mins=5"],
                   ["新命令整体替换该单位现有命令", "命令到本方下一个下令阶段才放行"]),
        "sop": V("不带键值：列出可改的 SOP 字段和取值；带键值：改 SOP（同 order add <单位> sop ...）",
                 "unit <单位> sop [字段=取值 ...]", ["unit 1/A sop", "unit 1/A sop acceptable_losses=\"Do or Die\" stand_off_range=2"],
                 ["取值有空格时可以不加引号：fire_discipline=Maximum Range", "游戏状态里读不到当前 SOP，只能改", "字段和取值不在白名单就被拒"])}},
    "order": {"what": "命令队列：下令、检查、列表、详情、撤回", "default": "list", "verbs": {
        "add": V("提交一条命令进队列，先跑 lint：有错误不入队；只有警告照样入队并带回警告",
                 "order add <单位> <命令> [格|接触] [键=值...] [then <命令> ...]｜order add <单位> sop 字段=取值｜"
                 "order add <单位> emission 键=值｜order add <单位> deploy <格>｜order add request ...（同 fire add request）",
                 ["order add 1/A move 2118", "order add 1/A hasty 2118 then hold",
                  "order add Mtr barrage 2118 rounds=20 mins=5", "order add 2/D assault 2321 start=08:30",
                  "order add 1/A deploy 1520", "order add 1/A sop fire_discipline=\"Maximum Range\""],
                 ["命令名：hold screen assault move(=Move Deliberate) hasty(=Move Hasty) oncall withdraw scoot barrage "
                  "resupply reorganize counterbattery hunt", "Barrage 要 rounds= 和 mins=（发数、持续分钟，都 ≥1），type=HE|ICM 默认 HE",
                  "盲射（目标附近没有在视的接触）会被警告：游戏通常不执行", "部署只能在开局阶段、只能放在本方开局区内",
                  "也可以把原始 JSON（submit 的 body 或 orders 数组）放进 input"],
                 [("命令", "见下方常见错误第一条"), ("格|接触", "格号 2118 / 21,18 / 21 18；接触写 c12 或 #12"),
                  ("键=值", "rounds mins type area start end dest target 或游戏原名字段，如 barrage_rounds=20"),
                  ("then", "多段命令，依次执行")]),
        "lint": V("只检查不入队：和 order add 同样的写法，返回错误和警告", "order lint <同 order add>",
                  ["order lint Mtr barrage 2830 rounds=20 mins=5", "order lint 1/A deploy 3020"],
                  ["lint 不算违规次数；order add 被拒才算"]),
        "list": V("本方命令队列：编号、种类、单位、命令、状态、被拒原因", "order list [--json]", ["order list"],
                  ["status: queued 待放行 / released 已放行 / done 游戏收下 / failed 游戏拒绝 / rejected 放行时被裁判扣下"]),
        "show": V("一条命令的详情（你提交的原文和状态）", "order show <编号>", ["order show s0-c0003", "order show 3"],
                  ["编号可以只写尾数"]),
        "withdraw": V("撤回还没放行的命令；all 撤回全部待放行", "order withdraw <编号|all>", ["order withdraw s0-c0003", "order rm all"],
                      ["已放行的命令撤不回：下一条新命令会替换它"])}},
    "fire": {"what": "炮击和火力请求：下令、列表、实际开火回报", "default": "list", "verbs": {
        "add": V("炮兵单位炮击（Barrage 命令）或向火力协调中心（FSCC）提请求",
                 "fire add <炮兵单位> <格|接触> rounds=N mins=N [type=HE|ICM]｜fire add request <类型> <格|接触> [target=soft] "
                 "[requester=单位] [spotter=单位]",
                 ["fire add Mtr 2118 rounds=20 mins=5", "fire add B 3-133 c12 rounds=36 mins=6 type=ICM",
                  "fire add request static 2830 target=hard", "fire add request smoke 2020"],
                 ["看不到目标的盲射游戏通常不执行：先有在视的接触", "Barrage 放行就开打，想晚点打就晚点提交",
                  "请求类型：static(=static_target，要 target=soft|hard|hq) enemy(=enemy_target，给在视的接触) smoke(=obscuration) cb minefield",
                  "Barrage 会替换该炮兵现有命令"]),
        "list": V("队列里的炮击命令和火力请求", "fire list", ["fire list"], ["要看实际打了没有，用 fire report"]),
        "report": V("本方炮兵（含迫击炮）实际开火回报：何时、哪个单位、对哪格、几发；放行 15 游戏分钟还没开火的提醒",
                    "fire report [--json]", ["fire report"],
                    ["回报来自本方弹药变化，按游戏分钟汇总", "fire_pending 提醒多半是目标看不到、不在射程、单位在移动或没弹药"])}},
    "contact": {"what": "本方已发现的敌方接触", "default": "list", "verbs": {
        "list": V("接触表：编号、是否在视、识别程度、最后位置和时间、报告", "contact list [--visible]",
                  ["contact list", "contact list --visible"], ["只有本方发现过的；看不到的敌人就是看不到"]),
        "show": V("一个接触的详情", "contact show <编号>", ["contact show 12", "contact c12"], ["编号写 12、c12、#12 都行"])}},
    "event": {"what": "本方事件流", "default": "tail", "verbs": {
        "list": V("事件列表", "event list [--since 编号] [--type 类型] [--last N]",
                  ["event list --since 40", "event list --type fire_report"], ["--since 用事件编号，不是游戏秒"]),
        "tail": V("最近 N 条事件（默认 15）", "event tail [N]", ["event tail", "event tail 30"], ["更早的用 event list --since"])}},
    "script": {"what": "反射脚本和唤醒脚本：列表、查看、登记、删除、检查、空跑", "default": "list", "verbs": {
        "list": V("本方脚本：触发方式、是否启用、运行次数、连续超限次数", "script list", ["script list"], ["一个脚本可以同时管多个单位"]),
        "show": V("脚本源码和触发方式", "script show <名字>", ["script show watch"], ["名字只能小写字母开头，含小写字母、数字、下划线"]),
        "put": V("登记或替换脚本（源码放 input），先跑 script lint：语法错误或单位名写错就不登记（--force 照样登记）",
                 "script put <名字> [--on minute|orders_phase|wake|event] [--types 事件,...]",
                 ["script put watch --on orders_phase   （input 放 Lua 源码）", "script put guard --on event --types new_contact,combat",
                  "script put digest --on wake   （input 放 Lua 源码，用 report(文本) 写唤醒时要看的内容）"],
                 ["沙盒里没有 io / os / require / load", "submit 的 body 和 order add 的原始 JSON 一样（kind、unit、orders…）",
                  "单位名比较区分大小写，要和 unit list 一致",
                  "--on wake 的唤醒脚本在每次唤醒前运行，report 的文字就是唤醒内容（几个唤醒脚本按名字顺序拼接）；没有唤醒脚本时是默认摘要"]),
        "delete": V("删除脚本", "script delete <名字>", ["script delete watch", "script rm watch"], ["删了就没了，源码不留"]),
        "lint": V("检查脚本：Lua 语法（沙盒编译，不运行）、引用的单位名、触发写法、沙盒里没有的库",
                  "script lint [<名字>] [--on ...]（不给名字就检查 input 里的源码）", ["script lint watch", "script lint --on minute"],
                  ["lint 只做静态检查；想看实际会下什么命令用 script test"]),
        "test": V("在沙盒里对当前局面空跑一次：显示会提交的命令（逐条 lint）、报警、print 输出；不入队、不叫醒、不改 memory",
                  "script test [<名字>] [--trigger minute|orders_phase|wake|event]（不给名字就跑 input 里的源码）",
                  ["script test watch", "script test --trigger orders_phase", "script test digest   （唤醒脚本：显示下次唤醒会看到的内容）"],
                  ["训练期没有局面，不能空跑"])}},
    "note": {"what": "本方笔记（只有本方能读写；对话会被裁剪，长期要记的写这里）", "default": "list", "verbs": {
        "list": V("列出笔记", "note list [目录]", ["note list"], ["scripts/ 目录是脚本存档，用 script 命令改"]),
        "show": V("读一份笔记", "note show <路径>", ["note show plan.md"], ["路径最多 6 段，字母、数字和 . _ - 空格"]),
        "put": V("写笔记（整份覆盖），正文放 input", "note put <路径>", ["note put plan.md   （input 放正文）"], ["会覆盖原文，只想加一段用 append"]),
        "append": V("在笔记末尾追加一段（正文放 input）", "note append <路径>", ["note append log.md   （input 放要追加的内容）"],
                    ["笔记不存在时新建"])}},
    "calc": {"what": "离线计算任务（Lua 沙盒，算视线、路程、火力覆盖等）", "default": "run", "verbs": {
        "run": V("跑一段 Lua：读全局 input，用 return 交结果",
                 "calc run [--data JSON]（代码放 input；或 input 放 {\"code\":..., \"input\":...}）",
                 ["calc run --data '{\"a\":[1,2,3]}'   （input：local s=0 for _,v in ipairs(input.a) do s=s+v end return s）"],
                 ["忘了 return 就没有结果", "沙盒里没有 io / os"])}},
    "exercise": {"what": "演习（只在训练期）：申请、列表、详情", "default": "list", "verbs": {
        "request": V("申请一场演习；受理后本回合立即结束，排到时由你本人在这个会话里亲自指挥，打完回到训练，结果随下一次唤醒给你",
                     "exercise request <full|small> <ai|self> [side=0|1] [pacing=turn|formal] [units=单位,单位,...]",
                     ["exercise request full ai", "exercise request small self side=1 units=\"1/RAG,2/RAG\""],
                     ["对内置 AI 只能打蓝方（side=0）",
                      "self 的对手是你的陪练：同一模型的另一个独立会话，读不到你的笔记，你也读不到它的；你只指挥 side 那一方",
                      "small 要列出动用的单位", "一次排一场",
                      "只有一台游戏机，演习排队轮流打；排队时间不算你的训练时间",
                      "设施故障时从头重开，并告诉你出了什么故障"]),
        "list": V("你的演习申请和结果", "exercise list", ["exercise list"], ["因设施故障作废的演习从头重开"]),
        "show": V("一场演习的详情和结果报告", "exercise show <编号>", ["exercise show p0-3"], ["编号见 exercise list"])}},
    "agent": {"what": "子代理：派出、查看", "default": "check", "verbs": {
        "spawn": V("派一个子代理做一项独立任务（同一模型、独立对话、看不到你的对话）；任务放 input 或写在名字后面",
                   "agent spawn <名字> [任务...]", ["agent spawn scout   （input 放任务说明）"],
                   ["子代理不能再派子代理", "任务说明要自包含"]),
        "check": V("子代理状态，取走已完成的报告；--wait N 等 N 秒", "agent check [--wait N]",
                   ["agent check", "agent check --wait 30"], ["报告下次唤醒时也会带上"])}},
    "turn": {"what": "回合：结束、状态、默认摘要、观察模式", "default": "status", "verbs": {
        "end": V("把回合交还给游戏，等到本方下一个下令阶段才返回，返回的就是唤醒内容；input 放 JSON 订阅列表可同时改订阅"
                 "（整体替换）；子代理用 --report 或 input 交报告",
                 "turn end [--report 文本]", ["turn end", "turn end   （input：[{\"type\":\"vp_change\"},{\"type\":\"timer\",\"every_min\":30}]）",
                                             "turn end --report \"北面两辆车\""],
                 ["已提交的命令保留", "唤醒内容由唤醒脚本（script put --on wake）决定，没有就是默认摘要",
                  "订阅类型：" + " ".join(sorted(R.SUB_TYPES)), "子代理必须交报告"]),
        "status": V("游戏时刻、终局还剩多久、下个下令阶段、兵力、队列", "turn status", ["turn status"],
                    ["下个下令阶段是按最近两次推算的估计"]),
        "summary": V("默认唤醒摘要的最新版（没有唤醒脚本时唤醒就带它）", "turn summary [--json]", ["turn summary"],
                     ["摘要只含上次醒来后的变化，不是全貌；全量看 unit list、contact list、map show"]),
        "observe": V("观察模式（只限指挥官）：回合保持打开，每游戏分钟一次更新；不调用工具的回复 = 等下一次更新",
                     "turn observe on|off", ["turn observe on", "turn observe off"], ["turn end 也会退出观察模式"])}},
}

VERB_ALIASES = {
    "ls": ["list"], "dir": ["list"], "all": ["list"], "cat": ["show"], "get": ["show"], "view": ["show"], "info": ["show", "status"],
    "read": ["show"], "open": ["show"], "find": ["search"], "grep": ["search"], "new": ["add", "put", "request", "spawn"],
    "create": ["add", "put", "request", "spawn"], "submit": ["add", "request"], "issue": ["add"], "give": ["add"],
    "rm": ["delete", "withdraw"], "del": ["delete", "withdraw"], "remove": ["delete", "withdraw"], "cancel": ["withdraw"],
    "write": ["put"], "save": ["put"], "set": ["put", "sop"], "edit": ["put"], "add": ["add", "append", "put", "request", "spawn"],
    "check": ["lint", "check"], "validate": ["lint"], "dryrun": ["test", "lint"], "exec": ["run"], "eval": ["run"],
    "start": ["spawn", "request"], "wait": ["check"], "done": ["end"], "finish": ["end"], "over": ["end"], "pass": ["end"],
    "stop": ["end"], "state": ["status"], "orders": ["order"], "doctrine": ["sop"], "line": ["path"], "terrain": ["show"],
    "last": ["tail"], "recent": ["tail"], "log": ["report", "tail"], "reports": ["report"], "fired": ["report"], "why": ["explain"],
    "rules": ["explain"], "observation": ["observe"], "watch": ["observe"], "summ": ["summary"], "brief": ["summary"],
}
NOUN_ALIASES = {"units": "unit", "orders": "order", "queue": "order", "cmd": "order", "command": "order", "commands": "order",
                "notes": "note", "scripts": "script", "events": "event", "contacts": "contact", "enemy": "contact",
                "docs": "doc", "manual": "doc", "library": "doc", "lib": "doc", "briefing": "brief", "mission": "brief",
                "vp": "score", "victory": "score", "terrain": "map", "hexes": "map", "artillery": "fire", "arty": "fire",
                "barrage": "fire", "exercises": "exercise", "drill": "exercise", "subagent": "agent", "subagents": "agent",
                "agents": "agent", "compute": "calc", "lua": "calc", "thinking": "turn", "summary": "turn", "status": "turn",
                "sop": "unit", "?": "help", "h": "help"}
# a noun alone, or with a target in place of the verb: the verb to use
TARGET_VERB = {"unit": "show", "contact": "show", "order": "show", "script": "show", "note": "show", "doc": "show",
               "exercise": "show"}
FLAGS = {"json", "help", "all", "visible", "force", "raw"}
EXAMPLES = ["arena doc show basics", "arena turn summary", "arena unit list", "arena order add 1/A move 2118",
            "arena fire report", "arena turn end"]

ORDER_WORDS = {"hold": "Hold", "screen": "Screen", "assault": "Assault", "attack": "Assault", "move": "Move Deliberate",
               "movedeliberate": "Move Deliberate", "deliberate": "Move Deliberate", "movehasty": "Move Hasty",
               "hasty": "Move Hasty", "oncall": "On Call", "withdraw": "Withdraw", "retreat": "Withdraw", "scoot": "Scoot",
               "barrage": "Barrage", "resupply": "Resupply", "reorganize": "Reorganize", "reorganise": "Reorganize",
               "reorg": "Reorganize", "counterbattery": "Counter Battery", "cb": "Counter Battery", "hunt": "Hunt"}
ORDER_KEYS = {"rounds": "barrage_rounds", "n": "barrage_rounds", "mins": "barrage_duration_mins",
              "min": "barrage_duration_mins", "minutes": "barrage_duration_mins", "duration": "barrage_duration_mins",
              "type": "barrage_type", "ammo": "barrage_type", "area": "barrage_area_fire", "timing": "barrage_timing",
              "start": "start_time", "at": "start_time", "from": "start_time", "end": "end_time", "until": "end_time",
              "dest": "destination_location", "to": "destination_location", "readiness": "reorg_readiness_target_pct",
              "supply": "reorg_resupply_target_pct", "reorg_mins": "reorg_duration"}
HEX_FIELDS = {"destination_location", "target_location"}
REQUEST_TYPES = {"static": "static_target", "statictarget": "static_target", "enemy": "enemy_target",
                 "enemytarget": "enemy_target", "smoke": "obscuration", "obscuration": "obscuration", "cb": "cb_scan",
                 "cbscan": "cb_scan", "minefield": "minefield", "mines": "minefield", "sead": "sead",
                 "directsupport": "direct_support", "ds": "direct_support"}
REQUEST_KEYS = {"target": "target_type", "requester": "requester_unit", "spotter": "spotter_unit", "mins": "duration_mins",
                "duration": "duration_mins", "projectile": "projectile_type", "ammo": "projectile_type", "type": "target_type"}
KIND_WORDS = {"sop": "doctrine", "doctrine": "doctrine", "emission": "emission", "emcon": "emission", "deploy": "deploy",
              "place": "deploy", "setup": "deploy"}
REQUEST_WORDS = {"request", "fscc", "support", "fs"}
CONTACT = re.compile(r"^(?:c|#|contact:?|接触)(\d+)$", re.I)


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------
def _width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _cell(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def table(rows, cols):
    """Plain-text table. cols: [(header, key or function of the row)]; the last column is not padded."""
    if not rows:
        return "（无）"
    cells = [[_cell(k(r) if callable(k) else r.get(k)) for _, k in cols] for r in rows]
    heads = [h for h, _ in cols]
    w = [max([_width(heads[i])] + [_width(c[i]) for c in cells]) for i in range(len(cols))]

    def line(vals):
        return "  ".join(v + " " * (w[i] - _width(v)) if i < len(vals) - 1 else v for i, v in enumerate(vals)).rstrip()
    return "\n".join([line(heads), line(["-" * min(x, 40) for x in w])] + [line(c) for c in cells])


def kv(d, keys=None):
    return "  ".join(f"{k}={_cell(d[k])}" for k in (d if keys is None else keys) if k in d and d[k] not in (None, "", [], {}))


def span(v):
    """[1, 29] -> 1–29"""
    return f"{v[0]}–{v[1]}" if isinstance(v, (list, tuple)) and len(v) == 2 else _cell(v)


def dur(sec):
    """754 -> 12 分 34 秒"""
    sec = int(sec or 0)
    return f"{sec // 60} 分 {sec % 60} 秒" if sec >= 60 else f"{sec} 秒"


OWNER = {"you": "本方", "enemy": "敌方", "none": "无主"}


def jdump(x):
    return json.dumps(x, ensure_ascii=False, indent=1, default=str)


def parse_json(text):
    if not isinstance(text, str) or not text.strip() or text.strip()[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_hex(tokens, i=0):
    """(hex, tokens used) at tokens[i]: 2118, 0412, 21,18, 21.18, 21/18, (21,18), or 21 18; (None, 0) otherwise."""
    if i >= len(tokens):
        return None, 0
    t = tokens[i].strip("()[]")
    m = re.match(r"^(\d{1,2})[,./](\d{1,2})$", t)
    if m:
        return int(m[1]) * 100 + int(m[2]), 1
    nxt = tokens[i + 1].strip("()[]") if i + 1 < len(tokens) else ""
    if re.match(r"^\d{1,2},?$", t) and re.match(r"^\d{1,2}$", nxt):
        return int(t.rstrip(",")) * 100 + int(nxt), 2
    if re.match(r"^\d{3,4}$", t):
        return int(t), 1
    return None, 0


def parse_contact(tok):
    m = CONTACT.match(tok or "")
    return int(m[1]) if m else None


def parse_value(v):
    low = v.lower()
    if re.match(r"^-?\d+$", v):
        return int(v)
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    return v


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def match_name(q, names):
    """Resolve a unit name the way a player types it -> (name, None) | (None, [candidates]) | (None, [])."""
    ql = q.strip().lower()
    if not ql:
        return None, []
    qs = ql.split("/")
    seg = lambda n: n.lower().split("/")  # noqa: E731
    tiers = [
        lambda n: n == q,
        lambda n: n.lower() == ql,
        lambda n: seg(n)[:len(qs)] == qs,
        lambda n: n.lower().startswith(ql),
        lambda n: len(seg(n)) >= len(qs) and all(a.startswith(b) for a, b in zip(seg(n), qs)),
        lambda n: ql in n.lower(),
        lambda n: _norm(q) and _norm(q) in _norm(n),
    ]
    for test in tiers:
        hits = [n for n in names if test(n)]
        if len(hits) == 1:
            return hits[0], None
        if hits:
            return None, sorted(hits)
    return None, []


def suggest(word, choices, n=3, cutoff=0.6):
    return difflib.get_close_matches(word, list(choices), n, cutoff)


def suggest_name(q, names, n=3, cutoff=0.6):
    """Unit names close to a misspelt one: compared with the whole name and with each of its parts (1/A/TF Alamo)."""
    q = q.lower()

    def score(name):
        parts = [name.lower()] + [p for p in re.split(r"[/\s]+", name.lower()) if p]
        return max(difflib.SequenceMatcher(None, q, p).ratio() for p in parts)
    ranked = sorted(((score(x), x) for x in names), key=lambda t: (-t[0], t[1]))
    return [x for sc, x in ranked if sc >= cutoff][:n]


def split_opts(tokens):
    """-> (positional tokens, {option: value or True}). --x=y, --x y, flags (FLAGS), -h."""
    pos, opts, i = [], {}, 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-h", "-?"):
            opts["help"] = True
        elif t.startswith("--") and len(t) > 2:
            k, eq, v = t[2:].partition("=")
            k = k.lower().replace("-", "_")
            if eq:
                opts[k] = v
            elif k in FLAGS or i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
                opts[k] = True
            else:
                opts[k] = tokens[i + 1]
                i += 1
        else:
            pos.append(t)
        i += 1
    return pos, opts


def tokenize(cmd):
    cmd = (cmd or "").strip()
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:                               # an unbalanced quote: plain words
        toks = cmd.split()
    if toks and toks[0].lower() in ("arena", "/arena", "mcp__arena__arena", "arena_arena"):
        toks = toks[1:]
    return toks


def resolve_noun(word):
    w = word.lower()
    return w if w in NOUNS else NOUN_ALIASES.get(w)


def resolve_verb(noun, word):
    verbs = NOUNS[noun]["verbs"]
    w = word.lower()
    if w in verbs:
        return w
    for cand in VERB_ALIASES.get(w, []):
        if cand in verbs:
            return cand
    return None


# ---------------------------------------------------------------------------------------------------------------------
# help text
# ---------------------------------------------------------------------------------------------------------------------
def help_text(noun=None, verb=None):
    if noun is None:
        lines = ["arena 命令行：arena <名词> <动词> [参数] [--选项]；多行内容放 input。先读 arena doc show basics。",
                 "arena help <名词>、arena <名词> --help、arena <名词> <动词> --help 看细节；--json 给原始数据。", "", "名词："]
        for n, d in NOUNS.items():
            vs = " ".join(d["verbs"]) or "[名词 [动词]]"
            lines.append(f"  {n:<9}{d['what']}：{vs}")
        lines += ["", "最常用的："] + [f"  {e}" for e in EXAMPLES]
        lines += ["", "单位名不分大小写、唯一前缀即可（1/a = 1/A/TF Alamo）；格号写 2118、21,18、21 18 都行；接触写 c12。"]
        return "\n".join(lines)
    d = NOUNS[noun]
    if noun == "help":
        return "\n".join([f"help：{d['what']}", f"用法：{d['usage']}", "例子："] + [f"  arena {e}" for e in d["ex"]] +
                         ["常见错误："] + [f"  - {e}" for e in d["err"]])
    if verb is None:
        lines = [f"{noun}：{d['what']}" + (f"（只写 {noun} = {noun} {d['default']}）" if d.get("default") else ""), "动词："]
        for v, h in d["verbs"].items():
            lines.append(f"  {v:<9}{h['use']}")
            lines.append(f"  {'':<9}用法：arena {h['usage']}")
        lines += ["例子："] + [f"  arena {h['ex'][0]}" for h in d["verbs"].values()]
        lines.append(f"每个动词的参数和常见错误：arena {noun} <动词> --help")
        return "\n".join(lines)
    h = d["verbs"][verb]
    lines = [f"{noun} {verb}：{h['use']}", f"用法：arena {h['usage']}"]
    if h["args"]:
        lines += ["参数："] + [f"  {a}：{b}" for a, b in h["args"]]
    lines += ["例子："] + [f"  arena {e}" for e in h["ex"]]
    lines += ["常见错误："] + [f"  - {e}" for e in h["err"]]
    return "\n".join(lines)


def format_driver(op, text):
    """The driver's answers (end_turn / spawn_subagent / check_subagents, JSON) as plain text."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(d, dict):
        return text
    if op == "check_subagents" and "subagents" in d:
        lines = ["子代理：" + ("、".join(f"{s['name']}（{'进行中' if s['state'] == 'running' else '已结束'}，{s['seconds']} 秒）"
                                       for s in d["subagents"]) or "无")]
        for r in d.get("reports") or []:
            lines += [f"[子代理 {r['name']} 的报告｜{r.get('reason')}]", str(r.get("report"))]
        if not d.get("reports"):
            lines.append("暂无新报告")
        return "\n".join(lines)
    if d.get("ok") is False or "error" in d:
        err = d.get("error")
        return "错误：" + (err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)) + \
            (f"（HTTP {d['http_status']}）" if d.get("http_status") else "")
    if op == "end_turn":
        run = d.get("subagents_still_running")
        if run is None:
            return "任务已结束，报告已交给指挥官。"
        return "回合已结束，等下一次唤醒。" + (f"仍在运行的子代理：{'、'.join(run)}" if run else "")
    if op == "spawn_subagent":
        return f"已派出子代理 {d.get('name')}；用 arena agent check 取结果"
    return text


# ---------------------------------------------------------------------------------------------------------------------
# docs: basics.md + the manuals
# ---------------------------------------------------------------------------------------------------------------------
class Docs:
    def __init__(self, library):
        self.lib = library
        self.basics = tools.Library(os.path.dirname(BASICS), exts=(".md",))

    def names(self):
        out = [("basics", "基础操作文档（裁判写，两边相同）", len(self.basics.lines("basics.md")) if os.path.isfile(BASICS) else 0)]
        try:
            out += [(f, "游戏手册", len(self.lib.lines(f))) for f in self.lib.files()]
        except OSError:
            pass
        return out

    def which(self, name):
        if name.lower() in ("basics", "basic", "basics.md", "基础", "基础操作"):
            if not os.path.isfile(BASICS):
                raise CliError("基础操作文档还没有（arena/library/basics.md）")
            return self.basics, "basics.md"
        try:
            return self.lib, self.lib.resolve(name)
        except ValueError as e:
            raise CliError(str(e), "doc list 看全部文档")

    def search(self, query, n):
        hits = self.basics.scored(query) + self.lib.scored(query)
        hits.sort(key=lambda s: (-s[0], -s[1], s[2] != "basics.md", s[2], s[3]))
        return [tools.Library.hit(*h, len(query.split())) for h in hits[:n]]


# ---------------------------------------------------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------------------------------------------------
class Cli:
    def __init__(self, referee, library, sub=False, syntax_check=None):
        """referee: tools.Referee of the side; library: tools.Library of the manuals; sub: a subagent's command line (no
        agent spawn/check); syntax_check(source) -> {"ok", "error"} (default: the sandbox's compile-only mode)."""
        self.referee, self.sub = referee, sub
        self.docs = Docs(library)
        self.syntax_check = syntax_check if syntax_check is not None else _sandbox_syntax
        self._names, self._brief, self._date = None, None, None
        with open(WHITELIST, encoding="utf-8") as f:
            self.sop = json.load(f)

    # ---- entry ------------------------------------------------------------------------------------------------------
    def run(self, cmd, input=None):
        self.status, self.turn_over = None, False
        if input is not None and not isinstance(input, str):
            input = json.dumps(input, ensure_ascii=False)
        try:
            out = self._run(cmd if isinstance(cmd, str) else "", input)
        except CliError as e:
            out = Out("错误：" + str(e) + (f"\n提示：{e.hint}" if e.hint else ""), error=True)
        except tools.NET_ERRORS as e:
            out = Out(f"错误：连不上裁判：{type(e).__name__}: {e}", error=True)
        if out.status is None:
            out.status = self.status
        out.turn_over = out.turn_over or self.turn_over
        if out.status is not None and not 200 <= out.status < 300:
            out.error = True
        out.text = tools.clip(out.text, MAX_TEXT)
        return out

    def _run(self, cmd, input):
        toks = tokenize(cmd)
        pos, opts = split_opts(toks)
        if not pos:
            return Out(help_text())
        noun = resolve_noun(pos[0])
        if noun is None and len(pos) > 1 and resolve_noun(pos[1]) and resolve_verb(resolve_noun(pos[1]), pos[0]):
            noun, pos = resolve_noun(pos[1]), [pos[1], pos[0]] + pos[2:]      # "list units" -> unit list
        if noun is None:
            s = suggest(pos[0].lower(), list(NOUNS) + list(NOUN_ALIASES))
            s = list(dict.fromkeys(resolve_noun(x) or x for x in s))
            raise CliError(f"没有名词 {pos[0]!r}。" + (f"你是不是想说：{'、'.join(s)}？" if s else ""), "arena help 看全部名词")
        if noun == "help":
            return Out(help_text("help")) if opts.get("help") and len(pos) == 1 else self._help(pos[1:])
        rest = pos[1:]
        verb = resolve_verb(noun, rest[0]) if rest else None
        if verb is not None:
            rest = rest[1:]
        if opts.get("help"):
            return Out(help_text(noun, verb))
        if verb is None:
            if not rest:
                verb = NOUNS[noun]["default"]
                if verb is None:
                    return Out(help_text(noun))
            elif noun == "unit":
                return self._unit_first(rest, opts, input)
            elif noun in TARGET_VERB and self._is_target(noun, rest[0]):
                verb = TARGET_VERB[noun]                                       # `contact c12` = contact show c12
            else:
                raise self._no_verb(noun, rest[0])
        return getattr(self, f"_{noun}_{verb}")(rest, opts, input)

    @staticmethod
    def _is_target(noun, word):
        """Whether `<noun> <word>` names a thing to show rather than a misspelt verb."""
        if suggest(word.lower(), list(NOUNS[noun]["verbs"]), 1, 0.75):
            return False
        if noun == "order":
            return bool(re.match(r"^(s\d-)?#?c?\d+$", word, re.I))
        if noun == "contact":
            return parse_contact(word) is not None or word.isdigit()
        return True

    @staticmethod
    def _no_verb(noun, word):
        verbs = NOUNS[noun]["verbs"]
        s = suggest(word.lower(), list(verbs) + [a for a, c in VERB_ALIASES.items() if any(x in verbs for x in c)])
        s = list(dict.fromkeys(resolve_verb(noun, x) or x for x in s))
        return CliError(f"{noun} 没有动词 {word!r}。" + (f"你是不是想说：{'、'.join(f'{noun} {x}' for x in s)}？" if s else
                                                        f"可用：{' '.join(verbs)}"), f"arena {noun} --help")

    def _help(self, args):
        if not args:
            return Out(help_text())
        noun = resolve_noun(args[0])
        if noun is None:
            s = suggest(args[0].lower(), list(NOUNS))
            raise CliError(f"没有名词 {args[0]!r}。" + (f"你是不是想说：{'、'.join(s)}？" if s else ""), "arena help")
        verb = resolve_verb(noun, args[1]) if len(args) > 1 and NOUNS[noun]["verbs"] else None
        if len(args) > 1 and verb is None and NOUNS[noun]["verbs"]:
            s = suggest(args[1].lower(), list(NOUNS[noun]["verbs"]))
            raise CliError(f"{noun} 没有动词 {args[1]!r}。" + (f"你是不是想说：{'、'.join(s)}？" if s else ""), f"arena help {noun}")
        return Out(help_text(noun, verb))

    # ---- referee calls ----------------------------------------------------------------------------------------------
    def call(self, method, path, query=None, body=None, ok=(200,)):
        status, data = self.referee.call(method, path, query=query, body=body)
        self.status = status
        if tools.is_turn_over(status, data):
            self.turn_over = True
        if status not in ok and not 200 <= status < 300:
            err = data.get("error") if isinstance(data, dict) else data
            extra = {k: v for k, v in data.items() if k != "error"} if isinstance(data, dict) else {}
            hint = None
            if status == 409 and self.turn_over:
                hint = "本回合已经结束（或你不在回合中）：执行 arena turn end，本方下一个下令阶段到了会带着唤醒内容返回"
            elif status == 404 and path.startswith(("view/", "queue", "scripts/test", "fires")):
                hint = "训练期没有对局，这个命令只在演习和正式赛里能用" if path != "view/units" else None
            raise CliError(f"裁判拒绝（HTTP {status}）：{err}" + (f" {json.dumps(extra, ensure_ascii=False)}" if extra else ""), hint)
        return data

    def get(self, path, query=None):
        return self.call("GET", path, query=query)

    @staticmethod
    def q(name):
        """A unit / command / script name as one URL path segment."""
        return urllib.parse.quote(str(name), safe="")

    @staticmethod
    def qp(path):
        """A note path (segments kept) for the URL."""
        return urllib.parse.quote(str(path), safe="/")

    def raw(self, data, opts, text):
        return Out(jdump(data) if opts.get("json") else text)

    # ---- names, contacts, clock -------------------------------------------------------------------------------------
    def unit_names(self, refresh=False):
        """Own unit names (game), else the rosters of the training briefing; None when nothing is known."""
        if self._names is None or refresh:
            status, data = self.referee.call("GET", "view/units")
            if status == 200 and isinstance(data, dict) and isinstance(data.get("units"), list):
                self._names = [u["name"] for u in data["units"] if isinstance(u, dict) and u.get("name")]
            else:
                b = self.briefing(quiet=True)
                sides = (b or {}).get("sides") if isinstance(b, dict) else None
                self._names = [u["name"] for s in (sides or {}).values() for u in s.get("units") or []] if sides else None
        return self._names

    def resolve_unit(self, toks, what="单位"):
        """(unit name, tokens used) from the front of toks; a name may span tokens ("1/A/TF Alamo" unquoted)."""
        if not toks:
            raise CliError(f"缺少{what}名", "unit list 看本方单位")
        names = self.unit_names()
        if not names:
            return toks[0], 1
        for refresh in (False, True):
            if refresh:
                names = self.unit_names(refresh=True) or names
            for k in range(min(len(toks), 6), 0, -1):
                name, cands = match_name(" ".join(toks[:k]), names)
                if name and (k == 1 or " ".join(toks[:k]).lower() == name.lower() or not match_name(toks[0], names)[0]
                             or match_name(toks[0], names)[0] == name):
                    return name, k
            name, cands = match_name(toks[0], names)
            if name:
                return name, 1
            if cands:
                raise CliError(f"{what}名 {toks[0]!r} 有歧义，可能是：{'、'.join(cands[:12])}" + ("…" if len(cands) > 12 else ""),
                               "写长一点或用引号写全名")
        s = suggest_name(toks[0], names)
        raise CliError(f"本方没有叫 {toks[0]!r} 的单位。" + (f"你是不是想说：{'、'.join(s)}？" if s else ""), "unit list 看本方单位")

    def briefing(self, quiet=False):
        if self._brief is None:
            status, data = self.referee.call("GET", "view/briefing")
            if status != 200:
                if quiet:
                    return None
                self.call("GET", "view/briefing")
            self._brief = data
        return self._brief

    def clock(self, sec):
        if sec is None:
            return "-"
        b = self.briefing(quiet=True) or {}
        hh = b.get("h_hour") or next((s.get("h_hour") for s in (b.get("sides") or {}).values()), None)
        if not hh:
            return f"{sec}s"
        h, m = (int(x) for x in hh.split(":")[:2])
        c = (h * 3600 + m * 60 + int(sec)) % 86400
        return f"{c // 3600:02d}:{c % 3600 // 60:02d}"

    def scenario_date(self, unit):
        if self._date is None:
            try:
                d = self.get("view/unit/" + self.q(unit))
            except CliError:
                d = {}
            for o in (d or {}).get("orders") or []:
                t = o.get("start_time") or o.get("end_time")
                if isinstance(t, str) and re.match(r"^\d{4}-\d\d-\d\d", t):
                    self._date = t[:10]
                    break
        return self._date

    # ---- command bodies -----------------------------------------------------------------------------------------------
    def _time(self, v, unit):
        if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?$", v):
            return v if v.count(":") == 2 else v + ":00"
        m = re.match(r"^(\d{1,2}):(\d\d)(?::(\d\d))?$", v)
        if not m:
            raise CliError(f"时刻 {v!r} 看不懂：写 08:30 或 \"1989-09-25 08:30:00\"")
        date = self.scenario_date(unit)
        if not date:
            raise CliError("推不出想定日期：时刻请写全，如 \"1989-09-25 08:30:00\"")
        return f"{date} {int(m[1]):02d}:{m[2]}:{m[3] or '00'}"

    def _order_specs(self, unit, toks):
        """[order dict] from `move 2118 then hold` style tokens."""
        specs, cur = [], []
        for t in toks:
            if t.lower() in ("then", ";", "->", "然后"):
                specs.append(cur)
                cur = []
            else:
                cur.append(t)
        specs.append(cur)
        out = []
        for sp in specs:
            if not sp:
                raise CliError("then 前后要有命令")
            word, used = _norm(sp[0]), 1
            if len(sp) > 1 and (word + _norm(sp[1])) in ORDER_WORDS and word in ("move", "on", "counter"):
                word, used = word + _norm(sp[1]), 2
            name = ORDER_WORDS.get(word) or next((n for n in R.ORDER_TYPES if _norm(n) == word), None)
            if name is None:
                s = suggest(word, list(ORDER_WORDS))
                raise CliError(f"没有命令 {sp[0]!r}。" + (f"你是不是想说：{'、'.join(s)}？" if s else "") +
                               f"可用：{' '.join(sorted(R.ORDER_TYPES))}", "arena order add --help")
            o, i = {"orders": name}, used
            while i < len(sp):
                t = sp[i]
                if "=" in t:
                    k, _, v = t.partition("=")
                    k = k.lower()
                    if k == "target":
                        c, (h, _) = parse_contact(v), parse_hex([v])
                        if c is not None:
                            o["target_contact"] = c
                        elif h is not None:
                            o["target_location"] = h
                        else:
                            raise CliError(f"target={v!r}：写格号（2118）或接触（c12）")
                    else:
                        k = ORDER_KEYS.get(k, k)
                        if k in HEX_FIELDS:
                            h, _ = parse_hex([v])
                            if h is None:
                                raise CliError(f"{k}={v!r} 不是格号")
                            o[k] = h
                        elif k in ("start_time", "end_time"):
                            o[k] = self._time(v, unit)
                        else:
                            o[k] = parse_value(v)
                    i += 1
                    continue
                c = parse_contact(t)
                if c is not None:
                    o["target_contact"] = c
                    i += 1
                    continue
                h, n = parse_hex(sp, i)
                if h is None:
                    raise CliError(f"看不懂 {t!r}：命令后面跟格号（2118 / 21,18 / 21 18）、接触（c12）或 键=值", "arena order add --help")
                o["target_location" if name == "Barrage" else "destination_location"] = h
                i += n
            if name == "Barrage" and "barrage_type" not in o:
                o["barrage_type"] = "HE"
            out.append(o)
        return out

    def _kv(self, toks, aliases=None, lists=()):
        out, last = {}, None
        for t in toks:
            if "=" not in t:
                # a value with spaces written without quotes: `fire_discipline=Maximum Range` (start task 11-01: Kimi's
                # first 13 SOP commands were refused for it); the word goes on the value before it, the referee's lint
                # then checks the whole value
                if last is None or not isinstance(out[last], (str, list)) or (isinstance(out[last], list) and not out[last]):
                    raise CliError(f"{t!r} 要写成 键=值（有空格的值加引号）")
                if isinstance(out[last], list):
                    out[last][-1] += " " + t
                else:
                    out[last] += " " + t
                continue
            k, _, v = t.partition("=")
            k = (aliases or {}).get(k.lower(), k)
            out[k] = [x.strip() for x in v.split(",") if x.strip()] if k in lists else parse_value(v)
            last = k
        return out

    def _request(self, toks):
        if not toks:
            raise CliError("缺少请求类型：static enemy smoke cb minefield", "arena fire add --help")
        rtype = REQUEST_TYPES.get(_norm(toks[0])) or (toks[0] if toks[0] in R.FIRE_SUPPORT_TYPES else None)
        if rtype is None:
            raise CliError(f"没有请求类型 {toks[0]!r}；可用：static(static_target) enemy(enemy_target) smoke(obscuration) cb(cb_scan) minefield")
        rq, i = {"request_type": rtype}, 1
        while i < len(toks):
            t = toks[i]
            c = parse_contact(t)
            if c is not None:
                rq["contact"] = c
                i += 1
                continue
            h, n = parse_hex(toks, i)
            if h is not None:
                rq["location"] = h
                i += n
                continue
            kvs = self._kv([t], REQUEST_KEYS)
            for k, v in kvs.items():
                if k in ("requester_unit", "spotter_unit"):
                    # a unit name with spaces may run on unquoted (`requester=HQ PzGrenBtl 111`): the plain words after
                    # it, up to the next 键=值 or contact, are offered to the name; what the name does not use is read on
                    more = []
                    for w in toks[i + 1:]:
                        if "=" in w or parse_contact(w) is not None:
                            break
                        more.append(w)
                    v, n = self.resolve_unit([str(v)] + more)
                    i += n - 1
                if k == "location":
                    v = parse_hex([str(v)])[0]
                rq[k] = v
            i += 1
        if rtype == "obscuration":
            rq.setdefault("target_type", "obscuration")
        if rtype == "static_target" and "target_type" not in rq:
            raise CliError("static 请求要写 target=soft|hard|hq（游戏不带就拒）")
        return {"kind": "fire_support", "request": rq}

    def build(self, toks, input, fire=False):
        """The /agent/queue body of `order add` / `fire add` / `unit X order` tokens (or input's raw JSON)."""
        raw = parse_json(input)
        if toks and toks[0].lower() in REQUEST_WORDS:
            return self._request(toks[1:])
        if isinstance(raw, dict) and raw.get("kind") and not toks:
            return raw
        unit, n = self.resolve_unit(toks)
        rest = toks[n:]
        if isinstance(raw, dict):
            return {"unit": unit, **raw} if raw.get("kind") else {"kind": "orders", "unit": unit, "orders": [raw]}
        if isinstance(raw, list):
            return {"kind": "orders", "unit": unit, "orders": raw}
        if not rest:
            raise CliError(f"{unit} 后面缺命令", "arena order add --help")
        kind = KIND_WORDS.get(rest[0].lower())
        if kind == "doctrine":
            if not rest[1:]:
                raise CliError("sop 后面要写 字段=取值", "arena unit sop --help")
            lists = [k for k, v in self.sop.get("values", {}).items() if v.get("list")]
            return {"kind": "doctrine", "unit": unit, "sop": self._kv(rest[1:], lists=lists)}
        if kind == "emission":
            return {"kind": "emission", "unit": unit, "emission": self._kv(rest[1:])}
        if kind == "deploy":
            h, _ = parse_hex(rest, 1)
            if h is None:
                raise CliError("deploy 后面要写格号，如 deploy 1520")
            return {"kind": "deploy", "unit": unit, "hex": h}
        if fire and _norm(rest[0]) not in ORDER_WORDS:
            rest = ["barrage"] + rest
        return {"kind": "orders", "unit": unit, "orders": self._order_specs(unit, rest)}

    @staticmethod
    def describe(body):
        k = body.get("kind")
        if k == "orders":
            parts = []
            for o in body.get("orders") or []:
                at = o.get("destination_location") or o.get("target_location")
                tgt = f" c{o['target_contact']}" if "target_contact" in o else (f" {at}" if at else "")
                extra = kv(o, [x for x in o if x not in ("orders", "destination_location", "target_location", "target_contact")])
                parts.append(f"{o.get('orders')}{tgt}" + (f"（{extra}）" if extra else ""))
            return f"{body.get('unit')}：" + " → ".join(parts)
        if k == "fire_support":
            return "火力请求：" + kv(body.get("request") or {})
        if k == "doctrine":
            return f"{body.get('unit')} SOP：" + kv(body.get("sop") or {})
        if k == "deploy":
            return f"{body.get('unit')} 部署到 {body.get('hex')}"
        return json.dumps(body, ensure_ascii=False)

    def _lint_text(self, res):
        lines = [f"错误：{e}" for e in res.get("errors") or []] + [f"警告：{w}" for w in res.get("warnings") or []]
        return "\n".join(lines)

    # ---- order / fire / unit ---------------------------------------------------------------------------------------
    def _submit(self, body, opts):
        data = self.call("POST", "queue", body=body)
        text = f"已入队 {data.get('cmd_id')}：{self.describe(body)}\n到本方下一个下令阶段放行；order list 看状态，fire report 看炮击实际打了没有。"
        if data.get("warnings"):
            text += "\n" + "\n".join(f"警告：{w}" for w in data["warnings"])
        return self.raw(data, opts, text)

    def _order_add(self, args, opts, input, fire=False):
        body = self.build(args, input, fire)
        try:
            return self._submit(body, opts)
        except CliError as e:
            raise CliError(f"{e}\n（没有入队：{self.describe(body)}）", "改正后重试；order lint 可以只检查不入队") from None

    def _order_lint(self, args, opts, input):
        body = self.build(args, input)
        res = self.call("POST", "queue/lint", body=body)
        head = f"检查：{self.describe(body)}\n" + ("通过" if res.get("ok") else "不通过（order add 会被拒）")
        if res.get("ok") and not res.get("warnings"):
            head += "，没有警告"
        return self.raw(res, opts, head + ("\n" + self._lint_text(res) if self._lint_text(res) else ""))

    def _queue_rows(self):
        return (self.get("view/queue") or {}).get("queue") or []

    def _order_list(self, args, opts, input, only_fire=False):
        rows = self._queue_rows()
        if only_fire:
            rows = [r for r in rows if r.get("kind") == "fire_support" or set(r.get("orders") or []) & R.INDIRECT_ORDERS]
        text = table(rows, [("编号", "cmd_id"), ("种类", "kind"), ("单位", "unit"),
                            ("命令", lambda r: "→".join(r.get("orders") or []) or None), ("状态", "status"), ("原因", "error")])
        return self.raw(rows, opts, text)

    def _cmd_id(self, word):
        rows = self._queue_rows()
        ids = [r["cmd_id"] for r in rows]
        if word in ids:
            return word
        num = word.lower().lstrip("#c").lstrip("0")
        hits = [i for i in ids if num and re.sub(r"^.*-c0*", "", i) == num] or [i for i in ids if i.endswith(word)]
        if len(hits) == 1:
            return hits[0]
        raise CliError(f"队列里没有命令 {word!r}" + (f"（可能是：{'、'.join(hits)}）" if hits else ""), "order list 看编号")

    def _order_show(self, args, opts, input):
        if not args:
            raise CliError("缺少命令编号", "order list 看编号")
        d = self.get("view/queue/" + self.q(self._cmd_id(args[0])))
        body = d.get("body") or {}
        text = f"{d['cmd_id']}  {d.get('status')}  {self.describe(body) if body else d.get('kind')}" + \
               (f"\n原因：{d['error']}" if d.get("error") else "") + f"\n提交时游戏秒 {d.get('sec_submitted')}" + \
               (f"，放行批 {d['batch']}" if d.get("batch") else "") + "\n原文：" + json.dumps(body, ensure_ascii=False)
        return self.raw(d, opts, text)

    def _order_withdraw(self, args, opts, input):
        if not args:
            raise CliError("缺少命令编号（或 all）", "order list 看编号")
        if args[0].lower() == "all":
            ids = [r["cmd_id"] for r in self._queue_rows() if r.get("status") == "queued"]
        else:
            ids = [self._cmd_id(a) for a in args]
        done = []
        for i in ids:
            self.call("DELETE", "queue/" + self.q(i))
            done.append(i)
        return Out(f"已撤回：{'、'.join(done)}" if done else "没有待放行的命令")

    def _fire_add(self, args, opts, input):
        return self._order_add(args, opts, input, fire=True)

    def _fire_list(self, args, opts, input):
        return self._order_list(args, opts, input, only_fire=True)

    def _fire_report(self, args, opts, input):
        d = self.get("view/fires")
        def target(r):
            if r.get("target_contact") is not None:
                return f"c{r['target_contact']}"
            return r.get("target_hex") or ("看不到的敌方单位" if r.get("target_hidden") else None)
        caller = {"own_order": "你的命令或脚本", "fscc": "游戏火力协调中心自行调用"}
        rows = [{**r, "at": r.get("clock") or self.clock(r.get("t")), "target": target(r), "by": caller.get(r.get("called_by")),
                 "ammo": "，".join(f"{k} {v}" for k, v in (r.get("by_type") or {}).items()) or None}
                for r in d.get("reports") or []]
        fired = [r for r in rows if r.get("type") == "fire_report"]
        late = [r for r in rows if r.get("type") == "fire_pending"]
        text = "实际开火（按本方弹药变化，每游戏分钟汇总）：\n" + table(
            fired, [("时刻", "at"), ("单位", "unit"), ("命令", "order"), ("目标", "target"), ("发数", "rounds"), ("弹种", "ammo"), ("谁调的", "by")])
        if late:
            text += "\n\n放行后迟迟没开火的提醒：\n" + table(late, [("时刻", "at"), ("单位", "unit"), ("目标格", "target_hex"),
                                                         ("放行于", "released_clock"), ("等了(分)", "minutes")])
        pend = d.get("pending") or []
        if pend:
            text += f"\n\n已放行、还没开火的炮击（{d.get('pending_after_min')} 游戏分钟没打会提醒一次）：\n" + table(
                pend, [("单位", "unit"), ("目标格", "target_hex"), ("放行于", "released_clock"), ("已等(分)", "waiting_min")])
        return self.raw(d, opts, text)

    def _unit_first(self, toks, opts, input):
        name, n = self.resolve_unit(toks)
        rest = toks[n:]
        verb = resolve_verb("unit", rest[0]) if rest else "show"
        if verb is None:
            raise CliError(f"unit {name} 后面的 {rest[0]!r} 不是动词；可用：show order sop", "arena unit --help")
        return getattr(self, f"_unit_{verb}")([name] + rest[1:], opts, input)

    def _unit_list(self, args, opts, input):
        d = self.get("view/units")
        rows = d.get("units") or []
        self._names = [r["name"] for r in rows]

        def order(r):
            o = r.get("order") or {}
            at = o.get("destination_location") or (f"c{o['target_contact']}" if o.get("target_contact") else None)
            return (o.get("orders") or "") + (f" {at}" if at else "") + (f" (+{r['orders'] - 1})" if (r.get("orders") or 0) > 1 else "")
        text = table(rows, [("单位", "name"), ("格", "hex"), ("状态", "status"), ("类型", "icon"),
                            ("子单位", lambda r: f"{r.get('subs_ok')}/{r.get('subs')}"), ("士气", "morale"), ("战备", "readiness"),
                            ("当前命令", order)])
        return self.raw(d, opts, f"本方 {len(rows)} 个单位\n" + text)

    def _unit_show(self, args, opts, input):
        name, _ = self.resolve_unit(args)
        d = self.get("view/unit/" + self.q(name))
        s = d.get("status") or {}
        lines = [f"{name}（{s.get('icon_type')}，上级 {s.get('parent_name')}）",
                 kv(s, ["status", "location", "morale", "readiness", "training", "size"])]
        orders = d.get("orders") or []
        lines.append("命令：" + ("无" if not orders else ""))
        for i, o in enumerate(orders, 1):
            lines.append(f"  {i}. " + kv(o, ["orders", "destination_location", "target_location", "target_contact", "target_hidden",
                                             "start_time", "end_time"] + [k for k in o if k.startswith(("barrage_", "reorg_"))]))
        subs = d.get("subunits") or []

        def ammo(x):
            return "，".join(f"{a.get('type') or a.get('ammo_id')} {a.get('rounds')}/{a.get('max_rounds')}" for a in x.get("ammo") or []) or None
        lines.append(f"子单位 {sum(1 for x in subs if x.get('status') == R.SUB_OK)}/{len(subs)} 能战斗：")
        lines.append(table(subs, [("#", "index"), ("名称", "name"), ("类型", "type"), ("状态", "status"), ("弹药", ammo)]))
        return self.raw(d, opts, "\n".join(lines))

    def _unit_order(self, args, opts, input):
        return self._order_add(args, opts, input)

    def _unit_sop(self, args, opts, input):
        name, n = self.resolve_unit(args)
        if args[n:] or parse_json(input):
            return self._order_add([name, "sop"] + args[n:], opts, input) if args[n:] else self._order_add([name], opts, input)
        vals = self.sop.get("values", {})
        rows = []
        for f in self.sop["fields"]:
            if f in self.sop.get("numbers", {}):
                lo, hi = self.sop["numbers"][f]
                rows.append({"field": f, "values": f"整数 {lo}–{hi}"})
            elif f in vals:
                v = vals[f]
                rows.append({"field": f, "values": " | ".join(v.get("seen", [])) +
                             (f"  （手册另有，未验证：{' | '.join(v['manual'])}）" if v.get("manual") else "") +
                             ("（列表）" if v.get("list") else "") + ("" if v.get("closed") else "（还可能有别的取值）")})
            else:
                rows.append({"field": f, "values": "?"})
        text = (f"{name} 的 SOP：游戏状态里读不到当前值，只能改。改法：arena unit {shlex.quote(name)} sop 字段=取值 ...\n" +
                table(rows, [("字段", "field"), ("取值", "values")]))
        return Out(text)

    # ---- contacts, events ------------------------------------------------------------------------------------------
    def _contact_list(self, args, opts, input):
        d = self.get("view/contacts")
        rows = d.get("contacts") or []
        if opts.get("visible"):
            rows = [r for r in rows if r.get("visible")]
        text = table(rows, [("接触", lambda r: f"c{r['contact']}"), ("在视", "visible"), ("识别", "state_name"), ("规模", "size"),
                            ("最后位置", "last_pos"), ("最后发现", lambda r: self.clock(r.get("last_seen"))), ("报告", "report")])
        return self.raw(d, opts, f"接触 {len(rows)} 个（在视 {sum(1 for r in rows if r.get('visible'))}）\n" + text)

    def _contact_show(self, args, opts, input):
        no = parse_contact(args[0]) if args else None
        if no is None and args and args[0].isdigit():
            no = int(args[0])
        if no is None:
            raise CliError("缺少接触编号（12、c12、#12）", "contact list 看编号")
        rows = (self.get("view/contacts") or {}).get("contacts") or []
        c = next((r for r in rows if r.get("contact") == no), None)
        if c is None:
            raise CliError(f"接触表里没有 c{no}", "contact list 看编号")
        text = (f"c{no}：{'在视' if c.get('visible') else '已失去'}，{c.get('state_name')}，规模约 {c.get('size')}\n"
                f"最后位置 {c.get('last_pos')}，首次发现 {self.clock(c.get('first_seen'))}，最后发现 {self.clock(c.get('last_seen'))}\n"
                f"报告：{c.get('report')}")
        return self.raw(c, opts, text)

    def _event_rows(self, since=0):
        return (self.get("view/events", {"since": since}) or {}).get("events") or []

    def _event_table(self, rows):
        return table(rows, [("编号", "id"), ("时刻", lambda e: self.clock(e.get("t"))), ("类型", "type"),
                            ("内容", lambda e: kv({k: v for k, v in e.items() if k not in ("id", "t", "type")}))])

    def _event_list(self, args, opts, input):
        rows = self._event_rows(int(opts.get("since") or 0))
        if opts.get("type"):
            rows = [e for e in rows if e.get("type") == opts["type"]]
        if opts.get("last"):
            rows = rows[-int(opts["last"]):]
        return self.raw(rows, opts, self._event_table(rows))

    def _event_tail(self, args, opts, input):
        n = int(args[0]) if args and args[0].isdigit() else int(opts.get("last") or 15)
        rows = self._event_rows()[-n:]
        return self.raw(rows, opts, self._event_table(rows))

    # ---- brief, score, map -----------------------------------------------------------------------------------------
    def _brief_show(self, args, opts, input):
        b = self.briefing()
        if not opts.get("json") and isinstance(b, dict) and "sides" in b:
            parts = [f"想定：{b.get('scenario')}（训练期：双方简报和单位表）"]
            for s, d in sorted(b["sides"].items()):
                parts.append(f"\n== 第 {s} 方：{d.get('side')}，指挥部 {d.get('command_post')} ==")
                parts.append(self._brief_lines(d))
                parts.append(table(d.get("units") or [], [("单位", "name"), ("类型", "type"), ("上级", "parent"),
                                                          ("子单位", "subunits"), ("后到", "arrives_later")]))
            if b.get("supplement"):
                parts.append("\n补充：" + str(b["supplement"]))
            return Out("\n".join(parts))
        text = f"想定：{b.get('scenario')}；你是 {b.get('you')}，指挥部 {b.get('your_command_post')}；" \
               f"对手：{'游戏内置 AI' if b.get('opponent') == 'game_ai' else '另一位指挥官'}\n" + self._brief_lines(b)
        if b.get("exercise"):
            text += "\n演习：" + json.dumps(b["exercise"], ensure_ascii=False)
        return self.raw(b, opts, text)

    @staticmethod
    def _brief_lines(d):
        lv, sa, m = d.get("victory_share_needed") or {}, d.get("starting_area") or {}, d.get("map") or {}
        cells = "、".join(f"{c['hex']}（{c['vp']}）" for c in d.get("vp_cells") or [])
        return "\n".join([
            f"时长 {d.get('game_hours')} 小时，开始于 {d.get('h_hour')}；地图 {m.get('columns')}×{m.get('rows')} 格（{'×'.join(map(str, m.get('km') or []))} km），格号 = 列×100+行",
            f"胜负：{d.get('victory_rule')}",
            f"胜档线：决定性 {lv.get('decisive')}% / 战术 {lv.get('tactical')}% / 勉强 {lv.get('marginal')}%",
            f"开局区：第 {span(sa.get('columns'))} 列、第 {span(sa.get('rows'))} 行（部署只能放在这里）",
            f"VP 格：{cells}", f"VP 归属：{d.get('vp_rule')}"])

    def _score_show(self, args, opts, input):
        d = self.get("view/score")
        lv, held, L = d.get("levels") or {}, d.get("vp_cells_held") or {}, d.get("losses") or {}
        share, vp = d.get("share"), d.get("vp") or {}
        you, en = vp.get("you") or {}, vp.get("enemy") or {}
        lines = [(f"当前份额估计：{share}%（开局 {d.get('starting_share')}%）；本方 {you.get('total')} = 存活兵力 {you.get('forces')}"
                  f" + VP 格 {you.get('cells')}，敌方 {en.get('total')} = 存活兵力 {en.get('forces')} + VP 格 {en.get('cells')}")
                 if share is not None else f"当前份额估计：暂无 —— {d.get('note')}",
                 f"胜档线（本方份额）：决定性 ≥{lv.get('decisive')}%，战术 ≥{lv.get('tactical')}%，勉强 ≥{lv.get('marginal')}%" +
                 (f"，争夺 {lv['contested_from']}–{lv.get('marginal', 0) - 1}%，更低为负" if lv.get("contested_from") else ""),
                 "VP 格：", table(d.get("vp_cells") or [], [("格", "hex"), ("分值", "vp"), ("归属", lambda c: OWNER.get(c["owner"], c["owner"])),
                                                           ("争夺", lambda c: "双方都在格上或相邻" if c.get("contested") else "")]),
                 f"VP 格合计：本方 {held.get('you')}，敌方 {held.get('enemy')}，无主 {held.get('none')}"]
        if d.get("share_range"):
            lo, hi = d["share_range"]
            lines.append(f"有争夺中的 VP 格：终局时游戏按格子附近的单位判归属，不一定归上表的“归属”（11-01 一局里双方同格的 3923，"
                         f"按终局总分反推是判给了后进来的一方）。争夺格全归敌方时份额 {lo}%，全归本方时 {hi}%。要保住就清掉格上和相邻的敌军。")
        for who, label in (("you", "本方"), ("enemy", "敌方")):
            x = L.get(who) or {}
            lost = (d.get("vp_lost") or {}).get(who)
            lines.append(f"{label}损失：开局 {x.get('subunits_start')} 个子单位，现存 {x.get('subunits_now')}，损失 {x.get('subunits_lost')}"
                         f"（按个数 {x.get('strength_pct')}%）" + (f"，折合 {lost} VP" if lost is not None else ""))
        fv = d.get("force_value_pct")
        if fv:
            lines.append(f"兵力（按 VP 价值，游戏判突然死亡用这个；上面的子单位个数比例不算）：本方 {fv['you']}%，敌方 {fv['enemy']}%；"
                         f"任一方低于 {d.get('sudden_death_below_pct')}% 触发突然死亡（Sudden Death）。")
        else:
            lines.append(f"兵力按 VP 价值低于 {d.get('sudden_death_below_pct')}% 触发突然死亡（Sudden Death）。")
        lines.append(f"来源：{d.get('vp_source')}；计分原则见 arena score explain")
        return self.raw(d, opts, "\n".join(lines))

    def _score_explain(self, args, opts, input):
        return Out("\n".join([
            "计分原则（FM01 第 30 节 Victory Conditions and End Game、15.1.2 Victory Status）：",
            "1. 终局按本方 VP 占双方 VP 总和的份额判胜负，档线见 brief show / score show（以游戏终局的 Victory Status 表为准）。",
            "2. VP 来自三处：保住本方子单位、打掉敌方子单位、占住 VP 格。",
            "3. 每个子单位有自己的 VP 值（游戏 Subunit Inspector 里看得到）；打坏和打毁都给满分（都算 mission kill）。",
            "4. VP 格：本方地面单位经过就占下；直升机和飞机不能占；同一分钟双方都经过则归属不变。终局时双方单位都在格上或相邻的，"
            "游戏按附近的单位判归属（Victory Status 页 Section C），score show 标为争夺并给份额上下限。",
            "5. 兵力 = 还能打的子单位 VP 之和 / 开局部队总 VP（按价值，不按个数）；任一方跌破 30% 触发突然死亡，游戏提前结束，"
            "跌破的一方结果降一档、对方升一档。",
            "6. 档线按开局兵力比例定，兵力不对等也不吃亏（A.1 表）。",
            "份额怎么算（11-01 用游戏终局 Victory Status 页核对过）：每方总分 = 还能打的子单位（Runner）的 VP + 持有 VP 格的分值；"
            "本方份额 = 本方总分 / 双方总分。打掉敌方子单位是让敌方总分变少；页上的 Enemy Losses Inflicted 只是展示，不加进总分。"
            "VP 格分值是游戏计分用的值，有的想定和简报正文里的数字不同（Alamo 是 5 倍），以 score show 为准。score show 给当前估计和分项。",
            "常见错误：只按 VP 格比例估份额 —— 第一届有人这样算，冒进丢了大量子单位，份额反而下降。",
            "原文：arena doc show fm01 --from 6978 --lines 120"]))

    def _terrain_rows(self, hexes):
        out = []
        for i in range(0, len(hexes), R.HEX_QUERY_MAX):
            out += (self.get("view/terrain", {"hexes": ",".join(map(str, hexes[i:i + R.HEX_QUERY_MAX]))}) or {}).get("terrain") or []
        return out

    TERRAIN_COLS = [("格", "hex"), ("高程m", "elev_m"), ("高m", "height_m"), ("地表", "terrain"), ("道路", "road_sides"),
                    ("水系", "water_sides"), ("坡", "slope_sides"), ("其他", "other_sides")]

    def _map_show(self, args, opts, input):
        if not args and not opts.get("box"):
            b = self.briefing()
            d = b if "sides" not in b else next(iter(b["sides"].values()))
            vp = {}
            try:
                vp = {c["hex"]: c["owner"] for c in (self.get("view/vp") or {}).get("vp") or []}
            except CliError:
                pass
            m, sa = d.get("map") or {}, d.get("starting_area") or {}
            cells = "、".join(f"{c['hex']}（{c['vp']} 分{'，' + OWNER.get(vp[c['hex']], vp[c['hex']]) if c['hex'] in vp else ''}）"
                              for c in d.get("vp_cells") or [])
            return Out(f"地图 {m.get('columns')} 列 × {m.get('rows')} 行，{'×'.join(map(str, m.get('km') or []))} km，每格约 500 m；"
                       f"格号 = 列×100+行（2118 = 第 21 列第 18 行），平顶六角格，偶数列下移半格\n"
                       f"本方开局区：第 {span(sa.get('columns'))} 列、第 {span(sa.get('rows'))} 行\nVP 格：{cells}\n"
                       "查地形：map show 20-24 15-20｜map show 2118 --radius 2｜map hex 2118｜map path 2118 2530")
        if opts.get("box"):
            c0, c1, r0, r1 = (int(x) for x in str(opts["box"]).split(","))
        elif len(args) >= 2 and all(re.match(r"^\d{1,2}-\d{1,2}$", a) for a in args[:2]):
            (c0, c1), (r0, r1) = [tuple(int(x) for x in a.split("-")) for a in args[:2]]
        else:
            h, _ = parse_hex(args)
            if h is None:
                raise CliError("看不懂范围：写 map show 20-24 15-20，或 map show 2118 --radius 2", "arena map show --help")
            r = int(opts.get("radius") or 1)
            c0, c1, r0, r1 = h // 100 - r, h // 100 + r, h % 100 - r - 1, h % 100 + r + 1
            hexes = [c * 100 + w for c in range(max(c0, 1), c1 + 1) for w in range(max(r0, 1), r1 + 1)
                     if hex_dist(h, c * 100 + w) <= r]
            return Out(table(self._terrain_rows(hexes), self.TERRAIN_COLS))
        hexes = [c * 100 + w for c in range(c0, c1 + 1) for w in range(r0, r1 + 1)]
        if len(hexes) > R.HEX_QUERY_MAX:
            raise CliError(f"范围 {len(hexes)} 格，一次最多 {R.HEX_QUERY_MAX} 格")
        rows = self._terrain_rows(hexes)
        return self.raw(rows, opts, table(rows, self.TERRAIN_COLS))

    def _hexes(self, args):
        out, i = [], 0
        while i < len(args):
            h, n = parse_hex(args, i)
            if h is None:
                raise CliError(f"{args[i]!r} 不是格号（2118 / 21,18 / 21 18）")
            out.append(h)
            i += n
        return out

    def _map_hex(self, args, opts, input):
        hexes = self._hexes(args)
        if not hexes:
            raise CliError("缺少格号", "arena map hex --help")
        rows = self._terrain_rows(hexes)
        return self.raw(rows, opts, table(rows, self.TERRAIN_COLS))

    def _map_path(self, args, opts, input):
        hexes = self._hexes(args)
        if len(hexes) != 2:
            raise CliError("要两个格号：map path 2118 2530")
        line = hex_line(*hexes)
        by = {r["hex"]: r for r in self._terrain_rows(line)}
        rows = [{"step": i, **by.get(h, {"hex": h})} for i, h in enumerate(line)]
        return self.raw(rows, opts, f"{hexes[0]} → {hexes[1]}：直线经过 {len(line)} 格，约 {hex_dist(*hexes) * 0.5:.1f} km（几何直线，不是行军路线）\n"
                        + table(rows, [("步", "step")] + self.TERRAIN_COLS[:5]))

    # ---- scripts ------------------------------------------------------------------------------------------------------
    def _script_list(self, args, opts, input):
        d = self.get("scripts")
        rows = [{"name": n, **v} for n, v in sorted((d.get("scripts") or {}).items())]
        text = table(rows, [("脚本", "name"), ("触发", lambda r: r["trigger"].get("on") + (f" {','.join(r['trigger']['types'])}" if r["trigger"].get("types") else "")),
                            ("启用", "enabled"), ("运行", "runs"), ("连续超限", "strikes"), ("字节", "bytes")])
        if d.get("note"):
            text += "\n" + d["note"]
        return self.raw(d, opts, text)

    def _script_source(self, name):
        d = self.call("GET", "notes/scripts/" + self.q(name) + ".json")
        rec = json.loads(d.get("content") or "{}")
        return rec.get("source") or "", rec.get("trigger")

    def _script_show(self, args, opts, input):
        if not args:
            raise CliError("缺少脚本名", "script list 看全部")
        try:
            src, tr = self._script_source(args[0])
        except CliError:
            raise CliError(f"没有脚本 {args[0]!r}", "script list 看全部") from None
        return self.raw({"name": args[0], "trigger": tr, "source": src}, opts,
                        f"脚本 {args[0]}，触发 {json.dumps(tr, ensure_ascii=False)}\n{src}")

    @staticmethod
    def _trigger(opts):
        if opts.get("trigger") and str(opts["trigger"]).strip().startswith("{"):
            try:
                return json.loads(opts["trigger"])
            except ValueError:
                raise CliError("--trigger 的 JSON 写错了") from None
        on = opts.get("on") or opts.get("trigger") or "minute"
        tr = {"on": on}
        if opts.get("types"):
            tr["types"] = [x.strip() for x in str(opts["types"]).split(",") if x.strip()]
        elif on not in ("minute", "orders_phase", "wake", "event") and on in R.SUB_TYPES:
            tr = {"on": "event", "types": [on]}
        return tr

    def lint_script(self, source, trigger):
        names = self.unit_names()
        return lint_source(source, trigger, names, self.syntax_check)

    def _script_lint(self, args, opts, input):
        if args:
            src, tr = self._script_source(args[0])
            tr = self._trigger(opts) if opts.get("on") or opts.get("trigger") else tr
        else:
            src, tr = input, self._trigger(opts)
            if not src:
                raise CliError("给脚本名，或把源码放进 input")
        res = self.lint_script(src, tr)
        text = ("通过" if res["ok"] else "不通过") + (f"；引用的本方单位：{'、'.join(res['units'])}" if res["units"] else "")
        return self.raw(res, opts, text + ("\n" + self._lint_text(res) if self._lint_text(res) else ""))

    def _script_put(self, args, opts, input):
        if not args:
            raise CliError("缺少脚本名", "arena script put --help")
        if not input:
            raise CliError("源码放进 input")
        tr = self._trigger(opts)
        try:
            check_trigger(tr)
        except ScriptError as e:
            raise CliError(str(e)) from None
        res = self.lint_script(input, tr)
        if not res["ok"] and not opts.get("force"):
            raise CliError("没有登记：\n" + self._lint_text(res), "改正后重试；确定没问题可加 --force")
        d = self.call("PUT", "scripts/" + self.q(args[0]), body={"source": input, "trigger": tr})
        text = f"已登记脚本 {args[0]}（触发 {json.dumps(tr, ensure_ascii=False)}）"
        return self.raw(d, opts, text + ("\n" + self._lint_text(res) if self._lint_text(res) else ""))

    def _script_delete(self, args, opts, input):
        if not args:
            raise CliError("缺少脚本名")
        d = self.call("DELETE", "scripts/" + self.q(args[0]))
        return Out(f"已删除脚本 {args[0]}" if d.get("ok") else f"没有脚本 {args[0]!r}")

    def _script_test(self, args, opts, input):
        body = {"name": args[0]} if args else {"source": input}
        if not args and not input:
            raise CliError("给脚本名，或把源码放进 input")
        if opts.get("trigger"):
            body["trigger"] = opts["trigger"]
        d = self.call("POST", "scripts/test", body=body)
        lines = [("运行成功" if d.get("ok") else f"运行出错：{d.get('error')}") +
                 f"（触发 {d.get('trigger')}）"]
        if d.get("alarm"):
            lines.append(f"会报警：{d['alarm']}")
        if d.get("report") is not None:
            lines += ["唤醒时会显示：" if d.get("trigger") == "wake" else "report 输出（只有唤醒脚本的会显示在唤醒里）：", d["report"]]
        elif d.get("trigger") == "wake" and d.get("ok"):
            lines.append("唤醒内容为空：脚本没有调用 report")
        for c in d.get("commands") or []:
            r = c.get("lint") or {}
            lines.append(f"会提交：{self.describe(c.get('body') or {})} —— " + ("通过" if r.get("ok") else "会被拒") +
                         ("；" + "；".join((r.get("errors") or []) + (r.get("warnings") or [])) if (r.get("errors") or r.get("warnings")) else ""))
        if not d.get("commands"):
            lines.append("不会提交命令")
        if d.get("log"):
            lines += ["print 输出："] + [f"  {x}" for x in d["log"]]
        lines.append("（空跑：没有入队、没有叫醒、memory 没保存）")
        return self.raw(d, opts, "\n".join(lines))

    # ---- notes ------------------------------------------------------------------------------------------------------
    def _note_list(self, args, opts, input):
        d = self.call("GET", "notes/" + (self.qp(args[0].strip("/")) + "/" if args else ""))
        return self.raw(d, opts, "笔记：" + ("、".join(d.get("entries") or []) or "（空）"))

    def _note_show(self, args, opts, input):
        if not args:
            return self._note_list(args, opts, input)
        d = self.call("GET", "notes/" + self.qp(args[0]))
        if "entries" in d:
            return self.raw(d, opts, f"{args[0]}/：" + ("、".join(d["entries"]) or "（空）"))
        return self.raw(d, opts, d.get("content") or "（空笔记）")

    def _note_put(self, args, opts, input, append=False):
        if not args:
            raise CliError("缺少笔记路径，如 plan.md")
        text = input or ""
        if append:
            try:
                old = self.call("GET", "notes/" + self.qp(args[0])).get("content") or ""
            except CliError:
                old = ""
            text = old + ("" if not old or old.endswith("\n") else "\n") + text
        elif not text:
            raise CliError("正文放进 input")
        self.call("PUT", "notes/" + self.qp(args[0]), body={"content": text})
        return Out(f"已{'追加到' if append else '写入'} {args[0]}（{len(text)} 字符）", note=(args[0], text))

    def _note_append(self, args, opts, input):
        if not input:
            raise CliError("要追加的内容放进 input")
        return self._note_put(args, opts, input, append=True)

    # ---- calc, exercise, doc --------------------------------------------------------------------------------------
    def _calc_run(self, args, opts, input):
        raw = parse_json(input)
        if isinstance(raw, dict) and "code" in raw:
            body = {"code": raw["code"], "input": raw.get("input")}
        else:
            if not input:
                raise CliError("Lua 代码放进 input", "arena calc run --help")
            data = None
            if opts.get("data"):
                try:
                    data = json.loads(opts["data"])
                except ValueError:
                    raise CliError("--data 不是合法 JSON") from None
            body = {"code": input, "input": data}
        d = self.call("POST", "compute", body=body)
        if not d.get("ok"):
            return Out(f"计算出错：{d.get('error')}" + (f"（超限：{d.get('limit_kind')}）" if d.get("limit") else ""), error=True)
        res = d.get("result")
        text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
        return self.raw(d, opts, f"结果：{text}" + ("\nprint：\n" + "\n".join(d["log"]) if d.get("log") else ""))

    def _exercise_request(self, args, opts, input):
        raw = parse_json(input)
        if isinstance(raw, dict):
            body = raw
        else:
            words = [a.lower() for a in args if "=" not in a]
            kv_ = self._kv([a for a in args if "=" in a], {"play_side": "play_side", "side": "play_side"}, lists=("units",))
            body = {"type": next((w for w in words if w in ("full", "small")), None),
                    "opponent": next((w for w in words if w in ("ai", "self")), None),
                    "play_side": kv_.get("play_side", 0), **({"pacing": kv_["pacing"]} if "pacing" in kv_ else {})}
            if kv_.get("units"):
                body["units"] = [self.resolve_unit([u])[0] for u in kv_["units"]]
            if not body["type"] or not body["opponent"]:
                raise CliError("要写类型 full|small 和对手 ai|self", "arena exercise request --help")
        d = self.call("POST", "exercise", body=body)
        out = self.raw(d, opts, f"演习已受理：{d.get('id') or json.dumps(d, ensure_ascii=False)}。本回合到此结束；排到时你会被叫去亲自指挥这场演习，打完回到训练，结果随下一次唤醒给你。")
        out.turn_over = bool(d.get("turn_over"))
        return out

    def _exercise_list(self, args, opts, input):
        d = self.get("exercises")
        rows = d.get("exercises") or []
        def result(e):
            q = e.get("request") or {}
            o = scorecard.own_view((e.get("report") or {}).get("official"), scorecard.scenario_key(q.get("scenario")),
                                   int(q.get("play_side") or 0)) or {}
            return f"{o.get('result')} {o.get('vp_ratio_pct')}%" + ("（由对方视角换算）" if o.get("derived") else "") if o.get("result") else ""
        text = table(rows, [("编号", "id"), ("状态", "status"), ("申请", lambda e: kv(e.get("request") or {})),
                            ("墙钟秒", "wall_sec"), ("官方结果", result), ("原因", "error")])
        tail = "\n成绩单：exercise show <编号>（完整演习打完才有）" if any(result(e) for e in rows) else ""
        return self.raw(d, opts, text + tail)

    def _exercise_show(self, args, opts, input):
        if not args:
            raise CliError("缺少演习编号", "exercise list 看编号")
        rows = (self.get("exercises") or {}).get("exercises") or []
        e = next((r for r in rows if r.get("id") == args[0]), None)
        if e is None:
            raise CliError(f"没有演习 {args[0]!r}", "exercise list 看编号")
        r, q = e.get("report") or {}, e.get("request") or {}
        key, side = scorecard.scenario_key(q.get("scenario")), int(q.get("play_side") or 0)
        card = r.get("official") and q.get("type") == "full" and scorecard.recard(r.get("scorecard"), r["official"], key, side,
                                                                                 vs_ai=q.get("opponent") == "ai")
        note = "（official 是结算页原文，对方视角）" if card and scorecard.view_side(r["official"], key) == 1 - side else ""
        if card:                                  # the card is above; a stored one may be from before views
            e = dict(e, report={k: v for k, v in r.items() if k != "scorecard"})
        return Out((card["text"] + f"\n\n详细记录{note}：\n" if card else "") + jdump(e))

    def _doc_list(self, args, opts, input):
        return Out(table([{"name": n, "what": w, "lines": c} for n, w, c in self.docs.names()],
                         [("文档", "name"), ("说明", "what"), ("行数", "lines")]) + "\n读：doc show <文档>；找：doc search <关键词>")

    def _doc_show(self, args, opts, input):
        if not args:
            raise CliError("缺少文档名", "doc list 看全部文档")
        lib, name = self.docs.which(" ".join(args))
        start = int(opts.get("from") or 1)
        basics = name == "basics.md"
        n = int(opts.get("lines") or (400 if basics else 100))
        lines = lib.lines(name)
        end = min(len(lines), start + min(n, 400 if basics else 150) - 1)
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(max(start, 1), end + 1))
        more = f"\n…还有 {len(lines) - end} 行：doc show {name if not basics else 'basics'} --from {end + 1}" if end < len(lines) else ""
        return Out(f"{name} 行 {start}-{end} / 共 {len(lines)} 行\n{body}{more}")

    def _doc_search(self, args, opts, input):
        q = " ".join(args)
        if not q.strip():
            raise CliError("缺少关键词")
        hits = self.docs.search(q, max(1, min(20, int(opts.get("max") or 8))))
        if not hits:
            return Out("没有找到")
        return self.raw(hits, opts, "\n\n".join(f"[{h['file']} 行 {h['lines']}｜命中 {h['matched']}]\n{h['snippet']}" for h in hits))

    # ---- agents and turns (the caller does these) ------------------------------------------------------------------
    def _agent_spawn(self, args, opts, input):
        if self.sub:
            raise CliError("子代理不能再派子代理")
        if not args:
            raise CliError("缺少子代理名字", "arena agent spawn --help")
        task = (input or "").strip() or " ".join(args[1:]).strip()
        if not task:
            raise CliError("任务说明放进 input（或写在名字后面）")
        return Out(driver=("spawn_subagent", {"name": args[0], "task": task}))

    def _agent_check(self, args, opts, input):
        if self.sub:
            raise CliError("子代理没有自己的子代理")
        wait = opts.get("wait") or (args[0] if args and args[0].isdigit() else 0)
        return Out(driver=("check_subagents", {"wait_s": int(wait)}))

    def _turn_end(self, args, opts, input):
        a, raw = {}, parse_json(input)
        if isinstance(raw, list):
            a["subscriptions"] = raw
        elif isinstance(raw, dict):
            a.update({k: v for k, v in raw.items() if k in ("subscriptions", "report")})
        elif input and input.strip():
            a["report"] = input.strip()
        if opts.get("report"):
            a["report"] = str(opts["report"])
        if self.sub and not a.get("report"):
            raise CliError("子代理结束任务要交报告：turn end --report \"...\"，或把报告放进 input")
        return Out(driver=("end_turn", a))

    def _turn_status(self, args, opts, input):
        d = self.get("view/summary")
        if "training" in d:
            t = d["training"]
            text = f"训练期：已过 {int(t.get('wall_sec') or 0) // 60} 分钟"
            return self.raw(d, opts, text)
        # no thinking time (r5-minden 11-04): depth matters, the clock of the player's thinking does not
        g, turn, nxt = d.get("game") or {}, d.get("turn") or {}, d.get("next_orders_phase") or {}
        q = {}
        for r in d.get("queue") or []:
            q[r.get("status")] = q.get(r.get("status"), 0) + 1
        f = d.get("force") or {}
        text = "\n".join([
            f"游戏时刻 {g.get('clock')}（第 {g.get('sec')} 秒），终局还剩 {int(g.get('remaining_sec') or 0) // 60} 分钟",
            f"本回合 {turn.get('turn_id')}" + ("，观察模式中" if turn.get("observing") else "") +
            (f"，子代理：{'、'.join(turn['subagents'])}" if turn.get("subagents") else ""),
            f"下个本方下令阶段：{nxt.get('clock') or '未知'}" + ("（估计）" if nxt.get("estimated") else ""),
            f"兵力 {f.get('pct')}%（{f.get('subunits_ok')}/{f.get('subunits')}），出局单位 {f.get('units_out')}",
            "队列：" + (kv(q) or "空")])
        return self.raw(d, opts, text)

    def _turn_summary(self, args, opts, input):
        d = self.get("view/summary")
        return Out(jdump(d) if opts.get("json") else json.dumps(d, ensure_ascii=False, separators=(",", ":")))

    def _turn_observe(self, args, opts, input):
        if self.sub:
            raise CliError("观察模式只限指挥官")
        on = parse_value(args[0]) if args else True
        if not isinstance(on, bool):
            raise CliError("写 turn observe on 或 turn observe off")
        d = self.call("POST", "observe", body={"on": on})
        return Out("已进入观察模式：每游戏分钟一次更新；不调用工具的回复 = 等下一次更新" if d.get("observing") else "已退出观察模式",
                   observe=bool(d.get("observing")))


def _sandbox_syntax(source):
    """Compile-only check in the Lua sandbox; None when the sandbox is not installed here."""
    try:
        from arena.sandbox import runner
        if not os.path.isfile(runner.lua_exe()):
            return None
        return runner.check_syntax(source)
    except OSError as e:
        return {"ok": False, "unavailable": True, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------------------------------------------------
# old tool calls (before season 2) as commands
# ---------------------------------------------------------------------------------------------------------------------
def _q(s):
    return shlex.quote(str(s))


def legacy(name, args):
    """(cmd, input) of an old tool call (get_summary, submit_command, end_turn, ...); None for an unknown name."""
    a = args if isinstance(args, dict) else {}
    j = lambda x: json.dumps(x, ensure_ascii=False)  # noqa: E731
    if name == "get_terrain":
        if a.get("box"):
            box = a["box"] if isinstance(a["box"], list) else json.loads(a["box"])
            return "map show --box " + ",".join(map(str, box)) + " --json", None
        hx = a.get("hexes") if isinstance(a.get("hexes"), list) else json.loads(a.get("hexes") or "[]")
        return "map hex " + " ".join(map(str, hx)) + " --json", None
    if name == "end_turn":
        body = {k: a[k] for k in ("subscriptions", "report") if a.get(k) is not None}
        return "turn end", (j(body) if body else None)
    simple = {
        "get_briefing": ("brief show --json", None), "get_summary": ("turn summary", None), "list_units": ("unit list --json", None),
        "get_unit": (f"unit show {_q(a.get('name', ''))} --json", None), "get_contacts": ("contact list --json", None),
        "get_vp": ("score show --json", None), "get_queue": ("order list --json", None), "get_thinking": ("turn status --json", None),
        "get_events": (f"event list --since {int(a.get('since') or 0)} --json", None),
        "submit_command": ("order add", j(a)), "withdraw_command": (f"order withdraw {_q(a.get('cmd_id', ''))}", None),
        "set_observe": ("turn observe " + ("on" if a.get("on") in (True, "true", 1) else "off"), None),
        "list_scripts": ("script list --json", None),
        "put_script": (f"script put {_q(a.get('name', ''))} --trigger {_q(j(a.get('trigger') or {'on': 'minute'}))} --force", a.get("source")),
        "delete_script": (f"script delete {_q(a.get('name', ''))}", None),
        "run_compute": ("calc run --json", j({"code": a.get("code"), "input": a.get("input")})),
        "notes_get": ((f"note show {_q(a['path'])}" if a.get("path") else "note list") + " --json", None),
        "notes_put": (f"note put {_q(a.get('path', ''))}", a.get("content")),
        "request_exercise": ("exercise request --json", j(a)), "list_exercises": ("exercise list --json", None),
        "library_search": (f"doc search {a.get('query', '')} --max {int(a.get('max_results') or 8)}", None),
        "library_read": ((f"doc show {_q(a['file'])} --from {int(a.get('start_line') or 1)} --lines "
                          f"{int(a['end_line']) - int(a.get('start_line') or 1) + 1 if a.get('end_line') else 100}")
                         if a.get("file") else "doc list", None),
        "spawn_subagent": (f"agent spawn {_q(a.get('name', ''))}", a.get("task")),
        "check_subagents": (f"agent check --wait {int(a.get('wait_s') or 0)}", None),
    }
    return simple.get(name)
