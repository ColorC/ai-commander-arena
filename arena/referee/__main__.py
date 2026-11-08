"""Start the referee.

    python -I arena/referee --run <run dir> --scenario alamo-at-soest --port 41010

(`python -I -m arena.referee` does not work from the repo root: -I drops the current directory from sys.path, so
`arena` cannot be found; this file puts the repo root on sys.path itself.)
Writes four token files under <run>/secrets/ (side0, side1, relay, admin), serves until stopped (the caller stops the
process by PID after the game ended or POST /admin/stop).
Reflex scripts and compute tasks run in the Lua sandbox (arena/sandbox, needs lua.exe: $ARENA_LUA or
arena/sandbox/bin/lua.exe); without it they are stored but not run and /agent/compute answers 501.
Exercise options (materials/10 2.10, arena/DESIGN.md 9): --pacing turn, --agents 0 (side 1 is the game's AI),
--prematch-sec, --units0/--units1 (small experiment), --workspace0/--workspace1 (the players' folders), --batch-prefix.
--mode training starts the training referee instead (no relay, no game; arena/referee/training.py).
"""
import argparse
import json
import os
import sys

if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    __package__ = "arena.referee"

from arena.referee import rules  # noqa: E402
from arena.referee.core import Referee  # noqa: E402
from arena.referee.server import Server, write_tokens  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402


class SideHosts:
    """One long-lived ScriptHost per side behind the single script runner the Referee takes."""

    def __init__(self, hosts):
        self.hosts = hosts

    def __call__(self, req):
        return self.hosts[req["side"]](req)

    def close(self):
        for h in self.hosts.values():
            h.close()


def sandbox(run_dir, scripts=True):
    """-> (script runner, compute runner), or (None, None) when the Lua sandbox is not installed."""
    from arena.sandbox import runner
    if not os.path.isfile(runner.lua_exe()):
        print(f"no lua.exe at {runner.lua_exe()}: scripts are stored but not run, compute is off", file=sys.stderr, flush=True)
        return None, None
    err = lambda name: os.path.join(run_dir, "logs", name)  # noqa: E731
    os.makedirs(os.path.join(run_dir, "logs"), exist_ok=True)
    hosts = SideHosts({s: runner.ScriptHost(rules.SCRIPT_MEM_MB, stderr_path=err(f"sandbox-side{s}.err"))
                       for s in (0, 1)}) if scripts else None
    return hosts, lambda code, input=None, cpu_s=rules.COMPUTE_CPU_S, mem_mb=rules.COMPUTE_MEM_MB: runner.compute(
        code, input, cpu_s=cpu_s, mem_mb=mem_mb, stderr_path=err("sandbox-compute.err"))


def names(text):
    """--units0 "a,b,c" -> ["a", "b", "c"] (unit names of the scenario have no commas)"""
    return [n.strip() for n in (text or "").split(",") if n.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run directory (logs/, secrets/, workspace/ are created in it)")
    ap.add_argument("--scenario", required=True, help="folder name under $ARENA_DATA/scenarios, e.g. alamo-at-soest")
    ap.add_argument("--port", type=int, default=41010)
    ap.add_argument("--host", default="0.0.0.0", help="the relay on the game machine connects over the LAN; use 127.0.0.1 for local tests")
    ap.add_argument("--data-root", help="override $ARENA_DATA/scenarios")
    ap.add_argument("--mode", choices=("match", "training"), default="match",
                    help="match: a game (also every exercise); training: the P4 training referee, no game")
    ap.add_argument("--no-sandbox", action="store_true", help="do not start the Lua sandbox (scripts stored, not run)")
    ap.add_argument("--pacing", choices=("formal", "turn"), default="formal",
                    help="turn: exercise pacing of 2.10 (gate off, own Orders phase waits for the commander)")
    ap.add_argument("--agents", default="0,1", help="sides played by an agent; the other side is the game's AI")
    ap.add_argument("--prematch-sec", type=float, help=f"deployment time (default {rules.PREMATCH_REAL_SEC} s; "
                                                       f"exercises {rules.EXERCISE_PREMATCH_SEC})")
    ap.add_argument("--units0", help="small experiment: comma-separated unit names side 0 may use (the rest hold)")
    ap.add_argument("--units1", help="small experiment: the same for side 1")
    ap.add_argument("--workspace0", help="side 0's workspace (notes and scripts); default <run>/workspace/side0")
    ap.add_argument("--workspace1", help="side 1's workspace")
    ap.add_argument("--batch-prefix", default="", help="in front of every release batch id (parallel exercise lanes)")
    ap.add_argument("--exercise", action="store_true", help="a P4 exercise: the briefing tells the agents so")
    ap.add_argument("--full-max", type=int, default=rules.EXERCISE_FULL_MAX, help="training: full exercises per player")
    ap.add_argument("--small-sec", type=float, default=rules.EXERCISE_SMALL_WALL_SEC,
                    help="training: real seconds of small experiments per player")
    ap.add_argument("--limit-sec", type=float, default=rules.TRAINING_REAL_SEC, help="training: its length (the director ends it)")
    ap.add_argument("--supplement-file", help="training: a supplementary session; the ruling's text (UTF-8) for the players")
    ap.add_argument("--self-full-min", type=int, default=0, help="training: full exercises per player that must be vs self")
    ap.add_argument("--carry-file", help="training: carried on from an earlier referee; JSON {players: {side: {clock_sec}}, exercises: [its finished exercises]}")
    a = ap.parse_args(argv)
    if a.batch_prefix and not all(c.isalnum() or c in "_-" for c in a.batch_prefix):
        ap.error("--batch-prefix: letters, digits, _ and - only")
    os.makedirs(a.run, exist_ok=True)
    sc = Scenario(a.scenario, a.data_root)
    workspaces = {s: w for s, w in ((0, a.workspace0), (1, a.workspace1)) if w}
    try:
        if a.mode == "training":
            from arena.referee.training import Training
            _, compute = (None, None) if a.no_sandbox else sandbox(a.run, scripts=False)
            supplement = None
            if a.supplement_file:
                with open(a.supplement_file, encoding="utf-8") as f:
                    supplement = f.read().strip()
            carry = None
            if a.carry_file:
                with open(a.carry_file, encoding="utf-8") as f:
                    carry = json.load(f)
            ref = Training(a.run, sc, compute_runner=compute, workspaces=workspaces, full_max=a.full_max,
                           small_wall_sec=a.small_sec, limit_sec=a.limit_sec, supplement=supplement, carry=carry,
                           self_full_min=a.self_full_min)
        else:
            players = tuple(int(x) for x in a.agents.split(",") if x.strip())
            scripts, compute = (None, None) if a.no_sandbox else sandbox(a.run)
            ref = Referee(a.run, sc, script_runner=scripts, compute_runner=compute,
                          pacing=a.pacing, players=players, prematch_sec=a.prematch_sec,
                          experiment={0: names(a.units0), 1: names(a.units1)}, workspaces=workspaces,
                          batch_prefix=a.batch_prefix, exercise=a.exercise)
    except ValueError as e:
        ap.error(str(e))
    srv = Server((a.host, a.port), ref, write_tokens(a.run))
    what = "training referee" if a.mode == "training" else "referee"
    print(f"{what} for {ref.sc.name} on {a.host}:{srv.server_address[1]}; tokens in {os.path.abspath(os.path.join(a.run, 'secrets'))}",
          flush=True)
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        ref.close()


if __name__ == "__main__":
    main()
