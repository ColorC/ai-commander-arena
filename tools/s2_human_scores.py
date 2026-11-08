"""Human reference from the Matrix summer tournament scores ($ARENA_DATA/human/matrix-summer-2026.tsv).

    python -I -X utf8 tools/s2_human_scores.py [tsv]

A tournament score is the game's multiplayer score: base points by result level + 100 game-over bonus + own VP share
in percent (our own end screens: Contested 300 + 100 + share). The share is the last two digits; the base points of
the higher levels are not settled by the data (7xx scores with shares 53-70 do not fit one table), so only the share
is used. The page does not say who played which side or who met whom; each player's two games of a round are
summed (a player normally plays one game per side), which is comparable to a model's two-game sum in a season
match. Byes/forfeits (750/750) and timed-out games are left out.
"""
import collections
import os
import statistics
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(path):
    games = collections.defaultdict(list)
    pairs = collections.defaultdict(list)
    for line in open(path, encoding="utf-8"):
        if line.startswith("#") or line.startswith("round\t") or not line.strip():
            continue
        rnd, scen, player, g1, g2 = line.rstrip("\n").split("\t")
        if (g1, g2) == ("750", "750") or "T" in g1 or "T" in g2:
            continue
        shares = [int(g) % 100 for g in (g1, g2)]
        games[scen] += shares
        pairs[scen].append((player, sum(shares)))
    for scen, sh in games.items():
        sums = sorted(s for _, s in pairs[scen])
        q = statistics.quantiles(sh, n=4)
        qs = statistics.quantiles(sums, n=4)
        print(f"{scen}: {len(sh)} games, share median {statistics.median(sh)}, quartiles {q[0]:.0f}/{q[2]:.0f}, "
              f"range {min(sh)}-{max(sh)}")
        print(f"  two-game sums of {len(sums)} players: median {statistics.median(sums)}, quartiles {qs[0]:.0f}/{qs[2]:.0f},"
              f" range {sums[0]}-{sums[-1]}: {sums}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("ARENA_DATA") or os.path.join(HERE, "data"), "human", "matrix-summer-2026.tsv"))
