"""
run_eval.py — Run pipeline on all commits, compare zero-shot vs fine-tuned vs developer.

Usage:
  python scripts/run_eval.py                          # full eval, both modes
  python scripts/run_eval.py --limit 1 --mode ollama  # smoke test
  python scripts/run_eval.py --mode lora              # LoRA only
"""
import argparse, csv, json, os, statistics, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agents.pipeline import run_pipeline, run_pipeline_pass_k
from agents.refactor_agent import unload_model


def load_commits(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_baseline(path):
    """Load developer SRR baseline from CSV."""
    rows = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows[row["sha"]] = {
                    "smells_before": int(row["smells_before"] or 0),
                    "smells_after": int(row["smells_after"] or 0),
                    "srr": float(row["srr"]) if row["srr"] else None,
                }
            except (ValueError, KeyError):
                continue
    return rows


def run_mode(commits, mode, limit=None, start=0, output_dir="results", pass_k=1, temperature=0.8):
    """Run pipeline in given mode on commits."""
    os.environ["REFACTOR_MODE"] = mode
    results = []
    n = min(len(commits), limit) if limit else len(commits)
    suffix = f"_{mode}_k{pass_k}" if pass_k > 1 else f"_{mode}"
    partial_path = os.path.join(output_dir, f"partial{suffix}.json")

    # Load existing partial results if resuming
    if start > 0 and os.path.isfile(partial_path):
        with open(partial_path) as f:
            results = json.load(f)
        print(f"  Loaded {len(results)} existing results from {partial_path}")

    for i, c in enumerate(commits[:n], 1):
        if i - 1 < start:
            continue
        sha_short = c["sha"][:7]
        # Set repo for per-repo classpath resolution
        repo_name = c.get("repo", "camel")
        os.environ["EVAL_REPO"] = repo_name
        k_label = f"k={pass_k}" if pass_k > 1 else ""
        print(f"  [{mode} {i}/{n}] {repo_name}/{sha_short} {k_label}...", end=" ", flush=True)
        try:
            if pass_k > 1:
                result = run_pipeline_pass_k(
                    c["sha"], c["before_dir"], c["after_dir"], c["rminer_types"],
                    k=pass_k, temperature=temperature,
                )
            else:
                result = run_pipeline(c["sha"], c["before_dir"], c["after_dir"], c["rminer_types"],
                                     smells_before=c.get("smells_before", 0))
            entry = {
                "sha": sha_short,
                "compile_ok": result.get("compile_ok", False),
                "smells_before": result.get("smells_before", 0),
                "smells_after": result.get("smells_after", 0),
                "srr": result.get("srr"),
                "test_pass_rate": result.get("test_pass_rate"),
                "attempts": result.get("attempt", 0),
                "k": pass_k,
            }
            status = "OK" if entry["compile_ok"] else "FAIL"
            srr_str = f"SRR={entry['srr']:.1f}%" if entry["srr"] is not None else "SRR=N/A"
            extra = f", k={pass_k}" if pass_k > 1 else f", attempts={entry['attempts']}"
            print(f"{status} ({srr_str}{extra})")
        except Exception as e:
            print(f"ERROR: {e}")
            entry = {
                "sha": sha_short, "compile_ok": False, "smells_before": 0,
                "smells_after": 0, "srr": None, "test_pass_rate": None, "attempts": 0,
                "k": pass_k,
            }
        results.append(entry)

        # Save after each commit so we don't lose progress
        os.makedirs(output_dir, exist_ok=True)
        with open(partial_path, "w") as f:
            json.dump(results, f, indent=2)

    return results


def compute_metrics(results):
    n = len(results)
    if n == 0:
        return {}
    compiled = [r for r in results if r["compile_ok"]]
    valid_srr = [r["srr"] for r in results if r["srr"] is not None]
    test_rates = [r["test_pass_rate"] for r in results if r["test_pass_rate"] is not None]

    return {
        "n": n,
        "compile_rate": len(compiled) / n * 100,
        "compiled": len(compiled),
        "median_srr": statistics.median(valid_srr) if valid_srr else None,
        "mean_srr": statistics.mean(valid_srr) if valid_srr else None,
        "srr_positive_rate": sum(1 for s in valid_srr if s > 0) / len(valid_srr) * 100 if valid_srr else None,
        "median_test_pass": statistics.median(test_rates) if test_rates else None,
        "n_with_tests": len(test_rates),
    }


def print_table(all_metrics):
    header = f"{'Approach':<20} {'Compile%':>9} {'Med SRR':>9} {'Mean SRR':>9} {'SRR>0%':>8} {'N':>4}"
    sep = "-" * len(header)
    print(f"\n{sep}")
    print(header)
    print(sep)

    for name, m in all_metrics.items():
        cr = f"{m['compile_rate']:.0f}%" if m.get("compile_rate") is not None else "N/A"
        ms = f"{m['median_srr']:.1f}%" if m.get("median_srr") is not None else "N/A"
        mn = f"{m['mean_srr']:.1f}%" if m.get("mean_srr") is not None else "N/A"
        sp = f"{m['srr_positive_rate']:.0f}%" if m.get("srr_positive_rate") is not None else "N/A"
        n = m.get("n", "-")
        print(f"{name:<20} {cr:>9} {ms:>9} {mn:>9} {sp:>8} {n:>4}")

    print(sep)


def main():
    p = argparse.ArgumentParser(description="Evaluate refactoring pipeline.")
    p.add_argument("--commits", default="data/commits.jsonl")
    p.add_argument("--baseline", default="data/srr_baseline.csv")
    p.add_argument("--limit", type=int, default=None, help="Process only first N commits")
    p.add_argument("--mode", choices=["ollama", "lora", "both"], default="both",
                   help="Which mode(s) to run (default: both)")
    p.add_argument("--start", type=int, default=0, help="Skip first N commits (0-indexed)")
    p.add_argument("--output", default="results", help="Output directory")
    p.add_argument("--pass-k", type=int, default=1,
                   help="pass@k: generate k candidates per commit, pick best (default: 1)")
    p.add_argument("--temperature", type=float, default=None,
                   help="Sampling temperature (default: 0.2 for k=1, 0.8 for k>1)")
    args = p.parse_args()

    os.makedirs(args.output, exist_ok=True)
    commits = load_commits(args.commits)
    all_results = {}
    all_metrics = {}

    k = args.pass_k
    temp = args.temperature if args.temperature is not None else (0.8 if k > 1 else 0.2)
    k_suffix = f" (pass@{k}, T={temp})" if k > 1 else ""

    # ── Run Ollama (zero-shot) ──────────────────────────────────────
    if args.mode in ("ollama", "both"):
        print(f"\n=== Ollama zero-shot{k_suffix} ===")
        key = f"ollama_k{k}" if k > 1 else "ollama"
        all_results[key] = run_mode(commits, "ollama", args.limit, args.start, args.output, k, temp)
        label = f"Ollama pass@{k}" if k > 1 else "Ollama zero-shot"
        all_metrics[label] = compute_metrics(all_results[key])

    # ── Switch to LoRA ──────────────────────────────────────────────
    if args.mode in ("lora", "both"):
        if args.mode == "both":
            print("\n  Switching mode: killing Ollama, loading LoRA...")
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
            time.sleep(5)

        print(f"\n=== LoRA fine-tuned{k_suffix} ===")
        key = f"lora_k{k}" if k > 1 else "lora"
        all_results[key] = run_mode(commits, "lora", args.limit, args.start, args.output, k, temp)
        label = f"LoRA pass@{k}" if k > 1 else "LoRA fine-tuned"
        all_metrics[label] = compute_metrics(all_results[key])

        # Unload LoRA model
        unload_model()

    # ── Developer baseline ──────────────────────────────────────────
    if os.path.isfile(args.baseline):
        baseline = load_baseline(args.baseline)
        dev_srr = [v["srr"] for v in baseline.values() if v["srr"] is not None]
        all_metrics["Developer"] = {
            "n": len(baseline),
            "compile_rate": 100.0,
            "compiled": len(baseline),
            "median_srr": statistics.median(dev_srr) if dev_srr else None,
            "mean_srr": statistics.mean(dev_srr) if dev_srr else None,
            "srr_positive_rate": sum(1 for s in dev_srr if s > 0) / len(dev_srr) * 100 if dev_srr else None,
            "median_test_pass": None,
            "n_with_tests": 0,
        }

    # ── Cordeiro et al. baselines (from paper Table 2, StarCoder2-15B-Instruct) ──────
    all_metrics["Cordeiro StarCoder2 pass@1"] = {
        "n": 5194, "compile_rate": None, "compiled": None,
        "median_srr": 37.5, "mean_srr": 39.45,
        "srr_positive_rate": None, "median_test_pass": 26.8, "n_with_tests": 0,
    }
    all_metrics["Cordeiro StarCoder2 pass@5"] = {
        "n": 5194, "compile_rate": None, "compiled": None,
        "median_srr": 43.2, "mean_srr": 44.36,
        "srr_positive_rate": None, "median_test_pass": 55.4, "n_with_tests": 0,
    }
    all_metrics["Cordeiro Developers"] = {
        "n": 5194, "compile_rate": None, "compiled": None,
        "median_srr": 23.5, "mean_srr": 24.27,
        "srr_positive_rate": None, "median_test_pass": 100.0, "n_with_tests": 0,
    }

    # ── Save results (merge with existing) ─────────────────────────
    results_path = os.path.join(args.output, "results.json")
    existing = {}
    if os.path.isfile(results_path):
        with open(results_path) as f:
            existing = json.load(f)
    existing.update(all_results)
    with open(results_path, "w") as f:
        json.dump(existing, f, indent=2)

    with open(os.path.join(args.output, "metrics.json"), "w") as f:
        json.dump(all_metrics, f, indent=2, default=str)

    # ── Print comparison table ──────────────────────────────────────
    print_table(all_metrics)

    print(f"\nResults saved to {args.output}/results.json")
    print(f"Metrics saved to {args.output}/metrics.json")


if __name__ == "__main__":
    main()
