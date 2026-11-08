"""Terrain table from an FPC map file (.fp10), the text the game shows in its hex info panel.

.fp10 layout (read from Aichelberg.fp10 / Eiterfeld.fp10, 2026-10-30): header with the map name, author, place,
columns and rows as int32, then one record per hex, column by column (record i = column i // rows + 1,
row i % rows + 1; hex number = column * 100 + row). Per record, 7 int32 then 5 strings (int32 length + UTF-16LE):
  ints   [elevation step, mobility?, cover?, content height m, unknown, visibility?, elevation m]
  strs   [water hex sides, road / rail hex sides, slope hex sides, (unused here), terrain description]
Hex-side strings list "<side 1..6>:<code> (<type>)". Every record is followed by int32 666.
The meaning of ints 1, 2, 4, 5 is inferred from averages per terrain type (marked with ? in the field names).

python -I tools/fpc_map.py <map.fp10> [out.tsv]
"""
import struct
import sys

FIELDS = ["elev_step", "mobility?", "cover?", "height_m", "unknown", "visibility?", "elev_m"]
SEP = b"\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00\x00\x00"  # -1, 0, 0 between the ints and the strings


def read(path):
    d = open(path, "rb").read()

    def rstr(p):
        n = struct.unpack_from("<i", d, p)[0]
        return d[p + 4:p + 4 + 2 * n].decode("utf-16-le", "replace"), p + 4 + 2 * n

    name, p = rstr(4)
    cols, rows = struct.unpack_from("<2i", d, p + 8)
    hexes, p = [], p
    while True:
        j = d.find(SEP, p)
        if j < 0:
            break
        ints = struct.unpack_from("<7i", d, j - 28)
        q, strs = j + 12, []
        for _ in range(5):
            s, q = rstr(q)
            strs.append(s.strip())
        i = len(hexes)
        col, row = i // rows + 1, i % rows + 1
        hexes.append({"hex": col * 100 + row, "col": col, "row": row, **dict(zip(FIELDS, ints)),
                      "water_sides": strs[0], "road_sides": strs[1], "slope_sides": strs[2], "other_sides": strs[3],
                      "terrain": strs[4]})
        p = q
    if len(hexes) != cols * rows:
        raise ValueError(f"{path}: {len(hexes)} hex records, header says {cols} x {rows}")
    return {"name": name, "cols": cols, "rows": rows, "hexes": hexes}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    m = read(sys.argv[1])
    keys = ["hex", "elev_m", "height_m", "terrain", "road_sides", "water_sides", "slope_sides", "other_sides"]
    out = open(sys.argv[2], "w", encoding="utf-8", newline="\n") if len(sys.argv) > 2 else sys.stdout
    print(f"# {m['name']} {m['cols']} x {m['rows']}", file=out)
    print("\t".join(keys), file=out)
    for h in m["hexes"]:
        print("\t".join(str(h[k]) for k in keys), file=out)
