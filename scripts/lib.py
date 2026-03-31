"""
lib.py — Shared constants and utilities for the refactoring pipeline.

Provides:
  - STRUCTURAL_TYPES, RENAME_TYPES, TYPE_GROUPS  (refactoring type sets)
  - default_dj_cp()          (resolve DesigniteJava classpath)
  - count_smells()           (parse DesigniteJava CSV output)
  - find_changed_files()     (diff before/after Java trees)
  - run_designite()          (invoke DesigniteJava on source)
  - copy_all_java_src()      (extract src/main/java from repo)
  - write_jsonl()            (emit commits.jsonl manifest)
"""
import os, subprocess, csv, shutil, tempfile

# ── Refactoring type constants ────────────────────────────────────

STRUCTURAL_TYPES = {
    "Extract Method", "Move Method", "Pull Up Method",
    "Push Down Method", "Extract Class", "Move Attribute",
    "Extract And Move Method", "Extract Superclass",
    "Extract Interface", "Inline Method", "Move Class",
    "Pull Up Attribute", "Push Down Attribute",
    "Inline Variable", "Extract Variable",
}

RENAME_TYPES = {
    "Rename Method", "Rename Variable", "Rename Parameter",
    "Rename Attribute", "Rename Class", "Rename Package",
}

TYPE_GROUPS = {
    "structural": STRUCTURAL_TYPES,
    "rename":     RENAME_TYPES,
    "all":        None,
}

# ── DesigniteJava classpath ───────────────────────────────────────

def default_dj_cp():
    """Resolve DesigniteJava classpath from env or default path."""
    env = os.environ.get("DESIGNITE_CP")
    if env:
        return env
    home = os.path.expanduser("~")
    # Prefer tools/DesigniteJava-src (classes + dependency jars)
    classes = os.path.join(home, "refactor_project",
                           "tools", "DesigniteJava-src", "target", "classes")
    libs = os.path.join(home, "refactor_project",
                        "tools", "DesigniteJava-src", "target", "lib")
    if os.path.isdir(classes) and os.path.isdir(libs):
        return f"{classes}:{libs}/*"
    # Fallback
    classes = os.path.join(home, "refactor_project",
                           "DesigniteJava-src", "target", "classes")
    libs = os.path.join(home, "refactor_project",
                        "DesigniteJava-src", "target", "lib")
    return f"{classes}:{libs}/*"

# ── Smell counting ────────────────────────────────────────────────

def count_smells(output_dir, changed_classes=None):
    """Count smells from DesigniteJava CSV output.

    If changed_classes is given, only count smells for those classes.
    """
    total = 0
    for fname in ["designCodeSmells.csv", "implementationCodeSmells.csv"]:
        fpath = os.path.join(output_dir, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if changed_classes is None:
                    total += 1
                elif len(row) >= 3 and row[2] in changed_classes:
                    total += 1
    return max(total, 0)

# ── Changed-file detection ────────────────────────────────────────

def find_changed_files(before_dir, after_dir):
    """Find changed .java files between before/after trees.

    Returns (module_paths, changed_class_names).
    """
    modules = set()
    changed_classes = set()

    def _scan(walk_dir, other_dir, check_missing=False):
        for root, _dirs, files in os.walk(walk_dir):
            for f in files:
                if not f.endswith(".java"):
                    continue
                this = os.path.join(root, f)
                rel = os.path.relpath(this, walk_dir)
                other = os.path.join(other_dir, rel)

                changed = False
                if check_missing:
                    changed = not os.path.exists(other)
                else:
                    if not os.path.exists(other):
                        changed = True
                    else:
                        with open(other, 'rb') as a, open(this, 'rb') as b:
                            if a.read() != b.read():
                                changed = True

                if changed:
                    parts = rel.replace("\\", "/").split("/")
                    for idx in range(len(parts)):
                        if (idx >= 2
                                and parts[idx-2] == "src"
                                and parts[idx-1] == "main"
                                and parts[idx] == "java"):
                            modules.add("/".join(parts[:idx-2]))
                            changed_classes.add(f[:-5])
                            break

    _scan(after_dir, before_dir)             # new / modified
    _scan(before_dir, after_dir, check_missing=True)  # deleted
    return modules, changed_classes

# ── DesigniteJava runner ──────────────────────────────────────────

def run_designite(src_dir, output_dir, module_paths, changed_classes,
                  dj_cp=None, timeout=90):
    """Run DesigniteJava on source and return smell count."""
    if dj_cp is None:
        dj_cp = default_dj_cp()

    tmpdir = tempfile.mkdtemp(prefix="dj_")
    try:
        src_link = os.path.join(tmpdir, "src", "Designite")
        out_dir  = os.path.join(tmpdir, "output")
        os.makedirs(src_link, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        found_java = False
        for mod in module_paths:
            java_dir = os.path.join(src_dir, mod, "src", "main", "java")
            if os.path.isdir(java_dir):
                for item in os.listdir(java_dir):
                    s = os.path.join(java_dir, item)
                    d = os.path.join(src_link, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                        found_java = True

        if not found_java:
            return 0

        try:
            cmd = ["java", "-Xmx1g", "-XX:+UseSerialGC", "-cp", dj_cp, "Designite.Designite"]
            subprocess.run(cmd, cwd=tmpdir, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

        smells = count_smells(out_dir, changed_classes)

        if os.path.isdir(output_dir):
            shutil.rmtree(output_dir)
        shutil.copytree(out_dir, output_dir)
        return smells
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ── Git / source helpers ──────────────────────────────────────────

def copy_all_java_src(repo, dest):
    """Walk repo, copy every src/main/java tree into dest preserving structure."""
    found = False
    for root, _dirs, _files in os.walk(repo):
        if root.endswith(os.path.join("src", "main", "java")):
            rel = os.path.relpath(root, repo)
            target = os.path.join(dest, rel)
            shutil.copytree(root, target, dirs_exist_ok=True)
            found = True
    return found

# ── JSONL writer ──────────────────────────────────────────────────

def write_jsonl(commits, output_path, pairs_dir="data/pairs"):
    """Write commits.jsonl manifest for a list of commit objects."""
    import json
    with open(output_path, "w") as out:
        for i, c in enumerate(commits, 1):
            record = {
                "sha": c["sha1"],
                "before_dir": f"{pairs_dir}/commit_{i:03d}/before/src",
                "after_dir":  f"{pairs_dir}/commit_{i:03d}/after/src",
                "rminer_types": [r["type"] for r in c.get("refactorings", [])]
            }
            out.write(json.dumps(record) + "\n")
    return len(commits)
