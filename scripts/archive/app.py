"""
app.py — Gradio UI for the Java refactoring pipeline.

Usage:
  python app.py                    # launches at localhost:7860
  python app.py --share            # public URL via Gradio tunnel
  python app.py --mode lora        # start in LoRA mode
"""
import argparse, csv, json, os, statistics, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

import gradio as gr
from lib import STRUCTURAL_TYPES

# Lazy imports — avoid loading heavy models at startup
_refactor_node = None
_refactor_k = None

def _get_refactor_node():
    global _refactor_node
    if _refactor_node is None:
        from agents.refactor_agent import refactor_node
        _refactor_node = refactor_node
    return _refactor_node

def _get_refactor_k():
    global _refactor_k
    if _refactor_k is None:
        from agents.refactor_agent import refactor_k_candidates
        _refactor_k = refactor_k_candidates
    return _refactor_k

PROJECT_ROOT = os.path.dirname(__file__)

# ── Default example ──────────────────────────────────────────────────
DEFAULT_CODE = """\
public class OrderProcessor {
    private List<Order> orders;
    private Database db;

    public void processOrders() {
        for (Order o : orders) {
            if (o.getStatus().equals("pending")) {
                double total = 0;
                for (Item i : o.getItems()) {
                    total += i.getPrice() * i.getQuantity();
                    if (i.getDiscount() > 0) {
                        total -= i.getPrice() * i.getQuantity() * i.getDiscount() / 100;
                    }
                }
                o.setTotal(total);
                o.setStatus("processed");
                db.save(o);
                System.out.println("Processed order " + o.getId() + " total=" + total);
            }
        }
    }
}"""

DEFAULT_TYPES = ["Extract Method", "Extract Variable"]


# ── Helpers ──────────────────────────────────────────────────────────

def _compile_check(java_code):
    """Quick compile check — returns (ok, error_msg)."""
    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(java_code)
        f.flush()
        try:
            r = subprocess.run(
                ["javac", "-proc:none", "-nowarn", f.name],
                capture_output=True, text=True, timeout=30,
            )
            return r.returncode == 0, r.stderr[:500] if r.stderr else ""
        except Exception as e:
            return False, str(e)
        finally:
            os.unlink(f.name)


def _load_results():
    """Load evaluation results from disk (Camel only, for backward compat)."""
    path = os.path.join(PROJECT_ROOT, "results", "results.json")
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _load_all_project_results():
    """Load results from all projects dynamically."""
    all_projects = {}
    # Camel (main results in results/results.json)
    camel_path = os.path.join(PROJECT_ROOT, "results", "results.json")
    if os.path.isfile(camel_path):
        with open(camel_path) as f:
            all_projects["camel"] = json.load(f)
    # All sub-project results in results/<name>/results.json
    results_dir = os.path.join(PROJECT_ROOT, "results")
    if os.path.isdir(results_dir):
        for entry in sorted(os.listdir(results_dir)):
            path = os.path.join(results_dir, entry, "results.json")
            if os.path.isfile(path) and entry != "results.json":
                with open(path) as f:
                    all_projects[entry] = json.load(f)
    return all_projects


def _load_baseline():
    """Load developer SRR baseline from CSV."""
    path = os.path.join(PROJECT_ROOT, "data", "srr_baseline.csv")
    if not os.path.isfile(path):
        return {}
    rows = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                rows[row["sha"]] = {
                    "smells_before": int(row["smells_before"] or 0),
                    "smells_after": int(row["smells_after"] or 0),
                    "srr": float(row["srr"]) if row["srr"] else None,
                }
            except (ValueError, KeyError):
                continue
    return rows


def _compute_approach_metrics(results):
    """Compute aggregate metrics for a list of per-commit results."""
    n = len(results)
    if n == 0:
        return {}
    compiled = [r for r in results if r.get("compile_ok")]
    srrs = [r["srr"] for r in results if r.get("srr") is not None]
    pos = [s for s in srrs if s > 0]
    return {
        "n": n,
        "compile_rate": len(compiled) / n * 100,
        "median_srr": statistics.median(srrs) if srrs else None,
        "mean_srr": statistics.mean(srrs) if srrs else None,
        "srr_positive_rate": len(pos) / len(srrs) * 100 if srrs else None,
    }


# ── Tab 1: Refactor Tool ────────────────────────────────────────────

def refactor_java(code, types, mode, k, temperature):
    """Main refactoring function with pass@k support."""
    if not code.strip():
        return "", "No input", ""

    os.environ["REFACTOR_MODE"] = mode
    k = int(k)
    temperature = float(temperature)
    rminer_types = types if types else ["Extract Method"]

    status_parts = [f"Mode: {mode}"]

    try:
        if k > 1:
            # pass@k: generate k candidates, pick best compiling one
            status_parts.append(f"pass@{k} (T={temperature})")
            candidates = _get_refactor_k()(code[:3000], rminer_types, k=k, temperature=temperature, mode=mode)
            if not candidates:
                return "", "LLM returned no candidates", ""

            # Pick best: first that compiles, else first non-empty
            best = candidates[0]
            best_compiles = False
            compiled_count = 0
            for c in candidates:
                ok, _ = _compile_check(c)
                if ok:
                    compiled_count += 1
                    if not best_compiles:
                        best = c
                        best_compiles = True
            refactored = best
            status_parts.append(f"Candidates: {len(candidates)}, compiled: {compiled_count}")
        else:
            state = {
                "before_code": code[:3000],
                "rminer_types": rminer_types,
                "attempt": 0,
            }
            result = _get_refactor_node()(state)
            refactored = result.get("refactored_code", "")
    except Exception as e:
        return "", f"Error: {e}", ""

    if not refactored.strip():
        return "", "LLM returned empty response", ""

    compile_ok, compile_err = _compile_check(refactored)
    compile_status = "Compiles" if compile_ok else f"Compile error: {compile_err[:200]}"
    status_parts.append(compile_status)

    before_lines = len(code.strip().splitlines())
    after_lines = len(refactored.strip().splitlines())
    delta = after_lines - before_lines
    sign = "+" if delta > 0 else ""
    status_parts.append(f"Lines: {before_lines} -> {after_lines} ({sign}{delta})")

    info = f"k={k}" if k > 1 else f"Attempts: 1"
    return refactored, "\n".join(status_parts), info


# ── Tab 2: Results Dashboard ────────────────────────────────────────

def load_dashboard():
    """Load results from ALL projects and return summary + per-project tables."""
    all_projects = _load_all_project_results()
    baseline = _load_baseline()

    # ── Cross-project summary table ──
    summary_rows = []
    for project, data in all_projects.items():
        for mode_key in ["ollama", "lora"]:
            if mode_key not in data:
                continue
            m = _compute_approach_metrics(data[mode_key])
            label = f"{project} / {'Ollama' if mode_key == 'ollama' else 'LoRA'}"
            summary_rows.append([
                label,
                project,
                "Ollama" if mode_key == "ollama" else "LoRA",
                m.get("n", 0),
                f"{m['compile_rate']:.0f}%" if m.get("compile_rate") is not None else "N/A",
                f"{m['median_srr']:.1f}%" if m.get("median_srr") is not None else "N/A",
                f"{m['mean_srr']:.1f}%" if m.get("mean_srr") is not None else "N/A",
                f"{m['srr_positive_rate']:.0f}%" if m.get("srr_positive_rate") is not None else "N/A",
            ])
        # Also check for pass@k keys
        for key in sorted(data.keys()):
            if key not in ("ollama", "lora"):
                m = _compute_approach_metrics(data[key])
                label = f"{project} / {key.replace('_', ' pass@')}"
                summary_rows.append([
                    label, project, key,
                    m.get("n", 0),
                    f"{m['compile_rate']:.0f}%" if m.get("compile_rate") is not None else "N/A",
                    f"{m['median_srr']:.1f}%" if m.get("median_srr") is not None else "N/A",
                    f"{m['mean_srr']:.1f}%" if m.get("mean_srr") is not None else "N/A",
                    f"{m['srr_positive_rate']:.0f}%" if m.get("srr_positive_rate") is not None else "N/A",
                ])

    # Developer baseline (Camel only)
    if baseline:
        dev_srrs = [v["srr"] for v in baseline.values() if v["srr"] is not None]
        pos = [s for s in dev_srrs if s > 0]
        summary_rows.append([
            "Developer (Camel)", "Apache Camel", "Developer",
            len(baseline), "100%",
            f"{statistics.median(dev_srrs):.1f}%" if dev_srrs else "N/A",
            f"{statistics.mean(dev_srrs):.1f}%" if dev_srrs else "N/A",
            f"{len(pos)/len(dev_srrs)*100:.0f}%" if dev_srrs else "N/A",
        ])

    # Published baselines
    summary_rows.append(["Cordeiro CoT+GPT-4", "Literature", "GPT-4", 5194, "N/A", "4.8%", "N/A", "N/A"])
    summary_rows.append(["Cordeiro CoT+LLaMA3", "Literature", "LLaMA3", 5194, "N/A", "15.2%", "N/A", "N/A"])

    # ── Per-commit table (all projects combined) ──
    per_commit = []
    for project, data in all_projects.items():
        ollama_list = data.get("ollama", [])
        lora_list = data.get("lora", [])
        ollama_map = {r["sha"]: r for r in ollama_list}
        lora_map = {r["sha"]: r for r in lora_list}
        all_shas = list(dict.fromkeys(
            [r["sha"] for r in ollama_list] + [r["sha"] for r in lora_list]
        ))
        for sha in all_shas:
            o = ollama_map.get(sha, {})
            l = lora_map.get(sha, {})
            o_srr = o.get("srr")
            l_srr = l.get("srr")
            o_comp = "Y" if o.get("compile_ok") else "N"
            l_comp = "Y" if l.get("compile_ok") else ("N" if l else "-")
            per_commit.append([
                project, sha,
                f"{o_srr:.1f}%" if o_srr is not None else "N/A",
                o_comp,
                f"{l_srr:.1f}%" if l_srr is not None else "N/A",
                l_comp,
            ])

    return summary_rows, per_commit


def refresh_dashboard():
    summary, per_commit = load_dashboard()
    return summary, per_commit


# ── Tab 3: Benchmark Comparison (charts) ────────────────────────────

def generate_charts():
    """Generate multi-project comparison charts."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None, None

    all_projects = _load_all_project_results()
    baseline = _load_baseline()

    # Chart 1: Compile rate by project (grouped bar)
    projects = []
    ollama_compile = []
    lora_compile = []
    for proj, data in all_projects.items():
        projects.append(proj.replace("commons-", "c-"))
        om = _compute_approach_metrics(data.get("ollama", []))
        lm = _compute_approach_metrics(data.get("lora", []))
        ollama_compile.append(om.get("compile_rate", 0) or 0)
        lora_compile.append(lm.get("compile_rate", 0) or 0)

    colors_ollama = "#4C78A8"
    colors_lora = "#F58518"

    fig1, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    if projects:
        x = np.arange(len(projects))
        width = 0.35
        ax1.bar(x - width/2, ollama_compile, width, label="Ollama", color=colors_ollama, alpha=0.85)
        ax1.bar(x + width/2, lora_compile, width, label="LoRA", color=colors_lora, alpha=0.85)
        ax1.set_ylabel("Compile Rate (%)", fontsize=12)
        ax1.set_title("Compile Rate by Project", fontsize=14, fontweight="bold")
        ax1.set_xticks(x)
        ax1.set_xticklabels(projects, fontsize=10)
        ax1.legend(fontsize=10)
        ax1.set_ylim(0, 105)
        for i, (oc, lc) in enumerate(zip(ollama_compile, lora_compile)):
            if oc > 0:
                ax1.text(i - width/2, oc + 1, f"{oc:.0f}%", ha="center", fontsize=9)
            if lc > 0:
                ax1.text(i + width/2, lc + 1, f"{lc:.0f}%", ha="center", fontsize=9)

    # Chart 2: SRR by project
    ollama_srr = []
    lora_srr = []
    for proj, data in all_projects.items():
        om = _compute_approach_metrics(data.get("ollama", []))
        lm = _compute_approach_metrics(data.get("lora", []))
        ollama_srr.append(om.get("median_srr", 0) or 0)
        lora_srr.append(lm.get("median_srr", 0) or 0)

    if projects:
        x = np.arange(len(projects))
        ax2.bar(x - width/2, ollama_srr, width, label="Ollama", color=colors_ollama, alpha=0.85)
        ax2.bar(x + width/2, lora_srr, width, label="LoRA", color=colors_lora, alpha=0.85)
        ax2.set_ylabel("Median SRR (%)", fontsize=12)
        ax2.set_title("Median SRR by Project", fontsize=14, fontweight="bold")
        ax2.set_xticks(x)
        ax2.set_xticklabels(projects, fontsize=10)
        ax2.legend(fontsize=10)
        ax2.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        for i, (os_, ls) in enumerate(zip(ollama_srr, lora_srr)):
            if os_ != 0:
                ax2.text(i - width/2, os_ + 0.5, f"{os_:.1f}%", ha="center", fontsize=9)
            if ls != 0:
                ax2.text(i + width/2, ls + 0.5, f"{ls:.1f}%", ha="center", fontsize=9)

        # Add Cordeiro baselines as horizontal lines
        ax2.axhline(y=4.76, color="#E45756", linestyle=":", alpha=0.7, label="Cordeiro GPT-4")
        ax2.axhline(y=15.15, color="#54A24B", linestyle=":", alpha=0.7, label="Cordeiro LLaMA3")
        ax2.legend(fontsize=9)

    fig1.tight_layout()

    # Second figure: overall aggregate comparison
    fig2, ax3 = plt.subplots(figsize=(10, 5))
    all_approaches = []
    all_compile = []
    all_srr_med = []

    for proj, data in all_projects.items():
        for mode_key in ["ollama", "lora"]:
            if mode_key in data:
                m = _compute_approach_metrics(data[mode_key])
                label = f"{proj.replace('commons-', 'c-')}\n{'Ollama' if mode_key == 'ollama' else 'LoRA'}"
                all_approaches.append(label)
                all_compile.append(m.get("compile_rate", 0) or 0)
                all_srr_med.append(m.get("median_srr", 0) or 0)

    if all_approaches:
        x = np.arange(len(all_approaches))
        colors = ["#4C78A8" if "Ollama" in a else "#F58518" for a in all_approaches]
        ax3.bar(x, all_compile, color=colors, alpha=0.85)
        ax3.set_ylabel("Compile Rate (%)", fontsize=12)
        ax3.set_title("Compile Rate: All Experiments", fontsize=14, fontweight="bold")
        ax3.set_xticks(x)
        ax3.set_xticklabels(all_approaches, fontsize=9, rotation=30, ha="right")
        ax3.set_ylim(0, 105)
        for i, v in enumerate(all_compile):
            ax3.text(i, v + 1, f"{v:.0f}%", ha="center", fontsize=9)

    fig2.tight_layout()

    return fig1, fig2


# ── Build UI ─────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--share", action="store_true")
    p.add_argument("--mode", default="ollama", choices=["ollama", "lora"])
    p.add_argument("--port", type=int, default=7860)
    args = p.parse_args()

    os.environ["REFACTOR_MODE"] = args.mode
    type_choices = sorted(STRUCTURAL_TYPES)

    css = """
    .main-title { text-align: center; margin-bottom: 0.5em; }
    .metric-box { padding: 1em; border-radius: 8px; text-align: center; }
    """

    with gr.Blocks(title="RefactorLLM — Java Refactoring Pipeline",
                    theme=gr.themes.Soft(), css=css) as demo:

        gr.Markdown(
            "# RefactorLLM: Agentic Java Refactoring Pipeline\n"
            "LangGraph-based pipeline comparing zero-shot vs LoRA fine-tuned CodeLlama "
            "for automated code smell reduction. *Built for ASE 2026.*",
            elem_classes=["main-title"]
        )

        with gr.Tabs():
            # ── TAB 1: Refactor Tool ─────────────────────────────
            with gr.Tab("Refactor Tool"):
                gr.Markdown("### Paste Java code and refactor it with LLM")
                with gr.Row():
                    with gr.Column(scale=1):
                        code_input = gr.Textbox(
                            label="Input Java Code",
                            lines=22, value=DEFAULT_CODE,
                        )
                        types_dropdown = gr.Dropdown(
                            choices=type_choices, multiselect=True,
                            value=DEFAULT_TYPES, label="Refactoring Types"
                        )
                        with gr.Row():
                            mode_radio = gr.Radio(
                                ["ollama", "lora"], value=args.mode,
                                label="Inference Mode",
                                info="ollama = zero-shot LLaMA 3 | lora = fine-tuned CodeLlama-7B"
                            )
                        with gr.Row():
                            k_slider = gr.Slider(
                                minimum=1, maximum=10, step=1, value=1,
                                label="pass@k (candidates)",
                                info="k=1: single generation | k>1: generate k, pick best compiling"
                            )
                            temp_slider = gr.Slider(
                                minimum=0.1, maximum=1.5, step=0.1, value=0.8,
                                label="Temperature",
                                info="Higher = more diverse candidates (0.2 for k=1, 0.8 for k>1)"
                            )
                        run_btn = gr.Button("Refactor", variant="primary", size="lg")

                    with gr.Column(scale=1):
                        code_output = gr.Textbox(
                            label="Refactored Code", lines=22,
                        )
                        with gr.Row():
                            status_output = gr.Textbox(label="Status", lines=3)
                            meta_output = gr.Textbox(label="Info", lines=3)

                run_btn.click(
                    refactor_java,
                    inputs=[code_input, types_dropdown, mode_radio, k_slider, temp_slider],
                    outputs=[code_output, status_output, meta_output],
                )

            # ── TAB 2: Results Dashboard ─────────────────────────
            with gr.Tab("Results Dashboard"):
                gr.Markdown("### Multi-Project Evaluation Results")
                refresh_btn = gr.Button("Refresh Results", variant="secondary")

                gr.Markdown("#### Cross-Project Summary")
                summary_table = gr.Dataframe(
                    headers=["Experiment", "Project", "Mode", "N", "Compile %",
                             "Median SRR", "Mean SRR", "SRR > 0 %"],
                    label="All Experiments",
                    interactive=False,
                )

                gr.Markdown("#### Per-Commit Results (All Projects)")
                commit_table = gr.Dataframe(
                    headers=["Project", "SHA", "Ollama SRR", "Compile (O)",
                             "LoRA SRR", "Compile (L)"],
                    label="Per-Commit Results",
                    interactive=False,
                )

                refresh_btn.click(
                    refresh_dashboard,
                    outputs=[summary_table, commit_table],
                )

                # Auto-load on tab render (disabled — click Refresh instead)
                # demo.load(refresh_dashboard, outputs=[summary_table, commit_table])

            # ── TAB 3: Charts ────────────────────────────────────
            with gr.Tab("Benchmark Charts"):
                gr.Markdown("### Visual Comparison")
                chart_btn = gr.Button("Generate Charts", variant="secondary")

                srr_chart = gr.Plot(label="SRR Comparison")
                scatter_chart = gr.Plot(label="Ollama vs LoRA (Per-Commit)")

                chart_btn.click(generate_charts, outputs=[srr_chart, scatter_chart])

            # ── TAB 4: About / Pipeline Info ─────────────────────
            with gr.Tab("Pipeline Architecture"):
                gr.Markdown("""
### Pipeline Architecture

```
START -> Parse Agent -> Refactor Agent -> Validate Agent -> [retry if compile fails, up to 3x] -> END
```

**Parse Agent** reads the Java source, identifies the primary changed file via diff analysis,
and counts pre-existing code smells using DesigniteJava.

**Refactor Agent** generates refactored code using either:
- **Ollama mode**: Zero-shot prompting of LLaMA 3 (8B) via local Ollama server
- **LoRA mode**: Fine-tuned CodeLlama-7B with LoRA adapter trained on 143 real refactoring commits

**Validate Agent** compiles the output, re-counts smells, computes SRR (Smell Reduction Rate),
and optionally runs EvoSuite regression tests.

**pass@k Sampling** generates k candidates at higher temperature, then selects the best
compiling candidate (or best SRR if multiple compile). This improves compile rates significantly.

---

### Metrics

| Metric | Definition |
|--------|-----------|
| **SRR** | (smells_before - smells_after) / smells_before * 100 |
| **Compile Rate** | % of commits where refactored code compiles |
| **SRR > 0 %** | % of commits with positive smell reduction |

---

### Datasets

| Project | Type | Commits | Description |
|---------|------|---------|-------------|
| **Apache Camel** | Large-scale | 35 | Enterprise integration framework |
| **Commons Lang** | Library | 9 | Java utility classes |
| **Commons IO** | Library | 15 | I/O utility library |
| **Google Gson** | Library | 15 | JSON serialization |

- **Identification**: RefactoringMiner 3.0.10 detects structural refactorings in commit history
- **Smell Detection**: DesigniteJava (46 smell types)
- **Training Data**: 143 commit pairs for LoRA fine-tuning (from Apache Camel)

---

### References

1. Cordeiro et al. (2024) — *An Empirical Study on Code Refactoring Capability of LLMs* (TOSEM)
2. Wu et al. (2024) — *iSMELL: Assembling LLMs with Expert Toolsets* (ASE 2024)
3. Oueslati et al. (2025) — *RefAgent: Multi-agent LLM-based Refactoring Framework*
4. SWE-Refactor Benchmark (2025) — 1,099 Java refactorings, 9 LLMs evaluated
5. Sharma et al. (2024) — *DesigniteJava 2.0* (MSR 2024)
""")

        gr.Markdown("---\n*RefactorLLM — ASE 2026 | LangGraph + CodeLlama + LoRA*")

    demo.launch(
        server_name="0.0.0.0",
        server_port=args.port,
        share=args.share,
    )


if __name__ == "__main__":
    main()
