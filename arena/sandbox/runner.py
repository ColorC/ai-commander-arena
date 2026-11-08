"""Run arena reflex scripts and compute tasks in the Lua sandbox (arena/DESIGN.md section 7, rules in 10号资料 2.8).

host.lua enforces the in-Lua limits (instruction count, wall time, Lua heap) and hides io / os / package / debug; this
module adds the process limits with a Windows job object (memory, CPU time, one process, killed with the job) and a
wall-clock watchdog, and restarts a host that died or hung.
  ScriptHost(mem_mb): one long-lived host per side; host(request) is the runner referee/scripts.py expects.
  compute(code, input): one fresh host per task -> {ok, limit, limit_kind, error, result, ms, cpu_ms, log}.
  check_syntax(source): compile only, in a fresh host -> {ok, error} (script lint, materials/18 2.1).
lua.exe: $ARENA_LUA, else arena/sandbox/bin/lua.exe (built by fpc/native/build.sh).
"""
import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = HERE / "host.lua"


def lua_exe():
    return os.environ.get("ARENA_LUA") or str(HERE / "bin" / "lua.exe")


# --- Python value -> Lua data literal (one line, loaded by host.lua with an empty environment) ---------------------
def _lua_str(s):
    out = ['"']
    for b in s.encode("utf-8", "replace"):
        if b == 0x22:
            out.append('\\"')
        elif b == 0x5C:
            out.append("\\\\")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append("\\%03d" % b)
    out.append('"')
    return "".join(out)


def _lua_num(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v) if -2 ** 63 <= v < 2 ** 63 else _lua_num(float(v))
    if math.isnan(v):
        return "(0/0)"
    if math.isinf(v):
        return "(1/0)" if v > 0 else "(-1/0)"
    return repr(v)


def to_lua(v, depth=0):
    if depth > 64:
        raise ValueError("value nested deeper than 64")
    if v is None:
        return "nil"
    if isinstance(v, (bool, int, float)):
        return _lua_num(v)
    if isinstance(v, str):
        return _lua_str(v)
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(to_lua(x, depth + 1) for x in v) + "}"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if x is None:
                continue
            key = _lua_str(k) if isinstance(k, str) else _lua_num(k) if isinstance(k, (bool, int, float)) else _lua_str(str(k))
            parts.append("[" + key + "]=" + to_lua(x, depth + 1))
        return "{" + ",".join(parts) + "}"
    return _lua_str(str(v))


# --- Windows job object ------------------------------------------------------------------------------------------
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x2
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class Job:
    """A job holding one process: memory cap, optional CPU-time cap, no children, killed when the job closes."""

    def __init__(self, mem_mb, cpu_s=None):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        self.ct, self.k32 = ctypes, ctypes.WinDLL("kernel32", use_last_error=True)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                        ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

        self.Extended, self.Accounting = Extended, Accounting
        self.k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        for fn in ("SetInformationJobObject", "QueryInformationJobObject"):
            getattr(self.k32, fn).argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD] + (
                [ctypes.c_void_p] if fn.startswith("Query") else [])
        self.k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        info = Extended()
        flags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                 | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        if cpu_s:
            flags |= JOB_OBJECT_LIMIT_PROCESS_TIME
            info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_s * 10_000_000)
        info.BasicLimitInformation.LimitFlags = flags
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = int(mem_mb) << 20
        if not self.k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")

    def assign(self, proc):
        if self.handle and not self.k32.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise OSError(self.ct.get_last_error(), "AssignProcessToJobObject")

    def usage(self):
        """-> (user CPU ms of the job, peak process memory MB), or (None, None) off Windows."""
        if not self.handle:
            return None, None
        acc, ext = self.Accounting(), self.Extended()
        self.k32.QueryInformationJobObject(self.handle, 1, self.ct.byref(acc), self.ct.sizeof(acc), None)
        self.k32.QueryInformationJobObject(self.handle, 9, self.ct.byref(ext), self.ct.sizeof(ext), None)
        return acc.TotalUserTime / 10_000, ext.PeakProcessMemoryUsed / (1 << 20)

    def close(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


# --- one host process ----------------------------------------------------------------------------------------------
class Host:
    def __init__(self, mem_mb, cpu_s=None, stderr_path=None):
        self.mem_mb, self.cpu_s = mem_mb, cpu_s
        self.job = Job(mem_mb, cpu_s)
        err = open(stderr_path, "ab") if stderr_path else subprocess.DEVNULL
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # host.lua only waits on stdin until the first request, so assigning the job right after start is in time
        self.proc = subprocess.Popen([lua_exe(), "-E", str(HOST)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=err, cwd=str(HERE), creationflags=flags)
        if stderr_path:
            err.close()
        try:
            self.job.assign(self.proc)
        except OSError:
            self.kill()
            raise
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.lines.put(line)
        except (OSError, ValueError):
            pass
        self.lines.put(None)

    def call(self, request, wall_s):
        """-> reply dict; a host that dies or does not answer within wall_s is killed: {"dead": "time"|"memory"|...}."""
        try:
            self.proc.stdin.write(to_lua(request).encode("ascii") + b"\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return {"dead": self._why_dead()}
        try:
            line = self.lines.get(timeout=wall_s)
        except queue.Empty:
            self.kill()
            return {"dead": "time"}
        if line is None:
            return {"dead": self._why_dead()}
        try:
            return json.loads(line.decode("utf-8", "replace"))
        except ValueError as e:
            self.kill()
            return {"dead": "crash", "detail": f"bad reply: {e}"}

    def _why_dead(self):
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.kill()
        cpu_ms, peak_mb = self.job.usage()
        if self.cpu_s and cpu_ms is not None and cpu_ms >= self.cpu_s * 1000 * 0.95:
            return "time"
        if peak_mb is not None and peak_mb >= self.mem_mb * 0.9:
            return "memory"
        return "crash"

    def alive(self):
        return self.proc.poll() is None

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except (OSError, ValueError):
                pass
        self.job.close()

    def close(self):
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        self.kill()


def _limit_kind(reply):
    return reply.get("dead") if reply.get("dead") in ("time", "memory") else reply.get("limit") or None


class ScriptHost:
    """One side's reflex-script host. host(request) -> result as referee/scripts.py describes it."""

    def __init__(self, mem_mb=64, stderr_path=None, wall_grace_s=1.0):
        self.mem_mb, self.stderr_path, self.grace = mem_mb, stderr_path, wall_grace_s
        self.host, self.lock, self.restarts = None, threading.Lock(), 0

    def __call__(self, req):
        lim = req.get("limits") or {}
        max_ms = lim.get("ms", 200)
        body = {"mode": "script", "name": req.get("name"), "source": req.get("source"), "trigger": req.get("trigger"),
                "input": req.get("input"), "events": req.get("events") or [], "persist": req.get("persist") or {},
                "max_instr": lim.get("instructions", 2_000_000), "max_ms": max_ms,
                "mem_mb": min(lim.get("memory_mb", self.mem_mb), self.mem_mb)}
        t0 = time.perf_counter()
        with self.lock:
            if self.host is not None and not self.host.alive():
                self.host.kill()
                self.host = None
            if self.host is None:
                self.host = Host(self.mem_mb, stderr_path=self.stderr_path)
            reply = self.host.call(body, max_ms / 1000 + self.grace)
            if "dead" in reply:
                self.restarts += 1
                self.host.kill()
                self.host = None
        kind = _limit_kind(reply)
        out = {"ok": bool(reply.get("ok")), "limit": bool(kind), "limit_kind": kind, "error": reply.get("error"),
               "instructions": int(reply.get("instructions") or 0), "ms": reply.get("ms"),
               "commands": reply.get("commands") or [], "alarm": reply.get("alarm"), "report": reply.get("report"),
               "persist": reply.get("persist"),
               "log": reply.get("log") or []}
        if "dead" in reply:
            out["ok"], out["ms"] = False, (time.perf_counter() - t0) * 1000
            out["error"] = f"host {reply['dead']}" + (f": {reply['detail']}" if reply.get("detail") else "")
            if kind == "time":
                out["instructions"] = body["max_instr"]
        if not isinstance(out["persist"], dict):
            out["persist"] = {} if out["ok"] and out["persist"] == [] else out["persist"]
        return out

    def close(self):
        with self.lock:
            if self.host is not None:
                self.host.close()
                self.host = None


def check_syntax(source, name="script", mem_mb=64, stderr_path=None):
    """Compile `source` in a fresh host without running any of it -> {"ok", "error"} ("<name>:<line>: ..." on a syntax
    error). A host that cannot start (no lua.exe) raises OSError."""
    host = Host(mem_mb, cpu_s=2, stderr_path=stderr_path)
    try:
        reply = host.call({"mode": "check", "name": name, "source": source}, 10)
    finally:
        host.close()
    if "dead" in reply:
        return {"ok": False, "error": f"host {reply['dead']}", "unavailable": True}
    return {"ok": bool(reply.get("ok")), "error": reply.get("error")}


def compute(code, input=None, cpu_s=10, mem_mb=256, stderr_path=None):
    """One compute task in a fresh host: CPU time capped by the job, wall time by the watchdog."""
    t0 = time.perf_counter()
    host = Host(mem_mb, cpu_s=cpu_s, stderr_path=stderr_path)
    try:
        reply = host.call({"mode": "task", "name": "task", "source": code, "input": input,
                           "max_ms": cpu_s * 1500, "mem_mb": mem_mb}, cpu_s * 2 + 5)
        cpu_ms, _ = host.job.usage()
    finally:
        host.close()
    kind = _limit_kind(reply)
    out = {"ok": bool(reply.get("ok")), "limit": bool(kind), "limit_kind": kind, "error": reply.get("error"),
           "result": reply.get("result"), "ms": reply.get("ms"), "cpu_ms": cpu_ms, "log": reply.get("log") or []}
    if "dead" in reply:
        out["ok"], out["ms"] = False, (time.perf_counter() - t0) * 1000
        out["error"] = f"host {reply['dead']}" + (f": {reply['detail']}" if reply.get("detail") else "")
    return out
