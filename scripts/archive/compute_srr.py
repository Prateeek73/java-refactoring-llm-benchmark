"""
compute_srr.py — Compute Smell Reduction Rate for commit pairs.

Usage:
  python scripts/compute_srr.py
  python scripts/compute_srr.py --input data/commits.jsonl --output data/srr_baseline.csv
  python scripts/compute_srr.py --dj-cp /path/to/classes:/path/to/lib/*
  python scripts/compute_srr.py --timeout 300 --smells-dir data/smells
"""
import json, os, csv, statistics, shutil, argparse, sys
sys.path.insert(0, os.path.dirname(__file__))
from lib import count_smells, find_changed_files, run_designite, default_dj_cp

def compute_srr(before_dir, after_dir, tmp_root, dj_cp=None, timeout=600):
    b_out = os.path.join(tmp_root, "before_smells")
    a_out = os.path.join(tmp_root, "after_smells")
    modules, changed_classes = find_changed_files(before_dir, after_dir)
    if not modules:
        return 0, 0, None
    sb = run_designite(before_dir, b_out, modules, changed_classes,
                       dj_cp=dj_cp, timeout=timeout)
    sa = run_designite(after_dir, a_out, modules, changed_classes,
                       dj_cp=dj_cp, timeout=timeout)
    srr = (sb - sa) / sb * 100 if sb > 0 else None
    return sb, sa, srr

def main():
    p = argparse.ArgumentParser(description="Compute SRR baseline for commit pairs.")
    p.add_argument("--input", default="data/commits.jsonl",
                   help="Input JSONL file (default: data/commits.jsonl)")
    p.add_argument("--output", default="data/srr_baseline.csv",
                   help="Output CSV (default: data/srr_baseline.csv)")
    p.add_argument("--dj-cp", default=None,
                   help="DesigniteJava classpath (default: env DESIGNITE_CP or auto)")
    p.add_argument("--timeout", type=int, default=600,
                   help="DesigniteJava timeout in seconds (default: 600)")
    p.add_argument("--smells-dir", default="data/smells",
                   help="Directory for smell outputs (default: data/smells)")
    args = p.parse_args()

    dj_cp = args.dj_cp or default_dj_cp()

    lines = open(args.input).readlines()
    total = len(lines)
    results = []
    for i, line in enumerate(lines, 1):
        c = json.loads(line)
        tmp = f"{args.smells_dir}/commit_{i:03d}"
        os.makedirs(tmp, exist_ok=True)
        sb, sa, srr = compute_srr(c["before_dir"], c["after_dir"], tmp,
                                   dj_cp=dj_cp, timeout=args.timeout)
        results.append({"sha": c["sha"][:7], "smells_before": sb,
                         "smells_after": sa, "srr": srr})
        tag = f"{srr:.1f}%" if srr is not None else "N/A"
        print(f"[{i}/{total}] {c['sha'][:7]}  before={sb}  after={sa}  SRR={tag}")

    with open(args.output, "w", newline="") as f:
        w = csv.DictWriter(f, ["sha", "smells_before", "smells_after", "srr"])
        w.writeheader()
        w.writerows(results)

    valid = [r["srr"] for r in results if r["srr"] is not None]
    if valid:
        print(f"\nDeveloper median SRR: {statistics.median(valid):.1f}%  (n={len(valid)})")
    else:
        print("\nNo valid SRR values found")

if __name__ == "__main__":
    main()