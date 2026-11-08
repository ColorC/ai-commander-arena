#!/usr/bin/env bash
# P5 formal match (materials/10 P5): one game of the match scenario under the full rules, player A against player B.
#
#   tools/p5_match.sh <local run dir> <player on side 0 (US): A | B> <training workspace root>
#
# Player A = Kimi (official Kimi Code CLI), player B = DeepSeek (opencode, proxy/deepseek-v4.1-flash), the same
# routes and models as the P4 exam and training. Each side gets the workspace (notes and scripts) of the player on
# it: <training workspace root>/player0 for A, player1 for B. Runs tools/p3_rehearsal.sh with the official CLI
# harness, both sides played by agents, formal pacing, 120-minute deployment, wall cap 430 minutes. Then the game's
# end screens are read (tools/fpc_endscreen.py, the user's Python: rapidocr) into the relay folder's end-result.json.
set -u
# the run executes a private copy (bash reads a script while it runs it; see p3_rehearsal.sh)
if [ -z "${ARENA_MATCH_COPY:-}" ]; then
  mkdir -p "$1/args"; cp "$0" "$1/args/p5_match.sh"
  ARENA_MATCH_COPY=1 ARENA_MATCH_HERE="$(cd "$(dirname "$0")/.." && pwd)" exec bash "$1/args/p5_match.sh" "$@"
fi
run=$1; side0=$2; ws=$3
case "$side0" in
  A) r0=kimi; r1=opencode; w0="$ws/player0"; w1="$ws/player1" ;;
  B) r0=opencode; r1=kimi; w0="$ws/player1"; w1="$ws/player0" ;;
  *) echo "player on side 0 must be A or B" >&2; exit 2 ;;
esac
[ -d "$w0" ] && [ -d "$w1" ] || { echo "no workspace $w0 or $w1" >&2; exit 2; }
mkdir -p "$run"
printf '{"side0_player":"%s","side0_cli":"%s","side1_cli":"%s","workspace0":"%s","workspace1":"%s"}\n' "$side0" "$r0" "$r1" "$w0" "$w1" > "$run/match.json"
cd "$ARENA_MATCH_HERE"
ARENA_HARNESS=cli ARENA_AGENTS=0,1 ARENA_WORKSPACE0="$w0" ARENA_WORKSPACE1="$w1" \
  bash tools/p3_rehearsal.sh "$run" "$r0" "$r1" 430
rc=$?
for d in "$run"/p3-rehearsal-*/; do
  ls "$d"end-*.bmp > /dev/null 2>&1 && python -X utf8 tools/fpc_endscreen.py "${d%/}" >> "$run/endscreen.out" 2>&1
done
exit $rc
