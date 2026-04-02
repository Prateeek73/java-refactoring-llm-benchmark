# LLM-Based Java Code Refactoring: Zero-Shot vs QLoRA Fine-Tuning on Apache Project Benchmarks

**A Replication and Extension of Cordeiro et al. (2024)**

## Abstract

This study compares zero-shot LLaMA 3 8B against QLoRA fine-tuned CodeLlama-7B for automated Java code refactoring on Apache project commits. Both approaches achieved 33--57% median SRR, comparable to Cordeiro et al.'s StarCoder2-15B (37.5--43.2%) despite using smaller 7B/8B models. The main result is negative: fine-tuning made things worse. Zero-shot consistently outperformed QLoRA on both smell reduction (51.6% vs 43.1% median SRR) and compilation rate (12% vs 3%).

## 1. Introduction

Cordeiro et al. (2024) evaluated StarCoder2-15B-Instruct on 5,194 commits across 30 Apache Java projects, achieving 37.5% median SRR (pass@1) up to 43.2% (pass@5) vs 23.5% for developers. Their work established SRR as the key metric for LLM-driven refactoring evaluation.

Tapader et al. (2025) fine-tuned GPT-3.5-Turbo for multilingual refactoring, achieving 94.78% compilability and 99.99% correctness for Java at 10-shot. However, they did not measure SRR, used only 10 samples, and evaluated with full IDE builds -- making direct comparison difficult.

We extend this work with: (1) **zero-shot prompting** with LLaMA 3 8B via Ollama, and (2) **QLoRA fine-tuning** of CodeLlama-7B-Instruct -- the first application of parameter-efficient fine-tuning to this benchmark.

## 2. Methodology

Full analysis notebooks: [output-analysis.ipynb](notebooks/output-analysis.ipynb), [model-analysis.ipynb](notebooks/model-analysis.ipynb), [repo-analysis.ipynb](notebooks/repo-analysis.ipynb). Results documentation: [RESULTS_DOCUMENTATION.md](results/RESULTS_DOCUMENTATION.md).

### 2.1 Pipeline

```
Clone Repos → RefactoringMiner 3.0.10 → DesigniteJava Scan → Commit Selection
    → Before/After Pair Extraction → QLoRA Training → Evaluation
```

### 2.2 Models

| | Ollama (Zero-Shot) | LoRA (Fine-Tuned) |
|---|---|---|
| **Base Model** | LLaMA 3 8B | CodeLlama-7B-Instruct-hf |
| **Quantization** | None | 4-bit NF4 (QLoRA) |
| **LoRA Config** | N/A | r=16, α=32, dropout=0.05, targets: q/k/v/o_proj |
| **Training** | None | 3 epochs, lr=2e-4, batch=1, grad_accum=16 |
| **Hardware** | NVIDIA RTX 5070 Laptop (8.5 GB VRAM) | Same |

### 2.3 Metrics

- **SRR:** $(smells_{before} - smells_{after}) / smells_{before} \times 100$
- **Compile Rate:** % of generated code compiling with single-file `javac` (lower bound -- no external deps)
- **SRR Positive Rate:** % of commits where $SRR > 0$

## 3. Experiments

All experiments ran March 30, 2026; each commit evaluated with both models.

- **Exp 1 — Random (N=20):** Random Camel commits (seed=42). Sanity check. Training: 19 samples.
- **Exp 2 — Filtered (N=100):** Camel commits ranked by smell richness. Training: 100 samples.
- **Exp 3 — Multi-Repo (N=71):** 71 commits across 19 Apache repos (myfaces-trinidad 25, oozie 4, incubator-brooklyn 5, etc.). Tests generalization. Training: 71 samples.

## 4. Results

### 4.1 Per-Experiment Comparison

| Metric | Exp 1 (N=20) | Exp 2 (N=100) | Exp 3 (N=71) |
|--------|:---:|:---:|:---:|
| **Ollama Median SRR** | 40.0% | **56.8%** | 44.9% |
| **Ollama Compile Rate** | 5.0% | **13.0%** | 11.3% |
| **LoRA Median SRR** | 33.3% | 53.5% | 34.6% |
| **LoRA Compile Rate** | 5.0% | 2.0% | 2.8% |

### 4.2 Aggregate (191 Paired Commits)

| Metric | Zero-Shot | QLoRA | Δ |
|---|:---:|:---:|:---:|
| **Median SRR** | **51.6%** | 43.1% | +8.5 |
| **Mean SRR** | **51.4%** | 49.1% | +2.3 |
| **Compile Rate** | **11.5%** (22/191) | 2.6% (5/191) | +8.9pp |
| **Total Smell Reduction** | **45.7%** | 43.9% | +1.8pp |

### 4.3 Paired Win Analysis

| Outcome | Count | % |
|---|---:|---:|
| **Zero-shot wins** | **159** | 83.2% |
| LoRA wins | 32 | 16.8% |

### 4.4 Comparison to Cordeiro et al.

| Approach | Model Size | N | Median SRR |
|---|---|---:|---:|
| Cordeiro zero-shot (pass@1) | StarCoder2-15B | 5,194 | 37.5% |
| Cordeiro pass@5 | StarCoder2-15B | 5,194 | 43.2% |
| Cordeiro Developers | — | 5,194 | 23.5% |
| **Ours: Zero-Shot** | **LLaMA 3 8B** | 191 | **51.6%** |
| **Ours: QLoRA** | **CodeLlama-7B** | 191 | **43.1%** |

Zero-shot (51.6%) exceeds StarCoder2 pass@5 (43.2%) despite being half the model size. QLoRA (43.1%) matches their best result. Both exceed the developer baseline (23.5%).

### 4.5 Key Findings

1. **Competitive with larger models.** Exp 2 Ollama (56.8%) exceeds StarCoder2-15B pass@5 (43.2%) by 13.6pp with a model half the size.
2. **Zero-shot beats fine-tuning.** Ollama won on 83% of paired commits across all experiments.
3. **Commit selection matters.** Filtering by smell richness (Exp 2) produced 56.8% vs 40.0% (random) median SRR.
4. **Fine-tuning hurt compilation.** LoRA: 5/191 compiled. Zero-shot: 22/191.

## 5. Analysis

SRR variance was high (σ = 28--44%) across experiments. Filtered commits (Exp 2) showed the lowest variance (σ ≈ 29), suggesting smell-rich commits produce more consistent results. Training data was uniform (~3,100 char instructions, ~3,000 char responses), possibly too homogeneous to teach diverse refactoring strategies.

## 6. Discussion

**Why zero-shot wins:** LLaMA 3 8B's broad pre-training covers Java syntax, compilation, and common patterns. Fine-tuning on 19--100 examples narrows the output distribution toward specific patterns while degrading general capability. The per-commit margin is small (mean +0.23% SRR), but zero-shot wins consistently (83%), suggesting a systematic shift rather than catastrophic failure -- consistent with the "alignment tax" in fine-tuning literature.

**Compilation rates** (2--13%) reflect single-file `javac` limits, not model quality. Cordeiro et al. achieved 28--57% pass rates with EvoSuite + Maven. Tapader et al. got 90--99% with full project builds. The build environment is the bottleneck.

**Threats to validity:** 191 commits vs Cordeiro's 5,194 -- higher SRR may partly reflect commit selection bias. Single-file compilation understates correctness. DesigniteJava weights all smells equally.

## 7. Conclusion

Zero-shot LLaMA 3 8B achieved 51.6% median SRR across 191 commits, exceeding Cordeiro et al.'s StarCoder2-15B pass@5 (43.2%) at half the model size. QLoRA fine-tuning on limited data (19--100 samples) consistently made results worse on both SRR (43.1% vs 51.6%) and compilability (2.6% vs 11.5%). Zero-shot won 83% of paired evaluations.

Next steps: larger training sets (1,000+ samples), full Maven/Gradle compilation, pass@k with $k > 1$, and weighted smell scoring.

## References

1. Cordeiro, J., Noei, S., & Zou, Y. (2024). "An Empirical Study on the Code Refactoring Capability of Large Language Models." ACM TOSEM. arXiv:2411.02320.
2. Tapader, A., et al. (2025). "Fine-Tuning LLMs for Code Refactoring." arXiv:2511.21788.
3. Tsantalis, N., et al. (2020). "RefactoringMiner 2.0." IEEE TSE.
4. Sharma, T., et al. (2016). "Designite — A Software Design Quality Assessment Tool." WSSE.
5. Dettmers, T., et al. (2023). "QLoRA: Efficient Finetuning of Quantized Language Models." NeurIPS.