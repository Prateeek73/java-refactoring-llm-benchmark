"""
validate_agent.py — LangGraph node: compile, count smells, compute SRR, run EvoSuite.

Writes refactored code to a temp tree, checks compilation, measures smells
with DesigniteJava, and optionally runs EvoSuite regression tests.
"""
import glob, os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from find_primary_java import find_primary_changed_java
from lib import count_smells, run_designite, find_changed_files

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

_classpath_cache = {}
_changed_files_cache = {}


def _repo_classpath(repo_name=None):
    """Build classpath for compilation (cached per repo).

    Checks: tools/{repo}-deps/, tools/camel-deps (fallback), repo target jars,
    repo target/dependency (Maven resolved), .m2 local repo.
    """
    if repo_name is None:
        repo_name = os.environ.get("EVAL_REPO", "camel")
    if repo_name in _classpath_cache:
        return _classpath_cache[repo_name]

    jars = []
    # 1. Pre-downloaded deps for this repo
    deps_dir = os.path.join(PROJECT_ROOT, "tools", f"{repo_name}-deps")
    if os.path.isdir(deps_dir):
        jars.extend(glob.glob(os.path.join(deps_dir, "*.jar")))
    # 2. Camel deps as fallback (for camel experiments)
    camel_deps = os.path.join(PROJECT_ROOT, "tools", "camel-deps")
    if os.path.isdir(camel_deps) and (repo_name == "camel" or not jars):
        jars.extend(glob.glob(os.path.join(camel_deps, "*.jar")))
    # 3. Repo target jars
    repo_dir = os.path.join(PROJECT_ROOT, "data", repo_name)
    if os.path.isdir(repo_dir):
        jars.extend(glob.glob(os.path.join(repo_dir, "**", "target", "*.jar"), recursive=True))
    # 4. Maven-resolved dependencies
    target_deps = os.path.join(repo_dir, "target", "dependency")
    if os.path.isdir(target_deps):
        jars.extend(glob.glob(os.path.join(target_deps, "*.jar")))

    _classpath_cache[repo_name] = ":".join(jars) if jars else ""
    return _classpath_cache[repo_name]


def _commit_index(path):
    m = re.search(r'(commit_\d+)', path)
    return m.group(1) if m else None


def _get_primary_relpath(before_dir, after_dir):
    """Get the relative path of the primary changed file within the source tree."""
    bf, af = find_primary_changed_java(before_dir, after_dir)
    if not af:
        return None, None, None
    rel = os.path.relpath(af, after_dir)
    return bf, af, rel


def _compile_check(java_code, rel_path, tmpdir):
    """Write code to temp tree and compile. Returns (success, java_file_path)."""
    java_file = os.path.join(tmpdir, "src", rel_path)
    os.makedirs(os.path.dirname(java_file), exist_ok=True)
    with open(java_file, "w") as f:
        f.write(java_code)

    compile_out = os.path.join(tmpdir, "classes")
    os.makedirs(compile_out, exist_ok=True)

    cmd = ["javac", "-d", compile_out, "-proc:none", "-nowarn"]
    cp = _repo_classpath()
    if cp:
        cmd += ["-cp", cp]
    cmd.append(java_file)

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode == 0, java_file


def _compute_smells(before_dir, after_dir, refactored_code, rel_path):
    """Copy before tree, replace primary file with refactored code, run DesigniteJava."""
    with tempfile.TemporaryDirectory(prefix="val_smells_") as tmpdir:
        # Copy entire before source tree
        src_copy = os.path.join(tmpdir, "src")
        shutil.copytree(before_dir, src_copy, dirs_exist_ok=True)

        # Replace the primary file with refactored code
        target = os.path.join(src_copy, rel_path)
        if os.path.exists(target):
            with open(target, "w") as f:
                f.write(refactored_code)

        cache_key = (before_dir, after_dir)
        if cache_key in _changed_files_cache:
            modules, changed_classes = _changed_files_cache[cache_key]
        else:
            modules, changed_classes = find_changed_files(before_dir, after_dir)
            _changed_files_cache[cache_key] = (modules, changed_classes)
        smell_out = os.path.join(tmpdir, "smell_output")
        smells = run_designite(src_copy, smell_out, modules, changed_classes)
        return smells


def _run_evosuite_tests(commit_id, refactored_code, rel_path, before_dir):
    """Run pre-generated EvoSuite tests against refactored code. Returns pass rate or None."""
    test_dir = os.path.join(PROJECT_ROOT, "data", "evosuite_tests", commit_id)
    if not os.path.isdir(test_dir):
        return None

    test_files = glob.glob(os.path.join(test_dir, "**", "*_ESTest.java"), recursive=True)
    if not test_files:
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="val_evo_") as tmpdir:
            # Write refactored code and compile it
            java_file = os.path.join(tmpdir, "src", rel_path)
            os.makedirs(os.path.dirname(java_file), exist_ok=True)
            with open(java_file, "w") as f:
                f.write(refactored_code)

            classes_dir = os.path.join(tmpdir, "classes")
            os.makedirs(classes_dir, exist_ok=True)

            camel_cp = _repo_classpath()
            compile_cmd = ["javac", "-d", classes_dir, "-proc:none", "-nowarn"]
            if camel_cp:
                compile_cmd += ["-cp", camel_cp]
            compile_cmd.append(java_file)
            r = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                return None

            # Compile EvoSuite tests against refactored classes
            evo_jar = glob.glob(os.path.join(PROJECT_ROOT, "tools", "evosuite*.jar"))
            if not evo_jar:
                return None

            test_cp = f"{classes_dir}:{evo_jar[0]}"
            if camel_cp:
                test_cp += f":{camel_cp}"
            test_classes_dir = os.path.join(tmpdir, "test_classes")
            os.makedirs(test_classes_dir, exist_ok=True)

            r = subprocess.run(
                ["javac", "-d", test_classes_dir, "-cp", test_cp, "-nowarn"] + test_files,
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                return None

            # Run tests with JUnit
            run_cp = f"{test_classes_dir}:{test_cp}"
            # Find test class names
            test_classes = []
            for tf in test_files:
                name = os.path.splitext(os.path.basename(tf))[0]
                # Try to extract package from test file
                pkg = None
                with open(tf, errors="replace") as fh:
                    for line in fh:
                        m = re.match(r'\s*package\s+([\w.]+)\s*;', line)
                        if m:
                            pkg = m.group(1)
                            break
                fqcn = f"{pkg}.{name}" if pkg else name
                test_classes.append(fqcn)

            r = subprocess.run(
                ["java", "-cp", run_cp, "org.junit.runner.JUnitCore"] + test_classes,
                capture_output=True, text=True, timeout=60,
            )
            # Parse JUnit output for pass/fail
            m = re.search(r'Tests run:\s*(\d+).*?Failures:\s*(\d+)', r.stdout)
            if m:
                total, failures = int(m.group(1)), int(m.group(2))
                return (total - failures) / total if total > 0 else None
            # If "OK" appears, all passed
            if "OK" in r.stdout:
                return 1.0
            return None
    except Exception:
        return None


def select_best_candidate(candidates, rel_path, before_dir, after_dir, smells_before):
    """From k candidates, pick the best: first filter by compile, then rank by SRR.
    Returns (best_code, compile_ok, smells_after, srr)."""
    if not candidates:
        return "", False, 0, None

    # Phase 1: filter to compiling candidates
    compiling = []
    for code in candidates:
        with tempfile.TemporaryDirectory(prefix="sel_") as tmpdir:
            ok, _ = _compile_check(code, rel_path, tmpdir)
        if ok:
            compiling.append(code)

    print(f"    pass@k: {len(compiling)}/{len(candidates)} compile", flush=True)

    if not compiling:
        # None compile — return first candidate, compute smells on it
        best = candidates[0]
        smells_after = _compute_smells(before_dir, after_dir, best, rel_path)
        srr = ((smells_before - smells_after) / smells_before * 100) if smells_before > 0 else None
        return best, False, smells_after, srr

    if len(compiling) == 1:
        best = compiling[0]
        smells_after = _compute_smells(before_dir, after_dir, best, rel_path)
        srr = ((smells_before - smells_after) / smells_before * 100) if smells_before > 0 else None
        return best, True, smells_after, srr

    # Phase 2: multiple compile — pick best SRR
    best_code, best_srr, best_smells = compiling[0], -999, 0
    for code in compiling:
        sa = _compute_smells(before_dir, after_dir, code, rel_path)
        srr = ((smells_before - sa) / smells_before * 100) if smells_before > 0 else 0
        if srr > best_srr:
            best_code, best_srr, best_smells = code, srr, sa
    return best_code, True, best_smells, best_srr


def validate_node(state: dict) -> dict:
    before_dir = os.path.join(PROJECT_ROOT, state["before_dir"])
    after_dir = os.path.join(PROJECT_ROOT, state["after_dir"])
    refactored_code = state["refactored_code"]

    _, _, rel_path = _get_primary_relpath(before_dir, after_dir)
    if not rel_path:
        return {"compile_ok": False, "smells_after": 0, "srr": None, "test_pass_rate": None}

    # Step 1: Compile check
    with tempfile.TemporaryDirectory(prefix="val_compile_") as tmpdir:
        compile_ok, _ = _compile_check(refactored_code, rel_path, tmpdir)

    attempt = state.get("attempt", 1)
    is_final_attempt = attempt >= 3

    # Skip expensive smell analysis on non-final failed retries
    if not compile_ok and not is_final_attempt:
        return {"compile_ok": False, "smells_after": 0, "srr": None, "test_pass_rate": None}

    # Step 2: Smell counting (final attempt or compile success)
    smells_after = _compute_smells(before_dir, after_dir, refactored_code, rel_path)

    # Step 3: SRR
    sb = state.get("smells_before", 0)
    srr = ((sb - smells_after) / sb * 100) if sb > 0 else None

    # Step 4: EvoSuite tests (only if compile succeeded)
    commit_id = _commit_index(state["before_dir"])
    test_pass_rate = None
    if compile_ok and commit_id:
        test_pass_rate = _run_evosuite_tests(commit_id, refactored_code, rel_path, before_dir)

    return {
        "compile_ok": compile_ok,
        "smells_after": smells_after,
        "srr": srr,
        "test_pass_rate": test_pass_rate,
    }
