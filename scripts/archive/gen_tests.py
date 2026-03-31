"""
gen_tests.py — Generate EvoSuite regression tests for each commit's "before" code.

Usage:
  python scripts/gen_tests.py
  python scripts/gen_tests.py --pairs-dir data/pairs --output-dir data/tests
  python scripts/gen_tests.py --evosuite ~/refactor_project/evosuite-1.2.0.jar
  python scripts/gen_tests.py --timeout 60 --limit 5
"""
import argparse, glob, os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from find_primary_java import find_primary_changed_java


def extract_package(java_file):
    """Extract package name from a Java source file."""
    with open(java_file, 'r', errors='replace') as f:
        for line in f:
            m = re.match(r'\s*package\s+([\w.]+)\s*;', line)
            if m:
                return m.group(1)
    return None


def extract_classname(java_file):
    """Extract class name from filename."""
    return os.path.splitext(os.path.basename(java_file))[0]


def compile_java(java_file, output_dir, classpath=None):
    """Compile a single Java file. Returns True on success."""
    cmd = ["javac", "-d", output_dir, "-nowarn"]
    if classpath:
        cmd += ["-cp", classpath]
    cmd.append(java_file)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def run_evosuite(evosuite_jar, target_class, classpath, output_dir, timeout=60, use_cp_file=False):
    """Run EvoSuite to generate tests. Returns True on success."""
    if use_cp_file:
        # classpath is a file path — read it
        with open(classpath) as f:
            cp_value = f.read().strip()
    else:
        cp_value = classpath
    cmd = [
        "java", "-jar", evosuite_jar,
        "-class", target_class,
        "-projectCP", cp_value,
        "-Dtest_dir=" + output_dir,
        "-Dsearch_budget=" + str(timeout),
        "-Dassertion_strategy=ALL",
        "-Dminimize=true",
        "-criterion", "branch",
    ]
    # Use env var to pass classpath if too long
    env = os.environ.copy()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 120, env=env)
        return r.returncode == 0
    except OSError:
        # ARG_MAX exceeded — write args to a script
        import tempfile as tf
        script = tf.NamedTemporaryFile(mode='w', suffix='.sh', delete=False)
        script.write("#!/bin/bash\n")
        script.write(f'java -jar {evosuite_jar} -class {target_class} -projectCP "$(cat {classpath})" '
                     f'-Dtest_dir={output_dir} -Dsearch_budget={timeout} '
                     f'-Dassertion_strategy=ALL -Dminimize=true -criterion branch\n')
        script.close()
        os.chmod(script.name, 0o755)
        r = subprocess.run(["bash", script.name], capture_output=True, text=True, timeout=timeout + 120)
        os.unlink(script.name)
        return r.returncode == 0


def main():
    p = argparse.ArgumentParser(description="Generate EvoSuite tests for commit pairs.")
    p.add_argument("--pairs-dir", default="data/pairs",
                   help="Directory with commit_NNN folders (default: data/pairs)")
    p.add_argument("--output-dir", default="data/evosuite_tests",
                   help="Output dir for generated tests (default: data/evosuite_tests)")
    p.add_argument("--evosuite", default=None,
                   help="Path to evosuite jar (default: auto-detect)")
    p.add_argument("--timeout", type=int, default=60,
                   help="EvoSuite search budget in seconds (default: 60)")
    p.add_argument("--limit", type=int, default=None,
                   help="Process only first N commits")
    p.add_argument("--java-home", default=None,
                   help="JAVA_HOME for EvoSuite (e.g. /usr/lib/jvm/java-11-openjdk-amd64)")
    p.add_argument("--repo", default="data/camel",
                   help="Path to Camel repo for classpath jars (default: data/camel)")
    args = p.parse_args()

    # Auto-detect EvoSuite jar
    evo_jar = args.evosuite
    if not evo_jar:
        candidates = glob.glob(os.path.expanduser("~/refactor_project/tools/evosuite*.jar"))
        if candidates:
            evo_jar = candidates[0]
        else:
            print("ERROR: EvoSuite jar not found. Use --evosuite <path>")
            sys.exit(1)
    print(f"EvoSuite: {evo_jar}")

    if args.java_home:
        os.environ["JAVA_HOME"] = args.java_home
        os.environ["PATH"] = os.path.join(args.java_home, "bin") + ":" + os.environ["PATH"]

    os.makedirs(args.output_dir, exist_ok=True)

    # Build classpath from camel-deps + repo target jars + Maven local repo
    project_root = os.path.join(os.path.dirname(__file__), "..")
    deps_dir = os.path.join(project_root, "tools", "camel-deps")
    jars = []
    if os.path.isdir(deps_dir):
        jars.extend(glob.glob(os.path.join(deps_dir, "*.jar")))
    repo_jars = glob.glob(os.path.join(args.repo, "**/target/*.jar"), recursive=True)
    jars.extend(repo_jars)
    # Maven local repo for transitive deps (log4j, maven, etc.)
    m2_repo = os.path.expanduser("~/.m2/repository")
    if os.path.isdir(m2_repo):
        m2_jars = glob.glob(os.path.join(m2_repo, "**", "*.jar"), recursive=True)
        jars.extend(m2_jars)
        print(f"Classpath: {len(jars)} jars (camel-deps + repo + {len(m2_jars)} from .m2)")
    else:
        print(f"Classpath: {len(jars)} jars (camel-deps + repo)")
    camel_cp = ":".join(jars) if jars else ""

    # Find all commit dirs
    commit_dirs = sorted(glob.glob(os.path.join(args.pairs_dir, "commit_*")))
    if args.limit:
        commit_dirs = commit_dirs[:args.limit]

    print(f"Processing {len(commit_dirs)} commits...\n")
    success, fail, skip = 0, 0, 0

    for cd in commit_dirs:
        name = os.path.basename(cd)
        before_dir = os.path.join(cd, "before")
        after_dir = os.path.join(cd, "after")

        if not os.path.isdir(before_dir) or not os.path.isdir(after_dir):
            print(f"  [{name}] SKIP — missing before/after dirs")
            skip += 1
            continue

        bf, _ = find_primary_changed_java(before_dir, after_dir)
        if not bf:
            print(f"  [{name}] SKIP — no changed Java file found")
            skip += 1
            continue

        pkg = extract_package(bf)
        cls = extract_classname(bf)
        fqcn = f"{pkg}.{cls}" if pkg else cls
        print(f"  [{name}] {fqcn} ...", end=" ", flush=True)

        # Compile with Camel jars + sourcepath for sibling source resolution
        with tempfile.TemporaryDirectory() as compile_dir:
            # Find all src/main/java dirs for sourcepath
            src_roots = glob.glob(os.path.join(cd, "before", "src", "**", "src", "main", "java"), recursive=True)
            sourcepath = ":".join(src_roots) if src_roots else ""

            # Write args to file to avoid ARG_MAX limit
            args_file = os.path.join(compile_dir, "javac_args.txt")
            with open(args_file, "w") as af:
                af.write(f"-d {compile_dir}\n")
                af.write("-nowarn\n-proc:none\n")
                if camel_cp:
                    af.write(f"-cp {camel_cp}\n")
                if sourcepath:
                    af.write(f"-sourcepath {sourcepath}\n")
                af.write(f"{bf}\n")
            r = subprocess.run(["javac", f"@{args_file}"], capture_output=True, text=True, timeout=60)
            compiled = r.returncode == 0

            if not compiled:
                print("FAIL (compile)")
                fail += 1
                continue

            # EvoSuite needs both compiled classes and Camel jars
            evo_cp = compile_dir + ":" + camel_cp if camel_cp else compile_dir
            test_out = os.path.join(args.output_dir, name)
            os.makedirs(test_out, exist_ok=True)

            # Write classpath to file for EvoSuite (avoid ARG_MAX)
            evo_cp_file = os.path.join(compile_dir, "evo_cp.txt")
            with open(evo_cp_file, "w") as ecf:
                ecf.write(evo_cp)

            ok = run_evosuite(evo_jar, fqcn, evo_cp_file, test_out, args.timeout, use_cp_file=True)
            if ok:
                # Check if test files were actually generated
                test_files = glob.glob(os.path.join(test_out, "**", "*.java"), recursive=True)
                if test_files:
                    print(f"OK ({len(test_files)} files)")
                    success += 1
                else:
                    print("FAIL (no tests generated)")
                    fail += 1
            else:
                print("FAIL (evosuite)")
                fail += 1

    print(f"\nDone: {success} OK, {fail} failed, {skip} skipped out of {len(commit_dirs)}")


if __name__ == "__main__":
    main()
