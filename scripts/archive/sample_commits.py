"""
sample_commits.py — Random sample N commits.

Usage:
  python scripts/sample_commits.py data/pure_commits.json -n 20
  python scripts/sample_commits.py data/pure_commits.json -n 20 -o data/sampled.json
  python scripts/sample_commits.py data/pure_commits.json -n 20 --seed 99
"""
import json, sys, argparse, random

def main():
    p = argparse.ArgumentParser(description="Random sample N commits from JSON.")
    p.add_argument("input", help="Input JSON file (array of commits)")
    p.add_argument("-n", "--count", type=int, required=True, help="Number of commits to sample")
    p.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    p.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = p.parse_args()

    data = json.load(open(args.input))
    if isinstance(data, dict):
        data = data.get("commits", [])

    random.seed(args.seed)
    sample = random.sample(data, min(args.count, len(data)))

    output_str = json.dumps(sample, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_str)
        print(f"Sampled {len(sample)}/{len(data)} commits → {args.output}", file=sys.stderr)
    else:
        print(output_str)
        print(f"Sampled {len(sample)}/{len(data)} commits", file=sys.stderr)

if __name__ == "__main__":
    main()