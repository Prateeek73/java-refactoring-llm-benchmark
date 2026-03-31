"""
setup_repo_eval.py — Set up eval dataset for a new repo from scan results.

Selects commits with smells, checks out before/after pairs, builds commits.jsonl.

Usage:
  python scripts/setup_repo_eval.py --repo commons-lang --limit 15
  python scripts/setup_repo_eval.py --repo gson --limit 15
  python scripts/setup_repo_eval.py --repo commons-io --limit 15
"""
import json, os, sys, csv, subprocess, shutil, argparse
sys.path.insert(0, os.path.dirname(__file__))
from lib import copy_all_java_src, STRUCTURAL_TYPES

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, help="Repo name (e.g. commons-lang)")
    p.add_argument("--limit", type=int, default=15, help="Max commits to select")
    p.add_argument("--min-smells", type=int, default=1, help="Min smells_before")
    args = p.parse_args()

    name = args.repo
    git_dir = f"data/{name}"
    scan_csv = f"data/{name}_scan.csv"
    structural_json = f"data/{name}_structural.json"
    refs_json = f"data/{name}_refs.json"
    pairs_dir = f"data/{name}_pairs"
    commits_jsonl = f"data/{name}_commits.jsonl"

    # Load scan results
    scan_rows = []
    with open(scan_csv) as f:
        for row in csv.DictReader(f):
            sb = int(row.get("smells_before", 0) or 0)
            if sb >= args.min_smells:
                scan_rows.append(row)
    scan_rows.sort(key=lambda r: int(r.get("smells_before", 0) or 0), reverse=True)
    selected_shas = {r["sha"] for r in scan_rows[:args.limit]}
    print(f"Selected {len(selected_shas)} commits with smells >= {args.min_smells}")

    # Load full commit data from RMiner output
    data = json.load(open(refs_json))
    commits_list = data.get("commits", [])
    commits_by_sha = {c["sha1"]: c for c in commits_list}

    selected_commits = []
    for sha in selected_shas:
        if sha in commits_by_sha:
            selected_commits.append(commits_by_sha[sha])

    if not selected_commits:
        sys.exit(f"No matching commits found in {refs_json}")

    print(f"Matched {len(selected_commits)} commits from RMiner data")

    # Checkout pairs
    if os.path.isdir(pairs_dir):
        shutil.rmtree(pairs_dir)

    jsonl_records = []
    for i, c in enumerate(selected_commits, 1):
        sha = c["sha1"]
        out = f"{pairs_dir}/commit_{i:03d}"
        os.makedirs(f"{out}/before", exist_ok=True)
        os.makedirs(f"{out}/after", exist_ok=True)

        subprocess.run(["git", "-C", git_dir, "checkout", sha + "~1", "--", "."],
                       capture_output=True, timeout=30)
        copy_all_java_src(git_dir, f"{out}/before/src")

        subprocess.run(["git", "-C", git_dir, "checkout", sha, "--", "."],
                       capture_output=True, timeout=30)
        copy_all_java_src(git_dir, f"{out}/after/src")

        # Get refactoring types for this commit
        rtypes = list(set(r["type"] for r in c.get("refactorings", [])
                         if r["type"] in STRUCTURAL_TYPES))
        if not rtypes:
            rtypes = list(set(r["type"] for r in c.get("refactorings", [])))[:3]

        # Get scan data
        scan_row = next((r for r in scan_rows if r["sha"] == sha), {})

        record = {
            "sha": sha,
            "before_dir": os.path.abspath(f"{out}/before"),
            "after_dir": os.path.abspath(f"{out}/after"),
            "rminer_types": rtypes,
            "smells_before": int(scan_row.get("smells_before", 0) or 0),
            "smells_after": int(scan_row.get("smells_after", 0) or 0),
        }
        jsonl_records.append(record)
        print(f"[{i:2d}/{len(selected_commits)}] {sha[:7]} smells={record['smells_before']} types={','.join(rtypes[:2])}")

    # Reset repo to HEAD
    subprocess.run(["git", "-C", git_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=30)

    # Write commits.jsonl
    with open(commits_jsonl, "w") as f:
        for r in jsonl_records:
            f.write(json.dumps(r) + "\n")

    print(f"\nSaved {len(jsonl_records)} records to {commits_jsonl}")
    print(f"Pairs in {pairs_dir}")
    print(f"\nRun eval: python scripts/run_eval.py --mode ollama --commits {commits_jsonl} --results results/{name}_results.json")

if __name__ == "__main__":
    main()
