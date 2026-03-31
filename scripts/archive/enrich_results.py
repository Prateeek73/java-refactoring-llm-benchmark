"""
enrich_results.py — Enrich eval results with SRR data from scan CSVs.

The eval pipeline sometimes can't compute SRR (smells_before=0 due to
DesigniteJava path issues). This script fills in the scan data.

Usage:
  python scripts/enrich_results.py
"""
import csv, json, os

PROJECTS = {
    "commons-lang": {
        "results": "results/commons-lang/results.json",
        "scan": "data/commons-lang_scan.csv",
        "commits": "data/commons-lang_commits.jsonl",
    },
    "commons-io": {
        "results": "results/commons-io/results.json",
        "scan": "data/commons-io_scan.csv",
        "commits": "data/commons-io_commits.jsonl",
    },
    "gson": {
        "results": "results/gson/results.json",
        "scan": "data/gson_scan.csv",
        "commits": "data/gson_commits.jsonl",
    },
}

def load_scan(path):
    """Load scan CSV into dict keyed by sha[:7]."""
    data = {}
    if not os.path.isfile(path):
        return data
    with open(path) as f:
        for row in csv.DictReader(f):
            sha7 = row["sha"][:7]
            data[sha7] = {
                "smells_before": int(row.get("smells_before", 0) or 0),
                "smells_after": int(row.get("smells_after", 0) or 0),
                "srr": float(row["srr"]) if row.get("srr") else None,
            }
    return data


def load_commits(path):
    """Load commits JSONL into dict keyed by sha[:7]."""
    data = {}
    if not os.path.isfile(path):
        return data
    with open(path) as f:
        for line in f:
            c = json.loads(line)
            sha7 = c["sha"][:7]
            data[sha7] = c
    return data


def enrich(results_path, scan_data, commits_data):
    """Enrich results with SRR data from scan."""
    if not os.path.isfile(results_path):
        print(f"  SKIP: {results_path} not found")
        return

    with open(results_path) as f:
        results = json.load(f)

    updated = 0
    for mode_key, items in results.items():
        for item in items:
            sha7 = item.get("sha", "")[:7]
            scan = scan_data.get(sha7, {})
            commit = commits_data.get(sha7, {})

            # Fill in smells_before from scan or commits data
            if item.get("smells_before", 0) == 0:
                sb = scan.get("smells_before") or commit.get("smells_before", 0)
                if sb > 0:
                    item["smells_before"] = sb
                    # Recompute SRR if we have smells_after
                    sa = item.get("smells_after", 0)
                    item["srr"] = (sb - sa) / sb * 100 if sb > 0 else None
                    updated += 1

    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Updated {updated} entries in {results_path}")


def main():
    for name, paths in PROJECTS.items():
        print(f"\n=== {name} ===")
        scan_data = load_scan(paths["scan"])
        commits_data = load_commits(paths["commits"])
        enrich(paths["results"], scan_data, commits_data)

    # Also enrich main Camel results with baseline data
    camel_results = "results/results.json"
    baseline_csv = "data/srr_baseline.csv"
    if os.path.isfile(camel_results) and os.path.isfile(baseline_csv):
        print(f"\n=== Apache Camel ===")
        scan_data = {}
        with open(baseline_csv) as f:
            for row in csv.DictReader(f):
                sha7 = row["sha"][:7]
                scan_data[sha7] = {
                    "smells_before": int(row.get("smells_before", 0) or 0),
                    "smells_after": int(row.get("smells_after", 0) or 0),
                    "srr": float(row["srr"]) if row.get("srr") else None,
                }
        enrich(camel_results, scan_data, {})

    print("\nDone!")


if __name__ == "__main__":
    main()
