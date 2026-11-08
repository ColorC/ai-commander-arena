#!/usr/bin/env bash
# P2 live smoke test (materials/10 P2): the referee here, two scripted agents (fake route, fixed tool calls), the relay
# and a two-player game on the game machine, stopped after N game minutes.
#
#   tools/p2_smoke.sh <local run dir> [game minutes, default 30]
#
# RELAY_EXTRA (optional) is appended to the relay's JSON arguments, e.g. RELAY_EXTRA=',"ReplayCloseSec":120'.
#
# Everything the run leaves is under <run>: referee logs, final.json, agent transcripts, stopper.jsonl, the relay's
# pulled folder (relay.log, rec.jsonl, release-*.json, collected debug log). Local processes are stopped by PID.
set -u
run=$1; minutes=${2:-30}
here=$(cd "$(dirname "$0")/.." && pwd)
BS='\'
# the game machine (README): FPC_GAME_ROOT its game folder, ARENA_GAME_NODE its name for the game-host adapter
# (default local), ARENA_GAMEHOST the adapter command (default tools/gamehost.py: the game on this machine)
node=${ARENA_GAME_NODE:-local}
gamehost() { if [ -n "${ARENA_GAMEHOST:-}" ]; then eval "$ARENA_GAMEHOST \"\$@\""; else python -I -X utf8 "$here/tools/gamehost.py" "$@"; fi; }
game=${FPC_GAME_ROOT:?set FPC_GAME_ROOT to the game folder on the game machine}
name="p2-smoke-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$run/args"
cd "$here"
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$run/smoke.log"; }

python -I -X utf8 arena/referee --run "$run" --scenario alamo-at-soest --port 41010 > "$run/referee.out" 2>&1 &
ref_pid=$!
for _ in $(seq 1 30); do [ -f "$run/secrets/relay.token" ] && break; sleep 1; done
[ -f "$run/secrets/relay.token" ] || { say "referee did not start"; kill $ref_pid; exit 1; }
say "referee pid $ref_pid"

# two scripted commanders: look, give one move order, register a minute script, subscribe to a 10-minute timer and
# their Orders phases; every later wake thinks 90 s (so the real-time gate holds the game for over a game minute) and
# queues one more move of the same unit, back and forth
python -I -X utf8 - "$run" <<'EOF'
import json, sys
run = sys.argv[1]
plan = {0: ("2/Sct", 2427, 2327), 1: ("1/RC/9 MR", 5111, 5311)}
subs = [{"type": "timer", "every_min": 10}, {"type": "orders_phase"}, {"type": "new_contact"}]
for side, (unit, a, b) in plan.items():
    move = lambda dest: {"name": "submit_command", "arguments": {"kind": "orders", "unit": unit,
                                                                 "orders": [{"orders": "Move Hasty", "destination_location": dest}]}}
    steps = [{"tool_calls": [{"name": "get_summary"}, {"name": "list_units"}, {"name": "get_contacts"}]},
             {"tool_calls": [move(a), {"name": "put_script", "arguments": {"name": "tick", "trigger": {"on": "minute"},
                             "source": "memory.n = (memory.n or 0) + 1\nif memory.n % 10 == 0 then print('minute', memory.n, #(view.units or {})) end"}}]},
             {"tool_calls": [{"name": "end_turn", "arguments": {"subscriptions": subs}}]}]
    for i in range(40):
        steps += [{"sleep": 90, "tool_calls": [{"name": "get_summary"}, move(b if i % 2 == 0 else a)]},
                  {"tool_calls": [{"name": "end_turn"}]}]
    json.dump(steps, open(f"{run}/args/agent{side}.json", "w", encoding="utf-8"), ensure_ascii=False)
EOF
pids=""
for s in 0 1; do
  python -I -X utf8 arena/agent/arena_agent.py --side $s --route fake --script "$run/args/agent$s.json" \
    --token-file "$run/secrets/side$s.token" --run "$run" > "$run/agent$s.out" 2>&1 &
  pids="$pids $!"
done
say "agents pids$pids"

gamehost push "$node" "$run/secrets/relay.token" "$game${BS}ai-arena${BS}in" > "$run/args/push.txt" 2>&1
printf '{"RunName":"%s","TokenFile":"%s","TimedMessages":1,"MaxMinutes":%d%s}' \
  "$name" "$game${BS}ai-arena${BS}in${BS}relay.token" "$(( minutes * 2 + 30 ))" "${RELAY_EXTRA:-}" | sed 's/\\/\\\\/g' > "$run/args/relay.json"
gamehost spawn "$node" "$name" --file "$here/fpc/remote/relay.ps1" --args "$run/args/relay.json" --cwd "$game" \
  --window hidden --deadline "$(( minutes * 2 + 40 ))m" --kind experiment \
  --purpose "ai-commander-arena P2 smoke: two-player Alamo at Soest, scripted agents, $minutes game minutes" --mem-gb 3 \
  > "$run/args/spawn.txt" 2>&1
say "relay spawned as $name (rc $?)"

python -I -X utf8 tools/p2_stopper.py "$run" "$minutes" "$(( minutes * 2 + 20 ))" > "$run/stopper.out" 2>&1
say "stopper done: $(tail -1 "$run/stopper.jsonl")"
gamehost wait "$node" "$name" --timeout 1800 > "$run/args/wait.txt" 2>&1
say "relay finished (wait rc $?)"
gamehost pull "$node" "$game${BS}ai-arena${BS}out${BS}$name" "$run/" > "$run/args/pull.txt" 2>&1
gamehost ps "$node" -c "Remove-Item -LiteralPath '$game${BS}ai-arena${BS}in${BS}relay.token' -Force" > "$run/args/token-rm.txt" 2>&1
sleep 20
for p in $pids $ref_pid; do kill "$p" 2> /dev/null; done
say "local processes stopped; relay folder $run/$name"
