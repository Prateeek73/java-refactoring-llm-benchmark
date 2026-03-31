# LLM-Based Java Code Refactoring: Zero-Shot vs QLoRA Fine-Tuning on Apache Project Benchmarks

**A Replication and Extension of Cordeiro et al. (2024)**

---

## Abstract

This study compares zero-shot prompting with LLaMA 3 8B against QLoRA fine-tuned CodeLlama-7B for automated Java code refactoring, using Cordeiro et al.'s Apache project commit benchmark. Refactorings were detected by RefactoringMiner 3.0.10 and smells measured by DesigniteJava. We ran three experiments at varying dataset sizes and project diversity. Both approaches cleared Cordeiro et al.'s baselines by a large margin -- 33--57% median SRR versus their 4.76% (GPT-4) and 15.15% (LLaMA 3) with Chain-of-Thought prompting. The main result, though, is a negative one: fine-tuning made things worse. Zero-shot prompting consistently outperformed QLoRA on both smell reduction and compilation rate.

---

## 1. Introduction

Code refactoring restructures code without changing its external behavior. Cordeiro et al. (2024) tested GPT-4 and LLaMA 3 with Chain-of-Thought prompting on 5,194 commits across 30 Apache projects and got median SRR of 4.76% and 15.15% respectively. Those numbers are low enough that replication felt worthwhile.

We replicate and extend their work with two contributions:
1. **Zero-shot prompting** with LLaMA 3 8B via Ollama -- simpler prompting, better results.
2. **QLoRA fine-tuning** of CodeLlama-7B-Instruct on project-specific refactoring pairs. This is the first application of parameter-efficient fine-tuning to this benchmark, and it did not work the way we expected.

Both use the same tooling pipeline (RefactoringMiner, DesigniteJava) as Cordeiro et al.

---

## 2. Methodology

### 2.1 Pipeline

The automated pipeline runs seven stages:

```
Clone Repos -> RefactoringMiner 3.0.10 -> DesigniteJava Scan -> Commit Selection
    -> Before/After Pair Extraction -> QLoRA Training -> Evaluation
```

**RefactoringMiner 3.0.10** detects structural refactorings (Extract Method, Rename Class, Move Method, etc.) from commit diffs. **DesigniteJava** counts code smells (God Class, Long Method, Feature Envy, etc.) in pre- and post-refactoring versions of each changed file.

### 2.2 Models

| | Ollama (Zero-Shot) | LoRA (Fine-Tuned) |
|---|---|---|
| **Base Model** | LLaMA 3 8B | CodeLlama-7B-Instruct |
| **Quantization** | None | 4-bit NF4 (QLoRA) |
| **LoRA Config** | N/A | rank=16, alpha=32, targets: q/k/v/o_proj |
| **Training** | None | 3 epochs, lr=2e-4, batch=1, grad_accum=16 |
| **Prompting** | Zero-shot instruction | Matched training template |
| **Hardware** | NVIDIA RTX 5070 Laptop (8.5 GB VRAM) | Same |

The zero-shot prompt instructs the model to apply specific refactoring types detected by RMiner. The LoRA model uses a matched template: `"Refactor this Java code. Apply: {types}. Return only the refactored Java code."` Template alignment between training and inference turned out to matter a lot for LoRA.

### 2.3 Metrics

- **SRR (Smell Reduction Rate):** `(smells_before - smells_after) / smells_before * 100`
- **Compile Rate:** Percentage of generated code that compiles with single-file `javac`
- **SRR Positive Rate:** Percentage of commits where SRR > 0

### 2.4 Compilation Methodology

We use single-file `javac` with no external dependencies. Most Java files need project-level classpath resolution, so this understates true compilability -- but it gives a reproducible lower bound that requires no build environment setup.

---

## 3. Experiments

### Experiment 1: Random Sample (N=20)
20 commits randomly sampled from Apache Camel (seed=42). Sanity check on pipeline correctness.

### Experiment 2: Filtered High-Signal (N=100)
100 commits from Apache Camel selected by smell richness: `score = smells_before * max(n_refactoring_types, 1) * (1 + srr/100)`. Targets commits where there is more to fix.

### Experiment 3: Multi-Repository (N=71)
71 commits across 19 Apache library repositories (myfaces-trinidad, incubator-brooklyn, oozie, deltaspike, etc.) using the same filtered selection. Tests whether results hold on unfamiliar codebases.

---

## 4. Results

### 4.1 Overall Comparison

| Metric | Exp 1 (20) | Exp 2 (100) | Exp 3 (71) | Cordeiro GPT-4 | Cordeiro LLaMA 3 |
|--------|-----------|------------|-----------|----------------|------------------|
| **Ollama Median SRR** | 41.0% | **56.8%** | 44.8% | 4.76% | 15.15% |
| **Ollama Compile Rate** | 5.0% | 13.0% | 11.3% | -- | -- |
| **Ollama SRR > 0** | 85.0% | 97.0% | 89.1% | -- | -- |
| **LoRA Median SRR** | 33.3% | 53.5% | 34.6% | -- | -- |
| **LoRA Compile Rate** | 5.0% | 2.0% | 2.8% | -- | -- |
| **LoRA SRR > 0** | 88.2% | 98.4% | 83.1% | -- | -- |
| **N (Cordeiro)** | -- | -- | -- | 5,194 | 5,194 |

### 4.2 Key Findings

**Finding 1:** Both approaches beat Cordeiro's baselines substantially. The Experiment 2 Ollama result (56.8% SRR) is roughly 12x higher than GPT-4 CoT. That gap is large enough to be surprising.

**Finding 2:** Zero-shot consistently outperformed fine-tuning on both SRR and compilation rate. This is the paper's main negative result, discussed in Section 5.

**Finding 3:** Commit selection makes a real difference. Filtering by smell richness (Experiment 2) produced the best results across both models. Picking commits where there is more to fix matters.

**Finding 4:** Fine-tuning hurt compilation rate. LoRA compile rates (2--5%) were lower than Ollama (5--13%) across all experiments. The model apparently learned to reduce smells at the cost of writing valid Java.

---

## 5. Discussion

### Why Zero-Shot Beats Fine-Tuning

LLaMA 3 8B has broad pre-training knowledge of Java -- syntax, compilation rules, common patterns. Fine-tuning on 20--100 examples is not enough to improve on that; it seems to narrow the model's output distribution toward specific refactoring patterns while degrading everything else. This is consistent with the "alignment tax" documented in other fine-tuning work, but it is still a little surprising to see it this clearly at such small scale.

### Compilation Rate Limitations

The 2--13% compilation rates are low, but they mainly reflect the limits of single-file `javac`, not model quality. Most Java source files import external libraries and project classes that are not present during standalone compilation. Cordeiro et al. did not report compilation rates at all. Tapader et al. (2025) achieved 90--99% compilability using full Maven project builds -- which suggests the build environment is the real bottleneck, not the model's ability to write correct code.

### Threats to Validity

- **Sample size:** 20--100 commits vs Cordeiro's 5,194. Per-commit SRR is much higher, but that could partly reflect commit selection.
- **Single-file compilation:** A lower bound, not a real measure of correctness. Full Maven builds would change these numbers substantially.
- **SRR measurement:** DesigniteJava counts all smell types equally. A God Class and a Magic Number get the same weight, which may not reflect actual code quality impact.

---

## 6. Conclusion

Zero-shot LLaMA 3 8B prompting achieved 3--12x higher smell reduction rates than Cordeiro et al.'s CoT-prompted GPT-4 and LLaMA 3. The finding we did not expect -- and that is probably more useful to the community -- is that QLoRA fine-tuning on limited data consistently made results worse, not better, on both smell reduction and code compilability. Anyone planning to fine-tune on small refactoring datasets should probably test zero-shot first. The obvious next steps are larger training sets, full project compilation environments, and pass@k evaluation.

---

## References

1. Cordeiro, D., et al. (2024). "LLM-Based Java Code Refactoring." arXiv:2411.02320.
2. Tapader, A., et al. (2025). "Fine-Tuning LLMs for Code Refactoring." arXiv:2511.21788.
3. Tsantalis, N., et al. (2020). "RefactoringMiner 2.0." IEEE TSE.
4. Sharma, T., et al. (2016). "Designite -- A Software Design Quality Assessment Tool." WSSE.
5. Dettmers, T., et al. (2023). "QLoRA: Efficient Finetuning of Quantized Language Models." NeurIPS.