"""
filter_highsignal.py
--------------------
Resample 20 high-signal commits from pure_commits.json.
Filters for structural refactoring types and (if available)
positive developer SRR from srr_baseline.csv.

Usage:  python scripts/filter_highsignal.py
"""
import json, csv, os, random, sys

STRUCTURAL_TYPES = {
    "Extract Method", "Move Method", "Pull Up Method",
    "Push Down Method", "Extract Class", "Move Attribute",
    "Extract And Move Method", "Extract Superclass",
    "Extract Interface", "Inline Method", "Move Class",
    "Pull Up Attribute", "Push Down Attribute",
}

TARGET_N = 35
SEED = 42

# ── 1. Load pure commits ──────────────────────────────────────────
pure_path = "data/pure_commits.json"
if not os.path.exists(pure_path):
    sys.exit(f"ERROR: {pure_path} not found")

commits = json.load(open(pure_path))
print(f"Loaded {len(commits)} pure refactoring commits")

# ── 2. Filter to structural refactoring types ─────────────────────
def get_types(c):
    return [r["type"] for r in c.get("refactorings", [])]

def structural_types(c):
    return [t for t in get_types(c) if t in STRUCTURAL_TYPES]

structural = [c for c in commits if structural_types(c)]
print(f"After structural-type filter: {len(structural)} commits")

if len(structural) < TARGET_N:
    print("WARNING: fewer structural commits than target, relaxing to all pure commits")
    structural = commits

# ── 3. Join SRR baseline if available ─────────────────────────────
srr_map = {}
srr_path = "data/srr_baseline.csv"
if os.path.exists(srr_path):
    with open(srr_path) as f:
        for row in csv.DictReader(f):
            sha7 = row["sha"][:7]
            try:
                srr_map[sha7] = {
                    "srr": float(row["srr"]) if row["srr"] else None,
                    "smells_before": int(row["smells_before"]),
                }
            except (ValueError, KeyError):
                pass
    print(f"Loaded SRR baseline for {len(srr_map)} commits")

    # Tag each commit with SRR info
    for c in structural:
        sha7 = c["sha1"][:7]
        info = srr_map.get(sha7)
        if info:
            c["_srr"] = info["srr"]
            c["_smells_before"] = info["smells_before"]
        else:
            c["_srr"] = None
            c["_smells_before"] = 0

    # First pass: positive SRR and smells_before > 50
    rich = [c for c in structural
            if c.get("_srr") is not None and c["_srr"] > 0
            and c.get("_smells_before", 0) > 50]
    print(f"  Commits with SRR > 0 and smells_before > 50: {len(rich)}")

    if len(rich) < TARGET_N:
        # Relax: SRR >= -5%
        rich = [c for c in structural
                if c.get("_srr") is not None and c["_srr"] >= -5
                and c.get("_smells_before", 0) > 10]
        print(f"  Relaxed (SRR >= -5%, smells > 10): {len(rich)}")

    if len(rich) < TARGET_N:
        # Relax further: just structural, no SRR filter
        rich = structural
        print(f"  Using all structural commits (no SRR filter): {len(rich)}")
else:
    rich = structural
    print("No srr_baseline.csv found — skipping SRR filter")

# ── 4. Score and rank ─────────────────────────────────────────────
def score(c):
    s = 0
    st = structural_types(c)
    s += len(set(st)) * 10        # diversity of structural types
    s += min(len(st), 10) * 2     # count (capped)
    if c.get("_srr") is not None:
        s += c["_srr"]            # higher SRR = better
    if c.get("_smells_before", 0) > 100:
        s += 5                    # richer commit
    return s

rich.sort(key=score, reverse=True)

# Take top 60 candidates, then sample TARGET_N for variety
top_pool = rich[:60]
random.seed(SEED)
if len(top_pool) <= TARGET_N:
    selected = top_pool
else:
    selected = random.sample(top_pool, TARGET_N)

# Sort selected by SHA for stable ordering
selected.sort(key=lambda c: c["sha1"])

# ── 5. Print summary table ────────────────────────────────────────
print(f"\n{'='*80}")
print(f"Selected {len(selected)} high-signal commits:")
print(f"{'#':>3}  {'SHA':7}  {'SRR':>8}  {'smells_b':>8}  Structural types")
print(f"{'-'*80}")
for i, c in enumerate(selected, 1):
    sha7 = c["sha1"][:7]
    srr = c.get("_srr")
    sb = c.get("_smells_before", "?")
    srr_str = f"{srr:.1f}%" if srr is not None else "N/A"
    types = ", ".join(sorted(set(structural_types(c))))
    print(f"{i:3d}  {sha7}  {srr_str:>8}  {sb:>8}  {types}")

# Median SRR
valid_srr = [c["_srr"] for c in selected if c.get("_srr") is not None]
if valid_srr:
    import statistics
    med = statistics.median(valid_srr)
    print(f"\nNew developer median SRR: {med:.1f}%  (n={len(valid_srr)})")
print(f"{'='*80}")

# ── 6. Save outputs ──────────────────────────────────────────────
# Clean up internal keys before saving
for c in selected:
    c.pop("_srr", None)
    c.pop("_smells_before", None)

out_json = "data/commits_35_highsignal.json"
with open(out_json, "w") as f:
    json.dump(selected, f, indent=2)
print(f"\nSaved {out_json}")

out_jsonl = "data/commits_35_highsignal.jsonl"
with open(out_jsonl, "w") as f:
    for i, c in enumerate(selected, 1):
        record = {
            "sha": c["sha1"],
            "before_dir": f"data/pairs/commit_{i:03d}/before/src",
            "after_dir": f"data/pairs/commit_{i:03d}/after/src",
            "rminer_types": [r["type"] for r in c.get("refactorings", [])]
        }
        f.write(json.dumps(record) + "\n")
print(f"Saved {out_jsonl}")

print(f"\nNext steps:")
print(f"  1. python scripts/checkout_pairs.py {out_json}")
print(f"  2. cp {out_jsonl} data/commits.jsonl")
print(f"  3. python scripts/compute_srr.py   (re-run SRR on new 20)")
