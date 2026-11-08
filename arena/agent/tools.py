"""Referee client, read-only library and the first-season tool surface of arena-agent.

Since season 2 the model sees one tool, `arena` (arena/agent/cli.py), which parses a command line and calls the
referee through `Referee` below and the library through `Library`. What stays here:
- `Referee`: the HTTP client for the /agent/* endpoints of arena/DESIGN.md 5.2, used by cli.Cli and by the runtime
  (wait, turn/begin).
- `Library`: read-only search and read over arena/library (season 1 manuals as .txt; cli also reads basics.md).
- SPECS / ALL / ToolBox: the 26 first-season tools. The model is no longer offered them; they are kept for
  cli.legacy() (old tool calls in scripted tests and dry runs are translated to `arena` command lines), for the
  driver's end_turn argument coercion, and for the contract tests that pin the endpoint shapes.
"""
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EVENTS = ("orders_phase", "new_contact", "combat", "unit_out", "strength_drop", "vp_change", "order_done", "script_alarm", "timer", "contact_near")
SUB_ONLY_OFF = ("spawn_subagent", "check_subagents")  # the two tools a subagent does not get
MAX_RESULT_CHARS = 20000
NET_ERRORS = (OSError, http.client.HTTPException)  # what a dead or confused referee connection raises
NAME_RULE = "小写字母开头，只含小写字母、数字、下划线，最长 32 个字符"


def P(type_, desc, **kw):
    return {"type": type_, "description": desc, **kw}


SUBSCRIPTION = P("object", "一条订阅，type 取值：orders_phase 本方下令阶段到；new_contact 新接触（可带 area={box:[列0,列1,行0,行1]} 或 {center:格号,radius_km}，min_size 限规模）；"
                 "combat 交火或伤亡；unit_out 本方单位出局；strength_drop 兵力下降（points=百分点）；vp_change VP 格易手；"
                 "order_done 命令完成；script_alarm 脚本报警；timer 定时（at=游戏秒 或 every_min=每N游戏分钟，N 不小于 10）；"
                 "contact_near 已发现的敌方接触进入某格半径内（center=格号, radius_km）。每条都可带 merge_sec（触发唤醒后的静默秒数）。",
                 properties={"type": P("string", "事件类型", enum=list(EVENTS))}, required=["type"], additionalProperties=True)
LOC = "格号：整数“列列行行”，前导零省掉（412 = 第4列第12行）"

# (name, method, path with {params}, location of the other args, description, properties, required)
SPECS = [
    ("get_briefing", "GET", "view/briefing", None, "任务简报全文、胜利条件、VP 格位置和分值、本方开局区。", {}, []),
    ("get_summary", "GET", "view/summary", None, "默认唤醒摘要的最新版：游戏时间、近期事件、本方变化、接触、VP 归属、队列状态、下个下令阶段时刻。", {}, []),
    ("list_units", "GET", "view/units", None, "本方全部单位一览（名称、位置、状态、兵力、当前命令概要）。", {}, []),
    ("get_unit", "GET", "view/unit/{name}", None, "单个本方单位详情：位置、状态、士气、战备、当前命令、逐车子单位和弹药、条令、发射状态。",
     {"name": P("string", "单位名，与 list_units 一致")}, ["name"]),
    ("get_contacts", "GET", "view/contacts", None, "本方接触表：只含本方已发现的敌方接触（接触编号、最后发现位置和时间、识别程度、报告文本、当前是否在视）。", {}, []),
    ("get_vp", "GET", "view/vp", None, "VP 格归属与分值。", {}, []),
    ("get_terrain", "GET", "view/terrain", "query", "全图地形表（人在地图上能看到的）：高程、地表、道路、河流、城镇。必须给 hexes 或 box 之一，避免一次取太多。",
     {"hexes": P("array", "要查的格号列表，" + LOC, items={"type": "integer"}),
      "box": P("array", "矩形范围 [列0, 列1, 行0, 行1]（含边界），范围大就分块查", items={"type": "integer"})}, []),
    ("get_queue", "GET", "view/queue", None, "本方命令队列：每条命令的编号、内容、状态（待放行、已放行、被拒）。", {}, []),
    ("get_thinking", "GET", "view/thinking", None, "本方开局以来的思考用时（指挥官、观察模式、子代理分开）和醒来次数。", {}, []),
    ("get_events", "GET", "view/events", "query", "本方事件列表。", {"since": P("integer", "只要编号大于它的事件（事件里带 id）")}, []),
    ("submit_command", "POST", "queue", "json",
     "提交一条命令进队列，到本方下一个下令阶段才放行。返回 cmd_id 与校验结果；校验没过的命令不进队列。"
     "kind=orders：给 unit 一组命令 orders=[{orders:\"命令名\", destination_location:格号, target_contact:接触编号 或 target_unit:本方单位名, "
     "start_time/end_time:\"1989-09-25 07:30:00\", barrage_*、reorg_* 等}]，会整体替换该单位现有命令。"
     "命令名只收这 13 个（区分大小写）：Hold、Screen、Assault、Move Deliberate、Move Hasty、On Call、Withdraw、Scoot、Barrage、Resupply、Reorganize、"
     "Counter Battery（炮兵反炮兵）、Hunt（直升机，必须带 destination_location）；游戏对不认识的命令名会悄悄改成 Screen，所以别处的名字裁判一律不收。"
     "Barrage（指定这个炮兵单位打哪里，实机实测写法）：{orders:\"Barrage\", target_location:格号 或 target_contact:接触编号, barrage_type:\"HE\"|\"ICM\", "
     "barrage_rounds:发数(≥1 的整数), barrage_duration_mins:持续分钟(≥1 的整数)}，可加 barrage_area_fire:true|false、barrage_timing:\"asap\"；"
     "瞄准用 target_location，不能用 destination_location；放行后当即开打，想晚点打就晚点提交；烟幕弹走 fire_support 的 obscuration 请求。kind=doctrine：改 unit 的条令 sop={字段:值}，只许白名单字段；"
     "kind=emission：发射管控 emission={...}；kind=fire_support：向本方火力支援控制中心（FSCC）提一个火力请求 request={request_type:\"static_target|enemy_target|obscuration|cb_scan|minefield|sead|direct_support\", location:格号, "
     "target_type:\"soft|hard|hq|obscuration|illumination\"（只认小写，static_target 必填）, requester_unit/spotter_unit:本方单位名, projectile_type/priority:字符串, duration_mins/delay:整数}，enemy_target 不给 location，改给 contact（当前在视的接触编号）。"
     "实机实测：游戏只校验 request_type、location、target_type 和 enemy_target 的目标，其余字段写什么都照收，是否生效未证实；sead、direct_support 怎么写都被拒。"
     "请求只是排进 FSCC 队列，由游戏挑当时可用的炮兵去打（见 FM01 25.4.1），没有可用火力的请求二三十游戏分钟后过期；要指定哪个炮兵打哪里，直接给它下 Barrage 命令（写法见上）。"
     "放行后被游戏拒绝、或放行时被裁判扣下的命令，会在事件里出现 command_failed（附原因）；游戏收下了但读回时少了某条命令的（如交战中的 Resupply），出现 command_changed；"
     "kind=deploy：开局部署，把 unit 放到本方开局区内的 hex（只在开局部署阶段有效）。命令名和字段含义查资料库；写错时返回的错误里会列出可用的名字和字段。",
     {"kind": P("string", "命令种类", enum=["orders", "doctrine", "emission", "fire_support", "deploy"]),
      "unit": P("string", "本方单位名（fire_support 可省略）"),
      "orders": P("array", "kind=orders 时的命令数组", items={"type": "object"}),
      "sop": P("object", "kind=doctrine 时的条令字段"),
      "emission": P("object", "kind=emission 时的发射状态"),
      "request": P("object", "kind=fire_support 时的请求"),
      "hex": P("integer", "kind=deploy 时的目标格，" + LOC)}, ["kind"]),
    ("withdraw_command", "DELETE", "queue/{cmd_id}", None, "撤回一条尚未放行的命令。", {"cmd_id": P("string", "submit_command 返回的 cmd_id")}, ["cmd_id"]),
    ("set_observe", "POST", "observe", "json", "开关观察模式（只指挥官可用）：你的回合保持打开，每游戏分钟收到一次更新，期间一直计入思考用时。开启后，不调用任何工具的回复就是“等下一次更新”；"
     "end_turn 或 set_observe(false) 退出观察模式。", {"on": P("boolean", "true 开，false 关")}, ["on"]),
    ("list_scripts", "GET", "scripts", None, "列出本方的反射脚本。", {}, []),
    ("put_script", "PUT", "scripts/{name}", "json",
     "登记或替换一个反射脚本，按触发时机自动运行。环境是 Lua 5.4 沙盒：只有 string / table / math / utf8 和安全的基础函数，没有 io / os / load / require / coroutine。"
     "全局量：view（本方视图，迷雾和你看到的一样）：view.units 是本方全部单位（数组，每行同 list_units：name、parent、hex、status（Active 在场；"
     "Reinforcement 增援未到，到场后变 Active）、morale、readiness、order（当前命令概要）、orders、subs_ok、subs），"
     "view.contacts 是接触表（数组，每行同 get_contacts：contact 编号、state_name、report、visible、last_pos、last_seen 等），"
     "其余键同唤醒摘要（game.sec / game.clock / game.remaining_sec、vp、events、own_changes、contacts_in_view、queue、force、next_orders_phase）；events（触发本次运行的事件；唤醒脚本是上次唤醒以来的全部事件）、trigger（\"minute\" / \"orders_phase\" / \"wake\" / \"event\"）、"
     "memory（脚本自己的持久表，经 JSON 往返，只能用字符串键或数组）。"
     "函数：submit(body)（提交命令，body 同 submit_command）、alarm(text)（叫醒指挥官）、print(...)（写进脚本日志）、"
     "report(...)（唤醒脚本写唤醒内容，每次一行；几个唤醒脚本按名字顺序拼接，没有唤醒脚本时唤醒带默认摘要）。"
     "一次运行太久会被作废；同一个脚本反复如此会被停用并叫醒你。",
     {"name": P("string", "脚本名，" + NAME_RULE), "source": P("string", "Lua 源码"),
      "trigger": P("object", "触发时机：{on:\"minute\"}（每游戏分钟，默认）、{on:\"orders_phase\"}（本方下令阶段）、{on:\"wake\"}（每次对局中唤醒前，输出就是唤醒内容）、{on:\"event\", types:[订阅事件类型...]}")}, ["name", "source"]),
    ("delete_script", "DELETE", "scripts/{name}", None, "删除一个反射脚本。", {"name": P("string", "脚本名")}, ["name"]),
    ("run_compute", "POST", "compute", "json",
     "离线计算任务（视线、路径、火力覆盖等）：在沙盒 Lua 5.4 里跑 code（环境同反射脚本：只有 string / table / math / utf8 和安全的基础函数）；code 里读全局变量 input，用 return 交回结果。",
     {"code": P("string", "Lua 源码"), "input": P("object", "传给脚本的 JSON 数据")}, ["code"]),
    ("notes_get", "GET", "notes/{path}", None, "读本方工作区笔记（只有本方能读写）。path 留空 = 列出所有笔记。", {"path": P("string", "笔记路径，如 plan.md；留空列目录")}, []),
    ("notes_put", "PUT", "notes/{path}", "json", "写（整份覆盖）本方工作区笔记。对话历史会被裁剪，预案和需要长期记住的东西写在这里。",
     {"path": P("string", "笔记路径，如 plan.md"), "content": P("string", "全文")}, ["path", "content"]),
    ("request_exercise", "POST", "exercise", "json",
     "（只在训练期可用）申请一场赛前演习。想定固定为赛用想定（第二届是 Eyes Ears and Teeth），演习里同样受战争迷雾限制，用的就是你的工作区（笔记和脚本）。"
     "type=full 完整演习；"
     "type=small 小规模实验：units 列出本方动用的单位（名字取自 get_briefing 里该方的单位表），其余单位原地待命。"
     "opponent=ai：对手是游戏内置 AI，你只指挥 play_side 那一方（游戏只能让内置 AI 指挥红方，所以 play_side 只能是 0）；opponent=self：对手是你的陪练（同一模型，但是另一个独立的会话，有自己的工作区和记忆；它读不到你的笔记，你也读不到它的），你只指挥 play_side 那一方，另一方由陪练指挥。"
     "play_side：0 = 蓝方（Player 1），1 = 红方（Player 2）。pacing=turn（默认）：和正式赛一样，每个本方下令阶段游戏停下等你；"
     "pacing=formal：实时节奏，思考时游戏照常推进。每场演习开局有一段部署时间。"
     "演习由你本人在你自己的会话里亲自指挥；只有一台游戏机，演习排队轮流打，排队时间不算你的训练时间；设施故障时从头重开，并告诉你出了什么故障。一次排一场。申请成功后本回合立即结束，打完回到训练，结果报告随下一次唤醒给你：结束方式、游戏秒数、双方损失清单、VP 格归属，"
     "游戏打到终局时另有游戏自己的终局结果（official）。不能受理时被拒绝（HTTP 409，错误正文写明原因）。",
     {"type": P("string", "full 完整演习 / small 小规模实验", enum=["full", "small"]),
      "opponent": P("string", "ai 内置 AI / self 你的陪练（同一模型的另一个独立会话）", enum=["ai", "self"]),
      "play_side": P("integer", "你指挥的一方：0 蓝方（Player 1），1 红方（Player 2）；opponent=self 时小规模实验的 units 属于这一方", enum=[0, 1]),
      "pacing": P("string", "turn 和正式赛相同的下令周期（默认）/ formal 实时节奏", enum=["turn", "formal"]),
      "units": P("array", "type=small 时必填：play_side 一方动用的单位名", items={"type": "string"})},
     ["type", "opponent", "play_side"]),
    ("list_exercises", "GET", "exercises", None,
     "（只在训练期可用）列出你的演习申请和结果：状态 queued 排队 / running 进行中 / done 完成 / failed 失败（附原因），"
     "完成的附结果报告原样。", {}, []),
]
LOCAL_SPECS = [
    ("library_search", "在资料库（FM01 到 FM07 手册、FAQ、快捷键表的文本）里按关键词找段落，返回文件名、行号范围和片段。关键词用空格隔开，匹配关键词多的段落排前面。",
     {"query": P("string", "关键词，空格分隔，不分大小写"), "max_results": P("integer", "返回几段，默认 8")}, ["query"]),
    ("library_read", "按文件名和行号范围读资料库。不给 file 时列出所有文件及行数。",
     {"file": P("string", "文件名，来自 library_search 或文件列表"), "start_line": P("integer", "起始行，默认 1"),
      "end_line": P("integer", "结束行（含），默认起始行往后 100 行")}, []),
    ("spawn_subagent", "派一个子代理去做一项独立任务（同一个模型，独立对话，不共享你的对话历史，工具和你一样但不能再派子代理）。"
     "立即返回；用 check_subagents 取结果。任务说明要自包含。",
     {"name": P("string", "子代理名字，自己定（" + NAME_RULE + "），如 deputy、chief_of_staff"), "task": P("string", "任务说明：要做什么、看哪些数据、交什么结果")}, ["name", "task"]),
    ("check_subagents", "查看子代理的状态并取走已完成任务的报告。wait_s>0 时，若有子代理还在跑且暂时没有新报告，最多等这么多秒。",
     {"wait_s": P("integer", "等待秒数，默认 0")}, []),
    ("end_turn", "结束本次回合（指挥官：把回合交还给游戏，对局中等到本方下一个下令阶段才返回，返回的就是唤醒内容；子代理：结束任务）。已提交的命令保留。可同时设置新的唤醒订阅，不给 subscriptions 则沿用原有订阅。",
     {"subscriptions": P("array", "新的订阅列表，整体替换旧的", items=SUBSCRIPTION),
      "report": P("string", "子代理必填：交给指挥官的任务结果，简明；指挥官可省略")}, []),
]


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


ALL = {s[0]: _fn(s[0], s[4], s[5], s[6]) for s in SPECS}
ALL.update({s[0]: _fn(*s) for s in LOCAL_SPECS})
BY_NAME = {s[0]: s for s in SPECS}


def schemas(sub=False):
    return [t for n, t in ALL.items() if not (sub and n in SUB_ONLY_OFF)]


def clip(s, n):
    return s if len(s) <= n else s[:n] + f"…[截断，原长 {len(s)} 字符]"


class ToolResult:
    def __init__(self, text, status=None, turn_over=False):
        self.text, self.status, self.turn_over = text, status, turn_over


class Referee:
    """The /agent/* endpoints with the side's bearer token; talks to localhost, so environment proxies are bypassed."""

    def __init__(self, base, token, timeout=30):
        self.base, self.timeout, self._auth = base.rstrip("/"), timeout, "Bearer " + token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, query=None, body=None, timeout=None):
        q = {k: v for k, v in (query or {}).items() if v is not None}
        url = f"{self.base}/agent/{path}" + ("?" + urllib.parse.urlencode(q) if q else "")
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": self._auth, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        try:
            return status, (json.loads(raw) if raw.strip() else None)
        except ValueError:
            return status, raw.decode("utf-8", "replace")


def is_turn_over(status, data):
    """409 is the referee's answer to a call made while the caller has no open turn (server answers: "side is idle...",
    "<name> has no open turn", "turn_id does not match the open turn")."""
    text = json.dumps(data, ensure_ascii=False).lower() if data is not None else ""
    return status == 409 and any(k in text for k in ("side is idle", "no open turn", "match the open turn"))


class Library:
    CHUNK = 12

    def __init__(self, root, exts=(".txt",)):
        self.root, self.exts = os.path.abspath(root), tuple(exts)
        self._lines, self._chunks = {}, {}

    def files(self):
        out = []
        for d, _, names in os.walk(self.root):
            out += [os.path.relpath(os.path.join(d, n), self.root).replace(os.sep, "/") for n in names if n.lower().endswith(self.exts)]
        return sorted(out)

    def lines(self, name):
        if name not in self._lines:
            self._lines[name] = Path(self.root, name).read_text(encoding="utf-8-sig", errors="replace").splitlines()
        return self._lines[name]

    def resolve(self, name):
        files = self.files()
        if name in files:
            return name
        hits = [f for f in files if name.lower() in f.lower()]
        if len(hits) == 1:
            return hits[0]
        raise ValueError(f"资料库里没有叫 {name!r} 的文件" if not hits else f"{name!r} 匹配多个文件：{', '.join(hits[:8])}")

    def chunks(self, name):
        if name not in self._chunks:
            out, run = [], []
            for i, line in enumerate(self.lines(name) + [""], 1):
                if line.strip():
                    run.append((i, line))
                    if len(run) < self.CHUNK:
                        continue
                if run:
                    text = "\n".join(l for _, l in run)
                    out.append((run[0][0], run[-1][0], text, text.lower()))
                    run = []
            self._chunks[name] = out
        return self._chunks[name]

    def scored(self, query):
        """[(words matched, occurrences, file, first line, last line, text, offset of the first hit)] of every chunk."""
        words = [w for w in query.lower().split() if w]
        if not words:
            raise ValueError("query 为空")
        out = []
        for f in self.files():
            for a, b, text, low in self.chunks(f):
                hit = [w for w in words if w in low]
                if hit:
                    out.append((len(hit), sum(low.count(w) for w in hit), f, a, b, text, low.find(hit[0])))
        return out

    @staticmethod
    def hit(m, _, f, a, b, t, at, nwords):
        return {"file": f, "lines": f"{a}-{b}", "matched": f"{m}/{nwords}", "snippet": clip(t[max(0, at - 150):at + 450], 600)}

    def search(self, query, limit=8):
        scored = self.scored(query)
        scored.sort(key=lambda s: (-s[0], -s[1], s[2], s[3]))
        return [self.hit(*s, len(query.split())) for s in scored[:limit]]

    def read(self, name, start=1, end=None):
        name = self.resolve(name)
        lines = self.lines(name)
        start = max(1, start)
        end = min(len(lines), end or start + 99, start + 149)
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
        return f"{name} 行 {start}-{end} / 共 {len(lines)} 行\n" + clip(body, 12000)


def _coerce(props, args):
    """Models sometimes send object/array arguments as JSON strings; fix that quietly."""
    out = dict(args)
    for k, v in args.items():
        t = props.get(k, {}).get("type")
        if isinstance(v, str) and t in ("object", "array", "integer", "number", "boolean"):
            try:
                out[k] = json.loads(v)
            except ValueError:
                pass
    return out


class ToolBox:
    def __init__(self, referee, library):
        self.referee, self.library = referee, library

    def run(self, name, args):
        """Execute a referee-backed or library tool; returns ToolResult (never raises for bad arguments or HTTP errors)."""
        try:
            if name in ("library_search", "library_read"):
                return ToolResult(self._library(name, _coerce(ALL[name]["function"]["parameters"]["properties"], args)))
            spec = BY_NAME.get(name)
            if spec is None:
                return ToolResult(json.dumps({"error": f"没有这个工具：{name}"}, ensure_ascii=False))
            _, method, path, where, _, props, required = spec
            args = _coerce(props, args)
            missing = [k for k in required if args.get(k) in (None, "")]
            if missing:
                return ToolResult(json.dumps({"error": f"缺少必填参数：{', '.join(missing)}"}, ensure_ascii=False))
            rest = {k: v for k, v in args.items() if k in props}
            fill = {k: urllib.parse.quote(str(rest.pop(k, "")), safe="/" if k == "path" else "") for k in props if "{" + k + "}" in path}
            for k in ("hexes", "box"):
                if isinstance(rest.get(k), list):
                    rest[k] = ",".join(str(h) for h in rest[k])
            if name == "get_terrain" and not (rest.get("hexes") or rest.get("box")):
                return ToolResult(json.dumps({"error": "get_terrain 需要 hexes 或 box"}, ensure_ascii=False))
            status, data = self.referee.call(method, path.format(**fill), query=rest if where == "query" else None,
                                             body=rest if where == "json" else None)
        except (*NET_ERRORS, ValueError) as e:  # unreachable referee, unreadable library file, bad library arguments
            return ToolResult(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if not 200 <= status < 300:
            text = json.dumps({"http_status": status, "error": data}, ensure_ascii=False)
        # an accepted exercise request ends the player's turn on the referee's side (training.py)
        accepted = name == "request_exercise" and 200 <= status < 300 and isinstance(data, dict) and data.get("turn_over")
        return ToolResult(clip(text, MAX_RESULT_CHARS), status, is_turn_over(status, data) or bool(accepted))

    def _library(self, name, args):
        if name == "library_search":
            n = max(1, min(20, int(args.get("max_results") or 8)))
            return json.dumps(self.library.search(str(args.get("query", "")), n) or "没有找到", ensure_ascii=False)
        if not args.get("file"):
            return json.dumps([{"file": f, "lines": len(self.library.lines(f))} for f in self.library.files()], ensure_ascii=False)
        return self.library.read(str(args["file"]), int(args.get("start_line") or 1), int(args["end_line"]) if args.get("end_line") else None)
