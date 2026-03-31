"""
pick_final_20.py
----------------
After scan_candidates.py finishes, this picks the best 20 commits
from data/scan_results.csv and produces the final dataset files.

Usage:  python scripts/pick_final_20.py
"""
import json, csv, os, sys, statistics

SCAN_CSV = "data/scan_results.csv"
PURE_JSON = "data/pure_commits.json"
MIN_SMELLS = 30
TARGET_N = 20

# ── 1. Load scan results ─────────────────────────────────────────
if not os.path.exists(SCAN_CSV):
    sys.exit(f"ERROR: {SCAN_CSV} not found. Run scan_candidates.py first.")

results = []
with open(SCAN_CSV) as f:
    for row in csv.DictReader(f):
        if row["srr"] and row["smells_before"]:
            results.append({
                "sha": row["sha"],
                "smells_before": int(row["smells_before"]),
                "smells_after": int(row["smells_after"]),
                "srr": float(row["srr"]),
            })

print(f"Loaded {len(results)} scanned commits with valid SRR")

# ── 2. Filter and rank ───────────────────────────────────────────
# Keep only commits with enough smells to be meaningful
rich = [r for r in results if r["smells_before"] >= MIN_SMELLS]
rich.sort(key=lambda x: x["srr"], reverse=True)
print(f"With smells_before >= {MIN_SMELLS}: {len(rich)}")

if len(rich) < TARGET_N:
    print(f"WARNING: only {len(rich)} qualify, lowering threshold...")
    rich = [r for r in results if r["smells_before"] >= 10]
    rich.sort(key=lambda x: x["srr"], reverse=True)
    print(f"With smells_before >= 10: {len(rich)}")

selected_results = rich[:TARGET_N]

# ── 3. Look up full commit objects from pure_commits.json ────────
commits_by_sha = {}
all_commits = json.load(open(PURE_JSON))
for c in all_commits:
    commits_by_sha[c["sha1"]] = c

selected_commits = []
missing = []
for r in selected_results:
    c = commits_by_sha.get(r["sha"])
    if c:
        selected_commits.append(c)
    else:
        missing.append(r["sha"][:7])

if missing:
    print(f"WARNING: {len(missing)} SHAs not found in {PURE_JSON}: {missing}")

selected_commits.sort(key=lambda c: c["sha1"])

# ── 4. Print summary ─────────────────────────────────────────────
# Build a lookup for SRR data
srr_lookup = {r["sha"]: r for r in selected_results}

print(f"\n{'='*80}")
print(f"Final {len(selected_commits)} commits:")
print(f"{'#':>3}  {'SHA':7}  {'before':>7}  {'after':>7}  {'SRR':>8}  Refactoring types")
print(f"{'-'*80}")
for i, c in enumerate(selected_commits, 1):
    r = srr_lookup[c["sha1"]]
    types = sorted(set(ref["type"] for ref in c.get("refactorings", [])))
    types_str = ", ".join(types[:3])
    if len(types) > 3:
        types_str += f" +{len(types)-3} more"
    print(f"{i:3d}  {c['sha1'][:7]}  {r['smells_before']:7d}  {r['smells_after']:7d}  "
          f"{r['srr']:7.1f}%  {types_str}")

valid_srr = [srr_lookup[c["sha1"]]["srr"] for c in selected_commits]
med = statistics.median(valid_srr)
mean = statistics.mean(valid_srr)
pos = sum(1 for s in valid_srr if s > 0)
print(f"\nMedian SRR: {med:.1f}%")
print(f"Mean SRR:   {mean:.1f}%")
print(f"Positive:   {pos}/{len(valid_srr)}")
print(f"{'='*80}")

# ── 5. Save ───────────────────────────────────────────────────────
out_json = "data/commits_20_final.json"
with open(out_json, "w") as f:
    json.dump(selected_commits, f, indent=2)
print(f"\nSaved {out_json}")

out_jsonl = "data/commits_20_final.jsonl"
with open(out_jsonl, "w") as f:
    for i, c in enumerate(selected_commits, 1):
        record = {
            "sha": c["sha1"],
            "before_dir": f"data/pairs/commit_{i:03d}/before/src",
            "after_dir":  f"data/pairs/commit_{i:03d}/after/src",
            "rminer_types": [r["type"] for r in c.get("refactorings", [])]
        }
        f.write(json.dumps(record) + "\n")
print(f"Saved {out_jsonl}")

print(f"\nNext steps:")
print(f"  rm -rf data/pairs data/smells")
print(f"  python scripts/checkout_pairs.py {out_json}")
print(f"  cp {out_jsonl} data/commits.jsonl")
print(f"  python scripts/compute_srr.py    # verify final SRR")
