"""tools/fpc_objectives.py: VP location records of FPC21.$$$ (High Sticking's start and 96 % saves of calibration hs-pb,
11-03: the line markers around them are skipped, a location's value can differ by side)."""
import os
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fpc_objectives  # noqa: E402


def record(hexno, mtype, owner, start, vp0, vp1, other=0):
    f = [0] * 75
    f[0], f[1], f[2], f[3], f[4], f[18], f[33], f[34], f[74] = hexno, other, owner, start, mtype, mtype * 65537, vp0, vp1, 102
    return struct.pack("<75i", *f)


def data(owners):
    return (b"\x00" * 6 + record(3715, 19, 2, 2, -1, -1, other=3716)
            + b"".join(record(h, t, o, s, a, b) for h, t, o, s, a, b in owners) + b"\x01" * 40)


START = [(2716, 15, 1, 1, 2500, 2500), (2504, 16, 2, 2, 2500, 2500), (3715, 15, 1, 1, 1500, 500),
         (1507, 14, 0, 0, 500, 1500), (2018, 14, 0, 0, 500, 1500), (3407, 15, 1, 1, 1500, 500)]


class Objectives(unittest.TestCase):
    def test_the_start_of_high_sticking(self):
        objs = fpc_objectives.objectives(data(START))
        self.assertEqual([o["hex"] for o in objs], [2716, 2504, 3715, 1507, 2018, 3407])   # not the line marker 3715-3716
        self.assertEqual(fpc_objectives.held(objs), {0: 1000, 1: 3500})                     # the end screens' opening VP Locations

    def test_the_soviets_took_everything(self):   # 96 %: the end screen's 9,000 for 11 MRR
        end = [(h, t, 1, s, a, b) for h, t, o, s, a, b in START]
        objs = fpc_objectives.objectives(data(end))
        self.assertEqual(fpc_objectives.held(objs), {0: 0, 1: 9000})
        self.assertEqual(fpc_objectives.held(objs, "owner_at_start"), {0: 1000, 1: 3500})

    def test_read_from_the_zip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.sav")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("FPC21.$$$", data(START[:2]))
            self.assertEqual([o["owner"] for o in fpc_objectives.read(path)], [1, 2])


if __name__ == "__main__":
    unittest.main()
