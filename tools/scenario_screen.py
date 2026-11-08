"""Static screen of every Flashpoint Campaigns Cold War scenario of the 10-30 briefing census: which ones make both
sides fight for the objectives instead of keeping their units safe. Deterministic, read-only, no game, no numpy.

    python -I -X utf8 tools/scenario_screen.py [census dir] [out dir]

defaults: census dir  $ARENA_DATA/runs/p1-briefs-20261030-070846
          out dir     $ARENA_DATA/scenario-screen
writes    <out dir>/screen.tsv      one row per scenario (all 74, with the exclusion reasons and the score)
          <out dir>/missions.json   each side's Situation header, Mission paragraph and commander's notes (<= 400 chars)
          <out dir>/screen.md       the top 8, the method, the Eyes Ears and Teeth check and the limits

Sources, per scenario folder of the census: start.sav (the game's 0 % auto-save) and rec.jsonl (load snapshot), plus
briefs.json beside them (duration, H hour, victory levels) and tools/fpc_objectives.py (the VP location records of
FPC21.$$$, read straight from the .sav).
  force value  sum of the "In Sum: ... totalling N Victory Points." lines of a side's formations in its briefing
               (what s2_vp_table.py reads per formation; EET: 17,394 / 23,807). Side 0 is the first briefing in the
               save, side 1 the second; checked against the snapshot's subunit counts (column sum_units_ok).
  objectives   every VP record (types 14/15/16): count, VP to each side, owner at the start.
  obj_ratio    objective VP / (objective VP + force value of both sides), per side's own VP column; obj_ratio is their mean.
  share0       (F0 + VP held by side 0 at the start) / (F0 + F1 + all VP held at the start); unclaimed ones count for nobody.
  stance       keyword counts over the Mission paragraph (weight 1) and the commander's notes (weight 0.5), then a label
               per side: 攻 attack, 守 defend, 迟滞 delay/withdraw, 侦察 screen/recon; both sides 攻 -> 遭遇 (meeting).
The duration comes from the briefing ("you will have N hours"); when the save has none, from the header line
"16:00hrs - 22:00hrs" of the scenario blurb; else unknown. The module comes from the table in materials/12.
"""
import collections
import json
import os
import re
import statistics
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fpc_objectives  # noqa: E402
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[1] / "data")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RUN16 = re.compile(rb"(?:[\x09\x20-\x7e\xa0-\xff]\x00){3,}")
MATERIAL12 = os.path.join(os.path.dirname(HERE), "materials", "12-p1-scenario-selection.md")
EET_DIR = os.path.join(os.environ.get("ARENA_DATA") or os.path.join(os.path.dirname(HERE), "data"), "scenarios", "eyes-ears-and-teeth")
SUM = re.compile(r"In Sum: (.*?) totalling ([\d,]+) Victory Points")

KEYWORDS = {
    "attack": r"attack|advance|seiz|captur|assault|destroy|clear|penetrat|break ?through|break ?out|exploit|recaptur|"
              r"re-?tak|retak|secure|reach|cross|occupy|drive|push|pursu|link up|relieve|eliminat|defeat|annihilat|"
              r"take control|\btake\b|\bgain\b|\bcut\b|\bopen\b|\bcontrol\b",
    "defend": r"defend|\bhold|protect|\bdeny|prevent|\bblock|guard|retain|\bstop|contain|maintain|\bkeep|stand firm|"
              r"not (?:be )?(?:allow|let)|plug",
    "delay": r"delay|slow|withdraw|retire|fall back|disengag|buy time|retreat|trade space|break contact|exfiltrat|"
             r"escape|extract|evacuat",
    "recon": r"screen|recon|reconnoit|scout|identify|locate|observe|probe|determine|\bfind\b|surveil",
}
KW = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}
LABEL = {"attack": "攻", "defend": "守", "delay": "迟滞", "recon": "侦察"}
SECTION_END = re.compile(r"^(?:[0-9A-Z]\.\s*)?(?:attachments|coordinating|execution|fire support|aviation|close air|"
                         r"command and|service|logistic|notes? from|commander'?s? (?:notes|intent)|map:|h hour)", re.I)
MISSION_HEAD = re.compile(r"^(?:\d+\.\s*)?(?:mission|orders)\s*:?$", re.I)
NOTES_HEAD = re.compile(r"^(?:\d+\.\s*)?(?:commander.?s? (?:notes|intent)|notes from the commander.*|intent)\s*:?$", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def text_lines(raw):
    """HTML-ish strings of the save -> plain lines (tags dropped, stray 1-char length bytes dropped)."""
    out = []
    for r in raw:
        r = re.sub(r"</?(?:p|h\d|li|ul|div|br)[^>]*>", "\n", r, flags=re.I)
        r = re.sub(r"<[^>]+>", "", r).replace("&nbsp;", " ")
        for ln in r.split("\n"):
            ln = ln.strip()
            if len(ln) > 1:
                out.append(ln)
    return out


def sections(lines):
    """(situation head, mission, notes) text of one side from its plain lines."""
    mission = notes = ""
    for i, ln in enumerate(lines):
        if MISSION_HEAD.match(ln):
            body = []
            for l2 in lines[i + 1:i + 40]:
                if SECTION_END.match(l2) or re.match(r"^(?:\d+\.\s*)?situation$", l2, re.I):
                    break
                body.append(l2)
            if body:
                mission = " ".join(body)
        if NOTES_HEAD.match(ln) or re.match(r"^(?:\d+\.\s*)?notes from the commander", ln, re.I):
            body = []
            for l2 in lines[i + 1:i + 30]:
                if re.match(r"^(?:map:|h hour)", l2, re.I):
                    break
                body.append(l2)
            notes = " ".join(body)
    if not mission:  # "A. Mission" with the order directly under it (no Situation / Mission sub-headings)
        for i, ln in enumerate(lines):
            if re.match(r"^[A-Z0-9]\.\s*(?:mission|orders)\s*:?$", ln, re.I):
                body = []
                for l2 in lines[i + 1:i + 30]:
                    if SECTION_END.match(l2) or re.match(r"^(?:\d+\.|[A-Z]\.)\s", l2):
                        break
                    body.append(l2)
                mission = " ".join(body)
                break
    if not mission:  # a briefing without the standard headings: whatever sits between Situation and Attachments
        txt = " ".join(lines)
        m = re.search(r"mission[:\s]+(.{20,600}?)(?:attachments|coordinating|$)", txt, re.I)
        mission = m.group(1) if m else ""
    return mission.strip(), notes.strip()


def parse_save(path):
    z = zipfile.ZipFile(path)
    data = z.read("FPC21.$$$")
    raw = [m.group().decode("utf-16-le") for m in RUN16.finditer(data)]
    mis = [i for i, l in enumerate(raw) if re.search(r"Mission</h3>|Mission</p>", l)]
    # the two sides' briefings are far apart; split the clusters of Mission headings
    clusters = [[mis[0]]] if mis else []
    for i in mis[1:]:
        if i - clusters[-1][-1] > 500:
            clusters.append([i])
        else:
            clusters[-1].append(i)
    starts = [c[0] - 15 for c in clusters[:2]]
    sides = []
    for k, st in enumerate(starts):
        en = starts[1] if k == 0 and len(starts) > 1 else len(raw)
        blk = raw[st:en]
        total, units, forms = 0, 0, 0
        for l in blk:
            m = SUM.search(l)
            if m:
                total += int(m.group(2).replace(",", ""))
                units += sum(int(x) for x in re.findall(r"(\d+) x ", m.group(1)))
                forms += 1
        # the briefing proper: from the greeting to "You are in command"
        end = next((j for j, l in enumerate(blk) if "You are in command" in l), min(len(blk), 200))
        lines = text_lines(blk[:end + 1])
        mission, notes = sections(lines)
        head = next((l for l in lines if re.search(r"^[A-Z][A-Za-z.]*\.? [A-Z]", l) and l.endswith(",")), "")
        sides.append({"force_vp": total, "sum_units": units, "formations": forms, "mission": mission, "notes": notes,
                      "addressee": head.rstrip(",")})
    # duration and blurb
    text = "\n".join(raw)
    hrs = re.search(r"you will have\s*(?:<b>)?\s*(\d+(?:\.\d+)?)\s*(?:</b>)?\s*hours?", text)
    hh = re.search(r"H hour is at\s*(?:<b>)?\s*(\d\d:?\d\d)", text)
    blurb = next((re.sub(r"<[^>]+>", "", l).strip() for l in raw[:200] if "<em>" in l and "|" in l), "")
    hdr = re.search(r"(\d\d):?(\d\d)\s*hrs?\s*-\s*(\d\d):?(\d\d)\s*hrs?", blurb)
    dur_header = None
    if hdr:
        a = int(hdr.group(1)) * 60 + int(hdr.group(2))
        b = int(hdr.group(3)) * 60 + int(hdr.group(4))
        dur_header = ((b - a) % 1440) / 60.0
    objs = fpc_objectives.objectives(data)
    uniq = {}
    for o in objs:
        uniq[o["hex"]] = o
    return {"sides": sides, "hours_text": float(hrs.group(1)) if hrs else None, "h_hour": hh.group(1) if hh else None,
            "hours_header": dur_header, "blurb": blurb, "objectives": list(uniq.values()), "dup_records": len(objs) - len(uniq)}


def parse_snap(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("k") != "snap":
                continue
            per = {0: collections.Counter(), 1: collections.Counter()}
            arrive = {0: [], 1: []}
            starts = {0: [], 1: []}
            for u in r["units"].values():
                s = u["s"]
                side = s.get("side")
                if side not in per:
                    continue
                per[side]["units"] += 1
                per[side]["subunits"] += len(u.get("u") or [])
                orders = u.get("o") or []
                st = orders[0].get("start_time") if orders else None
                if s.get("status") == "Reinforcement":
                    per[side]["rein_units"] += 1
                    per[side]["rein_subunits"] += len(u.get("u") or [])
                    if st:
                        arrive[side].append(st)
                elif st:
                    starts[side].append(st)
            return {"per": per, "arrive": arrive, "starts": starts}
    return None


def arrival_minutes(snap, side):
    """Reinforcement arrival, minutes after the earliest start time of the active units (the first order's start_time
    of a reinforcing unit looks like its arrival time; an inference, not read from a field named so)."""
    allstarts = snap["starts"][0] + snap["starts"][1]
    arr = snap["arrive"][side]
    if not allstarts or not arr:
        return None
    from datetime import datetime
    f = "%Y-%m-%d %H:%M:%S"
    t0 = min(datetime.strptime(x, f) for x in allstarts)
    mins = sorted(int((datetime.strptime(x, f) - t0).total_seconds() // 60) for x in arr)
    return mins


def stance(mission, notes):
    cnt = {}
    for k, rx in KW.items():
        cnt[k] = len(rx.findall(mission)) + 0.5 * len(rx.findall(notes))
    return cnt


def label_side(cnt, held_vp):
    atk, dfn, dly, rec = cnt["attack"], cnt["defend"], cnt["delay"], cnt["recon"]
    if dly > 0 and dly >= atk:
        return "迟滞"
    if rec > 0 and rec >= max(atk, dfn):
        return "侦察"
    if atk > dfn:
        return "攻"
    if dfn > atk:
        return "守"
    return "守" if held_vp > 0 else "攻"  # a tie or no keyword at all: who holds objectives defends


def load_modules():
    mods, disp = {}, {}
    if os.path.exists(MATERIAL12):
        for line in open(MATERIAL12, encoding="utf-8"):
            p = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(p) >= 9 and (p[1] == "基础" or "DLC" in p[1]):
                mods[norm(p[0])] = p[1]
                disp[norm(p[0])] = p[0]
    return mods, disp


def main(census, out):
    os.makedirs(out, exist_ok=True)
    briefs = json.load(open(os.path.join(census, "briefs.json"), encoding="utf-8"))
    mods, disp = load_modules()
    rows, missions = [], {}
    for key in sorted(briefs):
        b = briefs[key]
        folder = re.sub(r"[^A-Za-z0-9]+", "_", key)
        sav = os.path.join(census, folder, "start.sav")
        snap = parse_snap(os.path.join(census, folder, "rec.jsonl"))
        ps = parse_save(sav)
        n = norm(key)
        name = disp.get(n, key.replace("_", " "))
        module = mods.get(n, "?")
        hours = b.get("hours") or ps["hours_text"] or ps["hours_header"]
        hours_src = "简报" if b.get("hours") else ("简报文本" if ps["hours_text"] else ("开篇行" if ps["hours_header"] else "未知"))
        per = snap["per"]
        sd = ps["sides"]
        objs = ps["objectives"]
        F = [sd[0]["force_vp"], sd[1]["force_vp"]]
        obj_vp = [sum(o["vp"][s] for o in objs) for s in (0, 1)]
        held = [sum(o["vp"][s] for o in objs if o["owner_at_start"] == s) for s in (0, 1)]
        n_owner = [sum(1 for o in objs if o["owner_at_start"] == v) for v in (0, 1, 2)]
        den = F[0] + F[1] + held[0] + held[1]
        share0 = (F[0] + held[0]) / den if den else None
        ratio = [obj_vp[s] / (obj_vp[s] + F[0] + F[1]) if obj_vp[s] + F[0] + F[1] else 0 for s in (0, 1)]
        r_mean = (ratio[0] + ratio[1]) / 2
        cnts = [stance(sd[s]["mission"], sd[s]["notes"]) for s in (0, 1)]
        labs = [label_side(cnts[s], held[s]) for s in (0, 1)]
        if labs[0] == "攻" and labs[1] == "攻":
            labs = ["遭遇", "遭遇"]
        # sides that must go and take objectives: label attack/meeting, and what they do not hold at the start
        need = []
        for s in (0, 1):
            todo = sum(o["vp"][s] for o in objs if o["owner_at_start"] != s)
            todo_enemy = sum(o["vp"][s] for o in objs if o["owner_at_start"] == 1 - s)
            need.append((todo, todo_enemy))
        nogrid = [labs[s] in ("攻", "遭遇") and held[s] == 0 and obj_vp[s] > 0 for s in (0, 1)]
        lv = b.get("levels")
        passive = ["?", "?"]
        if lv and len(lv) >= 2:
            m0, m1 = lv[0][2], lv[1][2]
            sh = [share0 * 100, 100 - share0 * 100] if share0 is not None else [None, None]
            for s, (mine, other) in enumerate(((m0, m1), (m1, m0))):
                if sh[s] is None:
                    continue
                passive[s] = "胜" if sh[s] >= mine else ("败" if sh[s] <= 100 - other else "未分")
        rein = [per[s]["rein_units"] for s in (0, 1)]
        arr = [arrival_minutes(snap, s) for s in (0, 1)]
        arr_txt = ["/".join(str(x) for x in (a[0], a[len(a) // 2], a[-1])) if a else "-" for a in arr]
        unit_ok = all(sd[s]["sum_units"] == per[s]["subunits"] for s in (0, 1))
        row = {
            "key": key, "name": name, "module": module, "hours": hours, "hours_src": hours_src, "h_hour": b.get("h_hour") or ps["h_hour"] or "",
            "map": b.get("map", ""), "units0": per[0]["units"], "units1": per[1]["units"],
            "sub0": per[0]["subunits"], "sub1": per[1]["subunits"], "rein_units0": rein[0], "rein_units1": rein[1],
            "rein_sub0": per[0]["rein_subunits"], "rein_sub1": per[1]["rein_subunits"],
            "rein_arrival_min0": arr_txt[0], "rein_arrival_min1": arr_txt[1],
            "force_vp0": F[0], "force_vp1": F[1], "sum_units_ok": "是" if unit_ok else f"否({sd[0]['sum_units']}/{sd[1]['sum_units']})",
            "n_obj": len(objs), "obj_vp0": obj_vp[0], "obj_vp1": obj_vp[1],
            "obj_start_side0": n_owner[0], "obj_start_side1": n_owner[1], "obj_start_none": n_owner[2],
            "held_start0": held[0], "held_start1": held[1],
            "obj_ratio0": round(ratio[0], 4), "obj_ratio1": round(ratio[1], 4), "obj_ratio": round(r_mean, 4),
            "share0_start": round(share0, 4) if share0 is not None else "",
            "stance0": labs[0], "stance1": labs[1],
            "kw0": json.dumps(cnts[0]), "kw1": json.dumps(cnts[1]),
            "todo_vp0": need[0][0], "todo_vp1": need[1][0], "todo_from_enemy0": need[0][1], "todo_from_enemy1": need[1][1],
            "attacker_holds_nothing": "/".join(str(int(x)) for x in nogrid),
            "passive_start_status0": passive[0], "passive_start_status1": passive[1],
            "levels": json.dumps(lv) if lv else "",
        }
        rows.append(row)
        missions[key] = {"name": name, "blurb": ps["blurb"],
                         "side0": {"addressee": sd[0]["addressee"], "mission": sd[0]["mission"][:400], "notes": sd[0]["notes"][:400]},
                         "side1": {"addressee": sd[1]["addressee"], "mission": sd[1]["mission"][:400], "notes": sd[1]["notes"][:400]}}
        row["_cnt"] = cnts
        row["_objs"] = objs
    # ---- exclusion and score
    for r in rows:
        why = []
        if norm(r["key"]) == "eyesearsandteeth":
            why.append("EET")
        if max(r["units0"], r["units1"]) > 80:
            why.append("单位>80")
        if r["hours"] is None:
            why.append("时长未知")
        elif not 3 <= r["hours"] <= 8:
            why.append("时长不在3-8h")
        r["excluded"] = "、".join(why)
        s_ratio = min(r["obj_ratio"] / 0.5, 1.0) * 40
        l0, l1 = r["stance0"], r["stance1"]
        todo = [r["todo_vp0"], r["todo_vp1"]]
        tot = [max(r["obj_vp0"], 1), max(r["obj_vp1"], 1)]
        if l0 == "遭遇":
            s_att = 10 + 8 * min(1, (r["obj_start_none"] + 0.0) / max(r["n_obj"], 1) * 1.5)
            s_att = min(s_att, 18)
        else:
            atk = [i for i, l in enumerate((l0, l1)) if l == "攻"]
            if len(atk) == 1:
                a = atk[0]
                s_att = 15 + 10 * min(1.0, todo[a] / tot[a])
            elif len(atk) == 0:
                s_att = 0
            else:
                s_att = 5
        passive = [r["passive_start_status0"], r["passive_start_status1"]]
        if l0 != "遭遇":
            atk = [i for i, l in enumerate((l0, l1)) if l == "攻"]
            if len(atk) == 1 and passive[atk[0]] == "败":
                s_att += 5
            elif len(atk) == 1 and passive[atk[0]] == "未分":
                s_att += 2
        sh = r["share0_start"] if r["share0_start"] != "" else 0.5
        dev = abs(sh - 0.5)
        s_share = 15 if dev <= 0.15 else max(0.0, 15 * (1 - (dev - 0.15) / 0.15))
        rf = max(r["rein_units0"] / max(r["units0"], 1), r["rein_units1"] / max(r["units1"], 1))
        s_rein = 10 * min(1.0, rf / 0.25)
        r["score"] = round(s_ratio + s_att + s_share + s_rein, 1)
        r["score_parts"] = f"目标{s_ratio:.0f}+攻守{s_att:.0f}+份额{s_share:.0f}+增援{s_rein:.0f}"
    cols = [c for c in rows[0] if not c.startswith("_")]
    with open(os.path.join(out, "screen.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(cols) + "\n")
        for r in sorted(rows, key=lambda x: (x["excluded"] != "", -x["score"])):
            f.write("\t".join(str(r[c]) for c in cols) + "\n")
    with open(os.path.join(out, "missions.json"), "w", encoding="utf-8") as f:
        json.dump(missions, f, ensure_ascii=False, indent=1)
    write_md(out, rows, census)
    return rows


def nog(r):
    a = r["attacker_holds_nothing"].split("/")
    out = [f"{i} 方" for i, x in enumerate(a) if x == "1"]
    return "、".join(out) if out else "无"


def mult_table(r):
    """Objective ratio / start share if every objective's VP is multiplied by N."""
    F = r["force_vp0"] + r["force_vp1"]
    res = []
    for N in (2, 3, 5, 10):
        o = (r["obj_vp0"] + r["obj_vp1"]) / 2 * N
        ratio = o / (o + F)
        h0, h1 = r["held_start0"] * N, r["held_start1"] * N
        sh = (r["force_vp0"] + h0) / (F + h0 + h1)
        res.append((N, ratio, sh))
    need50 = F / max((r["obj_vp0"] + r["obj_vp1"]) / 2, 1)
    need40 = F * 0.4 / 0.6 / max((r["obj_vp0"] + r["obj_vp1"]) / 2, 1)
    return res, need40, need50


def write_md(out, rows, census):
    elig = sorted((r for r in rows if not r["excluded"]), key=lambda r: -r["score"])
    top = elig[:8]
    eet = next(r for r in rows if norm(r["key"]) == "eyesearsandteeth")
    ref = {}
    try:
        ref["vp"] = json.load(open(os.path.join(EET_DIR, "vp-table.json"), encoding="utf-8"))["briefing_total_by_era"]
        st = json.load(open(os.path.join(EET_DIR, "start.json"), encoding="utf-8"))
        ref["obj"] = sum(v[1] for g in st["vp_locations"].values() for v in g)
        ref["units"] = [len(x) for x in json.load(open(os.path.join(EET_DIR, "units.json"), encoding="utf-8"))["sides"].values()]
    except Exception as e:  # noqa: BLE001
        ref["err"] = str(e)
    ratios = sorted(r["obj_ratio"] for r in rows)
    L = []
    A = L.append
    A("# 想定静态筛查：哪些想定鼓励去完成目标\n")
    A(f"数据：{census} 的 74 个想定（start.sav + rec.jsonl + briefs.json）。脚本：tools/scenario_screen.py。全表见 screen.tsv，任务原文见 missions.json。\n")
    A("## 结论\n")
    nin = len(elig)
    A(f"- 74 个想定中排除后剩 {nin} 个（排除 EET、任一方超过 80 单位、时长不在 3–8 小时或时长未知）。")
    A(f"- 目标格占比全体中位数 {statistics.median(ratios) * 100:.1f}%，最高 {ratios[-1] * 100:.1f}%；在剩余 {nin} 个里 40% 以上的有 "
      f"{sum(1 for r in elig if r['obj_ratio'] >= 0.4)} 个，30% 以上的有 {sum(1 for r in elig if r['obj_ratio'] >= 0.3)} 个。")
    A(f"- EET 的目标格占比是 {eet['obj_ratio'] * 100:.1f}%，作对照。\n")
    A("## 前 8 名\n")
    A("| # | 想定 | 模块 | 时长 h | 单位 0/1 | 增援单位 0/1 | 部队价值 0/1 | 目标格 个/分值0/分值1 | 目标格占比 | 开局份额0 | 攻守 0/1 | 分项 | 总分 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(top, 1):
        A(f"| {i} | {r['name']} | {r['module']} | {r['hours']} | {r['units0']}/{r['units1']} | {r['rein_units0']}/{r['rein_units1']} | "
          f"{r['force_vp0']:,}/{r['force_vp1']:,} | {r['n_obj']}/{r['obj_vp0']:,}/{r['obj_vp1']:,} | {r['obj_ratio'] * 100:.1f}% | "
          f"{float(r['share0_start']) * 100:.1f}% | {r['stance0']}/{r['stance1']} | {r['score_parts']} | {r['score']} |")
    A("\n### 各自理由与任务原文（摘）\n")
    miss = json.load(open(os.path.join(out, "missions.json"), encoding="utf-8"))
    for i, r in enumerate(top, 1):
        m = miss[r["key"]]
        A(f"{i}. **{r['name']}**（{r['map']}，{r['hours']} h，H 时 {r['h_hour']}）：目标格 {r['n_obj']} 个，开局 0 方 {r['obj_start_side0']} / "
          f"1 方 {r['obj_start_side1']} / 无人 {r['obj_start_none']}；0 方需去拿 {r['todo_vp0']:,}、1 方需去拿 {r['todo_vp1']:,}"
          f"（其中在对方手里 {r['todo_from_enemy0']:,} / {r['todo_from_enemy1']:,}）；开局无格却要进攻的一方：{nog(r)}；"
          f"双方都不动时按开局份额落在的档位（只有 27 个有档线）：0 方 {r['passive_start_status0']}、1 方 {r['passive_start_status1']}。"
          f"\n   - 0 方任务：{m['side0']['mission'][:160]}\n   - 1 方任务：{m['side1']['mission'][:160]}")
    A("\n## 目标格分值乘 N 后的占比\n")
    A("目标格 VP 与开局持有的 VP 同乘 N（改想定文件），部队价值不变。表为前 8 名与占比最高的 5 个（已通过排除条件的）。需要 N 才能到 40% / 50% 占比。\n")
    A("| 想定 | 现占比 | ×2 占比/份额0 | ×3 | ×5 | ×10 | 到 40% 需 N | 到 50% 需 N |")
    A("|---|---|---|---|---|---|---|---|")
    seen = set()
    cand = top + sorted(elig, key=lambda r: -r["obj_ratio"])[:5]
    for r in cand:
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        res, n40, n50 = mult_table(r)
        cells = " | ".join(f"{ra * 100:.0f}% / {sh * 100:.0f}%" for _, ra, sh in res)
        A(f"| {r['name']} | {r['obj_ratio'] * 100:.1f}% | {cells} | {n40:.1f} | {n50:.1f} |")
    A("\n## 方法\n")
    A("- 部队价值：每方简报里各编队的 `In Sum: ... totalling N Victory Points.` 之和（与 s2_vp_table.py 读的同一来源，也是 EET 17,394 / 23,807 的出处）。"
      "0 方是存档里第一份简报，1 方是第二份；用快照子单位数核对（简报里 N x 数的是子单位；screen.tsv 的 sum_units_ok）。装备价值表没有用到，所以没有“查不到”的缺口。")
    A("- 目标格：fpc_objectives.objectives 直接读 .sav 的 FPC21.$$$（类型 14/15/16），按格号去重。开局归属取 owner_at_start。")
    A("- 目标格占比 = 目标格分值 /（目标格分值 + 双方部队价值）；两方分值不同时各算一次，取均值排序，tsv 里 obj_ratio0 / obj_ratio1 分列。")
    A("- 开局份额0 =（F0 + 0 方开局持有分值）/（F0 + F1 + 双方开局持有分值），无人占的格不计。")
    A("- 攻守：对 Mission 段落（权重 1）和指挥官附言（权重 0.5）数关键词（attack/advance/seize/capture/secure… 为攻；defend/hold/deny/block… 为守；"
      "delay/withdraw/slow… 为迟滞；screen/recon/identify… 为侦察），按“迟滞≥攻 → 迟滞；侦察≥max(攻,守) → 侦察；攻>守 → 攻；守>攻 → 守；平手看开局是否持格”定标签，"
      "两方都为攻则都标遭遇。关键词计数在 tsv 的 kw0/kw1。")
    A("- 打分（满分 90）：目标格占比 min(占比/50%,1)×40；攻守结构 25（单一明确攻方 15，再按攻方需去拿的分值占比加至 10；遭遇式按无人格占比最多 18；"
      "若有档线且攻方开局不动的档位是“败”再加 5、“未分”加 2）；开局份额 35–65% 得 15、往外线性降到 20/80% 为 0；增援单位占本方比例 ≥25% 得 10。")
    A("- 增援：快照里 status=Reinforcement 的单位；到达分钟（rein_arrival_min 的 最早/中位/最晚）取其第一条命令的 start_time 相对全体在场单位最早 start_time，是推测，未核实。")
    A("\n## EET 校验\n")
    if "err" in ref:
        A(f"- 读参照样本失败：{ref['err']}")
    ok = (eet["force_vp0"], eet["force_vp1"], eet["obj_vp0"]) == (17394, 23807, 13500)
    A(f"- 算出：部队价值 美(0) {eet['force_vp0']:,} / 苏(1) {eet['force_vp1']:,}，目标格合计 {eet['obj_vp0']:,}（1 方列 {eet['obj_vp1']:,}），6 个格。"
      f"期望 17,394 / 23,807 / 13,500。**{'一致' if ok else '不一致'}**。")
    if "vp" in ref:
        A(f"- $ARENA_DATA/scenarios 样本：vp-table briefing_total_by_era = {ref['vp']}；start.json 目标格合计 {ref.get('obj')}；units.json 单位数 {ref.get('units')}；"
          f"本脚本单位数 {eet['units0']}/{eet['units1']}。")
    A(f"- EET 开局份额0 {float(eet['share0_start']) * 100:.1f}%（材料 22 记 52%）；目标格占比 {eet['obj_ratio'] * 100:.1f}%。")
    bad = [r["name"] for r in rows if r["sum_units_ok"] != "是"]
    A(f"- 全体 74 个里简报编队子单位数与快照子单位数不符的：{len(bad)} 个{('（' + '、'.join(bad) + '）') if bad else ''}。")
    hs = [(r["name"], r["hours"]) for r in rows if r["hours_src"] != "简报"]
    A(f"- 时长不来自简报 H 时句的：{len(hs)} 个（来自开篇行时间窗或未知）。")
    A("\n## 局限\n")
    A("- 部队价值是简报里的开局编队总和，包含尚未到达的增援；增援单独的价值没算。")
    A("- 目标格取开局归属；终局时游戏会重新判无人格的归属（材料 22 第 4 节），这里没有。")
    A("- 攻守标签只是关键词，简报里“Seize and hold”这类混合句会偏攻；请对照 missions.json 人工复核前 8 名。")
    A("- 档线（胜负档）只在 27 个想定的简报里有，其余无法判“攻方不动是否输”，该项恒为“?”。")
    A("- 上面 12 个简报与快照子单位数对不上的想定，部队价值可能略有出入（例：Ate Up 简报多出 266 个子单位的编队）；前 8 名里没有这类想定。")
    A("- 目标格乘 N 时开局持有的分值也乘 N，所以开局份额会往持格多的一方偏（Falling From The Sky ×10 后 0 方份额只剩 23%），改想定时要同时调部队或归属。")
    A("- 只读开局存档，不含地形、距离、兵力对比的可达性；占比高不等于打得起来，仍需选定后跑一局空局验证。")
    open(os.path.join(out, "screen.md"), "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


if __name__ == "__main__":
    census = sys.argv[1] if len(sys.argv) > 1 else ARENA_DATA.as_posix() + "/runs/p1-briefs-20261030-070846"
    out = sys.argv[2] if len(sys.argv) > 2 else ARENA_DATA.as_posix() + "/scenario-screen"
    main(census, out)
