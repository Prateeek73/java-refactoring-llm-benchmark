"""
rminer_all.py — Run RMiner on all repos using shallow clones.

For each repo that needs RMiner:
1. Shallow-clone last N commits into a temp dir (gets actual blobs)
2. Run RMiner -bc on that range
3. Save refs.json and clean up temp clone

Usage:
  python scripts/rminer_all.py          # all repos
  python scripts/rminer_all.py --repo activemq  # single repo
"""
import argparse, json, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from lib import STRUCTURAL_TYPES
from run_all_repos import REPOS, load_progress, save_progress, sample_size

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RMINER = os.path.join(PROJECT_ROOT, "tools", "RefactoringMiner-3.0.10", "bin", "RefactoringMiner")
TMP_BASE = "/tmp/rminer_clones"


def compute_window(info):
    """How many commits to clone/scan for this repo."""
    needed = sample_size(info["ref_commits"])
    ref_rate = info["ref_commits"] / max(info["total_commits"], 1)
    # Need enough commits to find ~3x needed structural refactorings
    # structural rate ≈ ref_rate * 0.4
    window = int(needed * 3 / max(ref_rate * 0.4, 0.005))
    return max(200, min(window, 3000))


def process_repo(name, info):
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")

    # Check if already done with actual structural refactorings
    if os.path.isfile(refs_json):
        with open(refs_json) as f:
            data = json.load(f)
        structural = [c for c in data.get("commits", [])
                      if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
        if structural:
            print(f"[{name}] Already has {len(structural)} structural commits, skip")
            return True

    gh = info["gh"]
    window = compute_window(info)
    needed = sample_size(info["ref_commits"])

    print(f"[{name}] Shallow clone depth={window}, need {needed} commits")

    tmp_dir = os.path.join(TMP_BASE, name)
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(TMP_BASE, exist_ok=True)

    # Shallow clone with blob data
    url = f"https://github.com/{gh}.git"
    t0 = time.time()
    r = subprocess.run(
        ["git", "clone", f"--depth={window}", "--single-branch", url, tmp_dir],
        capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        print(f"[{name}] Clone FAILED: {r.stderr[:200]}")
        return False
    clone_time = time.time() - t0
    print(f"[{name}] Cloned in {clone_time:.0f}s")

    # Get commit range
    r = subprocess.run(["git", "-C", tmp_dir, "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=10)
    end_sha = r.stdout.strip()

    r = subprocess.run(["git", "-C", tmp_dir, "rev-list", "--max-parents=0", "HEAD"],
                       capture_output=True, text=True, timeout=10)
    start_sha = r.stdout.strip().split("\n")[0]

    r = subprocess.run(["git", "-C", tmp_dir, "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True, timeout=10)
    actual_count = r.stdout.strip()

    print(f"[{name}] RMiner -bc on {actual_count} commits: {start_sha[:7]}..{end_sha[:7]}")

    t0 = time.time()
    try:
        r = subprocess.run(
            [RMINER, "-bc", tmp_dir, start_sha, end_sha, "-json", refs_json],
            capture_output=True, text=True, timeout=3600
        )
        rminer_time = time.time() - t0
        # Parse the summary line
        for line in (r.stdout + r.stderr).split("\n"):
            if "Total count" in line or "Analyzed" in line:
                print(f"[{name}] {line.strip()}")
    except subprocess.TimeoutExpired:
        rminer_time = time.time() - t0
        print(f"[{name}] RMiner TIMEOUT after {rminer_time:.0f}s")

    # Check results
    if os.path.isfile(refs_json):
        with open(refs_json) as f:
            data = json.load(f)
        all_commits = data.get("commits", [])
        with_refs = [c for c in all_commits if c.get("refactorings")]
        structural = [c for c in all_commits
                      if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
        total_refs = sum(len(c.get("refactorings", [])) for c in with_refs)
        print(f"[{name}] {len(all_commits)} commits, {len(with_refs)} with refactorings, "
              f"{len(structural)} structural, {total_refs} total refs ({rminer_time:.0f}s)")
    else:
        print(f"[{name}] No output file!")

    # Clean up temp clone
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return os.path.isfile(refs_json)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", help="Single repo")
    p.add_argument("--skip", default="", help="Comma-separated repos to skip")
    args = p.parse_args()

    skip = set(args.skip.split(",")) if args.skip else set()
    # Always skip repos we already have
    skip.update(["commons-lang", "commons-io", "gson", "joda-time"])

    progress = load_progress()

    if args.repo:
        repos = {args.repo: REPOS[args.repo]}
    else:
        repos = {k: v for k, v in REPOS.items() if k not in skip}

    total = len(repos)
    done = 0
    for name, info in repos.items():
        if progress.get(name, {}).get("rminer"):
            # Verify it actually has structural data
            refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
            if os.path.isfile(refs_json):
                with open(refs_json) as f:
                    data = json.load(f)
                structural = [c for c in data.get("commits", [])
                              if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
                if structural:
                    done += 1
                    continue

        ok = process_repo(name, info)
        if ok:
            progress.setdefault(name, {})["rminer"] = True
            save_progress(progress)
            done += 1
        print(f"--- Progress: {done}/{total} repos done ---\n")

    # Cleanup
    if os.path.isdir(TMP_BASE):
        shutil.rmtree(TMP_BASE, ignore_errors=True)

    print(f"\n=== Complete: {done}/{total} repos processed ===")


if __name__ == "__main__":
    main()
