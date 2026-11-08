"""Per-scenario VP value of each subunit type, from the briefings the game writes into its 0 % auto-save.

    python -I -X utf8 tools/s2_vp_table.py <census dir> <scenario folder> <out json>

<census dir> is a pulled fpc/remote/job-p1-briefs.ps1 run (one folder per scenario with start.sav). Every formation
paragraph of a briefing says "In Sum: ... totalling N Victory Points." and "In Detail: 6 x M577A2 C2V, 4 x
Headquarters and 2 x M151 Jeep 4x4.", followed by the first unit's national era. Checked 11-01 over the 81 base and
DLC census saves: a subunit name has one value inside a scenario, but the same name can differ between scenarios
(AH-1F Cobra 533 or 544, PAH-I BO-105 254 or 279; not by the unit's training or morale, probably by the scenario
year's equipment), so the table is per scenario. The scenario's own formations are solved first (least squares);
names its formations cannot separate are pulled towards the median of the same name over all census scenarios.
The names are the subunit names GameAPI.GetUnitSubunits reports.
"""
import collections
import glob
import json
import os
import re
import sys
import zipfile

RUN16 = re.compile(rb"(?:[\x09\x20-\x7e\xa0-\xff]\x00){3,}")
SUM = re.compile(r"In Sum: .*?totalling ([\d,]+) Victory Points")
DET = re.compile(r"In Detail: (.*?)\.\s*$")
ERA = re.compile(r"^CW 80s .*\(\d{4}-\d{4}\)$")
PRIOR_WEIGHT = 0.05  # small: the scenario's own sums decide wherever they can


def items(detail):
    out = []
    for p in re.split(r", | and (?=\d+ x )", detail):
        m = re.match(r"(\d+) x (.+)$", p.strip())
        if not m:
            return None
        out.append((int(m.group(1)), m.group(2).strip()))
    return out


def formations(sav):
    z = zipfile.ZipFile(sav)
    lines = [m.group().decode("utf-16-le").strip() for m in RUN16.finditer(b"".join(z.read(n) for n in z.namelist()))]
    out = []
    for i, line in enumerate(lines):
        m = SUM.search(line)
        if not m:
            continue
        det = next((d for d in (DET.search(lines[j]) for j in range(i + 1, min(i + 4, len(lines)))) if d), None)
        era = next((lines[j] for j in range(i + 1, min(i + 12, len(lines))) if ERA.match(lines[j])), None)
        its = items(det.group(1)) if det else None
        if its and era:
            out.append({"total": int(m.group(1).replace(",", "")), "items": its, "era": era})
    return out


def fit(rows, keys, prior, weight, sweeps=400):
    """Weighted least squares with a pull towards prior, by coordinate descent (no numpy on this machine: its
    install is broken). rows: [(items {key: count}, total)]; minimises
    sum (row.x - total)^2 / total + weight * sum (x - prior)^2 / prior."""
    x = dict(prior)
    cols = collections.defaultdict(list)
    for r, (its, t) in enumerate(rows):
        for k, c in its.items():
            cols[k].append((r, c))
    res = [sum(c * x[k] for k, c in its.items()) - t for its, t in rows]
    wt = [1.0 / max(t, 1.0) for _, t in rows]
    for _ in range(sweeps):
        for k in keys:
            lam = weight / max(prior[k], 1.0)
            num = lam * prior[k] + sum(wt[r] * c * (c * x[k] - res[r]) for r, c in cols[k])
            den = lam + sum(wt[r] * c * c for r, c in cols[k])
            new = max(num / den, 0.0)
            if new != x[k]:
                for r, c in cols[k]:
                    res[r] += c * (new - x[k])
                x[k] = new
    return x, res


def main(census, scenario, out_path):
    every, own = [], []
    for sav in sorted(glob.glob(os.path.join(census, "*", "start.sav"))):
        name = os.path.basename(os.path.dirname(sav))
        for f in formations(sav):
            row = (collections.Counter(), float(f["total"]))
            for c, n in f["items"]:
                row[0][(f["era"], n)] += c
            every.append(row)
            if name == scenario:
                own.append((f, row))
    if not own:
        raise SystemExit(f"no formations for {scenario!r} under {census}")
    eras = {f["era"] for f, _ in own}
    # first pass, every scenario of the same nations: one value per name (scenario differences average out),
    # pulled weakly towards the nation's mean per subunit
    pool = [r for r in every if all(k[0] in eras for k in r[0])]
    allkeys = sorted({k for its, _ in pool for k in its})
    mean = {e: sum(t for its, t in pool if next(iter(its))[0] == e) /
               max(1, sum(c for its, _ in pool if next(iter(its))[0] == e for c in its.values())) for e in eras}
    g, _ = fit(pool, allkeys, {k: mean[k[0]] for k in allkeys}, 0.01)
    # second pass, this scenario only, pulled towards the first pass
    keys = sorted({k for _, (its, _) in own for k in its})
    x, resid = fit([row for _, row in own], keys, {k: g[k] for k in keys}, PRIOR_WEIGHT)
    worst = max(abs(e) / t for e, (_, (_, t)) in zip(resid, own))
    alone = collections.Counter(next(iter(its)) for its, _ in every if len(its) == 1)
    table = [{"era": e, "name": n, "vp": round(x[(e, n)], 1), "all_scenarios": round(g[(e, n)], 1),
              "seen_alone": alone[(e, n)]} for e, n in keys]
    k = len(keys)
    own = [f for f, _ in own]
    sides = collections.defaultdict(int)
    for f in own:
        sides[f["era"]] += f["total"]
    out = {"scenario": scenario, "source": census, "formations": len(own), "types": k,
           "worst_formation_error_pct": round(100 * worst, 2), "briefing_total_by_era": dict(sides), "table": table}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"{scenario}: {len(own)} formations, {k} types, worst formation error {100 * worst:.2f}%")
    for e, t in sorted(sides.items()):
        print(f"  {e}: {t} VP in the briefing")


if __name__ == "__main__":
    main(*sys.argv[1:4])
