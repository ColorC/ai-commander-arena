#!/usr/bin/env bash
# P1 step 3: computer-vs-computer calibration games on the game machine, two at a time, official result read from the
# Battle Over dialog (fpc/remote/job-p0.ps1 -EndScreens -TimedMessages 0, then tools/fpc_endscreen.py).
#
#   tools/p1_calibrate.sh <queue.tsv> <local out dir>
#
# queue.tsv: one game per line, "<tag><TAB><scenario><TAB><game minutes>". Games run in pairs; the second of a pair
# starts 90 s after the first (recorder.lua reads ai-arena\in\run.lua once at load, and the game names its debug
# log and auto-saves by start minute). Appends one line per game to <out>/results.tsv.
set -u
queue=$1; out=$2
here=$(cd "$(dirname "$0")/.." && pwd)
BS='\'
# the game machine (README): FPC_GAME_ROOT its game folder, ARENA_GAME_NODE its name for the game-host adapter
# (default local), ARENA_GAMEHOST the adapter command (default tools/gamehost.py: the game on this machine)
node=${ARENA_GAME_NODE:-local}
gamehost() { if [ -n "${ARENA_GAMEHOST:-}" ]; then eval "$ARENA_GAMEHOST \"\$@\""; else python -I -X utf8 "$here/tools/gamehost.py" "$@"; fi; }
game=${FPC_GAME_ROOT:?set FPC_GAME_ROOT to the game folder on the game machine}
remote_out="$game${BS}ai-arena${BS}out"
mkdir -p "$out/args"
[ -f "$out/results.tsv" ] || printf 'tag\tscenario\tvp_ratio_pct\tstarting_ratio_pct\tresult\tstars\tcommander\n' > "$out/results.tsv"

start_game() { # tag scenario minutes port
  local max=$(( $3 * 10 / 65 * 16 / 10 + 15 ))
  printf '{"Scenario":"%s","Config":"arena.ini","StyleOfPlay":2,"RunForMinutes":%d,"MaxMinutes":%d,"SleepTo":0,"PauseFrom":999999,"EndScreens":true,"Quiet":1,"TimedMessages":0,"Telnet":"127.0.0.1:%d","RunName":"p1-cal-%s"}' \
    "$2" "$3" "$max" "$4" "$1" > "$out/args/$1.json"
  gamehost spawn "$node" "fpc-p1-cal-$1" --file "$here/fpc/remote/job-p0.ps1" --args "$out/args/$1.json" \
    --cwd "$game" --window hidden --deadline "$(( max + 10 ))m" --kind experiment \
    --purpose "ai-commander-arena P1 calibration: $2 computer vs computer" --mem-gb 3 > "$out/args/$1.spawn.txt" 2>&1
  echo "$(date +%H:%M:%S) started $1 ($2, $3 min, max $max min)"
}

finish_game() { # tag scenario
  gamehost wait "$node" "fpc-p1-cal-$1" --timeout 7200 > "$out/args/$1.wait.txt" 2>&1
  gamehost pull "$node" "$remote_out${BS}p1-cal-$1" "$out/" > "$out/args/$1.pull.txt" 2>&1
  local run="$out/p1-cal-$1"
  if ls "$run"/end-*.bmp > /dev/null 2>&1; then
    python -X utf8 "$here/tools/fpc_endscreen.py" "$run" > "$run/endscreen.log" 2>&1
  fi
  python -I -X utf8 - "$run/end-result.json" "$1" "$2" >> "$out/results.tsv" <<'EOF'
import json, sys
path, tag, scen = sys.argv[1:4]
try:
    r = json.load(open(path, encoding="utf-8"))["result"]
except Exception:
    r = {}
print("\t".join(str(x) for x in (tag, scen, r.get("vp_ratio_pct", ""), r.get("starting_ratio_pct", ""),
                                 r.get("result", ""), r.get("stars", ""), r.get("commander", ""))))
EOF
  echo "$(date +%H:%M:%S) finished $1: $(tail -1 "$out/results.tsv")"
}

mapfile -t lines < "$queue"
i=0
while [ $i -lt ${#lines[@]} ]; do
  IFS=$'\t' read -r tagA scenA minA <<< "${lines[$i]}"
  start_game "$tagA" "$scenA" "$minA" 47823
  if [ $(( i + 1 )) -lt ${#lines[@]} ]; then
    IFS=$'\t' read -r tagB scenB minB <<< "${lines[$(( i + 1 ))]}"
    sleep 90
    start_game "$tagB" "$scenB" "$minB" 47824
    finish_game "$tagA" "$scenA"
    finish_game "$tagB" "$scenB"
  else
    finish_game "$tagA" "$scenA"
  fi
  i=$(( i + 2 ))
done
echo "$(date +%H:%M:%S) all done"
