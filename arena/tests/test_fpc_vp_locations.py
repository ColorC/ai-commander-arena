"""tools/fpc_vp_locations.py: VP locations from the Victory Status pages, as read (calibration ee-sc1 of 11-03:
$ARENA_DATA/runs/s2-calib-secc-1103/s2-cal-ee-sc1, Eyes Ears and Teeth, both sides passive)."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fpc_vp_locations  # noqa: E402

PAGE_3 = """B. OWNERSHIP OF VICTORY LOCATIONS
CONTESTED & NEUTRAL
Hex 1711 (1,000 vP)
Hex 3718 (2,500 vP)
Hex 1307 (2,500 VP)
Total: 8,500 VP
C. FORCE INFLUENCES ON OWNERSHIP OF VICTORY POINT LOCATIONS:
Unclaimed 0bjective, 2,500/2,500 VP at 3718: 0bjective remains unclaimed.
No units near objective"""
PAGE_4 = """Unclaimed 0bjective, 2,500/2,500 VP at 3718: 0bjective remains unclaimed.
No units near objective
NATo objective, 1,000/1,000 VP at 1711: NAT0 retains control.
unclaimea upjective, I,uuu/ I,uuu
Hex 4006 (1,000 VP)
Warsaw Pact 0bjective,1,500/1,500 VP at 2223:W:"""


class Read(unittest.TestCase):
    def test_section_c_and_b_as_read(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("end-victory-3.txt", PAGE_3), ("end-victory-4.txt", PAGE_4)):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(text)
            r = fpc_vp_locations.read_run(d)
        L = r["locations"]
        self.assertEqual((L[3718]["owner"], L[3718]["vp"]), ("Unclaimed", 2500))       # read on two pages, once
        self.assertEqual((L[1711]["owner"], L[1711]["vp"]), ("NATO", 1000))           # "NATo objective", "NAT0"
        self.assertEqual((L[2223]["owner"], L[2223]["vp"]), ("Warsaw Pact", 1500))
        self.assertEqual((L[1307]["owner"], L[4006]["vp"]), (None, 1000))             # Section B only
        self.assertEqual(r["total"], 2500 + 1000 + 1500 + 2500 + 1000)
        self.assertEqual(r["by_owner"]["Unclaimed"], 2500)


if __name__ == "__main__":
    unittest.main()
