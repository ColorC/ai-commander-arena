"""P5: the ruling text for the notes round between the two games (materials/10 P5, 10-31 ruling).

python -I -X utf8 tools/p5_notes_ruling.py <game 1 run dir> <out .txt>

Both players get the same text: what the round allows, the sides of game 2, and the record of game 1 (the game's own
end-of-game result, read by tools/fpc_endscreen.py into <relay folder>/end-result.json, and the referee's
exercise-report.json: both sides' loss lists and the VP cells). The director (tools/p4_training.py
--supplement-file) puts it under its own title at the end of the training prompt.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from arena.referee.world import Scenario  # noqa: E402

LEVELS = ("Decisive Win", "Tactical Win", "Marginal Win", "Contested Result")
LOSS = {"Marginal Win": "Marginal Loss", "Tactical Win": "Tactical Loss", "Decisive Win": "Decisive Loss"}
SIDES = ("美军（TF Alamo，0 方）", "波军（1 方）")


def level(levels, share):
    """The win or Contested level for a share, from official_levels (decisive / tactical / marginal win, lowest
    Contested); None below Contested."""
    for th, name in zip(levels, LEVELS):
        if share >= th:
            return name
    return None


def side_level(sc, side, share):
    """The game's tables mirror around 50.5 (start.json official_levels_source): a share s below Contested is the
    loss matching the enemy's win at 101 - s (Polish 25% is a Tactical Loss: TF Alamo 76% is a Tactical Win)."""
    own = level(sc.levels[side], share)
    if own:
        return own
    return LOSS.get(level(sc.levels[1 - side], 101 - share), "loss")


def ruling(run):
    with open(os.path.join(run, "match.json"), encoding="utf-8") as f:
        match = json.load(f)
    with open(os.path.join(run, "exercise-report.json"), encoding="utf-8") as f:
        report = json.load(f)
    found = glob.glob(os.path.join(run, "*", "end-result.json"))
    if len(found) != 1:
        raise SystemExit(f"need one end-result.json under {run}, found {len(found)}")
    with open(found[0], encoding="utf-8") as f:
        official = json.load(f)
    bo = official["pages"]["end-battle-over"]["parsed"]
    if "Alamo" not in bo.get("commander", ""):
        raise SystemExit(f"the Battle Over page is not TF Alamo's: {bo.get('commander')}")
    sc = Scenario("alamo-at-soest")
    us = bo["vp_ratio_pct"]
    shares = (us, 100 - us)
    p0 = match["side0_player"]
    players = (p0, "B" if p0 == "A" else "A")
    loss_lists = {SIDES[int(s)]: v for s, v in report["losses"].items()}
    lines = [
        "# 局间整理（与上文不同之处以本节为准）",
        "",
        "正式赛第 1 局已经打完。这是两局之间的整理回合，规则 P5：两局之间双方可以整理笔记，不能打演习。",
        "",
        "- 这次只有一个回合，最长 20 分钟真实时间。可以读第 1 局记录（本节）、查简报、地形和手册、改笔记、登记或删除脚本、"
        "做离线计算；不能申请演习，完整演习和小实验额度都是 0。回合结束整理期就结束，接着开第 2 局。",
        f"- 第 2 局换边：参赛方 {players[1]} 指挥{SIDES[0]}，参赛方 {players[0]} 指挥{SIDES[1]}。"
        "工作区原样带进第 2 局：这一回合改动的笔记和脚本就是你第 2 局开局时的样子。",
        "- 另一位参赛者同时在整理，拿到的是同一份第 1 局记录。",
        "",
        "## 第 1 局记录",
        "",
        f"- 参赛方 {players[0]} 指挥{SIDES[0]}，参赛方 {players[1]} 指挥{SIDES[1]}。",
        f"- 官方终局（游戏的 Battle Over 和信息中心）：美军 VP 份额 {shares[0]:g}%（{side_level(sc, 0, shares[0])}），"
        f"波军 {shares[1]:g}%（{side_level(sc, 1, shares[1])}）。",
        f"- Battle Over 原文数字（美军视角）：给敌方造成损失 {bo.get('inflicted_vp', 0):g} VP，占得 VP 格 "
        f"{bo.get('locations_vp', 0):g} VP，存活部队 {bo.get('survived_vp', 0):g} VP，开局比例 "
        f"{bo.get('starting_ratio_pct', 0):g}%，按 VP 计战备 {bo.get('combat_ready_pct', 0):g}%。",
        f"- 结束方式 {report.get('ended_by')}，游戏时钟 {report.get('game_clock')}，真实用时 "
        f"{(report.get('wall_sec') or 0) / 60:.0f} 分钟。",
        "- VP 格归属（裁判按地面单位经过推算，游戏不报格子归属；owner 0 美军、1 波军）：" +
        json.dumps(report.get("vp_cells"), ensure_ascii=False, separators=(",", ":")),
        "- 双方损失清单（每个单位开局和终局的子单位数）：" +
        json.dumps(loss_lists, ensure_ascii=False, separators=(",", ":")),
    ]
    return "\n".join(lines) + "\n"


def main(argv):
    text = ruling(argv[1])
    with open(argv[2], "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(argv[2], len(text))


if __name__ == "__main__":
    main(sys.argv)
