#!/usr/bin/env bash
# Season 2 scenario calibration on the game machine (materials/18 section 3.2 step 4): one game at a time (two games on one
# machine share the game's AppData files, materials/18 section 2.3), official result read from the end screens
# (fpc/remote/job-p0.ps1 -EndScreens -TimedMessages 0, then tools/fpc_endscreen.py).
#
#   tools/s2_calibrate.sh <queue.tsv> <local out dir>
#
# queue.tsv: one game per line, "<tag><TAB><scenario><TAB><game minutes><TAB><mode>[<TAB>attach]", mode one of
#   aivai         computer vs computer (arena.ini)
#   passive-blue  computer plays red, blue gives no orders at all (no -config, -StyleOfPlay 1, job-p0 -Passive)
#   freeze-red    computer vs computer, but every game minute red's units are put back to the orders they had
#                 (fpc/lua/p0-probe.lua freeze_side): red's commander AI never gets an order through
#   freeze-blue   the same for blue, to compare the freeze with a real passive-blue game
#   vpprobe:<side>:<hex,hex,...>  VP location probe: both sides human and passive (arena-h2h.ini), at load the other
#                 side's units leave those hexes and a ground unit of <side> is put on each (p0-probe.lua vp_probe);
#                 the end screens then give <side>'s "VP Locations" for the hexes that are VP locations
# Every game runs with job-p0 -Calibration (the probe's own test moves and orders are off).
# "attach": the job fpc-s2-cal-<tag> is already running on the game machine; only wait, pull and read it.
# Each game holds the game machine lock $ARENA_DATA/game-machine.lock (arena.harness.player.PidLock,
# the same lock tools/s2_director.py takes) from the machine check to the pull; held elsewhere: it waits (up to 4 h).
# The queue file is read again before each game, so lines can be appended while the driver runs.
# Run a copy of this script (bash reads a running script by byte offset; editing it mid-run breaks the driver).
# Any -config file leaves both sides human (game machine 10-30, tools/p4_training.py AI_STYLE; the first try of
# passive-blue with arena-redai.ini 11-01 00:15 played 5 hours with no unit of either side moving), and without one
# the computer only ever takes Player 2, so "computer plays blue, red passive" is not available from the launcher.
# Appends one line per game to <out>/results.tsv, with the multiplayer score lines from the end-screen OCR.
set -u
queue=$1; out=$2
here=${ARENA_HOME:-$(cd "$(dirname "$0")/.." && pwd)} # a copy run from elsewhere: ARENA_HOME=<repo>
BS='\'
# the game machine (README): FPC_GAME_ROOT its game folder, ARENA_GAME_NODE its name for the game-host adapter
# (default local), ARENA_GAMEHOST the adapter command (default tools/gamehost.py: the game on this machine)
node=${ARENA_GAME_NODE:-local}
gamehost() { if [ -n "${ARENA_GAMEHOST:-}" ]; then eval "$ARENA_GAMEHOST \"\$@\""; else python -I -X utf8 "$here/tools/gamehost.py" "$@"; fi; }
game=${FPC_GAME_ROOT:?set FPC_GAME_ROOT to the game folder on the game machine}
remote_out="$game${BS}ai-arena${BS}out"
mkdir -p "$out/args"
[ -f "$out/results.tsv" ] || printf 'tag\tscenario\tmode\tvp_ratio_pct\tstarting_ratio_pct\tresult\tstars\tcommander\tmultiplayer\tsurvived_vp\tlocations_vp\tinflicted_vp\n' > "$out/results.tsv"
winpid=$(cat /proc/$$/winpid)
machine_lock() { # acquire <tag> | release
  python -I -X utf8 - "$here" "$1" "$winpid" "$out" "${2:-}" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from arena.harness.player import PidLock
import os
lock = PidLock(os.path.join(os.environ.get("ARENA_DATA") or os.path.join(sys.argv[1], "data"), "game-machine.lock"), "the game machine")
if sys.argv[2] == "acquire":
    try:
        lock.acquire(pid=int(sys.argv[3]), run=sys.argv[4], game="calibration " + sys.argv[5])
    except Exception as e:
        print(e)
        sys.exit(1)
else:
    lock.release(pid=int(sys.argv[3]))
EOF
}
trap 'machine_lock release' EXIT

# the queue file is read again before every game: lines appended while the driver runs are played too
i=0
while :; do
  mapfile -t lines < "$queue"
  [ "$i" -lt "${#lines[@]}" ] || break
  line=${lines[$i]}; i=$((i + 1))
  IFS=$'\t' read -r tag scen minutes mode attach <<< "$line"
  [ -z "$tag" ] && continue
  case "$tag" in \#*) continue ;; esac
  freeze=-1; probe=; probe_side=1
  case "$mode" in
    aivai) cfg=arena.ini; style=2; passive=false ;;
    passive-blue) cfg=; style=1; passive=true ;;
    freeze-red) cfg=arena.ini; style=2; passive=false; freeze=1 ;;
    freeze-blue) cfg=arena.ini; style=2; passive=false; freeze=0 ;;
    vpprobe:[01]:*) cfg=arena-h2h.ini; style=3; passive=true; probe_side=${mode:8:1}; probe=${mode:10} ;;
    *) echo "unknown mode $mode for $tag"; continue ;;
  esac
  # wall-time cap: 11-01 af-ai1b (The Armored Fist, computer vs computer) made about 3 game minutes per wall minute,
  # the old cap (6.5 per minute with a 1.6 margin, 98 min for 300) would have stopped it before the end
  max=$(( minutes * 10 / 25 + 30 ))
  printf '{"Scenario":"%s","Config":"%s","StyleOfPlay":%d,"Passive":%s,"Calibration":true,"FreezeSide":%d,"VpProbe":"%s","VpProbeSide":%d,"RunForMinutes":%d,"MaxMinutes":%d,"SleepTo":0,"PauseFrom":999999,"EndScreens":true,"Quiet":1,"TimedMessages":0,"Telnet":"127.0.0.1:47823","RunName":"s2-cal-%s"}' \
    "$scen" "$cfg" "$style" "$passive" "$freeze" "$probe" "$probe_side" "$minutes" "$max" "$tag" > "$out/args/$tag.json"
  # 11-01 07:51: released after af-ai2c and taken again at once for af-ai3c while the start task had waited 56 min
  [ "${yield_after:-0}" = 1 ] && sleep 75
  waited=0 # the lock held elsewhere (the director's game): wait for it, one look a minute, up to 4 hours
  until msg=$(machine_lock acquire "$tag"); do
    [ "$waited" = 0 ] && echo "$(date +%H:%M:%S) $tag waits for the game machine lock: $msg"
    waited=$((waited + 1))
    if [ "$waited" -gt 240 ]; then echo "$(date +%H:%M:%S) not starting $tag: lock still held after 4 h; driver stops"; exit 4; fi
    sleep 60
  done
  if [ "${attach:-}" != attach ]; then
    # one game at a time on the game machine (materials/18 2.3): 11-01 00:29 a broken attach line let this driver start two
    # more games next to a running one (same Telnet port; one never loaded its Lua). Refuse and stop instead.
    games=$(gamehost ps "$node" -c "@(Get-CimInstance Win32_Process -Filter \"Name='FlashpointCampaigns.exe'\").Count" 2>&1 | head -1 | tr -dc '0-9')
    if [ "${games:-x}" != 0 ]; then
      echo "$(date +%H:%M:%S) not starting $tag: FlashpointCampaigns.exe processes on the game machine = '${games}'; driver stops"
      exit 5
    fi
  fi
  [ "${attach:-}" = attach ] || gamehost spawn "$node" "fpc-s2-cal-$tag" --file "$here/fpc/remote/job-p0.ps1" --args "$out/args/$tag.json" \
    --cwd "$game" --window hidden --deadline "$(( max + 10 ))m" --kind experiment \
    --purpose "ai-commander-arena season 2 calibration: $scen ($mode), one game, minimized" --mem-gb 3 > "$out/args/$tag.spawn.txt" 2>&1
  echo "$(date +%H:%M:%S) ${attach:+attached to }started $tag ($scen, $mode, $minutes min, max $max min)"
  gamehost wait "$node" "fpc-s2-cal-$tag" --timeout $(( (max + 15) * 60 )) > "$out/args/$tag.wait.txt" 2>&1
  gamehost pull "$node" "$remote_out${BS}s2-cal-$tag" "$out/" > "$out/args/$tag.pull.txt" 2>&1
  machine_lock release
  yield_after=1 # calibration is background work: before the next game, give a waiting director (it looks every 60 s) the machine
  run="$out/s2-cal-$tag"
  if ls "$run"/end-*.bmp > /dev/null 2>&1; then
    python -X utf8 "$here/tools/fpc_endscreen.py" "$run" > "$run/endscreen.log" 2>&1
  fi
  python -I -X utf8 - "$run" "$tag" "$scen" "$mode" >> "$out/results.tsv" <<'EOF'
import glob, json, os, re, sys
run, tag, scen, mode = sys.argv[1:5]
try:
    r = json.load(open(os.path.join(run, "end-result.json"), encoding="utf-8"))["result"]
except Exception:
    r = {}
mp = []
for f in sorted(glob.glob(os.path.join(run, "end-*.txt"))):
    for m in re.finditer(r"multiplayer score of (\d+)", open(f, encoding="utf-8", errors="replace").read()):
        mp.append(f"{os.path.basename(f)[4:-4]}={m.group(1)}")
print("\t".join(str(x) for x in (tag, scen, mode, r.get("vp_ratio_pct", ""), r.get("starting_ratio_pct", ""), r.get("result", ""),
                                 r.get("stars", ""), r.get("commander", ""), " ".join(mp), r.get("survived_vp", ""),
                                 r.get("locations_vp", ""), r.get("inflicted_vp", ""))))
EOF
  echo "$(date +%H:%M:%S) finished $tag: $(tail -1 "$out/results.tsv")"
done
echo "$(date +%H:%M:%S) all done"
