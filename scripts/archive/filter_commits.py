"""
filter_commits.py — Flexible commit filter with CLI options.

Usage examples:
  python scripts/filter_commits.py data/camel_refs_fixed.json
  python scripts/filter_commits.py data/camel_refs_fixed.json --types structural
  python scripts/filter_commits.py data/camel_refs_fixed.json --types "Extract Method,Move Method"
  python scripts/filter_commits.py data/camel_refs_fixed.json --min-refs 3
  python scripts/filter_commits.py data/camel_refs_fixed.json --min-types 2
  python scripts/filter_commits.py data/camel_refs_fixed.json --min-files 5
  python scripts/filter_commits.py data/camel_refs_fixed.json --types structural --min-refs 3 --min-files 4
  python scripts/filter_commits.py data/camel_refs_fixed.json --types structural -o data/structural.json
  python scripts/filter_commits.py data/camel_refs_fixed.json --types structural --sample 20
  python scripts/filter_commits.py data/camel_refs_fixed.json --exclude "Rename Method,Rename Variable"
  python scripts/filter_commits.py data/camel_refs_fixed.json --list-types
"""
import json, sys, os, argparse, random
sys.path.insert(0, os.path.dirname(__file__))
from lib import STRUCTURAL_TYPES, RENAME_TYPES, TYPE_GROUPS

def get_types(c):
    return [r["type"] for r in c.get("refactorings", [])]

def get_files(c):
    files = set()
    for r in c.get("refactorings", []):
        for side in ("leftSideLocations", "rightSideLocations"):
            for loc in r.get(side, []):
                fp = loc.get("filePath", "")
                if fp.endswith(".java"):
                    files.add(fp)
    return files

def matches_filter(c, args):
    refs = c.get("refactorings", [])
    if not refs:
        return False

    types = get_types(c)

    # --types filter
    if args.types:
        if args.types in TYPE_GROUPS:
            allowed = TYPE_GROUPS[args.types]
            if allowed is not None and not any(t in allowed for t in types):
                return False
        else:
            allowed = {t.strip() for t in args.types.split(",")}
            if not any(t in allowed for t in types):
                return False

    # --exclude filter
    if args.exclude:
        excluded = {t.strip() for t in args.exclude.split(",")}
        types_remaining = [t for t in types if t not in excluded]
        if not types_remaining:
            return False

    # --min-refs filter
    if args.min_refs and len(refs) < args.min_refs:
        return False

    # --min-types filter
    if args.min_types and len(set(types)) < args.min_types:
        return False

    # --min-files filter
    if args.min_files:
        if len(get_files(c)) < args.min_files:
            return False

    return True

def main():
    parser = argparse.ArgumentParser(
        description="Filter commits from RMiner JSON output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Input JSON file (RMiner output or commit list)")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument("--types", default=None,
                        help="'structural', 'rename', 'all', or comma-separated type names")
    parser.add_argument("--exclude", default=None,
                        help="Comma-separated types to exclude")
    parser.add_argument("--min-refs", type=int, default=None,
                        help="Min refactorings per commit")
    parser.add_argument("--min-types", type=int, default=None,
                        help="Min distinct refactoring types per commit")
    parser.add_argument("--min-files", type=int, default=None,
                        help="Min Java files touched")
    parser.add_argument("--sample", type=int, default=None,
                        help="Random sample N from results")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for --sample (default: 42)")
    parser.add_argument("--list-types", action="store_true",
                        help="Just list all refactoring types found, then exit")

    args = parser.parse_args()

    # Load input — handles both RMiner format {"commits":[...]} and plain list [...]
    data = json.load(open(args.input))
    if isinstance(data, dict):
        commits = data.get("commits", [])
    elif isinstance(data, list):
        commits = data
    else:
        sys.exit("ERROR: input must be a JSON array or object with 'commits' key")

    # --list-types mode
    if args.list_types:
        type_counts = {}
        for c in commits:
            for t in get_types(c):
                type_counts[t] = type_counts.get(t, 0) + 1
        for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            group = ("structural" if t in STRUCTURAL_TYPES
                     else "rename" if t in RENAME_TYPES
                     else "other")
            print(f"  {n:5d}  [{group:10s}]  {t}")
        print(f"\nTotal: {sum(type_counts.values())} refactorings across {len(commits)} commits")
        return

    # Filter
    filtered = [c for c in commits if matches_filter(c, args)]

    # Sample
    if args.sample and len(filtered) > args.sample:
        random.seed(args.seed)
        filtered = random.sample(filtered, args.sample)

    # Output
    output_str = json.dumps(filtered, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_str)
        print(f"Saved {len(filtered)} commits to {args.output}", file=sys.stderr)
    else:
        print(output_str)

    # Summary to stderr
    print(f"Filtered: {len(filtered)}/{len(commits)} commits", file=sys.stderr)
    if filtered:
        all_types = {}
        for c in filtered:
            for t in get_types(c):
                all_types[t] = all_types.get(t, 0) + 1
        top3 = sorted(all_types.items(), key=lambda x: -x[1])[:3]
        print(f"Top types: {', '.join(f'{t}({n})' for t,n in top3)}", file=sys.stderr)

if __name__ == "__main__":
    main()