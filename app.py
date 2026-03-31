"""
app.py — Gradio dashboard for LLM-based Java Refactoring experiments.

Usage:
  source .venv/bin/activate
  pip install gradio plotly pandas
  python app.py
"""
import json, os, subprocess, statistics, threading
import gradio as gr
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

RESULTS_DIR = "results"
EXPERIMENTS = {
    "Experiment 1": {
        "dir": "experiment_1",
        "label": "20 Random Camel Commits",
        "desc": "Random sample of 20 commits from Apache Camel",
        "exp_num": 1,
    },
    "Experiment 2": {
        "dir": "experiment_2",
        "label": "100 Filtered Camel Commits",
        "desc": "100 high-signal structural refactoring commits from Apache Camel",
        "exp_num": 2,
    },
    "Experiment 3": {
        "dir": "experiment_3",
        "label": "71 Multi-Repo Commits",
        "desc": "71 commits across 19 Apache library repositories",
        "exp_num": 3,
    },
}

CORDEIRO_BASELINES = {
    "Cordeiro CoT+GPT-4": {"median_srr": 4.76, "n": 5194},
    "Cordeiro CoT+LLaMA 3": {"median_srr": 15.15, "n": 5194},
}

COLORS = {
    "Ollama Zero-Shot": "#636EFA",
    "LoRA Fine-Tuned": "#EF553B",
    "Cordeiro CoT+GPT-4": "#00CC96",
    "Cordeiro CoT+LLaMA 3": "#AB63FA",
}

PIPELINE_STEPS = [
    {"id": "clone", "label": "1. Clone Repos", "desc": "Clone Apache project repositories"},
    {"id": "rminer", "label": "2. RefactoringMiner", "desc": "Detect structural refactorings"},
    {"id": "scan", "label": "3. DesigniteJava Scan", "desc": "Count code smells before/after"},
    {"id": "select", "label": "4. Select Commits", "desc": "Choose commits for evaluation"},
    {"id": "pairs", "label": "5. Extract Pairs", "desc": "Checkout before/after source pairs"},
    {"id": "train", "label": "6. Build Dataset & Train", "desc": "Fine-tune LoRA model"},
    {"id": "eval_ollama", "label": "7. Evaluate (Ollama)", "desc": "Zero-shot evaluation"},
    {"id": "eval_lora", "label": "8. Evaluate (LoRA)", "desc": "Fine-tuned LoRA evaluation"},
]


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_partial(exp_dir, mode):
    path = os.path.join(RESULTS_DIR, exp_dir, f"partial_{mode}.json")
    if os.path.isfile(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def load_commits(exp_dir):
    path = os.path.join(RESULTS_DIR, exp_dir, "commits.jsonl")
    if os.path.isfile(path):
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    return []


def load_config(exp_dir):
    path = os.path.join(RESULTS_DIR, exp_dir, "config.json")
    if os.path.isfile(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def load_progress(exp_dir):
    path = os.path.join(RESULTS_DIR, exp_dir, "progress.json")
    if os.path.isfile(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def load_partial_with_repo(exp_dir, mode):
    """Join partial results with commits.jsonl to get repo field."""
    partial = load_partial(exp_dir, mode)
    commits = load_commits(exp_dir)
    sha_to_repo = {}
    for c in commits:
        sha_to_repo[c["sha"][:7]] = c.get("repo", "unknown")
    for r in partial:
        r["repo"] = sha_to_repo.get(r.get("sha", ""), "unknown")
    return partial


def get_srr_values(data):
    return [r["srr"] for r in data if r.get("srr") is not None]


def get_compile_count(data):
    return sum(1 for r in data if r.get("compile_ok"))


def compute_stats(data):
    srrs = get_srr_values(data)
    n = len(data)
    compiled = get_compile_count(data)
    if not srrs:
        return {"n": n, "compiled": compiled, "compile_rate": 0,
                "median_srr": 0, "mean_srr": 0, "srr_positive_rate": 0,
                "valid_srr": 0}
    return {
        "n": n,
        "compiled": compiled,
        "compile_rate": compiled / n * 100 if n else 0,
        "median_srr": statistics.median(srrs),
        "mean_srr": statistics.mean(srrs),
        "srr_positive_rate": sum(1 for s in srrs if s > 0) / len(srrs) * 100,
        "valid_srr": len(srrs),
    }


def get_pipeline_status(exp_name):
    """Return list of status strings for each pipeline step."""
    exp_info = EXPERIMENTS[exp_name]
    progress = load_progress(exp_info["dir"])
    config = load_config(exp_info["dir"])
    repos = list((config.get("config", {}).get("repos", {})).keys())

    statuses = []

    # Clone
    if repos:
        done = sum(1 for r in repos if progress.get(r, {}).get("cloned"))
        statuses.append("complete" if done == len(repos) else ("partial" if done > 0 else "pending"))
    else:
        statuses.append("pending")

    # RMiner
    if repos:
        done = sum(1 for r in repos if progress.get(r, {}).get("rminer"))
        statuses.append("complete" if done == len(repos) else ("partial" if done > 0 else "pending"))
    else:
        statuses.append("pending")

    # Scan
    if repos:
        done = sum(1 for r in repos if progress.get(r, {}).get("scanned"))
        statuses.append("complete" if done == len(repos) else ("partial" if done > 0 else "pending"))
    else:
        statuses.append("pending")

    # Select
    statuses.append("complete" if progress.get("_selected", 0) > 0 else "pending")
    # Pairs
    statuses.append("complete" if progress.get("_pairs_done", 0) > 0 else "pending")
    # Train
    statuses.append("complete" if progress.get("_trained") else "pending")
    # Eval Ollama
    statuses.append("complete" if progress.get("_eval_ollama") else "pending")
    # Eval LoRA
    statuses.append("complete" if progress.get("_eval_lora") else "pending")

    return statuses


def render_pipeline_html(statuses):
    """Generate HTML stepper visualization."""
    colors = {"complete": "#22c55e", "partial": "#eab308", "running": "#3b82f6", "pending": "#9ca3af"}
    icons = {"complete": "&#10003;", "partial": "&#9679;", "running": "&#9654;", "pending": "&#9675;"}
    html = '<div style="display:flex;align-items:center;justify-content:center;gap:0;padding:20px 10px;flex-wrap:wrap;">'
    for i, step in enumerate(PIPELINE_STEPS):
        s = statuses[i] if i < len(statuses) else "pending"
        c = colors[s]
        icon = icons[s]
        html += f'''
        <div style="display:flex;flex-direction:column;align-items:center;min-width:90px;">
            <div style="width:40px;height:40px;border-radius:50%;background:{c};color:white;
                        display:flex;align-items:center;justify-content:center;font-size:18px;
                        font-weight:bold;box-shadow:0 2px 4px rgba(0,0,0,0.2);">{icon}</div>
            <div style="font-size:11px;margin-top:6px;text-align:center;color:#374151;
                        font-weight:{'bold' if s in ('running','partial') else 'normal'};">
                {step["label"]}</div>
            <div style="font-size:9px;color:#6b7280;text-align:center;">{step["desc"]}</div>
        </div>'''
        if i < len(PIPELINE_STEPS) - 1:
            next_s = statuses[i + 1] if i + 1 < len(statuses) else "pending"
            line_c = colors["complete"] if s == "complete" and next_s != "pending" else "#d1d5db"
            html += f'<div style="flex:1;height:3px;background:{line_c};min-width:20px;margin:0 -5px;margin-bottom:30px;"></div>'
    html += '</div>'
    return html


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH FUNCTIONS (15 total)
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Median SRR Comparison Bar ─────────────────────────────────────────────

def create_srr_comparison_bar():
    data = []
    for exp_name, exp_info in EXPERIMENTS.items():
        for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
            partial = load_partial(exp_info["dir"], mode)
            if partial:
                stats = compute_stats(partial)
                data.append({"Experiment": exp_name, "Approach": label,
                             "Median SRR (%)": stats["median_srr"]})
        for name, vals in CORDEIRO_BASELINES.items():
            data.append({"Experiment": exp_name, "Approach": name,
                         "Median SRR (%)": vals["median_srr"]})

    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    fig = px.bar(df, x="Experiment", y="Median SRR (%)", color="Approach",
                 barmode="group", title="Median SRR Comparison Across Experiments",
                 color_discrete_map=COLORS)
    fig.update_layout(yaxis_title="Median SRR (%)", template="plotly_white",
                      height=500, font=dict(size=13))
    return fig


# ── 2. Compile Rate Bar ──────────────────────────────────────────────────────

def create_compile_rate_bar():
    data = []
    for exp_name, exp_info in EXPERIMENTS.items():
        for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
            partial = load_partial(exp_info["dir"], mode)
            if partial:
                stats = compute_stats(partial)
                data.append({"Experiment": exp_name, "Approach": label,
                             "Compile Rate (%)": stats["compile_rate"]})
    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    fig = px.bar(df, x="Experiment", y="Compile Rate (%)", color="Approach",
                 barmode="group", title="Compilation Rate Comparison",
                 color_discrete_map=COLORS)
    fig.update_layout(yaxis_title="Compile Rate (%)", template="plotly_white",
                      height=500, font=dict(size=13))
    return fig


# ── 3. SRR Distribution Box Plot ─────────────────────────────────────────────

def create_srr_distribution(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    data = []
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        for s in get_srr_values(load_partial(exp_info["dir"], mode)):
            data.append({"Approach": label, "SRR (%)": s})
    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    fig = px.box(df, x="Approach", y="SRR (%)", color="Approach",
                 title=f"SRR Distribution — {exp_name}: {exp_info['label']}",
                 color_discrete_map=COLORS)
    fig.update_layout(template="plotly_white", height=500, showlegend=False, font=dict(size=13))
    return fig


# ── 4. SRR Histogram ─────────────────────────────────────────────────────────

def create_srr_histogram(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    fig = go.Figure()
    for mode, label, color in [("ollama", "Ollama Zero-Shot", COLORS["Ollama Zero-Shot"]),
                                ("lora", "LoRA Fine-Tuned", COLORS["LoRA Fine-Tuned"])]:
        srrs = get_srr_values(load_partial(exp_info["dir"], mode))
        if srrs:
            fig.add_trace(go.Histogram(x=srrs, name=label, opacity=0.7,
                                        marker_color=color, nbinsx=20))
    fig.update_layout(barmode="overlay",
                      title=f"SRR Histogram — {exp_name}",
                      xaxis_title="SRR (%)", yaxis_title="Count",
                      template="plotly_white", height=500, font=dict(size=13))
    return fig


# ── 5. Per-Commit Scatter (Ollama vs LoRA) ────────────────────────────────────

def create_per_commit_scatter(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    ollama = load_partial(exp_info["dir"], "ollama")
    lora = load_partial(exp_info["dir"], "lora")
    ollama_map = {r["sha"]: r for r in ollama}
    lora_map = {r["sha"]: r for r in lora}
    common = set(ollama_map.keys()) & set(lora_map.keys())

    data = []
    for sha in common:
        o_srr = ollama_map[sha].get("srr")
        l_srr = lora_map[sha].get("srr")
        if o_srr is not None and l_srr is not None:
            data.append({"SHA": sha, "Ollama SRR": o_srr, "LoRA SRR": l_srr})

    if not data:
        return _empty_fig("No matched commits")
    df = pd.DataFrame(data)
    fig = px.scatter(df, x="Ollama SRR", y="LoRA SRR", hover_data=["SHA"],
                     title=f"Per-Commit SRR: Ollama vs LoRA — {exp_name}",
                     color_discrete_sequence=["#AB63FA"])
    fig.add_shape(type="line", x0=-10, y0=-10, x1=110, y1=110,
                  line=dict(color="gray", dash="dash"))
    fig.add_annotation(text="Above = LoRA better", x=20, y=85, showarrow=False,
                      font=dict(size=11, color="gray"))
    fig.add_annotation(text="Below = Ollama better", x=80, y=15, showarrow=False,
                      font=dict(size=11, color="gray"))
    fig.update_layout(template="plotly_white", height=550, font=dict(size=13))
    return fig


# ── 6. Sorted SRR Curve ──────────────────────────────────────────────────────

def create_cumulative_srr(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    fig = go.Figure()
    for mode, label, color in [("ollama", "Ollama Zero-Shot", COLORS["Ollama Zero-Shot"]),
                                ("lora", "LoRA Fine-Tuned", COLORS["LoRA Fine-Tuned"])]:
        srrs = sorted(get_srr_values(load_partial(exp_info["dir"], mode)))
        if srrs:
            fig.add_trace(go.Scatter(x=list(range(1, len(srrs) + 1)), y=srrs,
                mode="lines+markers", name=label, line=dict(color=color), marker=dict(size=4)))
    fig.add_hline(y=4.76, line_dash="dash", line_color=COLORS["Cordeiro CoT+GPT-4"],
                  annotation_text="Cordeiro GPT-4 (4.76%)")
    fig.add_hline(y=15.15, line_dash="dash", line_color=COLORS["Cordeiro CoT+LLaMA 3"],
                  annotation_text="Cordeiro LLaMA 3 (15.15%)")
    fig.update_layout(title=f"Sorted SRR Curve — {exp_name}",
                      xaxis_title="Commit (sorted by SRR)", yaxis_title="SRR (%)",
                      template="plotly_white", height=500, font=dict(size=13))
    return fig


# ── 7. Smells Before vs After (Bar) ──────────────────────────────────────────

def create_smells_before_after(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    fig = go.Figure()
    for mode, label, color in [("ollama", "Ollama", COLORS["Ollama Zero-Shot"]),
                                ("lora", "LoRA", COLORS["LoRA Fine-Tuned"])]:
        partial = load_partial(exp_info["dir"], mode)
        if not partial:
            continue
        total_before = sum(r.get("smells_before", 0) for r in partial)
        total_after = sum(r.get("smells_after", 0) for r in partial)
        fig.add_trace(go.Bar(name=f"{label} Before", x=[label], y=[total_before],
                             marker_color=color, opacity=0.4))
        fig.add_trace(go.Bar(name=f"{label} After", x=[label], y=[total_after],
                             marker_color=color))
    fig.update_layout(title=f"Total Smells Before vs After — {exp_name}",
                      yaxis_title="Total Code Smells", template="plotly_white",
                      barmode="group", height=500, font=dict(size=13))
    return fig


# ── 8. Radar Chart ────────────────────────────────────────────────────────────

def create_radar_chart():
    categories = ["Median SRR", "Mean SRR", "Compile Rate", "SRR Positive %", "Valid SRR %"]
    fig = go.Figure()
    for mode, label, color in [("ollama", "Ollama Zero-Shot", COLORS["Ollama Zero-Shot"]),
                                ("lora", "LoRA Fine-Tuned", COLORS["LoRA Fine-Tuned"])]:
        all_data = []
        for exp_info in EXPERIMENTS.values():
            all_data.extend(load_partial(exp_info["dir"], mode))
        if not all_data:
            continue
        stats = compute_stats(all_data)
        valid_pct = stats["valid_srr"] / stats["n"] * 100 if stats["n"] else 0
        values = [stats["median_srr"], stats["mean_srr"], stats["compile_rate"],
                  stats["srr_positive_rate"], valid_pct]
        fig.add_trace(go.Scatterpolar(r=values, theta=categories, fill="toself",
                                       name=label, line=dict(color=color)))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                      title="Overall Performance Radar — All Experiments",
                      template="plotly_white", height=550, font=dict(size=13))
    return fig


# ── 9. SRR Heatmap (NEW) ─────────────────────────────────────────────────────

def create_srr_heatmap(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    ollama = load_partial(exp_info["dir"], "ollama")
    lora = load_partial(exp_info["dir"], "lora")
    if not ollama and not lora:
        return _empty_fig("No data")

    ollama_map = {r["sha"]: r.get("srr") for r in ollama}
    lora_map = {r["sha"]: r.get("srr") for r in lora}
    all_shas = list(dict.fromkeys([r["sha"] for r in ollama] + [r["sha"] for r in lora]))

    # Sort by average SRR descending
    def avg_srr(sha):
        vals = [v for v in [ollama_map.get(sha), lora_map.get(sha)] if v is not None]
        return statistics.mean(vals) if vals else -999
    all_shas.sort(key=avg_srr, reverse=True)

    # Limit to top 50 for readability
    all_shas = all_shas[:50]

    z = []
    text_labels = []
    for sha in all_shas:
        o_val = ollama_map.get(sha)
        l_val = lora_map.get(sha)
        # Replace None with float('nan') so plotly can handle it
        z.append([o_val if o_val is not None else float('nan'),
                  l_val if l_val is not None else float('nan')])
        text_labels.append([f"{o_val:.1f}%" if o_val is not None else "N/A",
                            f"{l_val:.1f}%" if l_val is not None else "N/A"])

    fig = go.Figure(data=go.Heatmap(
        z=z, x=["Ollama", "LoRA"], y=all_shas,
        colorscale="RdYlGn", zmid=50, zmin=-20, zmax=100,
        text=text_labels, texttemplate="%{text}", textfont={"size": 10},
    ))
    n_show = len(all_shas)
    fig.update_layout(title=f"SRR Heatmap (Top {n_show} commits) — {exp_name}",
                      yaxis=dict(dtick=1, autorange="reversed"),
                      template="plotly_white", height=max(400, n_show * 22),
                      font=dict(size=11))
    return fig


# ── 10. Violin Plot (NEW) ────────────────────────────────────────────────────

def create_srr_violin(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    data = []
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        for s in get_srr_values(load_partial(exp_info["dir"], mode)):
            data.append({"Approach": label, "SRR (%)": s})
    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    fig = px.violin(df, x="Approach", y="SRR (%)", color="Approach",
                    box=True, points="all",
                    title=f"SRR Violin Plot — {exp_name}",
                    color_discrete_map=COLORS)
    fig.update_layout(template="plotly_white", height=500, showlegend=False, font=dict(size=13))
    return fig


# ── 11. Funnel Chart (NEW) ───────────────────────────────────────────────────

def create_funnel_chart(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    fig = go.Figure()
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        partial = load_partial(exp_info["dir"], mode)
        n = len(partial)
        if n == 0:
            continue
        srrs = get_srr_values(partial)
        valid = len(srrs)
        positive = sum(1 for s in srrs if s > 0)
        compiled = get_compile_count(partial)
        high_srr = sum(1 for s in srrs if s > 50)

        stages = ["Total Commits", "Valid SRR", "SRR > 0%", "Compiled", "SRR > 50%"]
        values = [n, valid, positive, compiled, high_srr]
        pcts = [f"{v} ({v/n*100:.0f}%)" for v in values]

        fig.add_trace(go.Bar(
            name=label, x=stages, y=values, text=pcts, textposition="auto",
            marker_color=COLORS[label], opacity=0.8,
        ))

    fig.update_layout(title=f"Quality Funnel — {exp_name}", template="plotly_white",
                      barmode="group", height=500, font=dict(size=13),
                      yaxis_title="Number of Commits")
    return fig


# ── 12. Per-Repo Breakdown (NEW) ─────────────────────────────────────────────

def create_per_repo_breakdown():
    exp_info = EXPERIMENTS["Experiment 3"]
    data = []
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        partial = load_partial_with_repo(exp_info["dir"], mode)
        repo_groups = {}
        for r in partial:
            repo = r.get("repo", "unknown")
            repo_groups.setdefault(repo, [])
            if r.get("srr") is not None:
                repo_groups[repo].append(r["srr"])
        for repo, srrs in repo_groups.items():
            if srrs:
                data.append({"Repository": repo, "Approach": label,
                             "Median SRR (%)": statistics.median(srrs),
                             "N": len(srrs)})

    if not data:
        return _empty_fig("No Experiment 3 data")
    df = pd.DataFrame(data)
    # Sort by Ollama median SRR
    ollama_order = df[df["Approach"] == "Ollama Zero-Shot"].sort_values("Median SRR (%)", ascending=False)
    repo_order = ollama_order["Repository"].tolist()
    if not repo_order:
        repo_order = df["Repository"].unique().tolist()

    fig = px.bar(df, x="Repository", y="Median SRR (%)", color="Approach",
                 barmode="group", hover_data=["N"],
                 title="Per-Repository SRR — Experiment 3 (Multi-Repo)",
                 color_discrete_map=COLORS,
                 category_orders={"Repository": repo_order})
    fig.add_hline(y=4.76, line_dash="dash", line_color=COLORS["Cordeiro CoT+GPT-4"],
                  annotation_text="Cordeiro GPT-4")
    fig.add_hline(y=15.15, line_dash="dash", line_color=COLORS["Cordeiro CoT+LLaMA 3"],
                  annotation_text="Cordeiro LLaMA 3")
    fig.update_layout(template="plotly_white", height=550, font=dict(size=12),
                      xaxis_tickangle=-45)
    return fig


# ── 13. Smells Reduction Scatter (NEW) ────────────────────────────────────────

def create_smells_reduction_scatter(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    data = []
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        for r in load_partial(exp_info["dir"], mode):
            sb = r.get("smells_before", 0)
            sa = r.get("smells_after", 0)
            if sb > 0:
                data.append({"Approach": label, "Smells Before": sb, "Smells After": sa,
                             "SHA": r.get("sha", "")})
    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    fig = px.scatter(df, x="Smells Before", y="Smells After", color="Approach",
                     hover_data=["SHA"],
                     title=f"Smell Reduction Per Commit — {exp_name}",
                     color_discrete_map=COLORS)
    max_val = max(df["Smells Before"].max(), df["Smells After"].max()) * 1.1
    fig.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val,
                  line=dict(color="gray", dash="dash"))
    fig.add_annotation(text="Below line = smells reduced", x=max_val * 0.7, y=max_val * 0.3,
                      showarrow=False, font=dict(size=11, color="gray"))
    fig.update_layout(template="plotly_white", height=500, font=dict(size=13))
    return fig


# ── 14. Attempt Distribution (NEW) ───────────────────────────────────────────

def create_attempt_distribution(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    data = []
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        for r in load_partial(exp_info["dir"], mode):
            data.append({"Approach": label, "Attempts": r.get("attempts", 0)})
    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    fig = px.histogram(df, x="Attempts", color="Approach", barmode="group",
                       title=f"Retry Attempt Distribution — {exp_name}",
                       color_discrete_map=COLORS,
                       category_orders={"Attempts": [0, 1, 2, 3]})
    fig.update_layout(xaxis_title="Number of Attempts", yaxis_title="Count",
                      template="plotly_white", height=500, font=dict(size=13))
    return fig


# ── 15. SRR vs Smells Correlation (NEW) ──────────────────────────────────────

def create_srr_vs_smells_correlation(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    data = []
    for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
        for r in load_partial(exp_info["dir"], mode):
            if r.get("srr") is not None and r.get("smells_before", 0) > 0:
                data.append({"Approach": label, "Smells Before": r["smells_before"],
                             "SRR (%)": r["srr"], "SHA": r.get("sha", "")})
    if not data:
        return _empty_fig("No data")
    df = pd.DataFrame(data)
    try:
        fig = px.scatter(df, x="Smells Before", y="SRR (%)", color="Approach",
                         trendline="ols", hover_data=["SHA"],
                         title=f"SRR vs Initial Smell Count — {exp_name}",
                         color_discrete_map=COLORS)
    except Exception:
        # statsmodels not installed, skip trendline
        fig = px.scatter(df, x="Smells Before", y="SRR (%)", color="Approach",
                         hover_data=["SHA"],
                         title=f"SRR vs Initial Smell Count — {exp_name}",
                         color_discrete_map=COLORS)
    fig.update_layout(template="plotly_white", height=500, font=dict(size=13))
    return fig


# ── Helper ────────────────────────────────────────────────────────────────────

def _empty_fig(msg="No data available"):
    fig = go.Figure()
    fig.add_annotation(text=msg, xref="paper", yref="paper",
                      x=0.5, y=0.5, showarrow=False, font=dict(size=20, color="gray"))
    fig.update_layout(template="plotly_white", height=400)
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY TABLES
# ══════════════════════════════════════════════════════════════════════════════

def build_summary_table():
    rows = []
    for exp_name, exp_info in EXPERIMENTS.items():
        config = load_config(exp_info["dir"])
        started = config.get("started", "—")
        for mode, label in [("ollama", "Ollama Zero-Shot"), ("lora", "LoRA Fine-Tuned")]:
            partial = load_partial(exp_info["dir"], mode)
            if not partial:
                continue
            stats = compute_stats(partial)
            rows.append({
                "Experiment": exp_name,
                "Approach": label,
                "N": stats["n"],
                "Compiled": stats["compiled"],
                "Compile %": round(stats["compile_rate"], 1),
                "Median SRR %": round(stats["median_srr"], 1),
                "Mean SRR %": round(stats["mean_srr"], 1),
                "SRR > 0 %": round(stats["srr_positive_rate"], 1),
                "Valid SRR": stats["valid_srr"],
                "Started": started,
            })
    for name, vals in CORDEIRO_BASELINES.items():
        rows.append({
            "Experiment": "Baseline", "Approach": name, "N": vals["n"],
            "Compiled": "—", "Compile %": "—",
            "Median SRR %": vals["median_srr"], "Mean SRR %": "—",
            "SRR > 0 %": "—", "Valid SRR": "—", "Started": "—",
        })
    return pd.DataFrame(rows)


def build_per_commit_table(exp_name):
    exp_info = EXPERIMENTS[exp_name]
    rows = []
    for mode, label in [("ollama", "Ollama"), ("lora", "LoRA")]:
        partial = load_partial_with_repo(exp_info["dir"], mode)
        for r in partial:
            rows.append({
                "Approach": label,
                "SHA": r.get("sha", ""),
                "Repo": r.get("repo", "—"),
                "Compiled": "Yes" if r.get("compile_ok") else "No",
                "Smells Before": r.get("smells_before", 0),
                "Smells After": r.get("smells_after", 0),
                "SRR %": round(r["srr"], 1) if r.get("srr") is not None else "N/A",
                "Attempts": r.get("attempts", 0),
            })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE EXECUTION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

pipeline_log = []
pipeline_running = False
pipeline_current_step = ""


def run_pipeline_step(experiment, step_id, train_epochs=3, train_lr=2e-4,
                      train_batch=1, train_lora_r=16):
    global pipeline_log, pipeline_running, pipeline_current_step
    pipeline_running = True
    pipeline_current_step = step_id
    pipeline_log = []

    exp_num = EXPERIMENTS[experiment]["exp_num"]

    # Build command based on step
    if step_id == "eval_ollama":
        phase = "eval"
        mode_flag = ["--mode", "ollama"]
    elif step_id == "eval_lora":
        phase = "eval"
        mode_flag = ["--mode", "lora"]
    elif step_id == "train":
        phase = "train"
        mode_flag = []
    else:
        phase = step_id
        mode_flag = []

    cmd = ["python3", "scripts/run_experiment.py", "--exp", str(exp_num),
           "--phase", phase] + mode_flag

    pipeline_log.append(f"$ {' '.join(cmd)}\n")
    pipeline_log.append(f"Experiment: {experiment} | Step: {step_id}\n")
    pipeline_log.append("─" * 60 + "\n")

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in process.stdout:
            pipeline_log.append(line)
            if len(pipeline_log) > 500:
                pipeline_log = pipeline_log[-300:]
        process.wait()
        if process.returncode == 0:
            pipeline_log.append(f"\n*** Step '{step_id}' complete! ***\n")
        else:
            pipeline_log.append(f"\n*** Step '{step_id}' failed (exit {process.returncode}) ***\n")
    except Exception as e:
        pipeline_log.append(f"\n*** Error: {e} ***\n")
    finally:
        pipeline_running = False
        pipeline_current_step = ""

    return "".join(pipeline_log[-100:])


def start_pipeline_step(experiment, step_id, epochs, lr, batch, lora_r):
    global pipeline_running
    if pipeline_running:
        return f"Step '{pipeline_current_step}' is already running. Please wait."
    thread = threading.Thread(
        target=run_pipeline_step,
        args=(experiment, step_id, int(epochs), float(lr), int(batch), int(lora_r)),
        daemon=True
    )
    thread.start()
    return f"Step '{step_id}' started! Click 'Refresh' to see progress."


def get_pipeline_log():
    if not pipeline_log:
        return "No log yet. Run a pipeline step first."
    return "".join(pipeline_log[-100:])


def get_pipeline_running_status():
    if pipeline_running:
        return f"**Status: RUNNING** — Step: {pipeline_current_step}"
    if pipeline_log:
        return "**Status: FINISHED**"
    return "**Status: IDLE**"


# ══════════════════════════════════════════════════════════════════════════════
# EVALUATION ENGINE (standalone, kept from original)
# ══════════════════════════════════════════════════════════════════════════════

eval_log = []
eval_running = False


def run_evaluation(experiment, mode):
    global eval_log, eval_running
    eval_running = True
    eval_log = []
    exp_dir = EXPERIMENTS[experiment]["dir"]
    commits_path = os.path.join(RESULTS_DIR, exp_dir, "commits.jsonl")
    output_dir = os.path.join(RESULTS_DIR, exp_dir)

    env = os.environ.copy()
    if mode == "lora":
        env["LORA_MODEL_PATH"] = os.path.abspath(os.path.join(RESULTS_DIR, exp_dir, "lora_model"))

    cmd = ["python3", "scripts/run_eval.py", "--mode", mode,
           "--commits", commits_path, "--output", output_dir]
    eval_log.append(f"$ {' '.join(cmd)}\n─" + "─" * 59 + "\n")

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, env=env)
        for line in process.stdout:
            eval_log.append(line)
            if len(eval_log) > 500:
                eval_log = eval_log[-300:]
        process.wait()
        rc = "complete" if process.returncode == 0 else f"failed (exit {process.returncode})"
        eval_log.append(f"\n*** Evaluation {rc}! ***\n")
    except Exception as e:
        eval_log.append(f"\n*** Error: {e} ***\n")
    finally:
        eval_running = False
    return "".join(eval_log[-100:])


def start_eval(experiment, mode):
    global eval_running
    if eval_running:
        return "Evaluation already running!"
    thread = threading.Thread(target=run_evaluation, args=(experiment, mode), daemon=True)
    thread.start()
    return "Evaluation started! Click 'Refresh Log' to see progress."


def get_eval_log():
    return "".join(eval_log[-100:]) if eval_log else "No eval log yet."


def get_eval_status():
    if eval_running:
        return "**Status: RUNNING**"
    return "**Status: FINISHED**" if eval_log else "**Status: IDLE**"


# ══════════════════════════════════════════════════════════════════════════════
# GRADIO UI
# ══════════════════════════════════════════════════════════════════════════════

def build_app():
    with gr.Blocks(
        title="LLM Java Refactoring Dashboard",
        theme=gr.themes.Soft(primary_hue="blue"),
        css="""
        .gradio-container { max-width: 1400px; margin: auto; }
        .metric-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                       color: white; padding: 20px; border-radius: 12px; text-align: center; }
        .metric-card h3 { margin: 0; font-size: 14px; opacity: 0.9; }
        .metric-card h1 { margin: 5px 0 0 0; font-size: 32px; }
        """
    ) as app:

        gr.Markdown("""
        # LLM-Based Java Code Refactoring — Experiment Dashboard
        **Replication & extension of Cordeiro et al. (2024)** — Evaluating zero-shot LLaMA 3 8B (Ollama)
        vs QLoRA fine-tuned CodeLlama-7B on Apache project refactoring commits.
        *ASE 2026 Submission*
        """)

        # ── Tab 1: Overview ──────────────────────────────────────────────
        with gr.Tab("Overview"):
            # Metric highlight cards
            gr.Markdown("## Key Metrics")
            with gr.Row():
                all_ollama = []
                all_lora = []
                total_commits = 0
                for exp_info in EXPERIMENTS.values():
                    all_ollama.extend(load_partial(exp_info["dir"], "ollama"))
                    all_lora.extend(load_partial(exp_info["dir"], "lora"))
                total_commits = len(all_ollama) + len(all_lora)
                o_stats = compute_stats(all_ollama)
                l_stats = compute_stats(all_lora)

                gr.HTML(f'''<div class="metric-card">
                    <h3>Total Evaluations</h3><h1>{total_commits}</h1></div>''')
                gr.HTML(f'''<div class="metric-card" style="background:linear-gradient(135deg,#636EFA,#3b4fd4);">
                    <h3>Best Median SRR (Ollama)</h3><h1>{o_stats["median_srr"]:.1f}%</h1></div>''')
                gr.HTML(f'''<div class="metric-card" style="background:linear-gradient(135deg,#EF553B,#c0392b);">
                    <h3>Best Median SRR (LoRA)</h3><h1>{l_stats["median_srr"]:.1f}%</h1></div>''')
                gr.HTML(f'''<div class="metric-card" style="background:linear-gradient(135deg,#00CC96,#00a67d);">
                    <h3>Overall Compile Rate</h3>
                    <h1>{(o_stats["compiled"]+l_stats["compiled"])/(o_stats["n"]+l_stats["n"])*100:.1f}%</h1></div>''')

            gr.Markdown("## Results Summary")
            summary_table = gr.DataFrame(value=build_summary_table, label="All Results",
                                         interactive=False)
            refresh_btn = gr.Button("Refresh Data", variant="primary")
            refresh_btn.click(fn=build_summary_table, outputs=summary_table)

            gr.Markdown("""
            ## Key Findings
            - **Both models vastly outperform Cordeiro baselines** (3-12x improvement in median SRR)
            - **Ollama zero-shot consistently beats LoRA** on both SRR and compile rate
            - **QLoRA fine-tuning hurts compilability** (2-5% vs 5-13%) while SRR stays comparable
            - **Filtered commits (Exp 2) yield best results** — high-signal structural refactorings
            - **Compile rates remain low** (2-13%) due to single-file javac without dependency context
            - **Negative result**: Fine-tuning on limited data degrades rather than improves performance
            """)

        # ── Tab 2: Cross-Experiment ──────────────────────────────────────
        with gr.Tab("Cross-Experiment"):
            gr.Markdown("## Comparison Across All Experiments")
            with gr.Row():
                srr_bar = gr.Plot(value=create_srr_comparison_bar, label="Median SRR")
                compile_bar = gr.Plot(value=create_compile_rate_bar, label="Compile Rate")
            with gr.Row():
                radar = gr.Plot(value=create_radar_chart, label="Performance Radar")
                repo_bar = gr.Plot(value=create_per_repo_breakdown, label="Per-Repo (Exp 3)")

            refresh_cross = gr.Button("Refresh Charts", variant="primary")
            refresh_cross.click(fn=create_srr_comparison_bar, outputs=srr_bar)
            refresh_cross.click(fn=create_compile_rate_bar, outputs=compile_bar)
            refresh_cross.click(fn=create_radar_chart, outputs=radar)
            refresh_cross.click(fn=create_per_repo_breakdown, outputs=repo_bar)

        # ── Tab 3: Per-Experiment Deep Dive ──────────────────────────────
        with gr.Tab("Per-Experiment Analysis"):
            exp_selector = gr.Dropdown(choices=list(EXPERIMENTS.keys()),
                                        value="Experiment 2", label="Select Experiment")

            gr.Markdown("### Distribution Analysis")
            with gr.Row():
                box_plot = gr.Plot(label="SRR Box Plot")
                violin_plot = gr.Plot(label="SRR Violin Plot")
            with gr.Row():
                hist_plot = gr.Plot(label="SRR Histogram")
                funnel_plot = gr.Plot(label="Quality Funnel")

            gr.Markdown("### Per-Commit Comparisons")
            with gr.Row():
                scatter_plot = gr.Plot(label="Ollama vs LoRA Scatter")
                cumulative_plot = gr.Plot(label="Sorted SRR Curve")

            gr.Markdown("### Smell Analysis")
            with gr.Row():
                smells_bar = gr.Plot(label="Total Smells Before/After")
                smells_scatter = gr.Plot(label="Per-Commit Smell Reduction")

            gr.Markdown("### Detailed Views")
            heatmap_plot = gr.Plot(label="SRR Heatmap")
            with gr.Row():
                corr_plot = gr.Plot(label="SRR vs Initial Smells")
                attempt_plot = gr.Plot(label="Attempt Distribution")

            gr.Markdown("### Per-Commit Data")
            commit_table = gr.DataFrame(label="Commit Details", interactive=False)

            def update_all_exp_plots(exp_name):
                return (
                    create_srr_distribution(exp_name),
                    create_srr_violin(exp_name),
                    create_srr_histogram(exp_name),
                    create_funnel_chart(exp_name),
                    create_per_commit_scatter(exp_name),
                    create_cumulative_srr(exp_name),
                    create_smells_before_after(exp_name),
                    create_smells_reduction_scatter(exp_name),
                    create_srr_heatmap(exp_name),
                    create_srr_vs_smells_correlation(exp_name),
                    create_attempt_distribution(exp_name),
                    build_per_commit_table(exp_name),
                )

            all_exp_outputs = [box_plot, violin_plot, hist_plot, funnel_plot,
                               scatter_plot, cumulative_plot, smells_bar, smells_scatter,
                               heatmap_plot, corr_plot, attempt_plot, commit_table]

            exp_selector.change(fn=update_all_exp_plots, inputs=exp_selector,
                                outputs=all_exp_outputs)
            app.load(fn=lambda: update_all_exp_plots("Experiment 2"), outputs=all_exp_outputs)

        # ── Tab 4: Pipeline ──────────────────────────────────────────────
        with gr.Tab("Pipeline"):
            gr.Markdown("""
            ## Step-by-Step Pipeline Control
            Run individual pipeline phases for any experiment. Each step can be executed independently.

            **Requirements:**
            - For Ollama eval: `sudo systemctl start ollama`
            - For LoRA training/eval: `sudo systemctl stop ollama` (free GPU)
            """)

            pipe_exp = gr.Dropdown(choices=list(EXPERIMENTS.keys()),
                                    value="Experiment 1", label="Select Experiment")

            # Pipeline visualization
            pipeline_viz = gr.HTML(label="Pipeline Progress")
            pipe_status_md = gr.Markdown()
            refresh_pipe = gr.Button("Refresh Pipeline Status", variant="secondary")

            def update_pipeline_viz(exp_name):
                statuses = get_pipeline_status(exp_name)
                return render_pipeline_html(statuses), get_pipeline_running_status()

            pipe_exp.change(fn=update_pipeline_viz, inputs=pipe_exp,
                           outputs=[pipeline_viz, pipe_status_md])
            refresh_pipe.click(fn=update_pipeline_viz, inputs=pipe_exp,
                              outputs=[pipeline_viz, pipe_status_md])
            app.load(fn=lambda: update_pipeline_viz("Experiment 1"),
                    outputs=[pipeline_viz, pipe_status_md])

            # Training hyperparameters (shown for Train step)
            gr.Markdown("### Training Hyperparameters (for Step 6)")
            with gr.Row():
                t_epochs = gr.Number(value=3, label="Epochs", minimum=1, maximum=20)
                t_lr = gr.Number(value=2e-4, label="Learning Rate")
                t_batch = gr.Number(value=1, label="Batch Size", minimum=1, maximum=4)
                t_lora_r = gr.Number(value=16, label="LoRA Rank", minimum=4, maximum=64)

            # Step buttons and log
            gr.Markdown("### Run Steps")
            with gr.Row():
                step_selector = gr.Dropdown(
                    choices=[s["label"] for s in PIPELINE_STEPS],
                    value=PIPELINE_STEPS[0]["label"],
                    label="Select Step"
                )
                run_step_btn = gr.Button("Run Selected Step", variant="primary", size="lg")

            pipe_log = gr.Textbox(label="Pipeline Log", lines=20, max_lines=25,
                                   interactive=False, show_copy_button=True)
            refresh_log_btn = gr.Button("Refresh Log")

            def on_run_step(exp_name, step_label, epochs, lr, batch, lora_r):
                step_id = None
                for s in PIPELINE_STEPS:
                    if s["label"] == step_label:
                        step_id = s["id"]
                        break
                if not step_id:
                    return "Invalid step selected."
                return start_pipeline_step(exp_name, step_id, epochs, lr, batch, lora_r)

            run_step_btn.click(fn=on_run_step,
                              inputs=[pipe_exp, step_selector, t_epochs, t_lr, t_batch, t_lora_r],
                              outputs=pipe_log)
            refresh_log_btn.click(fn=get_pipeline_log, outputs=pipe_log)
            refresh_log_btn.click(fn=get_pipeline_running_status, outputs=pipe_status_md)

            # Step details accordion
            gr.Markdown("### Step Details")
            for step in PIPELINE_STEPS:
                with gr.Accordion(f"{step['label']} — {step['desc']}", open=False):
                    gr.Markdown(f"""
                    **Command:** `python3 scripts/run_experiment.py --exp N --phase {step['id']}`

                    **What it does:** {step['desc']}
                    """)

        # ── Tab 5: Run Evaluation ────────────────────────────────────────
        with gr.Tab("Run Evaluation"):
            gr.Markdown("""
            ## Run Evaluation Pipeline (Direct)
            Run eval directly via `run_eval.py` for more control (resume support, pass@k).

            **For Ollama:** `sudo systemctl start ollama`
            **For LoRA:** `sudo systemctl stop ollama`
            """)
            with gr.Row():
                with gr.Column(scale=1):
                    eval_exp = gr.Dropdown(choices=list(EXPERIMENTS.keys()),
                                            value="Experiment 2", label="Experiment")
                    eval_mode = gr.Radio(choices=["ollama", "lora"], value="ollama", label="Mode")
                    eval_btn = gr.Button("Start Evaluation", variant="primary", size="lg")
                    eval_status_md = gr.Markdown(value=get_eval_status)
                with gr.Column(scale=2):
                    eval_log_box = gr.Textbox(label="Evaluation Log", lines=25, max_lines=30,
                                              interactive=False, show_copy_button=True)
                    eval_refresh = gr.Button("Refresh Log")
            eval_btn.click(fn=start_eval, inputs=[eval_exp, eval_mode], outputs=eval_log_box)
            eval_refresh.click(fn=get_eval_log, outputs=eval_log_box)
            eval_refresh.click(fn=get_eval_status, outputs=eval_status_md)

        # ── Tab 6: Methodology ───────────────────────────────────────────
        with gr.Tab("Methodology"):
            gr.Markdown("""
            ## Experimental Setup

            ### Pipeline
            ```
            Clone → RefactoringMiner → DesigniteJava Scan → Select Commits
                → Extract Pairs → QLoRA Training → Evaluation
            ```

            ### Models
            | | Ollama (Zero-Shot) | LoRA (Fine-Tuned) |
            |--|--|--|
            | **Base Model** | LLaMA 3 8B | CodeLlama-7B-Instruct |
            | **Quantization** | — | 4-bit NF4 (QLoRA) |
            | **LoRA Config** | — | rank=16, alpha=32, targets: q/k/v/o_proj |
            | **Training** | None | 3 epochs, lr=2e-4, batch=1, grad_accum=16 |
            | **Prompting** | Zero-shot | Matched training template |

            ### Metrics
            - **SRR (Smell Reduction Rate):** `(smells_before - smells_after) / smells_before * 100`
            - **Compile Rate:** % of generated code that compiles with `javac` (single-file)
            - **SRR Positive Rate:** % of commits with SRR > 0

            ### Experiments
            | Experiment | Dataset | Selection | N |
            |--|--|--|--|
            | **Exp 1** | Apache Camel | Random (seed=42) | 20 |
            | **Exp 2** | Apache Camel | Filtered (smell-richness ranked) | 100 |
            | **Exp 3** | 19 Apache repos | Filtered (per-repo) | 71 |

            ### Baselines (Cordeiro et al., 2024)
            - GPT-4 with Chain-of-Thought: **4.76%** median SRR (N=5,194)
            - LLaMA 3 with Chain-of-Thought: **15.15%** median SRR (N=5,194)

            ### Key Differences from Cordeiro
            - We use **zero-shot** prompting (no CoT) — simpler yet more effective
            - We add **QLoRA fine-tuning** as a novel comparison
            - We use **single-file javac** compilation (vs full Maven builds)
            - Smaller sample sizes but focused on high-signal commits

            ### References
            - Cordeiro et al. (2024) — arxiv 2411.02320
            - Tapader et al. (2025) — arxiv 2511.21788
            """)

    return app


if __name__ == "__main__":
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
