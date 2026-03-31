"""
parse_agent.py — LangGraph node: read before code + count baseline smells.

Reads the primary changed Java file from before_dir, counts smells using
pre-computed DesigniteJava data or live analysis.
"""
import os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from find_primary_java import find_primary_changed_java
from lib import count_smells, run_designite, find_changed_files

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
MAX_CHARS = 3000  # match training data truncation


def _commit_index(path):
    """Extract 'commit_NNN' from a path like data/pairs/commit_001/before/src."""
    m = re.search(r'(commit_\d+)', path)
    return m.group(1) if m else None


def parse_node(state: dict) -> dict:
    before_dir = os.path.join(PROJECT_ROOT, state["before_dir"])
    after_dir = os.path.join(PROJECT_ROOT, state["after_dir"])

    bf, _ = find_primary_changed_java(before_dir, after_dir)
    if not bf:
        return {"before_code": "", "smells_before": 0}

    before_code = open(bf, errors="replace").read()[:MAX_CHARS]

    # Use pre-passed smells_before if available (from commits.jsonl)
    if state.get("smells_before", 0) > 0:
        return {"before_code": before_code, "smells_before": state["smells_before"]}

    # Try pre-computed smells
    commit_id = _commit_index(state["before_dir"])
    precomputed = os.path.join(PROJECT_ROOT, "data", "smells", commit_id, "before_smells") if commit_id else None

    if precomputed and os.path.isdir(precomputed):
        modules, changed_classes = find_changed_files(before_dir, after_dir)
        smells_before = count_smells(precomputed, changed_classes)
    else:
        # Compute live
        modules, changed_classes = find_changed_files(before_dir, after_dir)
        smell_out = os.path.join(PROJECT_ROOT, "data", "smells", commit_id or "tmp", "before_smells")
        smells_before = run_designite(before_dir, smell_out, modules, changed_classes)

    return {"before_code": before_code, "smells_before": smells_before}
