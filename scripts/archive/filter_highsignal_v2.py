"""
filter_highsignal_v2.py
-----------------------
Second-pass filter: from pure_commits.json, pick 60 candidates with
the most structural refactorings, then sample 20. Uses refactoring
count as proxy for smell richness (more structural refactorings =
more code touched = more detectable smells).

Usage:  python scripts/filter_highsignal_v2.py
"""
import json, os, random, sys

STRUCTURAL_TYPES = {
    "Extract Method", "Move Method", "Pull Up Method",
    "Push Down Method", "Extract Class", "Move Attribute",
    "Extract And Move Method", "Extract Superclass",
    "Extract Interface", "Inline Method", "Move Class",
    "Pull Up Attribute", "Push Down Attribute",
}

TARGET_N = 20
SEED = 42

# ── 1. Load pure commits ──────────────────────────────────────────
pure_path = "data/pure_commits.json"
if not os.path.exists(pure_path):
    sys.exit(f"ERROR: {pure_path} not found")

commits = json.load(open(pure_path))
print(f"Loaded {len(commits)} pure refactoring commits")

# ── 2. Score each commit by structural refactoring richness ───────
def get_structural(c):
    return [r["type"] for r in c.get("refactorings", [])
            if r["type"] in STRUCTURAL_TYPES]

def get_files_touched(c):
    """Count unique files mentioned in refactoring descriptions."""
    files = set()
    for r in c.get("refactorings", []):
        desc = r.get("description", "")
        # Extract .java file references from description
        for word in desc.split():
            if ".java" in word:
                files.add(word)
        # Also count leftSideLocations + rightSideLocations
        for loc in r.get("leftSideLocations", []):
            fp = loc.get("filePath", "")
            if fp.endswith(".java"):
                files.add(fp)
        for loc in r.get("rightSideLocations", []):
            fp = loc.get("filePath", "")
            if fp.endswith(".java"):
                files.add(fp)
    return len(files)

scored = []
for c in commits:
    st = get_structural(c)
    if not st:
        continue
    n_structural = len(st)
    n_unique_types = len(set(st))
    n_files = get_files_touched(c)
    n_total_refs = len(c.get("refactorings", []))

    # Score: heavily weight structural count and file coverage
    s = (n_structural * 5) + (n_unique_types * 10) + (n_files * 2) + n_total_refs
    scored.append((s, c, n_structural, n_unique_types, n_files))

scored.sort(key=lambda x: x[0], reverse=True)
print(f"Commits with structural refactorings: {len(scored)}")

# ── 3. Exclude the 20 we already tried (optional) ────────────────
# Load current highsignal to avoid re-picking poor ones
already_tried = set()
if os.path.exists("data/commits_20_highsignal.json"):
    tried = json.load(open("data/commits_20_highsignal.json"))
    already_tried = {c["sha1"] for c in tried}
    print(f"Excluding {len(already_tried)} already-tried commits")

scored = [(s, c, ns, nu, nf) for s, c, ns, nu, nf in scored
          if c["sha1"] not in already_tried]

# ── 4. Take top 60, sample 20 ────────────────────────────────────
top = scored[:60]
random.seed(SEED)
if len(top) <= TARGET_N:
    selected_items = top
else:
    selected_items = random.sample(top, TARGET_N)

selected_items.sort(key=lambda x: x[1]["sha1"])

# ── 5. Print summary ─────────────────────────────────────────────
print(f"\n{'='*90}")
print(f"Selected {len(selected_items)} high-signal commits (v2 — by structural richness):")
print(f"{'#':>3}  {'SHA':7}  {'Score':>6}  {'Struct':>6}  {'Types':>5}  {'Files':>5}  Top structural types")
print(f"{'-'*90}")
for i, (s, c, ns, nu, nf) in enumerate(selected_items, 1):
    sha7 = c["sha1"][:7]
    top_types = ", ".join(sorted(set(get_structural(c)))[:3])
    print(f"{i:3d}  {sha7}  {s:6.0f}  {ns:6d}  {nu:5d}  {nf:5d}  {top_types}")
print(f"{'='*90}")

# ── 6. Save ───────────────────────────────────────────────────────
selected = [item[1] for item in selected_items]

out_json = "data/commits_20_v2.json"
with open(out_json, "w") as f:
    json.dump(selected, f, indent=2)
print(f"\nSaved {out_json}")

out_jsonl = "data/commits_20_v2.jsonl"
with open(out_jsonl, "w") as f:
    for i, c in enumerate(selected, 1):
        record = {
            "sha": c["sha1"],
            "before_dir": f"data/pairs/commit_{i:03d}/before/src",
            "after_dir":  f"data/pairs/commit_{i:03d}/after/src",
            "rminer_types": [r["type"] for r in c.get("refactorings", [])]
        }
        f.write(json.dumps(record) + "\n")
print(f"Saved {out_jsonl}")

print(f"\nNext steps:")
print(f"  1. rm -rf data/pairs   # clear old checkouts")
print(f"  2. python scripts/checkout_pairs.py {out_json}")
print(f"  3. cp {out_jsonl} data/commits.jsonl")
print(f"  4. python scripts/compute_srr.py   # expect higher median SRR")
