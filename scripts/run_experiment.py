#!/usr/bin/env python3
"""
run_experiment.py — Unified experiment runner for all 3 experiments.

Full pipeline: clone -> RMiner -> scan -> select -> pairs -> evosuite -> train -> eval -> results

Experiments:
  1: 20 random Camel commits
  2: 100 filtered Camel commits (high-signal structural refactorings)
  3: 69 commits across 19 Apache library repos (per-repo counts specified)

Usage:
  python scripts/run_experiment.py --exp 1 --mode ollama
  python scripts/run_experiment.py --exp 2 --mode both --pass-k 5
  python scripts/run_experiment.py --exp 3 --mode lora

  # Custom intermediate paths:
  python scripts/run_experiment.py --exp 1 --commits-file data/exp1_commits.jsonl \\
      --pairs-dir data/exp1_pairs --results-dir results/experiment_1

  # Run specific phases:
  python scripts/run_experiment.py --exp 3 --phase clone,rminer,scan,select,pairs,evosuite,eval

  # EvoSuite with specific Java home:
  python scripts/run_experiment.py --exp 1 --java-home /usr/lib/jvm/java-11-openjdk-amd64
"""

import argparse, csv, glob, json, math, os, random, re, shutil, statistics
import subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(__file__))
from lib import (STRUCTURAL_TYPES, copy_all_java_src, count_smells,
                 find_changed_files, run_designite, default_dj_cp, write_jsonl)
from find_primary_java import find_primary_changed_java

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RMINER = os.path.join(PROJECT_ROOT, "tools", "RefactoringMiner-3.0.10", "bin", "RefactoringMiner")

# ── All 30 Cordeiro et al. repos ──────────────────────────────────────────
REPOS = {
    "camel":                        {"gh": "apache/camel",                          "ref_commits": 760,  "total_commits": 71671},
    "activemq":                     {"gh": "apache/activemq",                       "ref_commits": 546,  "total_commits": 11821},
    "accumulo":                     {"gh": "apache/accumulo",                       "ref_commits": 483,  "total_commits": 12599},
    "james-project":                {"gh": "apache/james-project",                  "ref_commits": 798,  "total_commits": 15629},
    "openmeetings":                 {"gh": "apache/openmeetings",                   "ref_commits": 181,  "total_commits": 3716},
    "jmeter":                       {"gh": "apache/jmeter",                         "ref_commits": 554,  "total_commits": 18266},
    "skywalking":                   {"gh": "apache/skywalking",                     "ref_commits": 166,  "total_commits": 8041},
    "jclouds":                      {"gh": "apache/jclouds",                        "ref_commits": 699,  "total_commits": 10878},
    "qpid-jms-amqp-0-x":            {"gh": "apache/qpid-jms-amqp-0-x",             "ref_commits": 374,  "total_commits": 7631},
    "attic-polygene-java":          {"gh": "apache/attic-polygene-java",            "ref_commits": 303,  "total_commits": 5820},
    "incubator-brooklyn":           {"gh": "apache/brooklyn-server",                "ref_commits": 541,  "total_commits": 13002},
    "incubator-druid":              {"gh": "apache/druid",                          "ref_commits": 538,  "total_commits": 14281},
    "systemml":                     {"gh": "apache/systemds",                       "ref_commits": 383,  "total_commits": 8603},
    "oozie":                        {"gh": "apache/oozie",                          "ref_commits": 151,  "total_commits": 2409},
    "apex-malhar":                  {"gh": "apache/apex-malhar",                    "ref_commits": 379,  "total_commits": 6097},
    "ode":                          {"gh": "apache/ode",                            "ref_commits": 136,  "total_commits": 3823},
    "incubator-gobblin":            {"gh": "apache/gobblin",                        "ref_commits": 365,  "total_commits": 6409},
    "servicecomb-pack":             {"gh": "apache/servicecomb-pack",               "ref_commits": 139,  "total_commits": 1683},
    "falcon":                       {"gh": "apache/falcon",                         "ref_commits": 141,  "total_commits": 2227},
    "myfaces-extcdi":               {"gh": "apache/myfaces-extcdi",                 "ref_commits": 130,  "total_commits": 1136},
    "incubator-pinot":              {"gh": "apache/pinot",                          "ref_commits": 498,  "total_commits": 11950},
    "brooklyn-library":             {"gh": "apache/brooklyn-library",               "ref_commits": 134,  "total_commits": 3811},
    "deltaspike":                   {"gh": "apache/deltaspike",                     "ref_commits": 192,  "total_commits": 2671},
    "incubator-iotdb":              {"gh": "apache/iotdb",                          "ref_commits": 212,  "total_commits": 11020},
    "apex-core":                    {"gh": "apache/apex-core",                      "ref_commits": 407,  "total_commits": 6122},
    "myfaces-trinidad":             {"gh": "apache/myfaces-trinidad",               "ref_commits": 172,  "total_commits": 4730},
    "incubator-shardingsphere":     {"gh": "apache/shardingsphere",                 "ref_commits": 2527, "total_commits": 43295},
    "incubator-dolphinscheduler":   {"gh": "apache/dolphinscheduler",               "ref_commits": 161,  "total_commits": 8473},
    "incubator-taverna-language":   {"gh": "apache/incubator-taverna-language",     "ref_commits": 158,  "total_commits": 3560},
    "hadoop-ozone":                 {"gh": "apache/ozone",                          "ref_commits": 264,  "total_commits": 7916},
}

# ── Experiment Configurations ─────────────────────────────────────────────

EXPERIMENT_CONFIGS = {
    1: {
        "name": "camel_random_20",
        "description": "20 random Camel commits (structural refactorings)",
        "repos": {"camel": 20},
        "selection": "random",
        "seed": 42,
    },
    2: {
        "name": "camel_filtered_100",
        "description": "100 filtered Camel commits (high-signal structural, ranked by smell richness)",
        "repos": {"camel": 100},
        "selection": "filtered",
    },
    3: {
        "name": "library_repos_69",
        "description": "69 commits across 19 Apache library repos",
        "repos": {
            "incubator-brooklyn": 5,
            "incubator-druid": 4,
            "systemml": 2,
            "oozie": 4,
            "apex-malhar": 1,
            "ode": 4,
            "incubator-gobblin": 1,
            "servicecomb-pack": 1,
            "falcon": 1,
            "myfaces-extcdi": 5,
            "incubator-pinot": 1,
            "brooklyn-library": 2,
            "deltaspike": 2,
            "incubator-iotdb": 4,
            "apex-core": 2,
            "myfaces-trinidad": 25,
            "incubator-shardingsphere": 2,
            "incubator-dolphinscheduler": 2,
            "incubator-taverna-language": 3,
        },
        "selection": "filtered",
    },
}

ALL_PHASES = ["clone", "rminer", "scan", "select", "pairs", "evosuite", "train", "eval"]

# ── Progress tracking ─────────────────────────────────────────────────────

def load_progress(exp_num, results_dir):
    path = os.path.join(results_dir, "progress.json")
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_progress(progress, exp_num, results_dir):
    os.makedirs(results_dir, exist_ok=True)
    with open(os.path.join(results_dir, "progress.json"), "w") as f:
        json.dump(progress, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: CLONE
# ═══════════════════════════════════════════════════════════════════════════

def compute_depth(info, needed):
    ref_rate = info["ref_commits"] / max(info["total_commits"], 1)
    depth = int(needed * 10 / max(ref_rate * 0.4, 0.005))
    return max(500, min(depth, 5000))


def ensure_repo(name, info, needed):
    """Ensure repo is cloned with blobs (shallow clone)."""
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    if os.path.isdir(os.path.join(repo_dir, ".git")):
        # Check if blobless (need blobs for checkout)
        config_file = os.path.join(repo_dir, ".git", "config")
        if os.path.isfile(config_file):
            with open(config_file) as f:
                if "partialclonefilter" not in f.read():
                    print(f"  [{name}] Repo OK")
                    return True
        # Blobless clone — re-clone with depth
        print(f"  [{name}] Blobless clone detected, re-cloning...")
        subprocess.run(["chmod", "-R", "u+w", repo_dir], capture_output=True, timeout=120)
        shutil.rmtree(repo_dir, ignore_errors=True)

    depth = compute_depth(info, needed)
    url = f"https://github.com/{info['gh']}.git"
    print(f"  [{name}] Cloning --depth={depth} from {url}...")
    r = subprocess.run(
        ["git", "clone", f"--depth={depth}", "--single-branch", url, repo_dir],
        capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        print(f"  [{name}] Clone FAILED: {r.stderr[:200]}")
        return False
    print(f"  [{name}] Cloned OK")
    return True


def phase_clone(config, progress, results_dir):
    print("\n" + "=" * 60)
    print("PHASE 1: CLONE REPOS")
    print("=" * 60)
    for name, needed in config["repos"].items():
        if progress.get(name, {}).get("cloned"):
            continue
        info = REPOS[name]
        ok = ensure_repo(name, info, needed)
        if ok:
            progress.setdefault(name, {})["cloned"] = True
            save_progress(progress, config["name"], results_dir)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: REFACTORING MINER
# ═══════════════════════════════════════════════════════════════════════════

def rminer_single(repo_dir, sha, timeout=120):
    """Run RMiner -c on a single commit."""
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
        return commits[0] if commits else None
    except (subprocess.TimeoutExpired, Exception):
        return None
    finally:
        if os.path.isfile(tmp_json):
            os.remove(tmp_json)


def run_rminer_for_repo(name, info, needed):
    """Find structural refactoring commits via random sampling (fast)."""
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")

    # Check cached
    if os.path.isfile(refs_json):
        with open(refs_json) as f:
            data = json.load(f)
        structural = [c for c in data.get("commits", [])
                      if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
        if len(structural) >= needed:
            print(f"  [{name}] RMiner: {len(structural)} structural commits (cached)")
            return True

    # Get all commit SHAs
    r = subprocess.run(
        ["git", "-C", repo_dir, "log", "--format=%H", "--max-count=10000"],
        capture_output=True, text=True, timeout=60
    )
    all_shas = [s.strip() for s in r.stdout.strip().split("\n") if s.strip()]
    if not all_shas:
        print(f"  [{name}] No commits found!")
        return False

    target = needed * 3  # buffer
    sample_count = min(len(all_shas), max(200, target * 10))
    sample_shas = random.sample(all_shas, sample_count)
    print(f"  [{name}] Sampling {sample_count} commits (need {target} structural)...")

    found = []
    checked = 0
    t0 = time.time()
    for sha in sample_shas:
        if len(found) >= target:
            break
        checked += 1
        commit_data = rminer_single(repo_dir, sha, timeout=90)
        if commit_data is None:
            continue
        refs = commit_data.get("refactorings", [])
        structural = [r for r in refs if r["type"] in STRUCTURAL_TYPES]
        if structural:
            found.append(commit_data)
            if checked % 10 == 0:
                rate = checked / (time.time() - t0)
                print(f"    [{name}] {len(found)}/{target} found, {checked} checked ({rate:.1f}/s)")
        if checked >= 500 and not found:
            # Accept any refactoring if no structural found
            print(f"  [{name}] No structural after 500, accepting any refactoring...")
            for sha2 in sample_shas[checked:checked + 200]:
                c = rminer_single(repo_dir, sha2, timeout=90)
                if c and c.get("refactorings"):
                    found.append(c)
                    if len(found) >= target:
                        break
            break

    elapsed = time.time() - t0
    print(f"  [{name}] Found {len(found)} commits in {elapsed:.0f}s")

    output = {"commits": found}
    with open(refs_json, "w") as f:
        json.dump(output, f, indent=2)
    return len(found) > 0


def phase_rminer(config, progress, results_dir):
    print("\n" + "=" * 60)
    print("PHASE 2: REFACTORING MINER")
    print("=" * 60)
    for name, needed in config["repos"].items():
        if progress.get(name, {}).get("rminer"):
            continue
        if not progress.get(name, {}).get("cloned"):
            print(f"  [{name}] Not cloned, skip")
            continue
        ok = run_rminer_for_repo(name, REPOS[name], needed)
        if ok:
            progress.setdefault(name, {})["rminer"] = True
            save_progress(progress, config["name"], results_dir)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: DESIGNITEJAVA SCAN
# ═══════════════════════════════════════════════════════════════════════════

def scan_repo(name, needed):
    """Scan structural commits with DesigniteJava to measure smells."""
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    # Check cached
    if os.path.isfile(scan_csv):
        with open(scan_csv) as f:
            rows = [r for r in csv.DictReader(f)]
        has_smells = [r for r in rows if int(r.get("smells_before", 0) or 0) > 0]
        if len(has_smells) >= needed:
            print(f"  [{name}] Scan: {len(has_smells)} commits with smells (cached)")
            return True

    with open(refs_json) as f:
        data = json.load(f)
    structural = [c for c in data.get("commits", [])
                  if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]
    if not structural:
        print(f"  [{name}] No structural commits to scan")
        return False

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
        tmpdir = tempfile.mkdtemp(prefix=f"scan_{name}_")
        try:
            before_src = os.path.join(tmpdir, "before", "src")
            after_src = os.path.join(tmpdir, "after", "src")
            os.makedirs(before_src, exist_ok=True)
            os.makedirs(after_src, exist_ok=True)

            subprocess.run(["git", "-C", repo_dir, "checkout", "--", "."],
                           capture_output=True, timeout=60)
            r = subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                continue
            copy_all_java_src(repo_dir, before_src)

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
                "sha": sha, "smells_before": sb, "smells_after": sa,
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
                tag = f"{srr:.1f}%" if srr is not None else "N/A"
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


def phase_scan(config, progress, results_dir):
    print("\n" + "=" * 60)
    print("PHASE 3: DESIGNITEJAVA SCAN")
    print("=" * 60)
    for name, needed in config["repos"].items():
        if progress.get(name, {}).get("scanned"):
            continue
        if not progress.get(name, {}).get("rminer"):
            print(f"  [{name}] No RMiner data, skip")
            continue
        ok = scan_repo(name, needed)
        if ok:
            progress.setdefault(name, {})["scanned"] = True
            save_progress(progress, config["name"], results_dir)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4: SELECT COMMITS
# ═══════════════════════════════════════════════════════════════════════════

def select_commits_for_repo(name, needed, selection_mode, seed=None):
    """Select commits for evaluation from scan results."""
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")

    with open(refs_json) as f:
        data = json.load(f)
    structural = [c for c in data.get("commits", [])
                  if any(r["type"] in STRUCTURAL_TYPES for r in c.get("refactorings", []))]

    if selection_mode == "random":
        rng = random.Random(seed)
        selected = rng.sample(structural, min(needed, len(structural)))
        print(f"  [{name}] Random: selected {len(selected)}/{len(structural)} structural commits")
        return selected

    # Filtered mode: rank by smell richness from scan CSV
    scan_data = {}
    if os.path.isfile(scan_csv):
        with open(scan_csv) as f:
            for row in csv.DictReader(f):
                scan_data[row["sha"]] = row

    # Score each commit
    scored = []
    for c in structural:
        sha = c["sha1"]
        scan = scan_data.get(sha, {})
        sb = int(scan.get("smells_before", 0) or 0)
        n_types = int(scan.get("n_structural", 0) or 0)
        srr_val = float(scan["srr"]) if scan.get("srr") else 0
        # Score: smells * types * (1 + srr/100) — favor high smells & diverse types
        score = sb * max(n_types, 1) * (1 + max(srr_val, 0) / 100)
        scored.append((score, c, sb))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top N with smells_before > 0 first, then fill with any
    selected = []
    for score, c, sb in scored:
        if len(selected) >= needed:
            break
        if sb > 0:
            selected.append(c)

    # If not enough with smells, fill with rest
    if len(selected) < needed:
        selected_shas = {c["sha1"] for c in selected}
        for score, c, sb in scored:
            if len(selected) >= needed:
                break
            if c["sha1"] not in selected_shas:
                selected.append(c)

    print(f"  [{name}] Filtered: selected {len(selected)}/{len(structural)} "
          f"(top by smell richness)")
    return selected


def phase_select(config, progress, results_dir, commits_file):
    print("\n" + "=" * 60)
    print("PHASE 4: SELECT COMMITS")
    print("=" * 60)

    all_records = []
    pair_counter = 0

    for name, needed in config["repos"].items():
        if not progress.get(name, {}).get("scanned"):
            # Try without scan if rminer exists
            if not progress.get(name, {}).get("rminer"):
                print(f"  [{name}] Not ready, skip")
                continue

        selected = select_commits_for_repo(
            name, needed, config["selection"], config.get("seed")
        )

        scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
        scan_data = {}
        if os.path.isfile(scan_csv):
            with open(scan_csv) as f:
                for row in csv.DictReader(f):
                    scan_data[row["sha"]] = row

        for c in selected:
            pair_counter += 1
            sha = c["sha1"]
            rtypes = list(set(r["type"] for r in c.get("refactorings", [])
                             if r["type"] in STRUCTURAL_TYPES))
            if not rtypes:
                rtypes = list(set(r["type"] for r in c.get("refactorings", [])))[:3]
            if not rtypes:
                rtypes = ["Extract Method"]

            scan = scan_data.get(sha, {})
            all_records.append({
                "sha": sha,
                "repo": name,
                "pair_id": f"commit_{pair_counter:03d}",
                "rminer_types": rtypes,
                "smells_before": int(scan.get("smells_before", 0) or 0),
                "smells_after": int(scan.get("smells_after", 0) or 0),
            })

    # Write commits file
    os.makedirs(os.path.dirname(commits_file), exist_ok=True)
    with open(commits_file, "w") as f:
        for r in all_records:
            f.write(json.dumps(r) + "\n")

    print(f"\n  Total selected: {len(all_records)} commits -> {commits_file}")
    progress["_selected"] = len(all_records)
    save_progress(progress, config["name"], results_dir)
    return all_records


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5: CHECKOUT PAIRS
# ═══════════════════════════════════════════════════════════════════════════

def phase_pairs(config, progress, results_dir, commits_file, pairs_dir):
    print("\n" + "=" * 60)
    print("PHASE 5: CHECKOUT BEFORE/AFTER PAIRS")
    print("=" * 60)

    if not os.path.isfile(commits_file):
        print(f"  No commits file at {commits_file}")
        return

    with open(commits_file) as f:
        records = [json.loads(line) for line in f if line.strip()]

    os.makedirs(pairs_dir, exist_ok=True)
    done = 0

    for rec in records:
        pair_id = rec["pair_id"]
        repo_name = rec.get("repo", "camel")
        sha = rec["sha"]
        repo_dir = os.path.join(PROJECT_ROOT, "data", repo_name)
        out = os.path.join(pairs_dir, pair_id)

        # Check cached
        before_dir = os.path.join(out, "before", "src")
        after_dir = os.path.join(out, "after", "src")
        if os.path.isdir(before_dir) and os.path.isdir(after_dir):
            # Check not empty
            has_java = any(f.endswith(".java") for _, _, files in os.walk(before_dir) for f in files)
            if has_java:
                done += 1
                continue

        os.makedirs(os.path.join(out, "before"), exist_ok=True)
        os.makedirs(os.path.join(out, "after"), exist_ok=True)

        # Checkout before (parent)
        subprocess.run(["git", "-C", repo_dir, "checkout", "--", "."],
                       capture_output=True, timeout=60)
        r = subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"  [{pair_id}] {sha[:7]} checkout FAIL: {r.stderr[:100]}")
            continue
        copy_all_java_src(repo_dir, before_dir)

        # Checkout after
        subprocess.run(["git", "-C", repo_dir, "checkout", sha, "--", "."],
                       capture_output=True, timeout=60)
        copy_all_java_src(repo_dir, after_dir)
        done += 1

        if done % 10 == 0:
            print(f"  Checked out {done}/{len(records)} pairs...")

    # Reset all repos
    for name in config["repos"]:
        repo_dir = os.path.join(PROJECT_ROOT, "data", name)
        if os.path.isdir(os.path.join(repo_dir, ".git")):
            subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                           capture_output=True, timeout=60)

    # Update commits file with before_dir / after_dir paths
    updated = []
    with open(commits_file) as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            pair_id = rec["pair_id"]
            rec["before_dir"] = os.path.abspath(os.path.join(pairs_dir, pair_id, "before"))
            rec["after_dir"] = os.path.abspath(os.path.join(pairs_dir, pair_id, "after"))
            updated.append(rec)

    with open(commits_file, "w") as f:
        for r in updated:
            f.write(json.dumps(r) + "\n")

    print(f"  Created {done}/{len(records)} pairs in {pairs_dir}")
    progress["_pairs_done"] = done
    save_progress(progress, config["name"], results_dir)

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6: EVOSUITE TEST GENERATION
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# JVM DISCOVERY — scanned once at startup
# Builds { version_int: path } from whatever is actually installed.
# ---------------------------------------------------------------------------

def scan_installed_jvms():
    """
    Walk /usr/lib/jvm and build {version_int: path} for every installed JDK.
    Only dirs that contain bin/java are included.
    Prefers the -amd64 suffixed entry when two entries share a version.
    """
    jvm_base = "/usr/lib/jvm"
    jvms = {}
    if not os.path.isdir(jvm_base):
        return jvms
    for entry in sorted(os.listdir(jvm_base)):
        full = os.path.join(jvm_base, entry)
        if not os.path.isdir(full):
            continue
        if not os.path.isfile(os.path.join(full, "bin", "java")):
            continue
        # Match "java-11-openjdk-amd64", skip legacy "java-1.x.0-..." symlinks
        m = re.match(r'java-(\d+)-', entry)
        if m:
            ver = int(m.group(1))
            if ver < 2:
                continue
            if ver not in jvms or entry.endswith("-amd64"):
                jvms[ver] = full
    return jvms


_INSTALLED_JVMS: dict = scan_installed_jvms()


def pick_jvm(requested_version):
    """
    Return JAVA_HOME for the best available JDK:
      1. Exact match
      2. Nearest higher (forward compatible)
      3. Nearest lower  (last resort)
      4. None           (caller falls back to system default)
    """
    req = int(requested_version)
    if req in _INSTALLED_JVMS:
        return _INSTALLED_JVMS[req]
    higher = sorted(v for v in _INSTALLED_JVMS if v > req)
    if higher:
        return _INSTALLED_JVMS[higher[0]]
    lower = sorted((v for v in _INSTALLED_JVMS if v < req), reverse=True)
    if lower:
        return _INSTALLED_JVMS[lower[0]]
    return None


# ---------------------------------------------------------------------------
# VERSION DETECTION — reads pom.xml
# ---------------------------------------------------------------------------

_java_home_cache: dict = {}   # repo_dir → (compile_home, evo_home)


def detect_java_version(repo_dir):
    """
    Detect required Java version from pom.xml.
    Returns bare int string e.g. "8", "11", "17".
    Normalizes "1.8" → "8"; floors at 8; defaults to "11".
    """
    pom = os.path.join(repo_dir, "pom.xml")
    if not os.path.isfile(pom):
        return "11"
    try:
        with open(pom, errors="replace") as f:
            content = f.read()
        patterns = [
            r'<maven\.compiler\.source>([\d.]+)</maven\.compiler\.source>',
            r'<maven\.compiler\.release>([\d.]+)</maven\.compiler\.release>',
            r'<java\.version>([\d.]+)</java\.version>',
            r'<source>([\d.]+)</source>',
        ]
        for pattern in patterns:
            m = re.search(pattern, content)
            if m:
                raw = m.group(1).strip()
                if raw.startswith("1."):
                    raw = raw.split(".")[1]
                v = int(raw)
                return str(max(v, 8))
    except Exception:
        pass
    return "11"


def get_java_home_for_repo(repo_dir):
    """
    Return (compile_home, evo_home) for a repo.

    compile_home — highest available JDK.
        We use the highest installed JDK for compilation because the pom.xml
        declares the *minimum* source level but the code at HEAD often uses
        newer APIs.  javac's -source/-target flags keep the bytecode compatible.

    evo_home — clamped to Java 11.
        EvoSuite 1.2.0 can load class files up to version 55 (Java 11).
        Java 8 (version 52) cannot load Java 11 bytecode, so 11 is the target.
        The fake-JDK wrapper injects --illegal-access=permit so XStream and
        EvoSuite's reflection work correctly on Java 9–16.
    """
    if repo_dir in _java_home_cache:
        return _java_home_cache[repo_dir]

    compile_home = (_INSTALLED_JVMS[max(_INSTALLED_JVMS)]
                    if _INSTALLED_JVMS else pick_jvm(detect_java_version(repo_dir)))
    evo_home     = pick_jvm("11") or pick_jvm("8")

    result = (compile_home, evo_home)
    _java_home_cache[repo_dir] = result
    return result


def _make_mvn_env(java_home):
    """Return os.environ copy with JAVA_HOME and PATH set for java_home."""
    env = os.environ.copy()
    if java_home:
        env["JAVA_HOME"] = java_home
        env["PATH"]      = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")
    return env


# ---------------------------------------------------------------------------
# FAKE-JDK WRAPPER
#
# Problem:
#   EvoSuite spawns client JVMs using System.getProperty("java.home"),
#   NOT the java found on PATH.  A PATH wrapper is never called for clients.
#
# Solution:
#   Build a fake JAVA_HOME that mirrors the real JDK via symlinks but
#   replaces bin/java with a shell wrapper that prepends the required flags.
#   Launch EvoSuite itself using fake_jdk/bin/java so that java.home is
#   set to fake_jdk — every client process EvoSuite spawns via
#       new ProcessBuilder(System.getProperty("java.home") + "/bin/java")
#   therefore also goes through our wrapper.
# ---------------------------------------------------------------------------

def _version_of_home(java_home):
    """Return the installed version integer for a JAVA_HOME path, or 8."""
    for ver, path in _INSTALLED_JVMS.items():
        if path == java_home:
            return ver
    return 8


def _build_fake_jdk(real_java_home, tmpdir, extra_flags):
    """
    Create a fake JDK inside tmpdir that symlinks the real JDK but
    replaces bin/java with a wrapper that prepends extra_flags.

    Returns the path to the fake bin/java wrapper.
    """
    fake_root = os.path.join(tmpdir, "fake_jdk")
    fake_bin  = os.path.join(fake_root, "bin")
    os.makedirs(fake_bin, exist_ok=True)

    real_bin = os.path.join(real_java_home, "bin")

    # Symlink everything in the real JDK root except bin/
    for name in os.listdir(real_java_home):
        src = os.path.join(real_java_home, name)
        dst = os.path.join(fake_root, name)
        if name != "bin" and not os.path.exists(dst):
            os.symlink(src, dst)

    # Symlink every binary in real bin/ except java
    for name in os.listdir(real_bin):
        src = os.path.join(real_bin, name)
        dst = os.path.join(fake_bin, name)
        if name != "java" and not os.path.exists(dst):
            os.symlink(src, dst)

    # Write the java wrapper
    real_java   = os.path.join(real_java_home, "bin", "java")
    flags_str   = " ".join(extra_flags)
    wrapper     = os.path.join(fake_bin, "java")
    with open(wrapper, "w") as f:
        f.write(
            f'#!/bin/bash\n'
            f'exec "{real_java}" {flags_str} "$@"\n'
        )
    os.chmod(wrapper, 0o755)
    return wrapper   # = fake_jdk/bin/java


def _evo_flags_for_version(ver):
    """
    Return the JVM flags needed to make EvoSuite 1.2.0 work on the given
    Java version.

    Java 8   — no extra flags needed; module system doesn't exist.

    Java 9+  — --illegal-access=permit is NOT sufficient.  It only suppresses
               the warning — it does NOT open java.base to unnamed modules.
               XStream (used by EvoSuite) calls readObject/writeObject on
               private fields in ArrayList, HashMap, etc., which live in
               java.base.  Without explicit --add-opens those calls throw
               InaccessibleObjectException / ConversionException at runtime.

               Root error seen without these flags:
                 "Unable to make private void java.util.ArrayList.readObject(...)"

    Java 17+ — --illegal-access was removed entirely; explicit opens are the
               only option (same set, just --illegal-access line dropped).
    """
    if ver <= 8:
        return []

    # These opens are required by EvoSuite 1.2.0 on Java 9–17+.
    # Covers: XStream serialization, EvoSuite instrumenter, Objenesis,
    #         and runtime test execution scaffolding.
    opens = [
        # XStream: ArrayList, HashMap, LinkedList, etc.
        "--add-opens=java.base/java.util=ALL-UNNAMED",
        # XStream + EvoSuite: Object, Class, ClassLoader
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        # EvoSuite instrumenter: Field, Method, Constructor access
        "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
        # XStream: ObjectInputStream / ObjectOutputStream
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        # EvoSuite runtime: sun.misc.Unsafe, internal VM access
        "--add-opens=java.base/sun.misc=ALL-UNNAMED",
        # NIO channels used by EvoSuite's communication layer
        "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
        "--add-opens=java.base/java.nio=ALL-UNNAMED",
        # Date/time types sometimes serialized by XStream
        "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
        "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
    ]

    if ver <= 16:
        # --illegal-access=permit is harmless on 9–16 and suppresses leftover
        # warnings from EvoSuite's own code that calls setAccessible without opens.
        return ["--illegal-access=permit"] + opens

    # Java 17+: --illegal-access was removed, opens only
    return opens


# ---------------------------------------------------------------------------
# ONE-TIME REPO BUILD
# ---------------------------------------------------------------------------

_mvn_attempted: set = set()


def _build_repo_once(repo_name, repo_dir):
    """
    Run 'mvn install -DskipTests' once per repo to populate target/ and ~/.m2
    with SNAPSHOT artifacts.  Multi-module SNAPSHOT projects like Camel
    require this because inter-module deps can't be resolved from remote repos.

    Results are cached by repo_name — subsequent calls are no-ops.
    """
    if repo_name in _mvn_attempted:
        return
    _mvn_attempted.add(repo_name)

    existing_jars = glob.glob(os.path.join(repo_dir, "**/target/*.jar"), recursive=True)
    if len(existing_jars) > 5:
        print(f"    [{repo_name}] Found {len(existing_jars)} JARs — skipping one-time build")
        return

    pom = os.path.join(repo_dir, "pom.xml")
    if not os.path.isfile(pom):
        return

    mvn_env = (_make_mvn_env(_INSTALLED_JVMS[max(_INSTALLED_JVMS)])
               if _INSTALLED_JVMS else None)

    print(f"    [{repo_name}] One-time mvn install (may take 10–30 min for large projects)...")
    try:
        r = subprocess.run(
            ["mvn", "install", "-DskipTests", "-B",
             "-Dmaven.javadoc.skip=true", "-Dcheckstyle.skip=true",
             "-Denforcer.skip=true",      "-Dspotless.check.skip=true",
             "-Drat.skip=true",           "--fail-at-end"],
            cwd=repo_dir, capture_output=True, text=True,
            timeout=3600, env=mvn_env
        )
        jar_count = len(glob.glob(os.path.join(repo_dir, "**/target/*.jar"), recursive=True))
        status    = "succeeded" if r.returncode == 0 else "partially succeeded (some modules failed)"
        print(f"      Build {status} ({jar_count} JARs)")
    except subprocess.TimeoutExpired:
        jar_count = len(glob.glob(os.path.join(repo_dir, "**/target/*.jar"), recursive=True))
        print(f"      Build timed out ({jar_count} JARs, proceeding with partial)")
    except FileNotFoundError:
        print(f"      Maven not found — run: sudo bash scripts/setup_jvms.sh")


# ---------------------------------------------------------------------------
# CLASSPATH RESOLUTION
# ---------------------------------------------------------------------------

def _get_mvn_classpath(module_dir, mvn_env):
    """Ask Maven to print the compile classpath for a module. Returns string or ''."""
    try:
        r = subprocess.run(
            ["mvn", "dependency:build-classpath", "-B", "-N",
             "-DincludeScope=compile",
             "-Dmdep.outputFile=/dev/stdout", "-q"],
            cwd=module_dir, capture_output=True, text=True,
            timeout=120, env=mvn_env
        )
        if r.returncode == 0 and r.stdout.strip():
            lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
            return max(lines, key=len) if lines else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def resolve_classpath(repo_name, repo_dir, java_home=None):
    """
    Build a colon-separated classpath for compiling and running EvoSuite.
    Triggers the one-time repo build on first call.
    """
    _build_repo_once(repo_name, repo_dir)

    jars = []

    # 1. Pre-downloaded per-repo deps
    deps_dir = os.path.join(PROJECT_ROOT, "tools", f"{repo_name}-deps")
    if os.path.isdir(deps_dir):
        jars.extend(glob.glob(os.path.join(deps_dir, "*.jar")))

    # 2. Generic camel-deps fallback
    camel_deps = os.path.join(PROJECT_ROOT, "tools", "camel-deps")
    if os.path.isdir(camel_deps) and repo_name == "camel":
        jars.extend(glob.glob(os.path.join(camel_deps, "*.jar")))

    # 3. Built artifacts in target/
    jars.extend(glob.glob(os.path.join(repo_dir, "**/target/*.jar"), recursive=True))

    # 4. target/dependency/ dirs
    for dep_dir in glob.glob(os.path.join(repo_dir, "**/target/dependency"), recursive=True):
        jars.extend(glob.glob(os.path.join(dep_dir, "*.jar")))

    # 5. ~/.m2 — repo's own group artifacts first, then general cache
    m2 = os.path.expanduser("~/.m2/repository")
    if os.path.isdir(m2):
        group_paths = {
            "camel":      "org/apache/camel",
            "activemq":   "org/apache/activemq",
            "oozie":      "org/apache/oozie",
            "falcon":     "org/apache/falcon",
            "deltaspike": "org/apache/deltaspike",
        }
        group = group_paths.get(repo_name)
        if group:
            group_dir = os.path.join(m2, group)
            if os.path.isdir(group_dir):
                jars.extend(glob.glob(os.path.join(group_dir, "**", "*.jar"), recursive=True))

        if len(jars) < 50:
            jars.extend(glob.glob(os.path.join(m2, "**", "*.jar"), recursive=True)[:2000])

    seen, unique = set(), []
    for j in jars:
        if j not in seen:
            seen.add(j)
            unique.append(j)
    return ":".join(unique) if unique else ""


# ---------------------------------------------------------------------------
# SUBMODULE DETECTION
# ---------------------------------------------------------------------------

def _extract_submodule(java_file_path, base_dir):
    """
    Derive the Maven submodule path from a Java source file path.

    e.g.  .../before/core/camel-core-reifier/src/main/java/... → core/camel-core-reifier
          .../before/src/main/java/...                          → '' (root module)
    """
    rel   = os.path.relpath(java_file_path, base_dir)
    parts = rel.replace("\\", "/").split("/")

    # Strip leading "src/" if the repo uses before/src/...
    if parts and parts[0] == "src":
        parts = parts[1:]

    # Find the src/main or src/test boundary
    for i, p in enumerate(parts):
        if p == "src" and i + 1 < len(parts) and parts[i + 1] in ("main", "test"):
            return "/".join(parts[:i]) if i > 0 else ""

    return None


# ---------------------------------------------------------------------------
# EVOSUITE TEST GENERATION — PER COMMIT
# ---------------------------------------------------------------------------

def gen_evosuite_for_commit(pair_dir, repo_name, repo_dir,
                            evo_jar, compile_java_home, evo_java_home,
                            sha, timeout=60):
    """
    Generate EvoSuite tests for one before/after commit pair.

    Flow:
      1. git checkout sha  — put the repo at the exact commit
      2. mvn install -pl submodule -am  — compile the submodule + its deps
      3. Verify the target .class file exists
      4. Build EvoSuite classpath via Maven
      5. Launch EvoSuite through a fake-JDK wrapper so that java.home
         points to the wrapper and client JVMs also get the required flags
      6. git clean -fdx  — restore the repo for the next commit

    Returns the test output directory on success, None on failure.
    """
    before_dir = os.path.join(pair_dir, "before")
    after_dir  = os.path.join(pair_dir, "after")

    bf, _af = find_primary_changed_java(before_dir, after_dir)
    if not bf:
        print("(no primary java file)", end=" ", flush=True)
        return None

    # Extract package / class / FQCN
    pkg = None
    with open(bf, errors="replace") as f:
        for line in f:
            m = re.match(r'\s*package\s+([\w.]+)\s*;', line)
            if m:
                pkg = m.group(1)
                break
    cls  = os.path.splitext(os.path.basename(bf))[0]
    fqcn = f"{pkg}.{cls}" if pkg else cls

    submodule = _extract_submodule(bf, before_dir)
    if submodule is None:
        print(f"(can't detect submodule for {os.path.basename(bf)})", end=" ", flush=True)
        return None

    module_dir  = os.path.join(repo_dir, submodule) if submodule else repo_dir
    classes_dir = os.path.join(module_dir, "target", "classes")

    mvn_env = (_make_mvn_env(_INSTALLED_JVMS[max(_INSTALLED_JVMS)])
               if _INSTALLED_JVMS else os.environ.copy())

    mvn_skip = [
        "-DskipTests", "-Dmaven.javadoc.skip=true",
        "-Dcheckstyle.skip=true", "-Denforcer.skip=true",
        "-Dspotless.check.skip=true", "-Dspotbugs.skip=true",
        "-Drat.skip=true",
        # Cross-compile to Java 11 so EvoSuite can load the bytecode
        "-Dmaven.compiler.source=11",
        "-Dmaven.compiler.target=11",
        "-Dmaven.compiler.release=11",
    ]

    try:
        # ── Step 1: checkout the commit ──────────────────────────────────
        subprocess.run(["git", "clean",    "-fdx"],
                       cwd=repo_dir, capture_output=True, timeout=120)
        subprocess.run(["git", "checkout", "--force", "."],
                       cwd=repo_dir, capture_output=True, timeout=30)
        r = subprocess.run(["git", "checkout", sha, "--force"],
                           cwd=repo_dir, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"(git checkout failed: {r.stderr[:80]})", end=" ", flush=True)
            return None

        if submodule and not os.path.isdir(module_dir):
            print(f"(submodule dir missing after checkout: {submodule})", end=" ", flush=True)
            return None

        # ── Step 2: compile the submodule ────────────────────────────────
        print(f"[{submodule or 'root'}] mvn...", end=" ", flush=True)

        pl_args = ["-pl", submodule, "-am"] if submodule else []
        r = subprocess.run(
            ["mvn", "install"] + pl_args + ["-B", "--fail-at-end"] + mvn_skip,
            cwd=repo_dir, capture_output=True, text=True,
            timeout=900, env=mvn_env
        )
        print(f"rc={r.returncode}", end=" ", flush=True)

        if r.returncode != 0:
            # Log errors and try a plain compile inside the module as fallback
            combined = (r.stdout or "") + "\n" + (r.stderr or "")
            errs = [l.strip() for l in combined.splitlines()
                    if "[ERROR]" in l
                    and not any(skip in l.lower() for skip in
                                ["re-run", "help 1", "full stack", "for more info",
                                 "after correcting", "resume the build"])
                    and l.strip() != "[ERROR]"]
            for e in errs[:3]:
                print(f"\n      {e[:150]}", end="", flush=True)

            if not (os.path.isdir(classes_dir) and os.listdir(classes_dir)):
                print("\n      retry: mvn compile in module...", end=" ", flush=True)
                r2 = subprocess.run(
                    ["mvn", "compile", "-B"] + mvn_skip,
                    cwd=module_dir, capture_output=True, text=True,
                    timeout=300, env=mvn_env
                )
                print(f"rc={r2.returncode}", end=" ", flush=True)

                if not (os.path.isdir(classes_dir) and os.listdir(classes_dir)):
                    print("(no classes produced)", end=" ", flush=True)
                    return None

        # ── Step 3: verify the target class exists ───────────────────────
        class_file = os.path.join(classes_dir, fqcn.replace(".", os.sep) + ".class")
        if not os.path.isfile(class_file):
            # Maybe it compiled to a different location — search for it
            hits = [p for p in
                    glob.glob(os.path.join(classes_dir, "**", cls + ".class"), recursive=True)]
            if hits:
                print(f"(found at {hits[0]} — unexpected path)", end=" ", flush=True)
            else:
                print(f"(class {cls}.class not in classes_dir)", end=" ", flush=True)
            return None

        # ── Step 4: build EvoSuite classpath ─────────────────────────────
        cp = _get_mvn_classpath(module_dir, mvn_env)
        if not cp:
            # Fallback: collect all target/classes dirs in the repo
            cp = ":".join(glob.glob(os.path.join(repo_dir, "**/target/classes"), recursive=True))

        evo_cp   = f"{classes_dir}:{cp}" if cp else classes_dir
        test_out = os.path.join(pair_dir, "evosuite_tests")
        os.makedirs(test_out, exist_ok=True)

        # ── Step 5: run EvoSuite ─────────────────────────────────────────
        #
        # THE PROBLEM with the previous "fake-JDK via launcher" approach:
        #   fake_jdk/bin/java is a bash script. When it exec's the real
        #   binary, the JVM introspects the real binary's path and sets
        #   java.home to the REAL JDK — not fake_jdk. So client processes
        #   spawn from the real JDK and never see our flags.
        #
        # THE FIX — two-part:
        #   Part A. Launch EvoSuite master with real java + --add-opens
        #           flags passed directly on the command line. Master gets
        #           the opens it needs immediately.
        #   Part B. Also pass -Djava.home=fake_jdk. This overrides
        #           System.getProperty("java.home") which EvoSuite reads
        #           when building the client ProcessBuilder command.
        #           Client processes therefore use fake_jdk/bin/java, which
        #           is our wrapper → real java + same flags. ✓

        evo_ver   = _version_of_home(evo_java_home) if evo_java_home else 8
        evo_flags = _evo_flags_for_version(evo_ver)
        real_java = (os.path.join(evo_java_home, "bin", "java")
                     if evo_java_home else "java")

        with tempfile.TemporaryDirectory(prefix="evo_") as tmpdir:
            # Write classpath to file to sidestep ARG_MAX
            cp_file = os.path.join(tmpdir, "evo_cp.txt")
            with open(cp_file, "w") as f:
                f.write(evo_cp)

            if evo_flags and evo_java_home:
                # Build fake JDK for client processes (Part B)
                fake_java    = _build_fake_jdk(evo_java_home, tmpdir, evo_flags)
                fake_jdk_dir = os.path.dirname(os.path.dirname(fake_java))  # tmpdir/fake_jdk
                flags_str    = " ".join(evo_flags)
                # Part A: real java + flags; Part B: -Djava.home override
                java_launch  = (f'"{real_java}" {flags_str}'
                                f' -Djava.home="{fake_jdk_dir}"')
                print(f"evo(Java{evo_ver}+opens+java.home={os.path.basename(fake_jdk_dir)})...",
                      end=" ", flush=True)
            else:
                # Java 8: no module system, no flags needed
                java_launch = f'"{real_java}"'
                print(f"evo(Java{evo_ver})...", end=" ", flush=True)

            script = os.path.join(tmpdir, "run_evo.sh")
            with open(script, "w") as f:
                f.write(
                    f'#!/bin/bash\n'
                    f'CP=$(cat "{cp_file}")\n'
                    f'{java_launch} -jar "{evo_jar}" '
                    f'-class "{fqcn}" '
                    f'-projectCP "$CP" '
                    f'-Dtest_dir="{test_out}" '
                    f'-Dsearch_budget={timeout} '
                    f'-Dassertion_strategy=ALL '
                    f'-Dminimize=true '
                    f'-criterion branch\n'
                )
            os.chmod(script, 0o755)

            try:
                evo_r = subprocess.run(
                    ["bash", script], capture_output=True, text=True,
                    timeout=timeout + 120
                )
                # Save full log for debugging
                evo_log_path = os.path.join(pair_dir, "evosuite_output.log")
                with open(evo_log_path, "w") as lf:
                    lf.write((evo_r.stdout or "") + "\n" + (evo_r.stderr or ""))

                # Surface key lines inline
                all_lines = ((evo_r.stdout or "") + (evo_r.stderr or "")).splitlines()
                important  = [l for l in all_lines
                              if any(k in l.lower() for k in
                                     ["error", "exception", "unsupported",
                                      "cannot", "wrote", "coverage"])]
                for line in important[-4:]:
                    print(f"\n      EVO: {line[:160]}", end="", flush=True)

            except subprocess.TimeoutExpired:
                print("(evo timeout)", end=" ", flush=True)

        test_files = glob.glob(os.path.join(test_out, "**", "*.java"), recursive=True)
        print(f"tests={len(test_files)}", end=" ", flush=True)
        return test_out if test_files else None

    finally:
        # ── Step 6: restore the repo ─────────────────────────────────────
        # git clean removes all target/ dirs so the next commit gets a clean build
        subprocess.run(["git", "clean", "-fdx"],
                       cwd=repo_dir, capture_output=True, timeout=120)


# ---------------------------------------------------------------------------
# PHASE ENTRY POINT
# ---------------------------------------------------------------------------

def phase_evosuite(config, progress, results_dir, commits_file, pairs_dir, java_home, evo_timeout):
    print("\n" + "=" * 60)
    print("PHASE 6: EVOSUITE TEST GENERATION")
    print("=" * 60)

    # Locate EvoSuite jar
    evo_jar = None
    for pattern in [os.path.join(PROJECT_ROOT, "tools", "evosuite*.jar")]:
        candidates = glob.glob(pattern)
        if candidates:
            evo_jar = candidates[0]
            break
    if not evo_jar:
        print("  EvoSuite jar not found in tools/ — skipping")
        return

    if not os.path.isfile(commits_file):
        print(f"  No commits file at {commits_file}")
        return

    with open(commits_file) as f:
        records = [json.loads(line) for line in f if line.strip()]

    print(f"  EvoSuite jar : {evo_jar}")
    if _INSTALLED_JVMS:
        print(f"  Installed JVMs: { ', '.join(f'Java {v}' for v in sorted(_INSTALLED_JVMS)) }")
    else:
        print(f"  Installed JVMs: none found in /usr/lib/jvm — using system default")
    print(f"  Strategy     : checkout commit → mvn compile submodule → fake-JDK EvoSuite")
    print(f"  Timeout      : {evo_timeout}s per commit")

    success, fail, skip = 0, 0, 0
    repo_original_heads: dict = {}

    for rec in records:
        pair_id   = rec["pair_id"]
        repo_name = rec.get("repo", "camel")
        sha       = rec["sha"]
        repo_dir  = os.path.join(PROJECT_ROOT, "data", repo_name)
        pair_dir  = os.path.join(pairs_dir, pair_id)

        if not os.path.isdir(pair_dir):
            skip += 1
            continue

        # Skip if tests already cached
        existing_tests = os.path.join(pair_dir, "evosuite_tests")
        if os.path.isdir(existing_tests):
            if glob.glob(os.path.join(existing_tests, "**", "*.java"), recursive=True):
                success += 1
                continue

        # Save original HEAD once per repo so we can restore at the end
        if repo_name not in repo_original_heads:
            try:
                r = subprocess.run(["git", "rev-parse", "HEAD"],
                                   cwd=repo_dir, capture_output=True, text=True, timeout=10)
                repo_original_heads[repo_name] = r.stdout.strip()
            except Exception:
                repo_original_heads[repo_name] = "HEAD"

        compile_home, evo_home = get_java_home_for_repo(repo_dir)
        effective_evo_home = java_home or evo_home   # caller override respected

        print(f"  [{pair_id}] {repo_name}/{sha[:7]}...", end=" ", flush=True)

        result = gen_evosuite_for_commit(
            pair_dir, repo_name, repo_dir,
            evo_jar, compile_home, effective_evo_home, sha, evo_timeout
        )

        if result:
            count = len(glob.glob(os.path.join(result, "**", "*.java"), recursive=True))
            print(f"OK ({count} files)")
            success += 1
        else:
            print("FAIL")
            fail += 1

    # Restore every repo to its original HEAD
    for repo_name, original_head in repo_original_heads.items():
        try:
            subprocess.run(
                ["git", "checkout", original_head, "--force"],
                cwd=os.path.join(PROJECT_ROOT, "data", repo_name),
                capture_output=True, timeout=30
            )
        except Exception:
            pass

    print(f"\n  EvoSuite: {success} OK, {fail} failed, {skip} skipped")
    progress["_evosuite_done"] = success
    save_progress(progress, config["name"], results_dir)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7: TRAINING (LoRA fine-tune)
# ═══════════════════════════════════════════════════════════════════════════

def build_training_dataset(commits_file, pairs_dir, output_dir):
    """Build HuggingFace-compatible training dataset from commit pairs."""
    os.makedirs(output_dir, exist_ok=True)
    dataset_file = os.path.join(output_dir, "train.jsonl")

    with open(commits_file) as f:
        records = [json.loads(line) for line in f if line.strip()]

    examples = []
    for rec in records:
        pair_id = rec["pair_id"]
        before_dir = os.path.join(pairs_dir, pair_id, "before")
        after_dir = os.path.join(pairs_dir, pair_id, "after")

        bf, af = find_primary_changed_java(before_dir, after_dir)
        if not bf or not af:
            continue

        with open(bf, errors="replace") as f:
            before_code = f.read()[:3000]
        with open(af, errors="replace") as f:
            after_code = f.read()[:3000]

        rtypes = rec.get("rminer_types", ["Extract Method"])
        prompt = (f"[INST] Refactor this Java code. Apply: {', '.join(rtypes)}.\n"
                  f"Return only the refactored Java code.\n\n```java\n{before_code}\n```[/INST]")
        completion = f"```java\n{after_code}\n```"

        examples.append({"text": f"{prompt}\n{completion}"})

    with open(dataset_file, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    print(f"  Built {len(examples)} training examples -> {dataset_file}")
    return dataset_file


def phase_train(config, progress, results_dir, commits_file, pairs_dir):
    print("\n" + "=" * 60)
    print("PHASE 7: TRAINING (LoRA fine-tune)")
    print("=" * 60)

    dataset_dir = os.path.join(results_dir, "finetune_dataset")
    dataset_file = build_training_dataset(commits_file, pairs_dir, dataset_dir)

    model_dir = os.path.join(results_dir, "lora_model")
    if os.path.isdir(model_dir) and os.listdir(model_dir):
        print(f"  Model already exists at {model_dir}, skipping training")
        progress["_trained"] = True
        save_progress(progress, config["name"], results_dir)
        return

    print(f"  Training with: python train.py --dataset {dataset_file} --output {model_dir}")
    r = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "train.py"),
         "--dataset", dataset_file, "--output", model_dir],
        cwd=PROJECT_ROOT, timeout=14400,  # 4h max
    )
    if r.returncode == 0:
        progress["_trained"] = True
        save_progress(progress, config["name"], results_dir)
    else:
        print("  Training failed — re-run with --phase train to retry")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 8: EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def phase_eval(config, progress, results_dir, commits_file, mode, pass_k, temperature):
    print("\n" + "=" * 60)
    print(f"PHASE 8: EVALUATION (mode={mode}, k={pass_k})")
    print("=" * 60)

    if not os.path.isfile(commits_file):
        print(f"  No commits file at {commits_file}")
        return

    # Point LoRA eval at this experiment's trained model
    lora_model_path = os.path.join(results_dir, "lora_model")
    if os.path.isdir(lora_model_path):
        os.environ["LORA_MODEL_PATH"] = lora_model_path
        print(f"  LoRA model: {lora_model_path}")

    # Use run_eval.py which handles the full pipeline invoke
    modes = ["ollama", "lora"] if mode == "both" else [mode]
    for m in modes:
        print(f"\n  --- Running {m} eval ---")
        cmd = [
            sys.executable, os.path.join(PROJECT_ROOT, "scripts", "run_eval.py"),
            "--commits", commits_file,
            "--mode", m,
            "--output", results_dir,
            "--pass-k", str(pass_k),
        ]
        if temperature is not None:
            cmd += ["--temperature", str(temperature)]

        env = os.environ.copy()
        if m == "lora" and os.path.isdir(lora_model_path):
            env["LORA_MODEL_PATH"] = lora_model_path
        r = subprocess.run(cmd, cwd=PROJECT_ROOT, timeout=14400, env=env)
        if r.returncode == 0:
            progress[f"_eval_{m}"] = True
            save_progress(progress, config["name"], results_dir)

    # Load and display results
    results_path = os.path.join(results_dir, "results.json")
    if os.path.isfile(results_path):
        with open(results_path) as f:
            results = json.load(f)
        print_results_table(results, config["name"])


# ═══════════════════════════════════════════════════════════════════════════
# RESULTS DISPLAY
# ═══════════════════════════════════════════════════════════════════════════

def compute_metrics(results_list):
    n = len(results_list)
    if n == 0:
        return {}
    compiled = [r for r in results_list if r.get("compile_ok")]
    valid_srr = [r["srr"] for r in results_list if r.get("srr") is not None]
    test_rates = [r["test_pass_rate"] for r in results_list if r.get("test_pass_rate") is not None]
    return {
        "n": n,
        "compile_rate": len(compiled) / n * 100,
        "compiled": len(compiled),
        "median_srr": statistics.median(valid_srr) if valid_srr else None,
        "mean_srr": statistics.mean(valid_srr) if valid_srr else None,
        "srr_positive_rate": (sum(1 for s in valid_srr if s > 0) / len(valid_srr) * 100
                              if valid_srr else None),
        "median_test_pass": statistics.median(test_rates) if test_rates else None,
        "n_with_tests": len(test_rates),
    }


def print_results_table(results, exp_name):
    header = f"\n{'='*70}\nRESULTS: {exp_name}\n{'='*70}"
    print(header)
    fmt = f"{'Approach':<20} {'N':>4} {'Compile%':>9} {'Med SRR':>9} {'Mean SRR':>9} {'SRR>0%':>8} {'Tests':>6}"
    print(fmt)
    print("-" * 70)

    for key, items in results.items():
        m = compute_metrics(items)
        cr = f"{m['compile_rate']:.0f}%" if m.get("compile_rate") is not None else "N/A"
        ms = f"{m['median_srr']:.1f}%" if m.get("median_srr") is not None else "N/A"
        mn = f"{m['mean_srr']:.1f}%" if m.get("mean_srr") is not None else "N/A"
        sp = f"{m['srr_positive_rate']:.0f}%" if m.get("srr_positive_rate") is not None else "N/A"
        tp = f"{m['n_with_tests']}" if m.get("n_with_tests") else "-"
        n = m.get("n", 0)
        print(f"{key:<20} {n:>4} {cr:>9} {ms:>9} {mn:>9} {sp:>8} {tp:>6}")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="Unified experiment runner — full pipeline for 3 experiments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Experiments:
  1  20 random Camel commits
  2  100 filtered Camel commits (high-signal structural)
  3  69 commits across 19 Apache library repos

Phases (comma-separated):
  clone    — Clone/ensure repos
  rminer   — Run RefactoringMiner
  scan     — DesigniteJava smell scan
  select   — Select commits for evaluation
  pairs    — Checkout before/after source pairs
  evosuite — Generate EvoSuite regression tests
  train    — Fine-tune LoRA model on selected data
  eval     — Run refactoring pipeline evaluation
""")

    p.add_argument("--exp", type=int, required=True, choices=[1, 2, 3],
                   help="Experiment number (1, 2, or 3)")
    p.add_argument("--phase", default=None,
                   help="Comma-separated phases to run (default: all)")
    p.add_argument("--mode", choices=["ollama", "lora", "both"], default="both",
                   help="Eval mode (default: both)")
    p.add_argument("--pass-k", type=int, default=1,
                   help="pass@k: generate k candidates per commit (default: 1)")
    p.add_argument("--temperature", type=float, default=None,
                   help="Sampling temperature (default: 0.2 for k=1, 0.8 for k>1)")

    # Custom intermediate paths
    p.add_argument("--commits-file", default=None,
                   help="Path for commits JSONL (default: results/experiment_N/commits.jsonl)")
    p.add_argument("--pairs-dir", default=None,
                   help="Path for before/after pairs (default: results/experiment_N/pairs)")
    p.add_argument("--results-dir", default=None,
                   help="Results output directory (default: results/experiment_N)")

    # EvoSuite / JVM
    p.add_argument("--java-home", default=None,
                   help="JAVA_HOME for EvoSuite (default: auto-detect)")
    p.add_argument("--evo-timeout", type=int, default=180,
                   help="EvoSuite search budget per commit in seconds (default: 180)")

    # Training
    p.add_argument("--skip-train", action="store_true",
                   help="Skip training phase (use existing model)")

    args = p.parse_args()

    config = EXPERIMENT_CONFIGS[args.exp]
    total_commits = sum(config["repos"].values())

    # Default paths
    results_dir = args.results_dir or os.path.join(PROJECT_ROOT, "results", f"experiment_{args.exp}")
    commits_file = args.commits_file or os.path.join(results_dir, "commits.jsonl")
    pairs_dir = args.pairs_dir or os.path.join(results_dir, "pairs")

    os.makedirs(results_dir, exist_ok=True)

    # Save experiment config
    with open(os.path.join(results_dir, "config.json"), "w") as f:
        json.dump({
            "experiment": args.exp,
            "config": config,
            "total_commits": total_commits,
            "mode": args.mode,
            "pass_k": args.pass_k,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f, indent=2)

    print(f"{'='*60}")
    print(f"EXPERIMENT {args.exp}: {config['description']}")
    print(f"Total commits: {total_commits}")
    print(f"Results: {results_dir}")
    print(f"Commits: {commits_file}")
    print(f"Pairs: {pairs_dir}")
    print(f"{'='*60}")

    phases = args.phase.split(",") if args.phase else ALL_PHASES
    progress = load_progress(args.exp, results_dir)
    t0 = time.time()

    for phase in phases:
        phase = phase.strip()
        if phase == "clone":
            phase_clone(config, progress, results_dir)
        elif phase == "rminer":
            phase_rminer(config, progress, results_dir)
        elif phase == "scan":
            phase_scan(config, progress, results_dir)
        elif phase == "select":
            phase_select(config, progress, results_dir, commits_file)
        elif phase == "pairs":
            phase_pairs(config, progress, results_dir, commits_file, pairs_dir)
        elif phase == "evosuite":
            phase_evosuite(config, progress, results_dir, commits_file, pairs_dir,
                           args.java_home, args.evo_timeout)
        elif phase == "train":
            if args.skip_train:
                print("\n  Skipping training (--skip-train)")
            else:
                phase_train(config, progress, results_dir, commits_file, pairs_dir)
        elif phase == "eval":
            phase_eval(config, progress, results_dir, commits_file,
                       args.mode, args.pass_k, args.temperature)
        else:
            print(f"  Unknown phase: {phase}")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"EXPERIMENT {args.exp} DONE in {elapsed/3600:.1f}h")
    print(f"Results: {results_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
