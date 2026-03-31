"""
run_all_repos.py — Master automation: clone, RMiner, scan, eval for all 30 Cordeiro repos.

Processes 1% of refactoring commits per repo with both Ollama and LoRA modes.
Fully resumable — skips completed steps on re-run.

Usage:
  python scripts/run_all_repos.py                    # full run
  python scripts/run_all_repos.py --phase clone      # clone only
  python scripts/run_all_repos.py --phase rminer     # RMiner only
  python scripts/run_all_repos.py --phase scan       # DesigniteJava scan only
  python scripts/run_all_repos.py --phase eval       # eval only (ollama+lora)
  python scripts/run_all_repos.py --phase eval --eval-mode ollama  # ollama only
  python scripts/run_all_repos.py --skip-repos camel,commons-lang  # skip already done
"""
import argparse, csv, json, math, os, random, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(__file__))
from lib import (STRUCTURAL_TYPES, copy_all_java_src, count_smells,
                 find_changed_files, run_designite, default_dj_cp)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# ── All 30 Cordeiro et al. repos ──────────────────────────────────
REPOS = {
    "camel":                        {"gh": "apache/camel",                          "ref_commits": 760,  "total_commits": 71671},
    "activemq":                     {"gh": "apache/activemq",                       "ref_commits": 546,  "total_commits": 11821},
    "accumulo":                     {"gh": "apache/accumulo",                       "ref_commits": 483,  "total_commits": 12599},
    "james-project":                {"gh": "apache/james-project",                  "ref_commits": 798,  "total_commits": 15629},
    "openmeetings":                 {"gh": "apache/openmeetings",                   "ref_commits": 181,  "total_commits": 3716},
    "jmeter":                       {"gh": "apache/jmeter",                         "ref_commits": 554,  "total_commits": 18266},
    "skywalking":                   {"gh": "apache/skywalking",                     "ref_commits": 166,  "total_commits": 8041},
    "jclouds":                      {"gh": "apache/jclouds",                        "ref_commits": 699,  "total_commits": 10878},
    "qpid-jms-amqp-0-x":           {"gh": "apache/qpid-jms-amqp-0-x",             "ref_commits": 374,  "total_commits": 7631},
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
    "incubator-taverna-language":    {"gh": "apache/incubator-taverna-language",     "ref_commits": 158,  "total_commits": 3560},
    "hadoop-ozone":                 {"gh": "apache/ozone",                          "ref_commits": 264,  "total_commits": 7916},
}

RMINER = os.path.join(PROJECT_ROOT, "tools", "RefactoringMiner-3.0.10", "bin", "RefactoringMiner")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "results", "all_repos_progress.json")


def load_progress():
    if os.path.isfile(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def save_progress(progress):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def sample_size(ref_commits):
    """1% of refactoring commits, minimum 1."""
    return max(1, int(ref_commits * 0.01))


# ── Phase 1: Clone ───────────────────────────────────────────────

def clone_repo(name, info):
    """Clone repo with blobless filter for speed."""
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"  [{name}] Already cloned")
        return True
    url = f"https://github.com/{info['gh']}.git"
    print(f"  [{name}] Cloning {url} ...")
    try:
        r = subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", url, repo_dir],
            capture_output=True, text=True, timeout=600
        )
        if r.returncode != 0:
            print(f"  [{name}] Clone FAILED: {r.stderr[:200]}")
            return False
        # Checkout HEAD (just files, no full history blobs)
        subprocess.run(["git", "-C", repo_dir, "checkout"], capture_output=True, timeout=120)
        print(f"  [{name}] Cloned OK")
        return True
    except subprocess.TimeoutExpired:
        print(f"  [{name}] Clone TIMEOUT")
        return False


def phase_clone(repos, progress):
    print("\n" + "="*60)
    print("PHASE 1: CLONE REPOS")
    print("="*60)
    for name, info in repos.items():
        if progress.get(name, {}).get("cloned"):
            continue
        ok = clone_repo(name, info)
        if ok:
            progress.setdefault(name, {})["cloned"] = True
            save_progress(progress)


# ── Phase 2: RefactoringMiner ────────────────────────────────────

def run_rminer(name, info, progress):
    """Run RMiner on recent commits to find structural refactorings."""
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    if os.path.isfile(refs_json):
        print(f"  [{name}] RMiner output exists")
        return True

    repo_dir = os.path.join(PROJECT_ROOT, "data", name)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"  [{name}] Not cloned, skip")
        return False

    needed = sample_size(info["ref_commits"])
    # Estimate how many commits to scan: need ~3x buffer
    ref_rate = info["ref_commits"] / max(info["total_commits"], 1)
    # Structural rate is usually ~30-50% of all refactorings
    structural_rate = ref_rate * 0.4
    commits_to_scan = min(info["total_commits"], max(500, int(needed * 5 / max(structural_rate, 0.01))))
    commits_to_scan = min(commits_to_scan, 2000)  # cap at 2000

    print(f"  [{name}] Running RMiner on last {commits_to_scan} commits (need {needed} samples)...")

    # Get start SHA
    r = subprocess.run(
        ["git", "-C", repo_dir, "log", f"--skip={commits_to_scan}", "-1", "--format=%H"],
        capture_output=True, text=True, timeout=30
    )
    start_sha = r.stdout.strip()
    if not start_sha:
        # Repo has fewer commits, use first commit
        r = subprocess.run(
            ["git", "-C", repo_dir, "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True, text=True, timeout=30
        )
        start_sha = r.stdout.strip().split("\n")[0]

    r = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30
    )
    end_sha = r.stdout.strip()

    try:
        r = subprocess.run(
            [RMINER, "-bc", repo_dir, start_sha, end_sha, "-json", refs_json],
            capture_output=True, text=True, timeout=3600  # 1 hour max
        )
        if r.returncode != 0 and not os.path.isfile(refs_json):
            print(f"  [{name}] RMiner FAILED: {r.stderr[:200]}")
            return False
        print(f"  [{name}] RMiner done")
        return True
    except subprocess.TimeoutExpired:
        print(f"  [{name}] RMiner TIMEOUT (1h) - using partial results if any")
        return os.path.isfile(refs_json)


def phase_rminer(repos, progress):
    print("\n" + "="*60)
    print("PHASE 2: REFACTORING MINER")
    print("="*60)
    for name, info in repos.items():
        if progress.get(name, {}).get("rminer"):
            continue
        if not progress.get(name, {}).get("cloned"):
            continue
        ok = run_rminer(name, info, progress)
        if ok:
            progress.setdefault(name, {})["rminer"] = True
            save_progress(progress)


# ── Phase 3: Filter + DesigniteJava Scan ─────────────────────────

def filter_and_scan(name, info, progress):
    """Filter structural refactorings and scan with DesigniteJava."""
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    structural_json = os.path.join(PROJECT_ROOT, "data", f"{name}_structural.json")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    if os.path.isfile(scan_csv):
        print(f"  [{name}] Scan CSV exists")
        return True

    if not os.path.isfile(refs_json):
        print(f"  [{name}] No RMiner output, skip")
        return False

    # Load and filter
    with open(refs_json) as f:
        data = json.load(f)
    commits = data.get("commits", [])

    structural = []
    for c in commits:
        types = [r["type"] for r in c.get("refactorings", []) if r["type"] in STRUCTURAL_TYPES]
        if types:
            structural.append(c)

    if not structural:
        print(f"  [{name}] No structural refactorings found in {len(commits)} commits")
        # Save empty structural file and scan
        with open(structural_json, "w") as f:
            json.dump({"commits": []}, f)
        with open(scan_csv, "w") as f:
            f.write("sha,smells_before,smells_after,srr,n_structural,structural_types\n")
        return True

    # Save filtered
    with open(structural_json, "w") as f:
        json.dump({"commits": structural}, f, indent=2)
    print(f"  [{name}] {len(structural)} structural refactoring commits from {len(commits)} total")

    # Pick sample to scan (scan more than needed to have selection)
    needed = sample_size(info["ref_commits"])
    to_scan = structural[:min(len(structural), needed * 3)]

    dj_cp = default_dj_cp()

    # Write CSV header
    with open(scan_csv, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, ["sha", "smells_before", "smells_after",
                                           "srr", "n_structural", "structural_types"])
        writer.writeheader()

    scanned = 0
    for i, c in enumerate(to_scan, 1):
        sha = c["sha1"]
        sha7 = sha[:7]
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
                               capture_output=True, timeout=30)
            if r.returncode != 0:
                print(f"  [{name} {i}/{len(to_scan)}] {sha7} checkout FAIL")
                continue
            copy_all_java_src(repo_dir, before_src)

            subprocess.run(["git", "-C", repo_dir, "checkout", sha, "--", "."],
                           capture_output=True, timeout=30)
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

            tag = f"{srr:.1f}%" if srr is not None else "N/A"
            print(f"  [{name} {i}/{len(to_scan)}] {sha7} SRR={tag} smells={sb}->{sa}")
            scanned += 1

        except Exception as e:
            print(f"  [{name} {i}/{len(to_scan)}] {sha7} ERROR: {e}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Reset repo
    subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=30)

    print(f"  [{name}] Scanned {scanned} commits")
    return True


def phase_scan(repos, progress):
    print("\n" + "="*60)
    print("PHASE 3: DESIGNITEJAVA SCAN")
    print("="*60)
    for name, info in repos.items():
        if progress.get(name, {}).get("scanned"):
            continue
        if not progress.get(name, {}).get("rminer"):
            continue
        ok = filter_and_scan(name, info, progress)
        if ok:
            progress.setdefault(name, {})["scanned"] = True
            save_progress(progress)


# ── Phase 4: Setup eval pairs ────────────────────────────────────

def setup_pairs(name, info):
    """Create before/after pairs and commits.jsonl for eval."""
    scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
    refs_json = os.path.join(PROJECT_ROOT, "data", f"{name}_refs.json")
    commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")
    pairs_dir = os.path.join(PROJECT_ROOT, "data", f"{name}_pairs")
    repo_dir = os.path.join(PROJECT_ROOT, "data", name)

    if os.path.isfile(commits_jsonl):
        with open(commits_jsonl) as f:
            n = sum(1 for line in f if line.strip())
        if n > 0:
            print(f"  [{name}] commits.jsonl exists ({n} commits)")
            return True

    if not os.path.isfile(scan_csv):
        print(f"  [{name}] No scan CSV, skip")
        return False

    # Load scan results, pick those with smells
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
        # If no smells found, use any scanned commits
        with open(scan_csv) as f:
            all_rows = list(csv.DictReader(f))
        selected_shas = {r["sha"] for r in all_rows[:needed]}

    if not selected_shas:
        print(f"  [{name}] No commits to select")
        return False

    # Load RMiner data for refactoring types
    commits_by_sha = {}
    if os.path.isfile(refs_json):
        with open(refs_json) as f:
            data = json.load(f)
        for c in data.get("commits", []):
            commits_by_sha[c["sha1"]] = c

    # Checkout pairs
    if os.path.isdir(pairs_dir):
        shutil.rmtree(pairs_dir)

    jsonl_records = []
    for i, sha in enumerate(selected_shas, 1):
        out = os.path.join(pairs_dir, f"commit_{i:03d}")
        os.makedirs(os.path.join(out, "before"), exist_ok=True)
        os.makedirs(os.path.join(out, "after"), exist_ok=True)

        subprocess.run(["git", "-C", repo_dir, "checkout", sha + "~1", "--", "."],
                       capture_output=True, timeout=30)
        copy_all_java_src(repo_dir, os.path.join(out, "before", "src"))

        subprocess.run(["git", "-C", repo_dir, "checkout", sha, "--", "."],
                       capture_output=True, timeout=30)
        copy_all_java_src(repo_dir, os.path.join(out, "after", "src"))

        # Get refactoring types
        c = commits_by_sha.get(sha, {})
        rtypes = list(set(r["type"] for r in c.get("refactorings", [])
                         if r["type"] in STRUCTURAL_TYPES))
        if not rtypes:
            rtypes = list(set(r["type"] for r in c.get("refactorings", [])))[:3]
        if not rtypes:
            rtypes = ["Extract Method"]  # fallback

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

    # Reset repo
    subprocess.run(["git", "-C", repo_dir, "checkout", "HEAD", "--", "."],
                   capture_output=True, timeout=30)

    with open(commits_jsonl, "w") as f:
        for r in jsonl_records:
            f.write(json.dumps(r) + "\n")

    print(f"  [{name}] Created {len(jsonl_records)} eval pairs")
    return True


def phase_setup(repos, progress):
    print("\n" + "="*60)
    print("PHASE 4: SETUP EVAL PAIRS")
    print("="*60)
    for name, info in repos.items():
        if progress.get(name, {}).get("pairs_ready"):
            continue
        if not progress.get(name, {}).get("scanned"):
            continue
        ok = setup_pairs(name, info)
        if ok:
            progress.setdefault(name, {})["pairs_ready"] = True
            save_progress(progress)


# ── Phase 5: Evaluation ──────────────────────────────────────────

def run_eval_for_repo(name, info, mode, progress):
    """Run eval pipeline for one repo in one mode."""
    commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")
    output_dir = os.path.join(PROJECT_ROOT, "results", name)

    if not os.path.isfile(commits_jsonl):
        print(f"  [{name}] No commits.jsonl, skip")
        return False

    os.makedirs(output_dir, exist_ok=True)

    # Check if results exist
    results_path = os.path.join(output_dir, "results.json")
    if os.path.isfile(results_path):
        with open(results_path) as f:
            existing = json.load(f)
        if mode in existing:
            print(f"  [{name}] {mode} results exist ({len(existing[mode])} entries)")
            return True

    print(f"  [{name}] Running {mode} eval...")
    try:
        env = os.environ.copy()
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        r = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "run_eval.py"),
             "--commits", commits_jsonl,
             "--mode", mode,
             "--output", output_dir],
            cwd=PROJECT_ROOT,
            timeout=7200,  # 2 hour max per repo
            capture_output=True, text=True,
        )
        print(r.stdout[-500:] if r.stdout else "")
        if r.stderr:
            print(f"  [{name}] stderr: {r.stderr[-200:]}")
        return True
    except subprocess.TimeoutExpired:
        print(f"  [{name}] Eval TIMEOUT (2h)")
        return True  # partial results saved
    except Exception as e:
        print(f"  [{name}] Eval ERROR: {e}")
        return False


def phase_eval(repos, progress, eval_mode="both"):
    print("\n" + "="*60)
    print(f"PHASE 5: EVALUATION ({eval_mode})")
    print("="*60)
    modes = ["ollama", "lora"] if eval_mode == "both" else [eval_mode]
    for mode in modes:
        print(f"\n--- Mode: {mode} ---")
        for name, info in repos.items():
            key = f"eval_{mode}"
            if progress.get(name, {}).get(key):
                continue
            if not progress.get(name, {}).get("pairs_ready"):
                continue
            ok = run_eval_for_repo(name, info, mode, progress)
            if ok:
                progress.setdefault(name, {})["eval_" + mode] = True
                save_progress(progress)


# ── Phase 6: Enrich results with scan data ───────────────────────

def enrich_all(repos):
    print("\n" + "="*60)
    print("PHASE 6: ENRICH RESULTS WITH SCAN DATA")
    print("="*60)
    for name in repos:
        results_path = os.path.join(PROJECT_ROOT, "results", name, "results.json")
        scan_csv = os.path.join(PROJECT_ROOT, "data", f"{name}_scan.csv")
        commits_jsonl = os.path.join(PROJECT_ROOT, "data", f"{name}_commits.jsonl")

        if not os.path.isfile(results_path):
            continue

        scan_data = {}
        if os.path.isfile(scan_csv):
            with open(scan_csv) as f:
                for row in csv.DictReader(f):
                    sha7 = row["sha"][:7]
                    scan_data[sha7] = {
                        "smells_before": int(row.get("smells_before", 0) or 0),
                        "smells_after": int(row.get("smells_after", 0) or 0),
                        "srr": float(row["srr"]) if row.get("srr") else None,
                    }

        commits_data = {}
        if os.path.isfile(commits_jsonl):
            with open(commits_jsonl) as f:
                for line in f:
                    if line.strip():
                        c = json.loads(line)
                        sha7 = c["sha"][:7]
                        commits_data[sha7] = c

        with open(results_path) as f:
            results = json.load(f)

        updated = 0
        for mode_key, items in results.items():
            for item in items:
                sha7 = item.get("sha", "")[:7]
                scan = scan_data.get(sha7, {})
                commit = commits_data.get(sha7, {})
                if item.get("smells_before", 0) == 0:
                    sb = scan.get("smells_before") or commit.get("smells_before", 0)
                    if sb > 0:
                        item["smells_before"] = sb
                        sa = item.get("smells_after", 0)
                        item["srr"] = (sb - sa) / sb * 100 if sb > 0 else None
                        updated += 1

        with open(results_path, "w") as f:
            json.dump(results, f, indent=2)
        if updated:
            print(f"  [{name}] Enriched {updated} entries")


# ── Summary ──────────────────────────────────────────────────────

def print_summary(repos, progress):
    print("\n" + "="*70)
    print(f"{'Repo':<30} {'Clone':>6} {'RMiner':>7} {'Scan':>6} {'Pairs':>6} {'Ollama':>7} {'LoRA':>6}")
    print("-"*70)
    total_commits = 0
    for name, info in repos.items():
        p = progress.get(name, {})
        needed = sample_size(info["ref_commits"])
        total_commits += needed
        row = (
            f"{name:<30}"
            f" {'OK' if p.get('cloned') else '--':>6}"
            f" {'OK' if p.get('rminer') else '--':>7}"
            f" {'OK' if p.get('scanned') else '--':>6}"
            f" {'OK' if p.get('pairs_ready') else '--':>6}"
            f" {'OK' if p.get('eval_ollama') else '--':>7}"
            f" {'OK' if p.get('eval_lora') else '--':>6}"
        )
        print(row)
    print("-"*70)
    print(f"Total 1% sample commits: {total_commits}")
    print("="*70)


# ── Main ─────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Master automation for all 30 repos.")
    p.add_argument("--phase", choices=["clone", "rminer", "scan", "setup", "eval", "enrich", "all"],
                   default="all", help="Run specific phase (default: all)")
    p.add_argument("--eval-mode", choices=["ollama", "lora", "both"], default="both")
    p.add_argument("--skip-repos", default="", help="Comma-separated repos to skip")
    p.add_argument("--only-repos", default="", help="Comma-separated repos to process (default: all)")
    p.add_argument("--summary", action="store_true", help="Print progress summary only")
    args = p.parse_args()

    skip = set(args.skip_repos.split(",")) if args.skip_repos else set()
    only = set(args.only_repos.split(",")) if args.only_repos else set()

    repos = {k: v for k, v in REPOS.items()
             if k not in skip and (not only or k in only)}

    progress = load_progress()

    if args.summary:
        print_summary(REPOS, progress)
        return

    start = time.time()
    phases = ["clone", "rminer", "scan", "setup", "eval", "enrich"] if args.phase == "all" else [args.phase]

    for phase in phases:
        if phase == "clone":
            phase_clone(repos, progress)
        elif phase == "rminer":
            phase_rminer(repos, progress)
        elif phase == "scan":
            phase_scan(repos, progress)
        elif phase == "setup":
            phase_setup(repos, progress)
        elif phase == "eval":
            phase_eval(repos, progress, args.eval_mode)
        elif phase == "enrich":
            enrich_all(repos)

    elapsed = time.time() - start
    print(f"\nTotal time: {elapsed/3600:.1f} hours")
    print_summary(REPOS, progress)


if __name__ == "__main__":
    main()
