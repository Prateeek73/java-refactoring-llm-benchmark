# LLM-Based Java Code Refactoring

**Zero-Shot vs QLoRA Fine-Tuning on Apache Project Benchmarks**

A replication and extension of [Cordeiro et al. (2024)](https://arxiv.org/abs/2411.02320) evaluating LLM-driven automated Java refactoring using smell reduction rate (SRR) as the primary metric.

---

## Results at a Glance

```
Median SRR (%) — Higher is Better

Ollama (Zero-Shot)  LoRA (Fine-Tuned)   Cordeiro Baselines
─────────────────── ─────────────────── ───────────────────

Exp 1 (20 random)
  Ollama   ████████████████████░░░░░░░░░░  41.0%
  LoRA     ████████████████░░░░░░░░░░░░░░  33.3%

Exp 2 (100 filtered)
  Ollama   ████████████████████████████░░  56.8%  ← BEST
  LoRA     ██████████████████████████░░░░  53.5%

Exp 3 (71 multi-repo)
  Ollama   ██████████████████████░░░░░░░░  44.8%
  LoRA     █████████████████░░░░░░░░░░░░░  34.6%

Cordeiro et al. Baselines (N=5,194)
  GPT-4    ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░   4.8%
  LLaMA 3  ███████░░░░░░░░░░░░░░░░░░░░░░░  15.2%
           ──────────────────────────────
           0%       25%       50%      75%
```

### Full Comparison Table

| Approach | Exp 1 (20 random) | Exp 2 (100 filtered) | Exp 3 (71 multi-repo) |
|----------|:-:|:-:|:-:|
| **Ollama Median SRR** | 41.0% | **56.8%** | 44.8% |
| **Ollama Compile Rate** | 5.0% | 13.0% | 11.3% |
| **LoRA Median SRR** | 33.3% | 53.5% | 34.6% |
| **LoRA Compile Rate** | 5.0% | 2.0% | 2.8% |
| Cordeiro GPT-4 (CoT) | 4.76% | 4.76% | 4.76% |
| Cordeiro LLaMA 3 (CoT) | 15.15% | 15.15% | 15.15% |

**Key takeaway:** Zero-shot LLaMA 3 8B achieves **3--12x higher SRR** than Cordeiro et al.'s GPT-4 and LLaMA 3 baselines. Fine-tuning (QLoRA) provides no improvement -- a notable negative result.

---

## Project Overview

This project implements an end-to-end pipeline for evaluating LLM-based Java code refactoring:

1. **Clone** Apache project repositories
2. **RefactoringMiner 3.0.10** -- detect structural refactorings in commit history
3. **DesigniteJava** -- count code smells before and after each refactoring
4. **Pair extraction** -- create (before, after) source code pairs
5. **QLoRA training** -- fine-tune CodeLlama-7B-Instruct with 4-bit quantization
6. **Evaluation** -- compare zero-shot vs fine-tuned smell reduction and compilation

### Models

| | Ollama (Zero-Shot) | LoRA (Fine-Tuned) |
|---|---|---|
| Base Model | LLaMA 3 8B | CodeLlama-7B-Instruct |
| Quantization | -- | 4-bit NF4 (QLoRA) |
| LoRA Config | N/A | rank=16, alpha=32 |
| Training | None | 3 epochs, lr=2e-4 |
| Hardware | RTX 5070 Laptop 8.5 GB | Same |

---

## Quick Start

### Prerequisites

- Python 3.10+
- NVIDIA GPU with 8+ GB VRAM
- [Ollama](https://ollama.ai) with `llama3:8b` model
- Java 8+ (for DesigniteJava and javac)

### Setup

```bash
git clone https://github.com/YOUR_USERNAME/refactor_project.git
cd refactor_project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gradio plotly pandas  # for dashboard
```

### Run Experiments

```bash
# Experiment 1: 20 random Camel commits
python3 scripts/run_experiment.py --exp 1 --mode both

# Experiment 2: 100 filtered Camel commits
python3 scripts/run_experiment.py --exp 2 --mode both

# Experiment 3: 71 multi-repo commits
python3 scripts/run_experiment.py --exp 3 --mode both
```

### Run Individual Phases

```bash
# Clone only
python3 scripts/run_experiment.py --exp 1 --phase clone

# Train LoRA model only
python3 scripts/run_experiment.py --exp 1 --phase train

# Evaluate with Ollama only
python3 scripts/run_experiment.py --exp 1 --phase eval --mode ollama
```

### Launch Dashboard

```bash
python3 app.py
# Opens at http://localhost:7860
```

The Gradio dashboard provides:
- Cross-experiment comparison charts (SRR, compile rate, radar)
- Per-experiment deep dive (violin plots, heatmaps, scatter plots, funnel charts)
- Step-by-step pipeline control (run any phase from the UI)
- Per-commit drill-down tables

---

## Experiments

### Experiment 1: Random Sample
- **Dataset:** 20 random commits from Apache Camel (seed=42)
- **Purpose:** Baseline validation on unbiased sample

### Experiment 2: Filtered High-Signal
- **Dataset:** 100 commits from Apache Camel, ranked by smell richness
- **Purpose:** Evaluate on high-quality refactoring opportunities

### Experiment 3: Multi-Repository
- **Dataset:** 71 commits across 19 Apache repos (myfaces-trinidad, incubator-brooklyn, oozie, etc.)
- **Purpose:** Test generalization across diverse codebases

---

## Project Structure

```
refactor_project/
  app.py                    # Gradio dashboard
  train.py                  # QLoRA fine-tuning script
  agents/
    pipeline.py             # LangGraph refactoring pipeline
    parse_agent.py          # Code parsing + smell counting
    refactor_agent.py       # LLM refactoring (Ollama + LoRA)
    validate_agent.py       # Compilation + SRR validation
  scripts/
    run_experiment.py       # Unified experiment runner (8 phases)
    run_eval.py             # Evaluation script with resume support
  tools/
    RefactoringMiner-3.0.10/
    DesigniteJava/
  results/
    experiment_1/           # 20 random Camel commits
    experiment_2/           # 100 filtered Camel commits
    experiment_3/           # 71 multi-repo commits
  data/                     # Cloned repos + intermediate data
```

---

## Key Findings

1. **Zero-shot LLaMA 3 8B outperforms fine-tuned models** across all metrics
2. **QLoRA fine-tuning is a negative result** -- degrades both SRR and compile rate
3. **Filtered commit selection matters** -- Exp 2 achieves best results by targeting smell-rich commits
4. **Single-file compilation is a bottleneck** -- 2-13% compile rate due to missing dependencies
5. **Both approaches massively outperform Cordeiro et al.** -- 3-12x improvement in median SRR

---

## References

- Cordeiro, D., et al. (2024). "LLM-Based Java Code Refactoring." [arXiv:2411.02320](https://arxiv.org/abs/2411.02320)
- Tapader, A., et al. (2025). "Fine-Tuning LLMs for Code Refactoring." [arXiv:2511.21788](https://arxiv.org/abs/2511.21788)
- Dettmers, T., et al. (2023). "QLoRA: Efficient Finetuning of Quantized Language Models." NeurIPS.

---

## License

This project is for academic research purposes (ASE 2026 submission).
