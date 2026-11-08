"""Game-machine adapter: the few operations the match scripts need on the Windows machine that runs the game.

    python -I -X utf8 tools/gamehost.py push  <node> <local file or dir> <dest dir>
    python -I -X utf8 tools/gamehost.py spawn <node> <job name> --file <job.ps1> [--args <params.json>] [--cwd <dir>]
                                              [--deadline <N>m] [other flags are accepted and ignored]
    python -I -X utf8 tools/gamehost.py wait  <node> <job name> [--timeout <seconds>]
    python -I -X utf8 tools/gamehost.py kill  <node> <job name>
    python -I -X utf8 tools/gamehost.py pull  <node> <remote dir> <local dir>
    python -I -X utf8 tools/gamehost.py ps    <node> -c "<PowerShell command>"
    python -I -X utf8 tools/gamehost.py run   <node> --file <job.ps1> [--args <params.json>] [--timeout <seconds>]
    python -I -X utf8 tools/gamehost.py occupancy <node> --json     (jobs still running: {"ok": true, "table": {"entries": [...]}})

This reference implementation serves node "local": the game runs on this same Windows machine. A job is a
PowerShell script (fpc/remote/*.ps1) started detached with the parameters of its JSON file (`{"Scenario": "...",
"MaxMinutes": 90, "Calibration": true}` -> -Scenario '...' -MaxMinutes 90 -Calibration:$true); its state and output
live in $ARENA_DATA/gamehost/<job name>/ (job.json, out.log). `wait` returns the job's exit code (124 on timeout).

The match scripts call whatever ARENA_GAMEHOST names (default: this file), always with this argument shape, so a
game on another machine only needs a command with the same verbs (over SSH, WinRM, a job runner, ...).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time


REPO = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path(os.environ.get("ARENA_DATA") or REPO / "data")
STATE = DATA / "gamehost"
POWERSHELL = os.environ.get("ARENA_POWERSHELL", "powershell")


def ps_quote(v):
    return "'" + str(v).replace("'", "''") + "'"


def ps_params(args):
    out = []
    for k, v in args.items():
        if isinstance(v, bool):
            out.append(f"-{k}:${'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            out.append(f"-{k} {v}")
        else:
            out.append(f"-{k} {ps_quote(v)}")
    return " ".join(out)


def opts(argv, flags):
    """--flag value pairs out of argv; unknown flags are skipped with their value."""
    got, rest, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            val = argv[i + 1] if i + 1 < len(argv) else ""
            if a[2:] in flags:
                got[a[2:]] = val
            i += 2
        else:
            rest.append(a)
            i += 1
    return got, rest


def job_dir(name):
    return STATE / name


def alive(pid):
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                          # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def cmd_push(node, src, dest):
    os.makedirs(dest, exist_ok=True)
    s = pathlib.Path(src)
    if s.is_dir():
        shutil.copytree(s, pathlib.Path(dest) / s.name, dirs_exist_ok=True)
    else:
        shutil.copy2(s, dest)
    return 0


def cmd_pull(node, src, dest):
    s = pathlib.Path(src)
    if not s.exists():
        print(f"not found: {src}", file=sys.stderr)
        return 1
    if s.is_dir():
        shutil.copytree(s, pathlib.Path(dest) / s.name, dirs_exist_ok=True)
    else:
        os.makedirs(dest, exist_ok=True)
        shutil.copy2(s, dest)
    return 0


def cmd_ps(node, argv):
    o, _ = opts(argv, {"c"})
    if "c" not in o and len(argv) >= 2 and argv[0] == "-c":
        o["c"] = argv[1]
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", o.get("c", "")]).returncode


def cmd_spawn(node, name, argv):
    o, _ = opts(argv, {"file", "args", "cwd", "deadline"})
    d = job_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    params = json.loads(pathlib.Path(o["args"]).read_text(encoding="utf-8-sig")) if o.get("args") else {}
    deadline = int(o["deadline"].rstrip("m")) * 60 if o.get("deadline") else 0
    job = {"name": name, "file": o["file"], "params": params, "cwd": o.get("cwd") or os.getcwd(),
           "deadline_sec": deadline, "started": time.time()}
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
    p = subprocess.Popen([sys.executable, "-I", "-X", "utf8", __file__, "_run", node, name], cwd=job["cwd"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, start_new_session=os.name != "nt")
    job["runner_pid"] = p.pid
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"spawned {name} (runner pid {p.pid}); output {d / 'out.log'}")
    return 0


def cmd_run(node, name):
    """The detached runner: PowerShell job as a child, exit code into job.json."""
    d = job_dir(name)
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    command = f"& {ps_quote(os.path.abspath(job['file']))} {ps_params(job['params'])}; exit $LASTEXITCODE"
    with open(d / "out.log", "ab") as log:
        p = subprocess.Popen([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                             cwd=job["cwd"], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        job["pid"] = p.pid
        (d / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            rc = p.wait(timeout=job["deadline_sec"] or None)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            rc = 124
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    job.update(rc=rc, ended=time.time())
    (d / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    return rc


def kill_tree(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(int(pid)), 9)
        except OSError:
            pass


def cmd_wait(node, name, argv):
    o, _ = opts(argv, {"timeout"})
    end = time.time() + float(o.get("timeout") or 0) if o.get("timeout") else None
    f = job_dir(name) / "job.json"
    while True:
        job = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        if job is None:
            print(f"no such job: {name}", file=sys.stderr)
            return 1
        if "rc" in job:
            print(f"{name} ended, rc {job['rc']}")
            return int(job["rc"])
        if job.get("runner_pid") and not alive(job["runner_pid"]):
            print(f"{name}: runner gone without an exit code", file=sys.stderr)
            return 1
        if end is not None and time.time() > end:
            print(f"{name}: still running after the timeout", file=sys.stderr)
            return 124
        time.sleep(2)


def cmd_run_sync(node, argv):
    """A short job in the foreground (health probe, stop-game): its output on stdout, its exit code returned."""
    o, _ = opts(argv, {"file", "args", "timeout"})
    params = json.loads(pathlib.Path(o["args"]).read_text(encoding="utf-8-sig")) if o.get("args") else {}
    command = f"& {ps_quote(os.path.abspath(o['file']))} {ps_params(params)}; exit $LASTEXITCODE"
    try:
        return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                              timeout=float(o["timeout"]) if o.get("timeout") else None).returncode
    except subprocess.TimeoutExpired:
        return 124


def cmd_occupancy(node):
    entries = []
    for f in sorted(STATE.glob("*/job.json")) if STATE.is_dir() else []:
        job = json.loads(f.read_text(encoding="utf-8"))
        if "rc" not in job and job.get("runner_pid") and alive(job["runner_pid"]):
            entries.append({"kind": "job", "name": job["name"], "pid": job.get("pid"), "job": job["name"],
                            "cmdline": job["file"], "purpose": ""})
    print(json.dumps({"ok": True, "table": {"entries": entries}}))
    return 0


def cmd_kill(node, name):
    f = job_dir(name) / "job.json"
    if not f.exists():
        return 1
    job = json.loads(f.read_text(encoding="utf-8"))
    for k in ("pid", "runner_pid"):
        if job.get(k) and alive(job[k]):
            kill_tree(job[k])
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    verb, node, rest = argv[0], argv[1], argv[2:]
    if node != "local" and verb != "_run":
        print(f"this adapter only serves node 'local' (got {node!r}); set ARENA_GAMEHOST to a command for other machines",
              file=sys.stderr)
        return 2
    if verb == "push":
        return cmd_push(node, rest[0], rest[1])
    if verb == "pull":
        return cmd_pull(node, rest[0], rest[1])
    if verb == "ps":
        return cmd_ps(node, rest)
    if verb == "spawn":
        return cmd_spawn(node, rest[0], rest[1:])
    if verb == "_run":
        return cmd_run(node, rest[0])
    if verb == "wait":
        return cmd_wait(node, rest[0], rest[1:])
    if verb == "kill":
        return cmd_kill(node, rest[0])
    if verb == "run":
        return cmd_run_sync(node, rest)
    if verb == "occupancy":
        return cmd_occupancy(node)
    print(f"unknown verb {verb!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
