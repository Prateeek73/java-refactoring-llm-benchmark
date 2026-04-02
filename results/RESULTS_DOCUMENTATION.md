# Results Documentation

Complete documentation of the results directory, analysis notebooks, generated visualizations, and data artifacts.

Main report: [REPORT.md](../REPORT.md) | Project readme: [README.md](../README.md)

---

## Folder Structure

```
results/
├── experiment_1/              # Random (N=20, Apache Camel)
│   ├── config.json            # Experiment configuration + start timestamp
│   ├── results.json           # Per-commit results (ollama + lora arrays)
│   ├── metrics.json           # Aggregated metrics per model + baselines
│   ├── finetune_dataset/      # Training JSONL (train.jsonl)
│   └── lora_model/            # Trained LoRA adapter weights + adapter_config.json
├── experiment_2/              # Filtered (N=100, Apache Camel)
├── experiment_3/              # Mixed (N=71, 19 Apache repos)
├── model_analysis.json        # LoRA config + training data stats
├── lora_vs_zeroshot_analysis.json  # Aggregate paired comparison (191 commits)
└── analysis/plots/            # Visualizations + CSVs from output-analysis.ipynb
```

---

## Analysis Notebooks

| Notebook | Saves To | Sections |
|---|---|---|
| [output-analysis.ipynb](../notebooks/output-analysis.ipynb) | `results/analysis/plots/` | Smells before/after, SRR distributions, experiment comparison, LoRA vs zero-shot, paired analysis, timing |
| [model-analysis.ipynb](../notebooks/model-analysis.ipynb) | `notebooks/plots/` | LoRA config, training data lengths, refactoring types, experiment timing |
| [repo-analysis.ipynb](../notebooks/repo-analysis.ipynb) | `data/plots/` | Commits per repo, refactoring types, per-experiment smell distributions, summary stats |

---

## Notebook Analysis Details

### output-analysis.ipynb

Loads `results/experiment_*/results.json` and builds a unified DataFrame of all 382 evaluations (191 commits × 2 models). Each record has: `sha`, `experiment`, `model`, `smells_before`, `smells_after`, `srr`, `compile_ok`.

**Section 1 — Smells Before vs After:** Grouped bar charts (before/after × model) for each experiment with reduction % annotations. Shows Filtered experiment has highest total reduction (52.2% Ollama, 50.4% LoRA).

**Section 2 — Distributions:** 2×3 histogram grids for both `smells_after` counts and SRR values, with mean/median lines. Followed by a 1×3 experiment-wise comparison (Mean SRR, Median SRR, Compile Rate) as grouped bars.

**Section 3 — LoRA vs Zero-Shot:** Per-experiment pivot tables and grouped bars for Mean SRR and Compile Rate.

**Section 4 — Paired Comparison:** Merges ollama and lora results on same `(sha, experiment)`. Computes `srr_diff = lora - ollama` and win counts. Result: zero-shot wins 159/191 (83.2%), mean difference +0.23% favoring zero-shot.

**Timing Cell:** Loads `config.json` start timestamps and `results.json` file modification times to compute duration, evaluations per experiment, and time per evaluation. Plots a 1×3 chart (duration, time/eval, stacked eval counts by model).

### model-analysis.ipynb

**Section 1 — LoRA Configuration:** Loads `adapter_config.json` from each experiment's `lora_model/` directory. Displays config comparison table (base model, r, alpha, dropout, bias, task type). All experiments use identical hyperparameters: r=16, α=32, dropout=0.05, CodeLlama-7B-Instruct.

**Section 2 — Training Data:** Parses `train.jsonl` files using `[INST]...[/INST]` format extraction. Measures instruction length, response length, total length per sample. Random: 19 samples, Filtered: 100, Mixed: 71.

**Section 3 — Refactoring Types:** Extracts `Apply: {types}` from each instruction. Top types across experiments: Extract Method, Extract Variable, Move Class, Inline Variable.

**Section 4 — Timing:** Computes experiment duration and time per commit from config timestamps. 1×2 bar chart (total duration, avg time per commit).

### repo-analysis.ipynb

Loads all `data/*_scan.csv` files (one per Apache repository). Each CSV has: `sha`, `smells_before`, `smells_after`, `srr`, `n_structural`, `structural_types`.

**Commits per Repository:** Bar chart of commit counts across all repos. Camel dominates.

**Refactoring Types:** Parses `structural_types` column, counts all types globally. Top 15 shown as horizontal bar chart.

**Per-Experiment Smell Distributions:** For each experiment (Random, Filtered, Mixed), loads the `ollama` array from `results.json` for unique commit data. Plots 1×2 histograms (smells before, smells after) with mean/median lines. Also a combined 2×3 grid view.

**Summary Statistics:** Per-repository aggregation (commits, smells mean/std, SRR mean/std/median, structural counts). Saved as `repo_summary.csv`. Plus overall dataset stats (total repos, commits, mean SRR, positive SRR rate).

---

## Generated Plots

### `results/analysis/plots/` (from output-analysis.ipynb)

| Plot | Description |
|---|---|
| `smells_before_after_per_experiment.png` | Total smells before vs after, grouped by model, with reduction % |
| `smells_after_histogram.png` | 2×3 grid: smell count distributions after refactoring |
| `srr_histogram_per_experiment_model.png` | 2×3 grid: SRR distributions with mean/median lines |
| `experiment_wise_comparison.png` | 1×3: Mean SRR, Median SRR, Compile Rate grouped bars |
| `lora_vs_zeroshot_by_experiment.png` | Mean SRR + Compile Rate pivot comparison |
| `experiment_timing_output.png` | 1×3: Duration, time/eval, stacked eval counts |

### `notebooks/plots/` (from model-analysis.ipynb)

| Plot | Description |
|---|---|
| `experiment_timing.png` | 1×2: Total duration + avg time per commit |

### `data/plots/` (from repo-analysis.ipynb)

| Plot | Description |
|---|---|
| `commits_per_repo.png` | Commits per repository bar chart |
| `refactoring_types.png` | Top 15 refactoring types (horizontal bars) |
| `random_smells_histogram.png` | Random experiment: smells before/after histograms |
| `filtered_smells_histogram.png` | Filtered experiment: smells before/after histograms |
| `mixed_smells_histogram.png` | Mixed experiment: smells before/after histograms |
| `all_experiments_smells_histogram.png` | Combined 2×3 grid: before/after for all experiments |

---

## Generated CSVs

| File | Contents |
|---|---|
| `results/analysis/plots/detailed_summary.csv` | Per-experiment, per-model: n, compiled, compile rate, smells (sum, mean), SRR (mean, median, std) |
| `results/analysis/plots/experiment_summary.csv` | Per-experiment totals: commits, compile rate, SRR stats, reduction % |
| `data/plots/repo_summary.csv` | Per-repository: commits, smells before/after (mean, std), SRR (mean, std, median), structural counts |

---

## Key JSON Artifacts

| File | Contents |
|---|---|
| `results/model_analysis.json` | LoRA config, training sample counts, instruction/response lengths, refactoring type breakdown |
| `results/lora_vs_zeroshot_analysis.json` | 191 paired commits, per-experiment SRR, win analysis (zero-shot: 159, LoRA: 32) |
| `results/experiment_*/metrics.json` | Per-experiment metrics + Cordeiro et al. baselines (StarCoder2-15B: 37.5% pass@1 / 43.2% pass@5, Developers: 23.5%) |