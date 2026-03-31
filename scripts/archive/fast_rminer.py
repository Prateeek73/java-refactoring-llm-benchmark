"""
fast_rminer.py — Fast RMiner using single-commit mode (-c) with random sampling.

Instead of scanning entire history with -bc (slow), randomly samples commits
and runs RMiner -c on each until we find enough structural refactorings.

Usage:
  python scripts/fast_rminer.py --repo activemq --needed 5
  python scripts/fast_rminer.py --all          # process all repos needing RMiner
"""
import argparse, json, os, random, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from lib import STRUCTURAL_TYPES

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RMINER = os.path.join(PROJECT_ROOT, "tools", "RefactoringMiner-3.0.10", "bin", "RefactoringMiner")

# Import repo info from master script
from run_all_repos import REPOS, load_progress, save_progress, sample_size


def get_commit_shas(repo_dir, max_count=5000):
    """Get list of commit SHAs from repo."""
    r = subprocess.run(
        ["git", "-C", repo_dir, "log", "--format=%H", f"--max-count={max_count}"],
        capture_output=True, text=True, timeout=60
    )
    return [sha.strip() for sha in r.stdout.strip().split("\n") if sha.strip()]


def rminer_single(repo_dir, sha, timeout=120):
    """Run RMiner on a single commit, return refactorings list."""
    tmp_json = f"/tmp/rminer_{sha[:7]}.json"
    try:
        r = subprocess.run(
            [RMINER, "-c", repo_dir, sha, "-json", tmp_json],
            capture_output=True, text=True, timeout=timeout
        )
        if r.returncode != 0 or not os.path.isfile(tmp_json):
            return None
        with open(tmp_json) as f:
            data = json.load(f)
        commits = data.get("commits", [])
        if commits:
            return commits[0]
        return None
    except (subprocess.TimeoutExpired, Exception):
        return None
    finally:
        if os.path.isfile(tmp_json):
            os.remove(tmp_json)


def process_repo(name, info, needed=None):
    """Find structural refactoring commits for one repo via random sampling."""
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    structural_json = os.path.join(PROJECT_ROOT, "data", f"{name}_structural.json")

    if os.path.isfile(refs_json):
        print(f"[{name}] refs.json already exists, skip")
        return True

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"[{name}] Not cloned, skip")
        return False

    if needed is None:
        needed = sample_size(info["ref_commits"])

    # We want 3x the needed amount to have buffer for scanning
    target = needed * 3
    ref_rate = info["ref_commits"] / max(info["total_commits"], 1)
    # Structural is ~30-50% of refactorings
    structural_rate = ref_rate * 0.35

    print(f"[{name}] Need {needed} commits (target {target} structural), ref_rate={ref_rate:.3f}")

    # Get all commit SHAs
    all_shas = get_commit_shas(repo_dir, max_count=min(info["total_commits"], 10000))
    print(f"[{name}] Got {len(all_shas)} commit SHAs")

    if not all_shas:
        print(f"[{name}] No commits found!")
        return False

    # Random sample - check more commits than needed to account for non-structural
    # Estimate: need to check target / structural_rate commits
    sample_size_needed = min(len(all_shas), max(200, int(target / max(structural_rate, 0.005))))
    sample_shas = random.sample(all_shas, min(len(all_shas), sample_size_needed))
    print(f"[{name}] Sampling {len(sample_shas)} commits...")

    found_commits = []
    checked = 0
    start_time = time.time()

    for sha in sample_shas:
        if len(found_commits) >= target:
            break

        checked += 1
        commit_data = rminer_single(repo_dir, sha, timeout=90)
        if commit_data is None:
            continue

        refs = commit_data.get("refactorings", [])
        structural = [r for r in refs if r["type"] in STRUCTURAL_TYPES]

        if structural:
            found_commits.append(commit_data)
            elapsed = time.time() - start_time
            rate = checked / elapsed if elapsed > 0 else 0
            print(f"  [{name} {checked}] {sha[:7]} -> {len(structural)} structural "
                  f"({len(found_commits)}/{target} found, {rate:.1f} commits/s)")
        elif checked % 50 == 0:
            elapsed = time.time() - start_time
            rate = checked / elapsed if elapsed > 0 else 0
            print(f"  [{name} {checked}] checked {checked}/{len(sample_shas)}, "
                  f"found {len(found_commits)}/{target} ({rate:.1f} commits/s)")

        # Safety: if we've checked 500+ and found nothing, something's wrong
        if checked >= 500 and len(found_commits) == 0:
            print(f"  [{name}] Checked 500 commits with 0 structural, accepting any refactoring...")
            # Broaden search - accept any refactoring
            for sha2 in sample_shas[checked:checked+200]:
                commit_data2 = rminer_single(repo_dir, sha2, timeout=90)
                if commit_data2 and commit_data2.get("refactorings"):
                    found_commits.append(commit_data2)
                    if len(found_commits) >= target:
                        break
            break

    elapsed = time.time() - start_time
    print(f"[{name}] Found {len(found_commits)} structural commits "
          f"from {checked} checked in {elapsed:.0f}s")

    # Save as refs.json format (compatible with existing pipeline)
    output = {"commits": found_commits}
    with open(refs_json, "w") as f:
        json.dump(output, f, indent=2)
    print(f"[{name}] Saved {refs_json}")

    # Also save structural.json
    with open(structural_json, "w") as f:
        json.dump(output, f, indent=2)

    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", help="Single repo to process")
    p.add_argument("--needed", type=int, help="Override number of commits needed")
    p.add_argument("--all", action="store_true", help="Process all repos needing RMiner")
    args = p.parse_args()

    progress = load_progress()

    if args.repo:
        if args.repo not in REPOS:
            sys.exit(f"Unknown repo: {args.repo}")
        ok = process_repo(args.repo, REPOS[args.repo], args.needed)
        if ok:
            progress.setdefault(args.repo, {})["rminer"] = True
            save_progress(progress)
    elif args.all:
        for name, info in REPOS.items():
            if progress.get(name, {}).get("rminer"):
                continue
            if not progress.get(name, {}).get("cloned"):
                continue
            ok = process_repo(name, info)
            if ok:
                progress.setdefault(name, {})["rminer"] = True
                save_progress(progress)
    else:
        p.error("Specify --repo NAME or --all")


if __name__ == "__main__":
    main()
