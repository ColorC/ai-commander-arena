#!/usr/bin/env bash
# P3 rehearsal (materials/10 P3): one whole game of the match scenario under the full rules, model against model.
#
#   tools/p3_rehearsal.sh <local run dir> [route side 0] [route side 1] [wall cap minutes, default 300] [stop at game minutes]
#
# The last argument is for a short check before the whole game (0 or left out: play to the end).
# Routes are arena/agent/routes.py names (default deepseek-proxy for both). The game plays to its own end; the
# stopper only stops it when the wall cap runs out. Everything the run leaves is under <run>: referee logs,
# final.json, agent transcripts, stopper.jsonl, the relay's pulled folder (relay.log, rec.jsonl, end-screen shots,
# collected debug log and saves). Local processes are stopped by PID.
#
# ARENA_HARNESS=cli: each side is played by an official CLI harness through arena/harness/driver.py instead of
# arena-agent; the route arguments are then the CLI names (kimi / opencode, default kimi for side 0 and opencode for
# side 1), and ARENA_HARNESS_MODEL0 / ARENA_HARNESS_MODEL1 pick the models (driver.py --model). Unset: unchanged.
# Against the game's AI (the P4 exam): ARENA_AGENTS=0 or 1 (the side an agent plays; referee --agents), ARENA_STYLE
# the game's StyleOfPlay for that, ARENA_CONFIG its config file (none: launch without -config). ARENA_PORT / ARENA_TELNET for a second lane
# (defaults 41010 / 127.0.0.1:47823). P5 matches: ARENA_WORKSPACE0 / ARENA_WORKSPACE1 = the workspace (notes and
# scripts from the training) of the player on that side (referee --workspace0/1; unset: <run>/workspace/side<N>).
set -u
# bash reads a script while it runs it: an edit of this file during a game (hours) would make the running copy read
# from a shifted offset (P3 10-30). So the run executes a private copy kept in the run dir.
if [ -z "${ARENA_SCRIPT_COPY:-}" ]; then
  mkdir -p "$1/args"; cp "$0" "$1/args/p3_rehearsal.sh"
  ARENA_SCRIPT_COPY=1 ARENA_HERE="$(cd "$(dirname "$0")/.." && pwd)" exec bash "$1/args/p3_rehearsal.sh" "$@"
fi
harness=${ARENA_HARNESS:-}
if [ "$harness" = cli ]; then def0=kimi; def1=opencode; else def0=deepseek-proxy; def1=deepseek-proxy; fi
run=$1; route0=${2:-$def0}; route1=${3:-$def1}; cap=${4:-300}; stop_min=${5:-0}
agents=${ARENA_AGENTS:-0,1}; port=${ARENA_PORT:-41010}; telnet=${ARENA_TELNET:-127.0.0.1:47823}
here=$ARENA_HERE
BS='\'
# the game machine (README): FPC_GAME_ROOT its game folder, ARENA_GAME_NODE its name for the game-host adapter
# (default local), ARENA_GAMEHOST the adapter command (default tools/gamehost.py: the game on this machine)
node=${ARENA_GAME_NODE:-local}
gamehost() { if [ -n "${ARENA_GAMEHOST:-}" ]; then eval "$ARENA_GAMEHOST \"\$@\""; else python -I -X utf8 "$here/tools/gamehost.py" "$@"; fi; }
game=${FPC_GAME_ROOT:?set FPC_GAME_ROOT to the game folder on the game machine}
name="p3-rehearsal-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$run/args"
cd "$here"
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$run/rehearsal.log"; }

python -I -X utf8 arena/referee --run "$run" --scenario alamo-at-soest --port "$port" --agents "$agents" \
  ${ARENA_WORKSPACE0:+--workspace0 "$ARENA_WORKSPACE0"} ${ARENA_WORKSPACE1:+--workspace1 "$ARENA_WORKSPACE1"} > "$run/referee.out" 2>&1 &
ref_pid=$!
for _ in $(seq 1 30); do [ -f "$run/secrets/relay.token" ] && break; sleep 1; done
[ -f "$run/secrets/relay.token" ] || { say "referee did not start"; kill $ref_pid; exit 1; }
say "referee pid $ref_pid"

pids=""
for s in ${agents//,/ }; do
  route=$route0; [ $s = 1 ] && route=$route1
  if [ "$harness" = cli ]; then
    model=ARENA_HARNESS_MODEL$s; model=${!model:-}
    env -u KIMI_API_KEY python -I -X utf8 arena/harness/driver.py --side $s --cli "$route" ${model:+--model "$model"} \
      --referee "http://127.0.0.1:$port" --token-file "$run/secrets/side$s.token" --run "$run" > "$run/agent$s.out" 2>&1 &
  else
    env python -I -X utf8 arena/agent/arena_agent.py --side $s --route "$route" \
      --referee "http://127.0.0.1:$port" --token-file "$run/secrets/side$s.token" --run "$run" > "$run/agent$s.out" 2>&1 &
  fi
  pids="$pids $!"
done
say "agents pids$pids (routes $route0 / $route1)"

# the token goes up under the run's own name: two lanes at once must not share (or delete) one relay.token
cp "$run/secrets/relay.token" "$run/secrets/$name.token"
gamehost push "$node" "$run/secrets/$name.token" "$game${BS}ai-arena${BS}in" > "$run/args/push.txt" 2>&1
extra=""
[ -n "${ARENA_STYLE:-}" ] && extra="$extra,\"StyleOfPlay\":$ARENA_STYLE"
[ -n "${ARENA_CONFIG:-}" ] && extra="$extra,\"Config\":\"${ARENA_CONFIG/#none/}\""   # none: no -config
printf '{"RunName":"%s","TokenFile":"%s","TimedMessages":1,"MaxMinutes":%d,"Referee":"http://%s:%s","Telnet":"%s"%s}' \
  "$name" "$game${BS}ai-arena${BS}in${BS}$name.token" "$(( cap + 10 ))" "${ARENA_REFEREE_HOST:-127.0.0.1}" "$port" "$telnet" "$extra" | sed 's/\\/\\\\/g' > "$run/args/relay.json"
gamehost spawn "$node" "$name" --file "$here/fpc/remote/relay.ps1" --args "$run/args/relay.json" --cwd "$game" \
  --window hidden --deadline "$(( cap + 20 ))m" --kind experiment \
  --purpose "ai-commander-arena P3 rehearsal: two-player Alamo at Soest, $route0 vs $route1, whole game" --mem-gb 3 \
  > "$run/args/spawn.txt" 2>&1
say "relay spawned as $name (rc $?)"

# stop_min 0 -> 100000: never stop on game time, only on the wall cap (the game ends by itself)
[ "$stop_min" -gt 0 ] 2> /dev/null || stop_min=100000
python -I -X utf8 tools/p2_stopper.py "$run" "$stop_min" "$cap" "http://127.0.0.1:$port" > "$run/stopper.out" 2>&1
say "stopper done: $(tail -1 "$run/stopper.jsonl")"
gamehost wait "$node" "$name" --timeout 1800 > "$run/args/wait.txt" 2>&1
say "relay finished (wait rc $?)"
gamehost pull "$node" "$game${BS}ai-arena${BS}out${BS}$name" "$run/" > "$run/args/pull.txt" 2>&1
gamehost ps "$node" -c "Remove-Item -LiteralPath '$game${BS}ai-arena${BS}in${BS}$name.token' -Force" > "$run/args/token-rm.txt" 2>&1
sleep 20
for p in $pids $ref_pid; do kill "$p" 2> /dev/null; done
say "local processes stopped; relay folder $run/$name"
