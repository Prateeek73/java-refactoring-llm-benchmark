# Experiment Plan — Java Refactoring Pipeline

**Date**: 2026-03-28
**Goal**: Run 3 experiments to evaluate LLM-based Java refactoring (Ollama zero-shot vs LoRA fine-tuned) with proper EvoSuite regression testing.

**Starting state**: Clean slate — no repos cloned, no data, no models. Everything runs from scratch.

---

## Experiments Overview

| Exp | Description | Commits | Source | Selection |
|-----|------------|---------|--------|-----------|
| 1 | 20 random Camel commits | 20 | apache/camel | Random (seed=42) |
| 2 | 100 filtered Camel commits | 100 | apache/camel | Ranked by smell richness |
| 3 | 69 commits across 19 library repos | 69 | 19 Apache repos | Filtered per repo |

### Experiment 3 — Per-Repo Commit Counts

| Repo | Commits |
|------|---------|
| incubator-brooklyn | 5 |
| incubator-druid | 4 |
| systemml | 2 |
| oozie | 4 |
| apex-malhar | 1 |
| ode | 4 |
| incubator-gobblin | 1 |
| servicecomb-pack | 1 |
| falcon | 1 |
| myfaces-extcdi | 5 |
| incubator-pinot | 1 |
| brooklyn-library | 2 |
| deltaspike | 2 |
| incubator-iotdb | 4 |
| apex-core | 2 |
| myfaces-trinidad | 25 |
| incubator-shardingsphere | 2 |
| incubator-dolphinscheduler | 2 |
| incubator-taverna-language | 3 |
| **Total** | **69** |

---

## Unified Script

All 3 experiments use a single script: `scripts/run_experiment.py`

```bash
# Full pipeline for each experiment:
python3 scripts/run_experiment.py --exp 1 --mode both
python3 scripts/run_experiment.py --exp 2 --mode both
python3 scripts/run_experiment.py --exp 3 --mode both
```

### Pipeline Phases (all run from scratch)

```
clone -> rminer -> scan -> select -> pairs -> evosuite -> train -> eval
```

Each phase is resumable and skips completed work on re-run.

---

## Execution Plan

### Pre-requisites (all confirmed)

1. Ollama running: `ollama serve` (port 11434 bound) with `llama3:8b` pulled
2. Java 17: `/usr/lib/jvm/java-17-openjdk-amd64`
3. Java 11: `/usr/lib/jvm/java-11-openjdk-amd64` (for EvoSuite)
4. Maven 3.9.14: `/opt/maven`
5. DesigniteJava built: `tools/DesigniteJava-src/target/classes/`
6. EvoSuite jar: `tools/evosuite-1.2.0.jar`

### Step 1: Run Experiment 1 (fastest, ~2-3h)

```bash
# 20 random Camel commits — good smoke test for full pipeline
python3 scripts/run_experiment.py --exp 1 --mode both
```

**What happens (all from scratch):**
1. **Clone**: Shallow clone of apache/camel (~1-2 min)
2. **RMiner**: Random sample commits, find structural refactorings (~10-30 min)
3. **Scan**: DesigniteJava smell analysis on ~60 candidates (~20 min)
4. **Select**: Randomly pick 20 structural commits (seed=42)
5. **Pairs**: Git checkout before/after source for 20 commits (~5 min)
6. **EvoSuite**: Generate regression tests per commit (~20 min)
7. **Train**: Fine-tune LoRA on 20 commits -> `results/experiment_1/lora_model/`
8. **Eval**: Run Ollama + LoRA on 20 commits, measure compile rate + SRR

```bash
# Check results:
cat results/experiment_1/results.json | python3 -m json.tool | head -50
```

### Step 2: Run Experiment 3 (medium, ~6-10h)

```bash
# 69 commits across 19 library repos — all from scratch
python3 scripts/run_experiment.py --exp 3 --mode both
```

**What happens:**
1. **Clone**: Shallow clone 19 repos (~30-60 min)
2. **RMiner**: Random sample per repo (~2-3h total)
3. **Scan**: DesigniteJava per repo (~1-2h)
4. **Select**: Pick specified count per repo (filtered by smell richness)
5. **Pairs**: Checkout 69 commit pairs (~15 min)
6. **EvoSuite**: Per-repo dependency resolution + test gen (~70 min)
7. **Train**: Fine-tune LoRA on 69 multi-repo commits -> `results/experiment_3/lora_model/`
8. **Eval**: Ollama + LoRA on 69 commits

### Step 3: Run Experiment 2 (longest, ~10-16h)

```bash
# 100 filtered Camel commits — largest experiment
python3 scripts/run_experiment.py --exp 2 --mode both
```

**What happens:**
- Clone: Reuses camel repo from Exp 1 (cached)
- RMiner: Reuses `camel_refs.json` from Exp 1 (cached)
- Scan: May need to scan more candidates (~300 for 3x buffer)
- Select: Top 100 by smell richness * structural diversity
- Train: Fine-tune LoRA on 100 commits -> `results/experiment_2/lora_model/`
- Eval: 100 commits x 2 modes

```bash
# Optional: pass@5 sampling
python3 scripts/run_experiment.py --exp 2 --mode ollama --pass-k 5
```

### Step 4: Compare Results

```bash
for exp in 1 2 3; do
  echo "=== Experiment $exp ==="
  python3 -c "
import json, statistics
r = json.load(open('results/experiment_$exp/results.json'))
for mode, items in r.items():
    compiled = [i for i in items if i.get('compile_ok')]
    srrs = [i['srr'] for i in items if i.get('srr') is not None]
    print(f'  {mode}: n={len(items)}, compile={len(compiled)/len(items)*100:.0f}%, '
          f'med_srr={statistics.median(srrs):.1f}% ({len(srrs)} valid)' if srrs else
          f'  {mode}: n={len(items)}, compile={len(compiled)/len(items)*100:.0f}%, no SRR')
"
done
```

---

## 3 LoRA Models

Each experiment trains its own LoRA adapter on different data:

| Experiment | Training Data | Model Path | Purpose |
|---|---|---|---|
| 1 | 20 random Camel commits | `results/experiment_1/lora_model/` | Baseline (small, single-repo) |
| 2 | 100 filtered Camel commits | `results/experiment_2/lora_model/` | Best single-repo (more data, high-signal) |
| 3 | 69 multi-repo commits | `results/experiment_3/lora_model/` | Generalization (cross-repo) |

Training uses QLoRA (4-bit CodeLlama-7B), checkpoints every 50 steps (keeps last 3), resumable with `--resume`.

---

## EvoSuite JVM Strategy

EvoSuite 1.2.0 requires careful JVM management:

### Problem
- Different repos target different Java versions (8, 11, 17)
- EvoSuite needs compiled `.class` files + project classpath
- Large classpaths can exceed ARG_MAX

### Solution (implemented in run_experiment.py)

1. **Java version detection**: Reads `pom.xml` for `maven.compiler.source` / `java.version`
2. **Dependency resolution**: Runs `mvn dependency:copy-dependencies` per repo
3. **Classpath construction**: Combines repo deps + tools deps + .m2 cache
4. **JVM selection**: Uses `--java-home` flag or auto-detects from `/usr/lib/jvm/`
5. **ARG_MAX workaround**: Writes classpath to file, passes via shell script

```bash
# Override Java home if needed:
python3 scripts/run_experiment.py --exp 3 --java-home /usr/lib/jvm/java-11-openjdk-amd64
```

### Fallback for dependency resolution failures
If Maven can't resolve deps (old repos, missing parent POMs):
1. Script tries tools/{repo}-deps/ first
2. Falls back to tools/camel-deps/ (generic Java deps)
3. Falls back to .m2 local repository

---

## Results Storage

```
results/
  experiment_1/
    config.json          # experiment config + metadata
    progress.json        # phase completion tracker
    commits.jsonl        # selected commits with metadata
    pairs/               # before/after source checkouts
      commit_001/{before,after}/src/
    finetune_dataset/    # training data
      train.jsonl
    lora_model/          # trained LoRA adapter for this experiment
    results.json         # eval results {ollama: [...], lora: [...]}
    metrics.json         # aggregate metrics
    partial_ollama.json  # incremental results (during eval)
    partial_lora.json
  experiment_2/
    ... (same structure, 100 commits)
  experiment_3/
    ... (same structure, 69 commits)
```

---

## Estimated Time Budget (from scratch)

| Phase | Exp 1 (20) | Exp 2 (100) | Exp 3 (69) |
|-------|-----------|------------|-----------|
| Clone | ~2min | cached | ~30-60min (19 repos) |
| RMiner | ~20min | cached | ~2-3h |
| Scan | ~20min | ~2h | ~1-2h |
| Select | <1min | <1min | <1min |
| Pairs | ~5min | ~20min | ~15min |
| EvoSuite | ~20min | ~100min | ~70min |
| Train | ~15min | ~1h | ~45min |
| Eval (Ollama) | ~30min | ~2.5h | ~1.5h |
| Eval (LoRA) | ~1h | ~5h | ~3h |
| **Total** | **~2-3h** | **~12-16h** | **~8-12h** |

---

## Running Order (Recommended)

1. **Experiment 1 first** — fastest, validates the full pipeline end-to-end
2. **Experiment 3 second** — multi-repo, tests generalization across projects
3. **Experiment 2 last** — largest, highest quality data for paper results

Note: Exp 2 reuses Camel clone + RMiner from Exp 1, so run Exp 1 first.

---

## Troubleshooting

### EvoSuite fails with "Could not find or load main class"
- Check Java version: `java -version` (need 11+)
- Verify classpath: check if `mvn dependency:copy-dependencies` succeeded
- Try: `--java-home /usr/lib/jvm/java-11-openjdk-amd64`

### RMiner timeout
- Some repos have large histories; the script caps at 500 random samples
- Re-run with `--phase rminer` to retry

### LoRA out of memory
- RTX 5070 Laptop has 8.5GB VRAM
- Ensure Ollama is stopped: `pkill ollama`
- Model uses 4-bit quantization, should fit

### Training crashes mid-way
- Checkpoints saved every 50 steps (last 3 kept)
- Resume: `python3 scripts/run_experiment.py --exp N --phase train` (auto-resumes)

### Scan shows 0 smells
- Check DesigniteJava is built: `ls tools/DesigniteJava-src/target/classes/`
- Check DESIGNITE_CP env var

### Pipeline compile failures
- Many repos have complex dependencies
- Compile rate of 30-50% is normal for cross-repo evaluation
- The retry logic (3 attempts) helps improve compile rate
