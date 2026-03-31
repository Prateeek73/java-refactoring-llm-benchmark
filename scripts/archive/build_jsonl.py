"""
build_jsonl.py — Build commits.jsonl manifest from JSON commit list.

Usage:
  python scripts/build_jsonl.py data/commits_20.json
  python scripts/build_jsonl.py data/commits_20.json -o data/commits.jsonl
  python scripts/build_jsonl.py data/commits_20.json --pairs-dir data/pairs
"""
import json, sys, os, argparse
sys.path.insert(0, os.path.dirname(__file__))
from lib import write_jsonl

def main():
    p = argparse.ArgumentParser(description="Build JSONL manifest from commit list.")
    p.add_argument("input", help="Input JSON file (array of commit objects)")
    p.add_argument("-o", "--output", default="data/commits.jsonl",
                   help="Output JSONL path (default: data/commits.jsonl)")
    p.add_argument("--pairs-dir", default="data/pairs",
                   help="Pairs directory prefix (default: data/pairs)")
    args = p.parse_args()

    commits = json.load(open(args.input))
    if isinstance(commits, dict):
        commits = commits.get("commits", [])

    n = write_jsonl(commits, args.output, pairs_dir=args.pairs_dir)
    print(f"Written {n} records to {args.output}")

if __name__ == "__main__":
    main()