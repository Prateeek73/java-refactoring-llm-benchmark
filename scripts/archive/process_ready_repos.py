"""
process_ready_repos.py — Scan, setup pairs, and run eval on repos that have RMiner done.

Runs incrementally — safe to run multiple times, skips completed steps.

Usage:
  python scripts/process_ready_repos.py                    # scan + setup + eval (ollama+lora)
  python scripts/process_ready_repos.py --eval-mode ollama # ollama only
  python scripts/process_ready_repos.py --skip-eval        # scan + setup only
  python scripts/process_ready_repos.py --only activemq    # single repo
"""
import argparse, csv, json, os, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(__file__))
from lib import (STRUCTURAL_TYPES, copy_all_java_src, find_changed_files,
                 run_designite, default_dj_cp)
from run_all_repos import REPOS, load_progress, save_progress, sample_size

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def scan_repo(name, info):
    """Scan structural commits with DesigniteJava."""
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    if os.path.isfile(scan_csv):
        with open(scan_csv) as f:
            rows = list(csv.DictReader(f))
        if rows:
            print(f"  [{name}] scan.csv exists ({len(rows)} rows)")
            return True

    if not os.path.isfile(refs_json):
        return False

    with open(refs_json) as f:
        data = json.load(f)

    structural = [c for c in data.get("commits", [])
                  if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]

    if not structural:
        # Write empty CSV
        with open(scan_csv, "w") as f:
            f.write("sha,smells_before,smells_after,srr,n_structural,structural_types\n")
        print(f"  [{name}] No structural refactorings")
        return True

    needed = sample_size(info["ref_commits"])
    to_scan = structural[:min(len(structural), needed * 3)]
    dj_cp = default_dj_cp()

    print(f"  [{name}] Scanning {len(to_scan)} of {len(structural)} structural commits...")

    with open(scan_csv, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, ["sha", "smells_before", "smells_after",
                                           "srr", "n_structural", "structural_types"])
        writer.writeheader()

    scanned = 0
    for i, c in enumerate(to_scan, 1):
        sha = c["sha1"]
        types = sorted(set(r["type"] for r in c.get("refactorings", [])
                          if r["type"] in STRUCTURAL_TYPES))
        tmpdir = tempfile.mkdtemp(prefix=f"scan_{name}_")
        try:
            before_src = os.path.join(tmpdir, "before", "src")
            after_src = os.path.join(tmpdir, "after", "src")
            os.makedirs(before_src, exist_ok=True)
            os.makedirs(after_src, exist_ok=True)

            subprocess.run(["git", "-C", repo_dir, "checkout", "--", "."],
                           capture_output=True, timeout=30)
            r = subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                               capture_output=True, timeout=60)
            if r.returncode != 0:
                continue
            copy_all_java_src(repo_dir, before_src)

            subprocess.run(["git", "-C", repo_dir, "checkout", sha, "--", "."],
                           capture_output=True, timeout=60)
            copy_all_java_src(repo_dir, after_src)

            modules, changed_classes = find_changed_files(before_src, after_src)
            if not modules:
                sb, sa, srr = 0, 0, None
            else:
                b_out = os.path.join(tmpdir, "b_smells")
                a_out = os.path.join(tmpdir, "a_smells")
                os.makedirs(b_out, exist_ok=True)
                os.makedirs(a_out, exist_ok=True)
                sb = run_designite(before_src, b_out, modules, changed_classes,
                                   dj_cp=dj_cp, timeout=90)
                sa = run_designite(after_src, a_out, modules, changed_classes,
                                   dj_cp=dj_cp, timeout=90)
                srr = (sb - sa) / sb * 100 if sb > 0 else None

            row = {
                "sha": sha,
                "smells_before": sb,
                "smells_after": sa,
                "srr": f"{srr:.1f}" if srr is not None else "",
                "n_structural": len(types),
                "structural_types": ",".join(types),
            }
            with open(scan_csv, "a", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, ["sha", "smells_before", "smells_after",
                                                   "srr", "n_structural", "structural_types"])
                writer.writerow(row)
            scanned += 1
            tag = f"{srr:.1f}%" if srr is not None else "N/A"
            if i % 5 == 0 or i == len(to_scan):
                print(f"    [{name} {i}/{len(to_scan)}] {sha[:7]} SRR={tag}")
        except Exception as e:
            print(f"    [{name} {i}] {sha[:7]} ERROR: {e}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=30)
    print(f"  [{name}] Scanned {scanned} commits")
    return scanned > 0 or True  # even if 0, scan is done


def setup_pairs(name, info):
    """Create before/after pairs and commits.jsonl."""
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")
    pairs_dir = os.path.join(PROJECT_ROOT, "data", f"{name}_pairs")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    if os.path.isfile(commits_jsonl):
        with open(commits_jsonl) as f:
            n = sum(1 for l in f if l.strip())
        if n > 0:
            print(f"  [{name}] commits.jsonl exists ({n} commits)")
            return True

    if not os.path.isfile(scan_csv):
        return False

    scan_rows = []
    with open(scan_csv) as f:
        for row in csv.DictReader(f):
            sb = int(row.get("smells_before", 0) or 0)
            if sb >= 1:
                scan_rows.append(row)
    scan_rows.sort(key=lambda r: int(r.get("smells_before", 0) or 0), reverse=True)

    needed = sample_size(info["ref_commits"])
    selected_shas = {r["sha"] for r in scan_rows[:needed]}

    if not selected_shas:
        with open(scan_csv) as f:
            all_rows = list(csv.DictReader(f))
        selected_shas = {r["sha"] for r in all_rows[:needed]}

    if not selected_shas:
        print(f"  [{name}] No commits to select")
        # Write empty jsonl
        with open(commits_jsonl, "w") as f:
            pass
        return True

    commits_by_sha = {}
    if os.path.isfile(refs_json):
        with open(refs_json) as f:
            data = json.load(f)
        for c in data.get("commits", []):
            commits_by_sha[c["sha1"]] = c

    if os.path.isdir(pairs_dir):
        shutil.rmtree(pairs_dir)

    jsonl_records = []
    for i, sha in enumerate(sorted(selected_shas), 1):
        out = os.path.join(pairs_dir, f"commit_{i:03d}")
        os.makedirs(os.path.join(out, "before"), exist_ok=True)
        os.makedirs(os.path.join(out, "after"), exist_ok=True)

        subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                       capture_output=True, timeout=60)
        copy_all_java_src(repo_dir, os.path.join(out, "before", "src"))

        subprocess.run(["git", "-C", repo_dir, "checkout", sha, "--", "."],
                       capture_output=True, timeout=60)
        copy_all_java_src(repo_dir, os.path.join(out, "after", "src"))

        c = commits_by_sha.get(sha, {})
        rtypes = list(set(r["type"] for r in c.get("refactorings", [])
                         if r["type"] in STRUCTURAL_TYPES))
        if not rtypes:
            rtypes = list(set(r["type"] for r in c.get("refactorings", [])))[:3]
        if not rtypes:
            rtypes = ["Extract Method"]

        scan_row = next((r for r in scan_rows if r["sha"] == sha), {})
        record = {
            "sha": sha,
            "before_dir": os.path.abspath(os.path.join(out, "before")),
            "after_dir": os.path.abspath(os.path.join(out, "after")),
            "rminer_types": rtypes,
            "smells_before": int(scan_row.get("smells_before", 0) or 0),
            "smells_after": int(scan_row.get("smells_after", 0) or 0),
        }
        jsonl_records.append(record)

    subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=30)

    with open(commits_jsonl, "w") as f:
        for r in jsonl_records:
            f.write(json.dumps(r) + "\n")
    print(f"  [{name}] Created {len(jsonl_records)} eval pairs")
    return True


def run_eval(name, mode):
    """Run eval pipeline for one repo."""
    commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")
    output_dir = os.path.join(PROJECT_ROOT, "results", name)

    if not os.path.isfile(commits_jsonl):
        return False

    with open(commits_jsonl) as f:
        n = sum(1 for l in f if l.strip())
    if n == 0:
        print(f"  [{name}] No commits to eval")
        return True

    os.makedirs(output_dir, exist_ok=True)

    results_path = os.path.join(output_dir, "results.json")
    if os.path.isfile(results_path):
        with open(results_path) as f:
            existing = json.load(f)
        if mode in existing and len(existing[mode]) >= n:
            print(f"  [{name}] {mode} already done ({len(existing[mode])} results)")
            return True

    print(f"  [{name}] Running {mode} eval on {n} commits...")
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "run_eval.py"),
             "--commits", commits_jsonl,
             "--mode", mode,
             "--output", output_dir],
            cwd=PROJECT_ROOT,
            timeout=7200,
            capture_output=False,  # Show output in real-time
        )
        return True
    except subprocess.TimeoutExpired:
        print(f"  [{name}] Eval TIMEOUT")
        return True
    except Exception as e:
        print(f"  [{name}] Eval ERROR: {e}")
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval-mode", choices=["ollama", "lora", "both"], default="both")
    p.add_argument("--skip-eval", action="store_true")
    p.add_argument("--only", help="Single repo to process")
    p.add_argument("--skip", default="", help="Comma-separated repos to skip")
    args = p.parse_args()

    skip = set(args.skip.split(",")) if args.skip else set()
    progress = load_progress()

    repos = {args.only: REPOS[args.only]} if args.only else {
        k: v for k, v in REPOS.items() if k not in skip
    }

    for name, info in repos.items():
        if not progress.get(name, {}).get("rminer"):
            continue

        print(f"\n{'='*50}")
        print(f"Processing: {name} (need {sample_size(info['ref_commits'])} commits)")
        print(f"{'='*50}")

        # Scan
        if not progress.get(name, {}).get("scanned"):
            ok = scan_repo(name, info)
            if ok:
                progress.setdefault(name, {})["scanned"] = True
                save_progress(progress)

        # Setup pairs
        if not progress.get(name, {}).get("pairs_ready"):
            if progress.get(name, {}).get("scanned"):
                ok = setup_pairs(name, info)
                if ok:
                    progress.setdefault(name, {})["pairs_ready"] = True
                    save_progress(progress)

        # Eval
        if args.skip_eval:
            continue

        if not progress.get(name, {}).get("pairs_ready"):
            continue

        modes = ["ollama", "lora"] if args.eval_mode == "both" else [args.eval_mode]
        for mode in modes:
            key = f"eval_{mode}"
            if not progress.get(name, {}).get(key):
                ok = run_eval(name, mode)
                if ok:
                    progress.setdefault(name, {})[key] = True
                    save_progress(progress)

    # Print summary
    print(f"\n{'='*60}")
    done_scan = sum(1 for n in repos if progress.get(n, {}).get("scanned"))
    done_pairs = sum(1 for n in repos if progress.get(n, {}).get("pairs_ready"))
    done_ollama = sum(1 for n in repos if progress.get(n, {}).get("eval_ollama"))
    done_lora = sum(1 for n in repos if progress.get(n, {}).get("eval_lora"))
    print(f"Scanned: {done_scan}, Pairs: {done_pairs}, Ollama: {done_ollama}, LoRA: {done_lora}")


if __name__ == "__main__":
    main()
