"""A season-2 player's home (materials/18 2.2): one persistent identity, one ACP session for the whole season.

<homes>/<kind>/<name>/ (kind "players" or "sparring"):
  session.json   cli, model, session id, the fixed cwd, and every session/new, session/load, session/resume with the
                 time, phase, run, driver PID and CLI PID (the record of the season's one conversation)
  session.lock   the driver that holds the session now: PID + process start time (a lock whose process is gone, or
                 whose PID now belongs to another process, is stale and may be taken over)
  cwd/           the CLI's working directory, never changed (Kimi keys its session store by it); Kimi's agent file
  workspace/     notes and scripts (the referees' --workspace<side> for this player; nothing else reads it)
  state/         CLI state: the opencode config directories, the season system prompt that was given
  scratch/<sub>/ each subagent's own working directory (not the player's notes)

The session's system prompt is the season prompt below, the same text in every phase, so the provider's prompt cache
survives a phase switch; each phase's rules come as the first message of the phase (driver.py, phase_intro).
A sparring partner (the opponent in exercises against oneself) is the same model with a home of its own under
sparring/: its own session, workspace and memory; neither can read the other's folders.
"""
import ctypes
import hashlib
import json
import os
import time

KINDS = ("players", "sparring")
SEASON_SYSTEM = (
    "你是 AI 指挥官竞技场第二届的{who}。整个赛季（起步课题、训练、演习、正式赛）都在这一个会话里进行，前面的对话就是你的记忆；"
    "你的笔记和脚本也一直保留。\n\n"
    "每进入一个新阶段，你会先收到一条以“[阶段开始”开头的消息，里面是这个阶段的规则全文、你指挥哪一方、对手是谁。"
    "以最新一条阶段消息为准，之前阶段的规则作废。\n\n"
    "你只能通过裁判提供的工具行动，没有别的文件、命令或网络。每次唤醒是一个回合，按阶段规则里的写法结束回合。\n\n"
    "设施故障（游戏没起来、卡死、中继失联、CLI 进程意外退出、模型服务连续出错）由裁判自动从头重开那一局，不算你的时间；"
    "重开时会在消息里明确告诉你出了什么故障。\n")
WHO = {"players": "参赛选手（{name}）",
       "sparring": "陪练（陪 {name} 练习的对手）：同一个模型，在演习里扮演 {name} 的对手。你有自己的笔记和记忆，"
                   "读不到 {name} 的笔记，{name} 也读不到你的。每一局都尽全力去赢"}


def season_system(kind, name):
    return SEASON_SYSTEM.format(who=WHO[kind].format(name=name))


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() if t is None else t))


def proc_start_time(pid):
    """The start time of a running process (Windows: creation FILETIME as Unix seconds; elsewhere /proc start ticks),
    None when no such process is running. PID + start time names one process: a reused PID has another start time."""
    if pid is None:
        return None
    if os.name == "nt":
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return None
            ft = [ctypes.c_ulonglong() for _ in range(4)]
            if not k32.GetProcessTimes(ctypes.c_void_p(h), *[ctypes.byref(x) for x in ft]):
                return None
            return round(ft[0].value / 1e7 - 11644473600, 3)
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            return float(f.read().split(")")[-1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def same_process(pid, started):
    t = proc_start_time(pid)
    return t is not None and started is not None and abs(t - float(started)) < 1.0


class SessionBusy(RuntimeError):
    pass


def _write_json(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Home:
    def __init__(self, root, name, kind="players"):
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        self.root, self.name, self.kind = os.path.abspath(root), name, kind
        self.dir = os.path.join(self.root, kind, name)
        self.cwd, self.workspace = os.path.join(self.dir, "cwd"), os.path.join(self.dir, "workspace")
        self.state, self.scratch_root = os.path.join(self.dir, "state"), os.path.join(self.dir, "scratch")
        self.session_path, self.lock_path = os.path.join(self.dir, "session.json"), os.path.join(self.dir, "session.lock")
        for d in (self.cwd, self.workspace, self.state, self.scratch_root):
            os.makedirs(d, exist_ok=True)

    def scratch(self, agent):
        d = os.path.join(self.scratch_root, agent)
        os.makedirs(d, exist_ok=True)
        return d

    def system_text(self):
        return season_system(self.kind, self.name)

    # ---- session.json ---------------------------------------------------------------------------------------------
    def record(self):
        return _read_json(self.session_path) or {}

    @property
    def session_id(self):
        return self.record().get("session_id")

    def check_cli(self, cli, model):
        """The season's session belongs to one CLI and model: a home made for another refuses to be loaded by this one."""
        rec = self.record()
        if rec.get("session_id") and (rec.get("cli"), rec.get("model")) != (cli, model):
            raise ValueError(f"{self.dir}: the session is {rec.get('cli')} {rec.get('model')}, not {cli} {model}")

    def note_open(self, op, session_id, cli, model, cli_pid, phase=None, run=None, **extra):
        """Record one session/new|load|resume. A new session id on a home that has one is refused (continuity)."""
        rec = self.record()
        if op == "new":
            if rec.get("session_id"):
                raise ValueError(f"{self.dir} already has session {rec['session_id']}: load it, do not start another")
            rec = {"player": self.name, "kind": self.kind, "cli": cli, "model": model, "session_id": session_id,
                   "cwd": self.cwd, "created": now_iso(), "system_sha1": hashlib.sha1(self.system_text().encode("utf-8")).hexdigest(),
                   "history": []}
        elif session_id != rec.get("session_id"):
            raise ValueError(f"{op} of {session_id} but the home's session is {rec.get('session_id')}")
        rec["history"].append({"op": op, "time": now_iso(), "phase": phase, "run": run, "driver_pid": os.getpid(),
                               "cli_pid": cli_pid, "session_id": session_id, **extra})
        _write_json(self.session_path, rec)
        return rec

    def note_phase_text(self, sha1):
        """Remember that a phase text was given in full in this session (the next time a reference is enough)."""
        rec = self.record()
        seen = rec.setdefault("phase_texts", [])
        if sha1 not in seen:
            seen.append(sha1)
            _write_json(self.session_path, rec)

    def phase_text_seen(self, sha1):
        return sha1 in (self.record().get("phase_texts") or [])

    # ---- the lock ----------------------------------------------------------------------------------------------------
    def acquire(self, phase=None, run=None, pid=None):
        """Take the session for this process. SessionBusy when a live driver holds it. -> the lock record"""
        return PidLock(self.lock_path, f"{self.name}'s session").acquire(pid, phase=phase, run=run)

    def release(self, pid=None):
        return PidLock(self.lock_path).release(pid)

    def holder(self):
        """The live lock holder's record, or None."""
        return PidLock(self.lock_path).holder()


class PidLock:
    """A lock file naming its holder by PID + process start time. A holder that is gone, or a PID that now belongs to
    another process, leaves a stale lock that the next taker removes. Used for a player's session and for the game
    machine (one game at a time across director processes)."""

    def __init__(self, path, what=None):
        self.path, self.what = path, what or os.path.basename(path)

    def acquire(self, pid=None, **info):
        pid = os.getpid() if pid is None else pid
        mine = {"pid": pid, "started": proc_start_time(pid), "since": now_iso(), **info}
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                held = _read_json(self.path) or {}
                if held.get("pid") == pid and same_process(pid, held.get("started")):
                    return held
                if held and same_process(held.get("pid"), held.get("started")):
                    raise SessionBusy(f"{self.what} is held by PID {held.get('pid')} "
                                      f"({', '.join(f'{k} {v}' for k, v in held.items() if k not in ('pid', 'started'))})")
                stale = f"{self.path}.stale-{os.getpid()}-{int(time.time() * 1000)}"
                try:
                    os.replace(self.path, stale)  # one taker wins the rename; the others retry
                    os.remove(stale)
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(mine, f, ensure_ascii=False)
            return mine
        raise SessionBusy(f"could not take {self.path}")

    def release(self, pid=None):
        pid = os.getpid() if pid is None else pid
        held = _read_json(self.path) or {}
        if held.get("pid") == pid:
            try:
                os.remove(self.path)
            except OSError:
                pass
            return True
        return False

    def holder(self):
        held = _read_json(self.path)
        return held if held and same_process(held.get("pid"), held.get("started")) else None
