"""
pipeline.py — LangGraph StateGraph wiring the refactoring pipeline.

Graph:  START → parse → refactor → validate → conditional:
                          ^                       |
                          +--- (compile fail &    |
                                attempt < 3) -----+
                                            else → END
"""
import os
from langgraph.graph import StateGraph, START, END
from agents.state import RefactorState
from agents.parse_agent import parse_node
from agents.refactor_agent import refactor_node, refactor_k_candidates
from agents.validate_agent import validate_node, select_best_candidate, _get_primary_relpath

MAX_ATTEMPTS = 3


def _should_retry(state: dict) -> str:
    if not state.get("compile_ok", False) and state.get("attempt", 0) < MAX_ATTEMPTS:
        return "refactor"
    return END


def build_graph():
    g = StateGraph(RefactorState)

    g.add_node("parse", parse_node)
    g.add_node("refactor", refactor_node)
    g.add_node("validate", validate_node)

    g.add_edge(START, "parse")
    g.add_edge("parse", "refactor")
    g.add_edge("refactor", "validate")
    g.add_conditional_edges("validate", _should_retry, {"refactor": "refactor", END: END})

    return g.compile()


# Compiled graph — reusable across calls
graph = build_graph()


def run_pipeline(sha, before_dir, after_dir, rminer_types, smells_before=None):
    """Run the full refactoring pipeline for a single commit."""
    initial_state = {
        "sha": sha,
        "before_dir": before_dir,
        "after_dir": after_dir,
        "rminer_types": rminer_types,
        "before_code": "",
        "smells_before": smells_before or 0,
        "refactored_code": "",
        "attempt": 0,
        "smells_after": 0,
        "srr": None,
        "compile_ok": False,
        "test_pass_rate": None,
        "confidence": 0.0,
        "needs_review": True,
    }
    return graph.invoke(initial_state)


PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


def run_pipeline_pass_k(sha, before_dir, after_dir, rminer_types, k=5, temperature=0.8):
    """pass@k pipeline: generate k candidates, pick best compiling one."""
    # Step 1: parse (reuse parse_node)
    state = {
        "sha": sha, "before_dir": before_dir, "after_dir": after_dir,
        "rminer_types": rminer_types,
    }
    parsed = parse_node(state)
    before_code = parsed["before_code"]
    smells_before = parsed["smells_before"]

    if not before_code.strip():
        return {"compile_ok": False, "smells_before": 0, "smells_after": 0,
                "srr": None, "test_pass_rate": None, "attempt": 0, "k": k}

    # Step 2: generate k candidates
    candidates = refactor_k_candidates(before_code, rminer_types, k=k, temperature=temperature)
    if not candidates:
        return {"compile_ok": False, "smells_before": smells_before, "smells_after": 0,
                "srr": None, "test_pass_rate": None, "attempt": k, "k": k}

    # Step 3: select best candidate
    abs_before = os.path.join(PROJECT_ROOT, before_dir)
    abs_after = os.path.join(PROJECT_ROOT, after_dir)
    _, _, rel_path = _get_primary_relpath(abs_before, abs_after)
    if not rel_path:
        return {"compile_ok": False, "smells_before": smells_before, "smells_after": 0,
                "srr": None, "test_pass_rate": None, "attempt": k, "k": k}

    best_code, compile_ok, smells_after, srr = select_best_candidate(
        candidates, rel_path, abs_before, abs_after, smells_before
    )

    # Step 4: EvoSuite tests if compiled
    import re
    test_pass_rate = None
    if compile_ok:
        from agents.validate_agent import _run_evosuite_tests, _commit_index
        commit_id = _commit_index(before_dir)
        if commit_id:
            test_pass_rate = _run_evosuite_tests(commit_id, best_code, rel_path, abs_before)

    return {
        "compile_ok": compile_ok,
        "smells_before": smells_before,
        "smells_after": smells_after,
        "srr": srr,
        "test_pass_rate": test_pass_rate,
        "attempt": len(candidates),
        "k": k,
        "candidates_generated": len(candidates),
    }
