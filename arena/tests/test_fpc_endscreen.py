"""tools/fpc_endscreen.py: the official result from the recognised text of the end screens, each side's kept apart
(training p1-1 of 11-02: Battle Over was side 0's, the information center side 1's, and the shot cut off the right
edge of the text)."""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
for name in ("fitz", "rapidocr_onnxruntime"):          # OCR and image modules: not needed to parse text
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["rapidocr_onnxruntime"].RapidOCR = getattr(sys.modules["rapidocr_onnxruntime"], "RapidOCR", None)
import fpc_endscreen  # noqa: E402

# the recognised lines of p1-1 ($ARENA_DATA/runs/s2-training-115957/games/ex-p1-1), cut as shot
BATTLE_OVER = """To:
COL. Wagner, commanding 1st Bde.
You inflicted losses of 21,474 VPs on the enemy and secured victory
OLDIRONSIDES
6,500 VPs. Units under your command worth 29,921 VPs survived. Y
68% and this significantly improves on your starting ratio of 56%.
You and your forces obtained a Marginal Win."""
FINAL = """Final Report
To:
MG. Raffensperger, commanding 7. PD (-).
You inflicted losses of 12,416 vPs on the enemy and secured victory loc
VPs. Units under your command worth 17,419 VPs survived. Your VP rat
significantly less than your starting ratio of 44%.
You and your forces suffered a Marginal Loss."""
VICTORY = "Your force obtained 32% of the total victory points (VPs), which corresponds to a Marginal Loss."
SUDDEN = """COL. Wagner, commanding 1st Bde.
Casualties and withdrawals amongst our units have exceeded 70% by value.
You inflicted losses of 13,906 VPs on the enemy and secured victory locations worth 0 VPs. Units under your command
worth 12,531 VPs survived. Your VP ratio is 28% and this is significantly less than your starting ratio of 56%."""


# training p1-3 ($ARENA_DATA/runs/s2-training-182545/games/ex-p1-3): Battle Over side 0's, cut off
# to "commanding 1st"; the information center side 1's, named in its heading only
BATTLE_OVER_CUT = """To:
COL. Wagner, commanding 1st
You inflicted losses of 16,648 V
worth 6,500 VPs. Units under yo
OLDIRONSIDES
ratio is 63% and this is within 15
You and your forces obtained a"""
TOC_SIDE1 = """Final Report
ViCTOrY STAtus And ViCTOrY PC
A.7.PD(-) VICTORY STATUS, AS OF07:00HR
Your force obtained 37% of the total victory points
This corresponds to a multiplayer score of 737.
7. PD (-)"""
BILL_SIDE1 = """VictoryStatus&Conditions
Current % of Starting Forces
57 %
Scenario Initial VPs
38,893
Scenario Enemy Losses Inflicted
11,291
Scenario VP Locations
0
Scenario Surviving Forces
22,245
MARGINAL
DECISIVE LOSS
TACTICALLOSS"""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def pages(**texts):
    return {name: {"parsed": fpc_endscreen.parse(t)} for name, t in texts.items()}


class Parse(unittest.TestCase):
    def test_numbers_at_a_cut_off_line_end(self):
        p = fpc_endscreen.parse(BATTLE_OVER)
        self.assertEqual((p["commander"], p["vp_ratio_pct"], p["locations_vp"], p["survived_vp"]), ("1st Bde", 68.0, 6500.0, 29921.0))
        f = fpc_endscreen.parse(FINAL)
        self.assertEqual((f["commander"], f["inflicted_vp"], f["survived_vp"], f["result"]), ("7. PD (-)", 12416.0, 17419.0, "Marginal Loss"))
        self.assertNotIn("vp_ratio_pct", f)
        self.assertNotIn("locations_vp", f)

    def test_the_commander_is_its_line_not_the_text_after_it(self):
        p = fpc_endscreen.parse(SUDDEN)
        self.assertEqual((p["commander"], p["vp_ratio_pct"]), ("1st Bde", 28.0))

    @unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)  # the side names come from the scenario data
    def test_a_cut_off_or_heading_commander_is_the_scenarios_name(self):
        p = fpc_endscreen.parse(BATTLE_OVER_CUT)
        self.assertEqual((p["commander"], p["inflicted_vp"], p["locations_vp"], p["vp_ratio_pct"]), ("1st Bde", 16648.0, 6500.0, 63.0))
        self.assertEqual(fpc_endscreen.parse(TOC_SIDE1)["commander"], "7. PD (-)")
        for heading in ("A. 1ST BDE VICT0RY STATUS, AS OF 07:00 HRS, JUL 22 1989", "A.1ST BDEVICTORY STATUS,AS OF07:00HRS,JUL221989"):
            self.assertEqual(fpc_endscreen.parse(heading)["commander"], "1st Bde")
        self.assertNotIn("commander", fpc_endscreen.parse("ESTIMATED VICTORY STATUS AND"))   # the page title
        self.assertEqual(fpc_endscreen.canonical("Xyz Div"), "Xyz Div")              # no known side: as read

    def test_the_butchers_bill_rows(self):
        b = fpc_endscreen.parse(BILL_SIDE1)
        self.assertEqual((b["inflicted_vp"], b["locations_vp"], b["survived_vp"]), (11291.0, 0.0, 22245.0))
        self.assertNotIn("result", b)                             # "DECISIVE LOSS" there is the level table's

    def test_the_result_is_its_sentences_only(self):
        for text, level in ((BATTLE_OVER, "Marginal Win"), (FINAL, "Marginal Loss"), (VICTORY, "Marginal Loss"),
                            ("which would correspond to a Contested Result.", "Contested Result")):
            self.assertEqual(fpc_endscreen.parse(text)["result"], level)
        self.assertNotIn("result", fpc_endscreen.parse("MARGINAL\nDECISIVE LOSS\nTACTICAL WIN\nCONTESTED&NEUTR"))


class Combine(unittest.TestCase):
    def test_two_sides_screens_stay_apart(self):
        r = fpc_endscreen.combine(pages(**{"end-battle-over": BATTLE_OVER, "end-final": FINAL, "end-victory-2": VICTORY}))
        self.assertEqual((r["commander"], r["vp_ratio_pct"]), ("1st Bde", 68.0))
        self.assertNotIn("vp_share_pct", r)                      # side 1's share is not merged into side 0's result
        self.assertEqual(r["views"]["information-center"]["vp_share_pct"], 32.0)
        self.assertEqual(r["views"]["information-center"]["commander"], "7. PD (-)")

    def test_one_sides_screens_merge(self):
        final = FINAL.replace("MG. Raffensperger, commanding 7. PD (-).", "COL. Wagner, commanding 1st Bde.")
        r = fpc_endscreen.combine(pages(**{"end-battle-over": BATTLE_OVER, "end-final": final, "end-victory-2": VICTORY}))
        self.assertEqual((r["vp_ratio_pct"], r["vp_share_pct"]), (68.0, 32.0))
        r = fpc_endscreen.combine(pages(**{"end-battle-over": BATTLE_OVER, "end-victory-2": VICTORY}))   # no final report
        self.assertEqual(r["vp_share_pct"], 32.0)

    def test_the_other_sides_information_center_is_a_view_of_its_own(self):   # calibration ee-sc3 of 11-03
        own = "A. 11TH ARMORED CAVALRY REG VICT0RY STATUS, AS 0F 11:01 HRS\nYour force obtained 52% of the total victory points"
        enemy = "A. 8TH REC0N BN VICT0RY STATUS, AS 0F 11:01 HRS, JUL 21 1989\nYour force obtained 48% of the total victory points"
        r = fpc_endscreen.combine(pages(**{"end-battle-over": BATTLE_OVER, "end-toc": own, "end-enemy-victory-top": enemy}))
        self.assertEqual(r["views"]["information-center"]["vp_share_pct"], 52.0)
        self.assertEqual(r["views"]["enemy-information-center"]["vp_share_pct"], 48.0)
        self.assertNotIn("vp_share_pct", r)                      # 11th ACR is not Battle Over's 1st Bde: nothing merged

    @unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)  # the side names come from the scenario data
    def test_a_cut_off_battle_over_and_a_headed_information_center_stay_apart(self):
        r = fpc_endscreen.combine(pages(**{"end-battle-over": BATTLE_OVER_CUT, "end-bill": BILL_SIDE1, "end-toc": TOC_SIDE1}))
        self.assertEqual((r["commander"], r["vp_ratio_pct"], r["locations_vp"]), ("1st Bde", 63.0, 6500.0))
        self.assertNotIn("result", r)                             # both result sentences cut off
        ic = r["views"]["information-center"]
        self.assertEqual((ic["commander"], ic["vp_share_pct"], ic["survived_vp"]), ("7. PD (-)", 37.0, 22245.0))


if __name__ == "__main__":
    unittest.main()
