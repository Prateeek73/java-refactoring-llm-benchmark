"""
find_primary_java.py — Find the primary changed Java file in a commit pair.

Unlike the guide's version (which picks the largest file in the whole dir),
this finds files that actually changed between before/after, then returns
the largest changed file — the one most likely to be the main refactored class.

Usage as module:
  from find_primary_java import find_primary_changed_java
  before_file, after_file = find_primary_changed_java(before_dir, after_dir)
"""
import os


def find_primary_java(directory):
    """Return path to largest .java file — proxy for the main class."""
    files = []
    for root, _, fnames in os.walk(directory):
        for f in fnames:
            if f.endswith(".java"):
                p = os.path.join(root, f)
                files.append((os.path.getsize(p), p))
    return max(files)[1] if files else None


def find_primary_changed_java(before_dir, after_dir):
    """Find the largest changed .java file between before/after dirs.

    Returns (before_path, after_path) for the primary changed file,
    or (None, None) if no changed Java files found.
    """
    changed = []
    for root, _, files in os.walk(after_dir):
        for f in files:
            if not f.endswith(".java"):
                continue
            af = os.path.join(root, f)
            rel = os.path.relpath(af, after_dir)
            bf = os.path.join(before_dir, rel)

            is_changed = False
            if not os.path.exists(bf):
                is_changed = True
            else:
                with open(bf, 'rb') as b, open(af, 'rb') as a:
                    if b.read() != a.read():
                        is_changed = True

            if is_changed:
                size = os.path.getsize(af)
                changed.append((size, rel))

    if not changed:
        return None, None

    # Pick largest changed file
    _, rel = max(changed)
    bf = os.path.join(before_dir, rel)
    af = os.path.join(after_dir, rel)

    # before_path might not exist (new file)
    bf = bf if os.path.exists(bf) else None
    return bf, af
