"""P0.5: read the official result from shots of FPC's end-of-game screens.

python tools/fpc_endscreen.py <run dir with end-*.bmp>   writes <run dir>/end-*.png, end-*.txt and end-result.json

The end-of-game dialogs draw their text themselves (no window text), so the job shoots them (arena.shot) and this
script recognises the text (RapidOCR) and pulls out the numbers. The "Battle Over" dialog (every style of play;
a computer-vs-computer game exits right after it) says, for the side it is addressed to:
  "LTC. Searby, commanding 9th/12th Royal Lancers. You inflicted losses of <n> VPs on the enemy and secured victory
   locations worth <n> VPs. Units under your command worth <n> VPs survived. Your VP ratio is <r>% and this is ...
   your starting ratio of <s>%. You and your forces suffered a Marginal Loss." plus a row of stars.
The hot-seat information center's Victory Status page says "Your force obtained <share>% of the total victory
points (VPs), which corresponds to a <level>." (parsed too when shot).
The information center can be another side's than Battle Over: with two seats, Battle Over is side 0's and the
information center either side's (side 1's in calibration af-pb 11-01 and training p1-1, side 0's in p0-3, 11-02). end-result.json "result" is Battle Over's,
with the information center's fields added only when it is the same commander's; result["views"] keeps each one
("battle-over", "information-center") for the scorecard to take the player's own. The shots cut off the right edge
of the text, so a number at a line's end can be missing; the patterns also catch what is left of those sentences.
The information center names its side only in its heading "A. 7. PD (-) VICTORY STATUS, AS OF 07:00 HRS, …", and a
cut-off Battle Over can leave "commanding 1st" (training p1-3, 11-02): each commander read is written as the
scenario's own name of that side ($ARENA_DATA/scenarios/*/official.json) when exactly one fits, so the scorecard can tell them apart.
Runs with the user's Python (not -I): the system site-packages Pillow is incomplete, rapidocr needs the user one.
"""
import functools
import glob
import json
import os
import re
import sys

import fitz  # PyMuPDF: .bmp -> .png
from rapidocr_onnxruntime import RapidOCR

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# scenario data (README): $ARENA_DATA/scenarios/<scenario>/, ARENA_DATA default <repo>/data
DATA = os.path.join(os.environ.get("ARENA_DATA") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"), "scenarios")
NUM = r"([\d][\d,]*(?:\.\d+)?)"
# the information center's heading, as read: "A. 1ST BDE VICT0RY STATUS, AS OF 07:00 HRS", "A.7.PD(-) VICTORY STATUS, AS OF07:00HR",
# "A. 8TH REC0N BN VICT0RY STATUS, AS 0F 11:01 HRS" (Eyes Ears and Teeth 11-03)
HEADING = r"^(?:A\.\s*)?(.+?)\s*VICT[O0]RY\s*STATUS\s*,?\s*AS\s*[O0]F"
PATTERNS = {
    "commander": r"commanding\s+(.+?)\.\s",          # when no line of its own (parse: "commanding 7. PD (-)." is one)
    # Battle Over's sentences ("16,648 V" when cut off), then the Butcher's Bill's rows ("Scenario Enemy Losses
    # Inflicted 11,291", "Scenario VP Locations 0", "Scenario Surviving Forces 22,245": p1-3, its side's own)
    "inflicted_vp": (r"inflicted\s+losses\s+of\s+" + NUM + r"\s+V", r"Enemy\s+Losses\s+Inflicted\s+" + NUM),
    "locations_vp": (r"victory\s+locations\s+worth\s+" + NUM + r"\s+VPs", NUM + r"\s+VPs\.\s+Units\s+under",
                     r"Scenario\s+VP\s+Locations\s+" + NUM),
    "survived_vp": (r"worth\s+" + NUM + r"\s+VPs\s+survived", r"Surviving\s+Forces\s+" + NUM),
    "vp_ratio_pct": (r"VP\s+ratio\s+is\s+" + NUM + r"\s*%", NUM + r"\s*%\s+and\s+this\b"),
    "starting_ratio_pct": r"starting\s+ratio\s+of\s+" + NUM + r"\s*%",
    # in its sentence only ("obtained a", "suffered a", "corresponds to a"): the Victory Status pages also show the
    # level table ("MARGINAL", "DECISIVE LOSS", "TACTICALLOSS": training p0-4, p1-4, p1-5 of 11-02 read that)
    "result": r"(?:obtained|suffered|correspond(?:s)?\s+to)\s+an?\s+((?:Decisive|Marginal|Major|Minor|Tactical|Strategic)\s+"
              r"(?:Win|Loss|Victory|Defeat)|Contested\s+Result|Draw)\b",
    "combat_ready_pct": r"finished\s+the\s+battle\s+with\s+" + NUM + r"\s*%\s+by\s+VP\s+value",
    "vp_share_pct": r"obtained\s+(?:approximately\s+)?" + NUM + r"\s*%\s*of\s+the\s+total\s+victory\s+points",
    "force_points": r"force\s+worth\s+" + NUM + r"\s+points\s+obtained",
    "awarded_vp_pct": r"points\s+obtained\s+" + NUM + r"\s*%\s*of\s+awarded",
    "enemy_points": r"enemy\s+force\s+worth\s+" + NUM + r"\s+points",
    "adjusted_net_score_pct": r"Adjusted\s+Net\s+Score.*?\bis\s+" + NUM + r"\s*%",
}
TEXT_FIELDS = {"commander", "result"}


def ocr_lines(engine, png):
    result, _ = engine(png, use_cls=False)  # the angle classifier flips some dialog lines upside down
    boxes = sorted(result or [], key=lambda r: (round(r[0][0][1] / 8), r[0][0][0]))  # rows top-down, then left-right
    return [r[1] for r in boxes]


def parse(text):
    flat = re.sub(r"\s+", " ", text)
    out = {}
    for key, pats in PATTERNS.items():
        m = next((m for m in (re.search(p, flat, re.I | re.S) for p in ([pats] if isinstance(pats, str) else pats)) if m), None)
        if m:
            v = m.group(1).replace(",", "")
            out[key] = v if key in TEXT_FIELDS else float(v)
    # the addressee's line "COL. Wagner, commanding 1st Bde.": the name up to the line's end (names hold ". " too)
    line = next((re.search(r"commanding\s+(.+?)\.?\s*$", s) for s in text.splitlines() if "commanding " in s), None)
    if line:
        out["commander"] = line.group(1)
    if "commander" not in out:      # the information center's pages: the side is in the heading only
        head = next(filter(None, (re.search(HEADING, s.strip(), re.I) for s in text.splitlines())), None)
        if head:
            out["commander"] = head.group(1)
    if out.get("commander"):
        out["commander"] = canonical(out["commander"])
    # performance stars, filled / all, from the line made only of stars (unit badges can hold a star too)
    row = next((s for s in text.splitlines() if s.strip() and set(s.strip()) <= {chr(0x2605), chr(0x2606)}), "")
    if row:
        out["stars"] = f"{row.count(chr(0x2605))}/{row.count(chr(0x2605)) + row.count(chr(0x2606))}"
    return out


def _key(name):
    return re.sub(r"[^0-9a-z]", "", str(name or "").lower())


@functools.lru_cache(maxsize=None)
def known_commanders():
    """Every scenario's side names as the end screens write them: official.json "7. PD (-) (East German)" is "7. PD (-)"."""
    out = set()
    for path in glob.glob(os.path.join(DATA, "*", "official.json")):
        with open(path, encoding="utf-8") as f:
            out.update(n.rsplit(" (", 1)[0].strip() for n in (json.load(f).get("sides") or {}).values())
    return tuple(sorted(out))


def same_commander(a, b):
    """The two names read are one side's: equal but for spaces and punctuation, or one cut off from the other."""
    a, b = _key(a), _key(b)
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def canonical(name):
    """The commander as read -> the scenario's own name of that side when exactly one known side fits it (the read one
    can be cut off at the shot's edge, "1st", or spaced otherwise, "7.PD(-)"); as read otherwise."""
    if len(_key(name)) < 3:
        return name
    hits = [n for n in known_commanders() if same_commander(name, n)]
    return hits[0] if len(hits) == 1 else name


def combine(pages):
    """The official result from the pages' parsed fields (module docstring): Battle Over's, the information center's
    added when it is the same commander's (or either commander is unknown), both kept under "views"."""
    views = {}
    for name in sorted(pages):              # end-bill, end-final, end-tab-*, end-toc, end-victory-*: one information
        # center; end-enemy*: the other side's (job-p0's "View Enemy Scenario Information Center"), never merged into it
        view = ("battle-over" if name == "end-battle-over" else
                "enemy-information-center" if name.startswith("end-enemy") else "information-center")
        v = views.setdefault(view, {})
        for k, x in pages[name]["parsed"].items():
            v.setdefault(k, x)
    bo, ic = views.get("battle-over") or {}, views.get("information-center") or {}
    result = dict(bo)
    if not bo.get("commander") or not ic.get("commander") or same_commander(bo["commander"], ic["commander"]):
        for k, x in ic.items():
            result.setdefault(k, x)
    views = {k: v for k, v in views.items() if v}
    if views:
        result["views"] = views
    return result


def main(run):
    engine = RapidOCR()
    pages = {}
    for bmp in sorted(glob.glob(os.path.join(run, "end-*.bmp"))):
        name = os.path.splitext(os.path.basename(bmp))[0]
        png = os.path.join(run, name + ".png")
        fitz.Pixmap(bmp).save(png)
        lines = ocr_lines(engine, png)
        with open(os.path.join(run, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        pages[name] = {"lines": len(lines), "parsed": parse("\n".join(lines))}
        print(name, json.dumps(pages[name], ensure_ascii=False))
    result = combine(pages)
    with open(os.path.join(run, "end-result.json"), "w", encoding="utf-8") as f:
        json.dump({"result": result, "pages": pages}, f, ensure_ascii=False, indent=1)
    print("result", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
