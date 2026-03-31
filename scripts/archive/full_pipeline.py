"""
full_pipeline.py — Complete pipeline: clone, RMiner, scan, pairs, eval for each repo.

Uses proper shallow clones (with blobs) so all phases work correctly.
Each repo is processed end-to-end before moving to the next.

Usage:
  python scripts/full_pipeline.py                     # all repos
  python scripts/full_pipeline.py --only activemq     # single repo
  python scripts/full_pipeline.py --eval-mode ollama  # ollama only
"""
import argparse, csv, json, os, shutil, subprocess, sys, tempfile, time, statistics
sys.path.insert(0, os.path.dirname(__file__))
from lib import (STRUCTURAL_TYPES, copy_all_java_src, find_changed_files,
                 run_designite, default_dj_cp)
from run_all_repos import REPOS, load_progress, save_progress, sample_size

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RMINER = os.path.join(PROJECT_ROOT, "tools", "RefactoringMiner-3.0.10", "bin", "RefactoringMiner")


def compute_depth(info):
    """Compute clone depth to find enough structural refactorings."""
    needed = sample_size(info["ref_commits"])
    ref_rate = info["ref_commits"] / max(info["total_commits"], 1)
    depth = int(needed * 3 / max(ref_rate * 0.4, 0.005))
    return max(200, min(depth, 3000))


def ensure_repo(name, info):
    """Ensure repo is properly cloned with blobs."""
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    # Check if this is a full/shallow clone (has blobs) vs blobless
    needs_reclone = False
    config_file = os.path.join(repo_dir, ".git", "config")
    if os.path.isfile(config_file):
        with open(config_file) as f:
            content = f.read()
        if "partialclonefilter" in content:
            needs_reclone = True  # blobless

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        needs_reclone = True  # not cloned

    # Also check if refs.json SHAs actually exist in this clone
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    if not needs_reclone and os.path.isfile(refs_json):
        try:
            with open(refs_json) as f:
                data = json.load(f)
            commits = data.get("commits", [])
            if commits:
                test_sha = commits[0]["sha1"]
                r = subprocess.run(
                    ["git", "-C", repo_dir, "cat-file", "-t", test_sha],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode != 0:
                    print(f"  [{name}] refs.json SHAs don't match clone - will re-run RMiner")
                    # Delete stale data files
                    for suffix in ["_refs.json", "_structural.json", "_scan.csv", "_commits.jsonl"]:
                        old = os.path.join(PROJECT_ROOT, "data", f"{name}{suffix}")
                        if os.path.isfile(old):
                            os.remove(old)
                    pairs_dir = os.path.join(PROJECT_ROOT, "data", f"{name}_pairs")
                    if os.path.isdir(pairs_dir):
                        shutil.rmtree(pairs_dir, ignore_errors=True)
        except (json.JSONDecodeError, KeyError, IndexError):
            # Corrupted refs.json - delete it
            os.remove(refs_json)

    if not needs_reclone:
        print(f"  [{name}] Repo OK")
        return True

    # Need to re-clone with proper shallow clone
    depth = compute_depth(info)
    url = f"https://github.com/{info['gh']}.git"

    if os.path.isdir(repo_dir):
        print(f"  [{name}] Re-cloning with --depth={depth}...")
        subprocess.run(["chmod", "-R", "u+w", repo_dir], capture_output=True, timeout=120)
        shutil.rmtree(repo_dir, ignore_errors=True)
        if os.path.isdir(repo_dir):
            subprocess.run(["rm", "-rf", repo_dir], capture_output=True, timeout=120)
        # Delete old data files
        for suffix in ["_refs.json", "_structural.json", "_scan.csv", "_commits.jsonl"]:
            old = os.path.join(PROJECT_ROOT, "data", f"{name}{suffix}")
            if os.path.isfile(old):
                os.remove(old)
        pairs_dir = os.path.join(PROJECT_ROOT, "data", f"{name}_pairs")
        if os.path.isdir(pairs_dir):
            shutil.rmtree(pairs_dir, ignore_errors=True)
    else:
        print(f"  [{name}] Cloning with --depth={depth}...")

    r = subprocess.run(
        ["git", "clone", f"--depth={depth}", "--single-branch", url, repo_dir],
        capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        print(f"  [{name}] Clone FAILED: {r.stderr[:200]}")
        return False
    print(f"  [{name}] Cloned OK")
    return True


def run_rminer_phase(name, info):
    """Run RMiner on repo."""
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")

    # Check existing refs
    if os.path.isfile(refs_json):
        try:
            with open(refs_json) as f:
                data = json.load(f)
            structural = [c for c in data.get("commits", [])
                          if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
            if structural:
                print(f"  [{name}] RMiner: {len(structural)} structural commits (cached)")
                return True
        except json.JSONDecodeError:
            print(f"  [{name}] Corrupted refs.json, re-running RMiner")
            os.remove(refs_json)

    # Get commit range
    r = subprocess.run(["git", "-C", repo_dir, "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=10)
    end_sha = r.stdout.strip()

    r = subprocess.run(["git", "-C", repo_dir, "rev-list", "--max-parents=0", "HEAD"],
                       capture_output=True, text=True, timeout=10)
    start_sha = r.stdout.strip().split("\n")[0]

    r = subprocess.run(["git", "-C", repo_dir, "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True, timeout=10)
    count = r.stdout.strip()

    print(f"  [{name}] Running RMiner on {count} commits...")
    t0 = time.time()
    try:
        r = subprocess.run(
            [RMINER, "-bc", repo_dir, start_sha, end_sha, "-json", refs_json],
            capture_output=True, text=True, timeout=3600
        )
        elapsed = time.time() - t0
        for line in (r.stdout + r.stderr).split("\n"):
            if "Total count" in line or "Analyzed" in line:
                print(f"  [{name}] {line.strip()} ({elapsed:.0f}s)")
    except subprocess.TimeoutExpired:
        print(f"  [{name}] RMiner TIMEOUT after {time.time()-t0:.0f}s")

    if os.path.isfile(refs_json):
        try:
            with open(refs_json) as f:
                data = json.load(f)
            structural = [c for c in data.get("commits", [])
                          if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
            print(f"  [{name}] Found {len(structural)} structural commits")
            return len(structural) > 0
        except json.JSONDecodeError:
            print(f"  [{name}] RMiner output corrupted, deleting")
            os.remove(refs_json)
            return False
    return False


def scan_phase(name, info):
    """Scan structural commits with DesigniteJava."""
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    # Check cached
    if os.path.isfile(scan_csv):
        with open(scan_csv) as f:
            rows = [r for r in csv.DictReader(f)]
        has_smells = [r for r in rows if int(r.get("smells_before", 0) or 0) > 0]
        if has_smells:
            print(f"  [{name}] Scan: {len(has_smells)} commits with smells (cached)")
            return True
        elif rows:
            # Had scan but no smells - might be from blobless clone. Re-scan.
            print(f"  [{name}] Re-scanning (previous scan had 0 smells)...")
            os.remove(scan_csv)

    with open(refs_json) as f:
        data = json.load(f)
    structural = [c for c in data.get("commits", [])
                  if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]

    needed = sample_size(info["ref_commits"])
    to_scan = structural[:min(len(structural), needed * 3)]
    dj_cp = default_dj_cp()

    print(f"  [{name}] Scanning {len(to_scan)} commits with DesigniteJava...")

    with open(scan_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, ["sha", "smells_before", "smells_after",
                                     "srr", "n_structural", "structural_types"])
        writer.writeheader()

    success = 0
    for i, c in enumerate(to_scan, 1):
        sha = c["sha1"]
        types = sorted(set(r["type"] for r in c.get("refactorings", [])
                          if r["type"] in STRUCTURAL_TYPES))
        tmpdir = tempfile.mkdtemp(prefix=f"scan_")
        try:
            before_src = os.path.join(tmpdir, "before", "src")
            after_src = os.path.join(tmpdir, "after", "src")
            os.makedirs(before_src, exist_ok=True)
            os.makedirs(after_src, exist_ok=True)

            # Reset first
            subprocess.run(["git", "-C", repo_dir, "checkout", "--", "."],
                           capture_output=True, timeout=60)

            # Before
            r = subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                continue
            copy_all_java_src(repo_dir, before_src)

            # After
            subprocess.run(["git", "-C", repo_dir, "checkout", sha, "--", "."],
                           capture_output=True, timeout=60)
            copy_all_java_src(repo_dir, after_src)

            modules, changed = find_changed_files(before_src, after_src)
            if not modules:
                sb, sa, srr = 0, 0, None
            else:
                b_out = os.path.join(tmpdir, "b_smells")
                a_out = os.path.join(tmpdir, "a_smells")
                os.makedirs(b_out, exist_ok=True)
                os.makedirs(a_out, exist_ok=True)
                sb = run_designite(before_src, b_out, modules, changed, dj_cp=dj_cp, timeout=90)
                sa = run_designite(after_src, a_out, modules, changed, dj_cp=dj_cp, timeout=90)
                srr = (sb - sa) / sb * 100 if sb > 0 else None

            row = {
                "sha": sha,
                "smells_before": sb,
                "smells_after": sa,
                "srr": f"{srr:.1f}" if srr is not None else "",
                "n_structural": len(types),
                "structural_types": ",".join(types),
            }
            with open(scan_csv, "a", newline="") as f:
                writer = csv.DictWriter(f, ["sha", "smells_before", "smells_after",
                                             "srr", "n_structural", "structural_types"])
                writer.writerow(row)
            success += 1

            if sb > 0:
                tag = str(round(srr, 1)) + "%" if srr is not None else "N/A"
                print(f"    [{i}/{len(to_scan)}] {sha[:7]} smells={sb}->{sa} SRR={tag}")
        except Exception as e:
            print(f"    [{i}/{len(to_scan)}] {sha[:7]} ERROR: {e}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Reset repo
    subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=60)
    print(f"  [{name}] Scanned {success} commits")
    return True


def setup_pairs_phase(name, info):
    """Create before/after eval pairs."""
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")
    pairs_dir = os.path.join(PROJECT_ROOT, "data", f"{name}_pairs")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    # Check cached
    if os.path.isfile(commits_jsonl):
        with open(commits_jsonl) as f:
            n = sum(1 for l in f if l.strip())
        if n > 0:
            print(f"  [{name}] Pairs: {n} commits ready (cached)")
            return True

    # Load scan results
    scan_rows = []
    if os.path.isfile(scan_csv):
        with open(scan_csv) as f:
            for row in csv.DictReader(f):
                sb = int(row.get("smells_before", 0) or 0)
                if sb >= 1:
                    scan_rows.append(row)
    scan_rows.sort(key=lambda r: int(r.get("smells_before", 0) or 0), reverse=True)

    needed = sample_size(info["ref_commits"])
    selected_shas = [r["sha"] for r in scan_rows[:needed]]

    if not selected_shas:
        # Use any structural commits from refs
        with open(refs_json) as f:
            data = json.load(f)
        structural = [c for c in data.get("commits", [])
                      if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
        selected_shas = [c["sha1"] for c in structural[:needed]]

    if not selected_shas:
        print(f"  [{name}] No commits available")
        return False

    # Load RMiner data
    commits_by_sha = {}
    with open(refs_json) as f:
        data = json.load(f)
    for c in data.get("commits", []):
        commits_by_sha[c["sha1"]] = c

    if os.path.isdir(pairs_dir):
        shutil.rmtree(pairs_dir)

    records = []
    for i, sha in enumerate(selected_shas, 1):
        out = os.path.join(pairs_dir, f"commit_{i:03d}")
        os.makedirs(os.path.join(out, "before"), exist_ok=True)
        os.makedirs(os.path.join(out, "after"), exist_ok=True)

        subprocess.run(["git", "-C", repo_dir, "checkout", "--", "."],
                       capture_output=True, timeout=60)
        r = subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"    Skipping {sha[:7]}: checkout failed")
            continue
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
        records.append({
            "sha": sha,
            "before_dir": os.path.abspath(os.path.join(out, "before")),
            "after_dir": os.path.abspath(os.path.join(out, "after")),
            "rminer_types": rtypes,
            "smells_before": int(scan_row.get("smells_before", 0) or 0),
            "smells_after": int(scan_row.get("smells_after", 0) or 0),
        })

    subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=60)

    with open(commits_jsonl, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"  [{name}] Created {len(records)} eval pairs")
    return len(records) > 0


def eval_phase(name, mode):
    """Run eval for one mode."""
    commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")
    output_dir = os.path.join(PROJECT_ROOT, "results", name)

    with open(commits_jsonl) as f:
        n = sum(1 for l in f if l.strip())
    if n == 0:
        return True

    os.makedirs(output_dir, exist_ok=True)

    results_path = os.path.join(output_dir, "results.json")
    if os.path.isfile(results_path):
        with open(results_path) as f:
            existing = json.load(f)
        if mode in existing and len(existing[mode]) >= n:
            print(f"  [{name}] {mode} eval: done ({len(existing[mode])} results)")
            return True

    print(f"  [{name}] Running {mode} eval on {n} commits...")
    r = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "run_eval.py"),
         "--commits", commits_jsonl,
         "--mode", mode,
         "--output", output_dir],
        cwd=PROJECT_ROOT,
        timeout=7200,
    )
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="Single repo")
    p.add_argument("--skip", default="", help="Comma-separated repos to skip")
    p.add_argument("--eval-mode", choices=["ollama", "lora", "both"], default="both")
    p.add_argument("--skip-eval", action="store_true")
    args = p.parse_args()

    skip = set(args.skip.split(",")) if args.skip else set()
    progress = load_progress()

    if args.only:
        repos = {args.only: REPOS[args.only]}
    else:
        repos = {k: v for k, v in REPOS.items() if k not in skip}

    total = len(repos)
    start_time = time.time()

    for idx, (name, info) in enumerate(repos.items(), 1):
        print(f"\n{'='*60}")
        print(f"[{idx}/{total}] {name} (need {sample_size(info['ref_commits'])} commits)")
        print(f"{'='*60}")

        try:
            # Phase 1: Ensure proper clone
            if not ensure_repo(name, info):
                continue
            progress.setdefault(name, {})["cloned"] = True

            # Phase 2: RMiner
            if not progress.get(name, {}).get("rminer_verified"):
                ok = run_rminer_phase(name, info)
                if ok:
                    progress[name]["rminer"] = True
                    progress[name]["rminer_verified"] = True
                    save_progress(progress)
                else:
                    continue

            # Phase 3: Scan
            if not progress.get(name, {}).get("scan_verified"):
                ok = scan_phase(name, info)
                if ok:
                    progress[name]["scanned"] = True
                    progress[name]["scan_verified"] = True
                    progress[name].pop("pairs_ready", None)
                    save_progress(progress)

            # Phase 4: Setup pairs
            if not progress.get(name, {}).get("pairs_verified"):
                ok = setup_pairs_phase(name, info)
                if ok:
                    progress[name]["pairs_ready"] = True
                    progress[name]["pairs_verified"] = True
                    save_progress(progress)
                else:
                    continue

            # Phase 5: Eval
            if args.skip_eval:
                continue

            modes = ["ollama", "lora"] if args.eval_mode == "both" else [args.eval_mode]
            for mode in modes:
                key = f"eval_{mode}"
                if not progress.get(name, {}).get(key):
                    try:
                        ok = eval_phase(name, mode)
                        if ok:
                            progress[name][key] = True
                            save_progress(progress)
                    except Exception as e:
                        print(f"  [{name}] {mode} eval error: {e}")

        except Exception as e:
            print(f"  [{name}] FATAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue

    elapsed = time.time() - start_time
    hours = elapsed / 3600

    # Summary
    print(f"\n{'='*60}")
    print(f"DONE in {hours:.1f}h")
    done_eval = sum(1 for n in repos if progress.get(n, {}).get("eval_ollama"))
    print(f"Repos with Ollama eval: {done_eval}/{total}")
    done_lora = sum(1 for n in repos if progress.get(n, {}).get("eval_lora"))
    print(f"Repos with LoRA eval: {done_lora}/{total}")


if __name__ == "__main__":
    main()
