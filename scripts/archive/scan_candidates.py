"""
scan_candidates.py — Batch-scan commits for SRR and pick the best N.

Scans structural refactoring candidates, computes SRR for each,
saves progress incrementally (safe to Ctrl+C and resume).

Usage:
  # Scan 100 structural candidates
  python scripts/scan_candidates.py --input data/pure_commits.json --limit 100

  # Resume a previous scan (skips already-scanned SHAs)
  python scripts/scan_candidates.py --input data/pure_commits.json --limit 50

  # Start fresh (delete old scan results)
  python scripts/scan_candidates.py --input data/pure_commits.json --limit 100 --fresh

  # After scanning, pick best 20 with smells_before >= 30
  python scripts/scan_candidates.py --pick 20 --min-smells 30

  # Scan + pick in one go
  python scripts/scan_candidates.py --input data/pure_commits.json --limit 100 --pick 20

  # Custom repo and DJ classpath
  python scripts/scan_candidates.py --input data/pure_commits.json --repo data/camel --dj-cp /path/to/cp
"""
import json, os, subprocess, csv, shutil, tempfile, sys, argparse, statistics
sys.path.insert(0, os.path.dirname(__file__))
from lib import (STRUCTURAL_TYPES, count_smells, find_changed_files,
                 run_designite, copy_all_java_src, default_dj_cp, write_jsonl)


def checkout_and_compute(sha, tmpdir, repo, dj_cp, timeout):
    """Checkout before/after for one commit, compute SRR, cleanup."""
    before_src = os.path.join(tmpdir, "before", "src")
    after_src  = os.path.join(tmpdir, "after", "src")
    os.makedirs(before_src, exist_ok=True)
    os.makedirs(after_src, exist_ok=True)

    subprocess.run(["git", "-C", repo, "checkout", "--", "."],
                   capture_output=True, timeout=30)

    r = subprocess.run(["git", "-C", repo, "checkout", sha + "~1", "--", "."],
                       capture_output=True, timeout=30)
    if r.returncode != 0:
        return None, None, None
    copy_all_java_src(repo, before_src)

    subprocess.run(["git", "-C", repo, "checkout", sha, "--", "."],
                   capture_output=True, timeout=30)
    copy_all_java_src(repo, after_src)

    modules, changed_classes = find_changed_files(before_src, after_src)
    if not modules:
        return 0, 0, None

    b_out = os.path.join(tmpdir, "b_smells")
    a_out = os.path.join(tmpdir, "a_smells")
    os.makedirs(b_out, exist_ok=True)
    os.makedirs(a_out, exist_ok=True)

    sb = run_designite(before_src, b_out, modules, changed_classes,
                       dj_cp=dj_cp, timeout=timeout)
    sa = run_designite(after_src, a_out, modules, changed_classes,
                       dj_cp=dj_cp, timeout=timeout)
    srr = (sb - sa) / sb * 100 if sb > 0 else None
    return sb, sa, srr


def do_scan(args):
    """Scan candidates and save results to CSV."""
    dj_cp = args.dj_cp or default_dj_cp()

    commits = json.load(open(args.input))
    if isinstance(commits, dict):
        commits = commits.get("commits", [])
    print(f"Loaded {len(commits)} commits from {args.input}")

    def structural_score(c):
        types = [r["type"] for r in c.get("refactorings", [])
                 if r["type"] in STRUCTURAL_TYPES]
        return len(types), len(set(types))

    structural = [(c, structural_score(c)) for c in commits
                  if structural_score(c)[0] > 0]
    structural.sort(key=lambda x: (x[1][1], x[1][0]), reverse=True)
    print(f"With structural refactorings: {len(structural)}")

    # Load already-scanned
    scanned = {}
    if not args.fresh and os.path.exists(args.scan_csv):
        with open(args.scan_csv) as f:
            for row in csv.DictReader(f):
                scanned[row["sha"]] = row
        print(f"Already scanned: {len(scanned)} (will skip)")
    elif args.fresh and os.path.exists(args.scan_csv):
        os.remove(args.scan_csv)

    candidates = [(c, sc) for c, sc in structural
                  if c["sha1"] not in scanned][:args.limit]
    total = len(candidates)
    print(f"Scanning {total} new candidates...\n")

    write_header = not os.path.exists(args.scan_csv)
    csvfile = open(args.scan_csv, "a", newline="")
    writer = csv.DictWriter(csvfile, ["sha", "smells_before", "smells_after",
                                       "srr", "n_structural", "structural_types"])
    if write_header:
        writer.writeheader()

    positive_count = sum(1 for s in scanned.values()
                         if s.get("srr") and s["srr"] != "" and float(s["srr"]) > 0)

    for i, (c, (n_st, n_uniq)) in enumerate(candidates, 1):
        sha = c["sha1"]
        sha7 = sha[:7]
        types_str = ",".join(sorted(set(
            r["type"] for r in c.get("refactorings", [])
            if r["type"] in STRUCTURAL_TYPES
        )))

        tmpdir = tempfile.mkdtemp(prefix="scan_")
        try:
            sb, sa, srr = checkout_and_compute(sha, tmpdir, args.repo,
                                                dj_cp, args.timeout)
        except Exception as e:
            print(f"[{i}/{total}] {sha7}  ERROR: {e}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            continue
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        row = {
            "sha": sha,
            "smells_before": sb if sb is not None else 0,
            "smells_after": sa if sa is not None else 0,
            "srr": f"{srr:.1f}" if srr is not None else "",
            "n_structural": n_st,
            "structural_types": types_str,
        }
        writer.writerow(row)
        csvfile.flush()

        tag = f"{srr:.1f}%" if srr is not None else "N/A"
        if srr is not None and srr > 0:
            positive_count += 1

        marker = " ***" if srr is not None and srr > 5 else ""
        print(f"[{i}/{total}] {sha7}  before={sb}  after={sa}  SRR={tag}  "
              f"({n_st} structural: {types_str}){marker}")

        if args.early_stop and positive_count >= args.early_stop:
            print(f"\nFound {positive_count} positive-SRR commits — stopping early")
            break

    csvfile.close()
    print(f"\nScan results saved to {args.scan_csv}")


def do_pick(args):
    """Pick best N commits from scan results."""
    if not os.path.exists(args.scan_csv):
        sys.exit(f"ERROR: {args.scan_csv} not found. Run scan first.")

    results = []
    with open(args.scan_csv) as f:
        for row in csv.DictReader(f):
            if row["srr"] and row["smells_before"]:
                results.append({
                    "sha": row["sha"],
                    "smells_before": int(row["smells_before"]),
                    "smells_after": int(row["smells_after"]),
                    "srr": float(row["srr"]),
                })

    print(f"Loaded {len(results)} scanned commits with valid SRR")

    rich = [r for r in results if r["smells_before"] >= args.min_smells]
    rich.sort(key=lambda x: x["srr"], reverse=True)
    print(f"With smells_before >= {args.min_smells}: {len(rich)}")

    if len(rich) < args.pick:
        rich = [r for r in results if r["smells_before"] >= 10]
        rich.sort(key=lambda x: x["srr"], reverse=True)
        print(f"Relaxed to smells_before >= 10: {len(rich)}")

    selected_results = rich[:args.pick]

    # Look up full commit objects
    if not os.path.exists(args.input):
        sys.exit(f"ERROR: {args.input} not found (needed to look up full commit data)")

    all_commits = json.load(open(args.input))
    if isinstance(all_commits, dict):
        all_commits = all_commits.get("commits", [])
    commits_by_sha = {c["sha1"]: c for c in all_commits}

    selected_commits = []
    for r in selected_results:
        c = commits_by_sha.get(r["sha"])
        if c:
            selected_commits.append(c)

    selected_commits.sort(key=lambda c: c["sha1"])
    srr_lookup = {r["sha"]: r for r in selected_results}

    # Print summary
    print(f"\n{'='*80}")
    print(f"Final {len(selected_commits)} commits:")
    print(f"{'#':>3}  {'SHA':7}  {'before':>7}  {'after':>7}  {'SRR':>8}  Types")
    print(f"{'-'*80}")
    for i, c in enumerate(selected_commits, 1):
        r = srr_lookup[c["sha1"]]
        types = sorted(set(ref["type"] for ref in c.get("refactorings", [])))
        t_str = ", ".join(types[:3])
        if len(types) > 3:
            t_str += f" +{len(types)-3}"
        print(f"{i:3d}  {c['sha1'][:7]}  {r['smells_before']:7d}  {r['smells_after']:7d}  "
              f"{r['srr']:7.1f}%  {t_str}")

    valid_srr = [srr_lookup[c["sha1"]]["srr"] for c in selected_commits]
    if valid_srr:
        med = statistics.median(valid_srr)
        mean = statistics.mean(valid_srr)
        pos = sum(1 for s in valid_srr if s > 0)
        print(f"\nMedian SRR: {med:.1f}%   Mean: {mean:.1f}%   Positive: {pos}/{len(valid_srr)}")
    print(f"{'='*80}")

    # Save
    out_json = args.output_json
    with open(out_json, "w") as f:
        json.dump(selected_commits, f, indent=2)
    print(f"\nSaved {out_json}")

    out_jsonl = args.output_jsonl
    n = write_jsonl(selected_commits, out_jsonl, pairs_dir=args.pairs_dir)
    print(f"Saved {out_jsonl} ({n} records)")

    print(f"\nNext steps:")
    print(f"  rm -rf data/pairs data/smells")
    print(f"  python scripts/checkout_pairs.py {out_json}")
    print(f"  cp {out_jsonl} data/commits.jsonl")
    print(f"  python scripts/compute_srr.py")


def main():
    p = argparse.ArgumentParser(
        description="Scan candidates for SRR and/or pick best N.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Scan options
    p.add_argument("--input", default="data/pure_commits.json",
                   help="Input JSON (default: data/pure_commits.json)")
    p.add_argument("--repo", default="data/camel",
                   help="Git repo path (default: data/camel)")
    p.add_argument("--limit", type=int, default=0,
                   help="Max candidates to scan (0 = skip scan)")
    p.add_argument("--scan-csv", default="data/scan_results.csv",
                   help="Scan results CSV (default: data/scan_results.csv)")
    p.add_argument("--fresh", action="store_true",
                   help="Delete old scan results and start fresh")
    p.add_argument("--dj-cp", default=None,
                   help="DesigniteJava classpath (default: env or auto)")
    p.add_argument("--timeout", type=int, default=120,
                   help="DesigniteJava timeout per run in seconds (default: 120)")
    p.add_argument("--early-stop", type=int, default=30,
                   help="Stop scanning after N positive-SRR commits (default: 30)")

    # Pick options
    p.add_argument("--pick", type=int, default=0,
                   help="Pick best N commits from scan results (0 = skip pick)")
    p.add_argument("--min-smells", type=int, default=30,
                   help="Min smells_before for pick selection (default: 30)")
    p.add_argument("--output-json", default="data/commits_final.json",
                   help="Output JSON for picked commits")
    p.add_argument("--output-jsonl", default="data/commits_final.jsonl",
                   help="Output JSONL for picked commits")
    p.add_argument("--pairs-dir", default="data/pairs",
                   help="Pairs dir prefix for JSONL (default: data/pairs)")

    args = p.parse_args()

    if args.limit == 0 and args.pick == 0:
        p.error("Specify --limit N to scan, --pick N to select, or both.")

    if args.limit > 0:
        do_scan(args)

    if args.pick > 0:
        do_pick(args)

if __name__ == "__main__":
    main()
