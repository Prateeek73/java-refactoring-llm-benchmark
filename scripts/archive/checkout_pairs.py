"""
checkout_pairs.py — Checkout before/after Java source for each commit.

Usage:
  python scripts/checkout_pairs.py data/commits_20.json
  python scripts/checkout_pairs.py data/commits_20.json --repo data/camel
  python scripts/checkout_pairs.py data/commits_20.json --output-dir data/pairs
  python scripts/checkout_pairs.py data/commits_20.json --clean
"""
import json, os, subprocess, shutil, sys, argparse
sys.path.insert(0, os.path.dirname(__file__))
from lib import copy_all_java_src

def main():
    p = argparse.ArgumentParser(description="Checkout before/after Java source pairs.")
    p.add_argument("input", help="Input JSON file (array of commit objects)")
    p.add_argument("--repo", default="data/camel", help="Git repo path (default: data/camel)")
    p.add_argument("--output-dir", default="data/pairs", help="Output dir (default: data/pairs)")
    p.add_argument("--clean", action="store_true", help="Remove output dir before starting")
    args = p.parse_args()

    commits = json.load(open(args.input))
    if isinstance(commits, dict):
        commits = commits.get("commits", [])

    if args.clean and os.path.isdir(args.output_dir):
        shutil.rmtree(args.output_dir)

    total = len(commits)
    for i, c in enumerate(commits, 1):
        sha = c["sha1"]
        out = f"{args.output_dir}/commit_{i:03d}"
        os.makedirs(f"{out}/before", exist_ok=True)
        os.makedirs(f"{out}/after",  exist_ok=True)

        subprocess.run(
            ["git", "-C", args.repo, "checkout", sha + "~1", "--", "."],
            capture_output=True
        )
        if not copy_all_java_src(args.repo, f"{out}/before/src"):
            print(f"  [WARN] no Java src found for before {sha[:7]}")

        subprocess.run(
            ["git", "-C", args.repo, "checkout", sha, "--", "."],
            capture_output=True
        )
        if not copy_all_java_src(args.repo, f"{out}/after/src"):
            print(f"  [WARN] no Java src found for after {sha[:7]}")

        print(f"[{i:2d}/{total}] {sha[:7]} done")

    print("Checkout complete.")

if __name__ == "__main__":
    main()