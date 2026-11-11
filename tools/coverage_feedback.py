"""Coverage-feedback driver using the stdlib ``trace`` module.

Differential feedback is applied in the order required by the task:

1. legal synthetic corpus structures and chunk-split combinations are
   expanded first (seeded generation in :mod:`tests.corpus`);
2. business rejections use fixed samples and are never regenerated.

This driver runs the full suite under line tracing, reports per-file line
coverage for the streaming/normalization modules and exits non-zero if the
measured coverage falls below the threshold. Missing lines are printed so the
corpus generator can be extended deliberately rather than by accident.
"""

import linecache
import os
import sys
import trace
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACKED_PACKAGES = ("ndjson_pipe/decoder.py", "ndjson_pipe/normalize.py",
                    "ndjson_pipe/importer.py", "ndjson_pipe/reference.py")
COVERAGE_THRESHOLD = 0.95


def run_with_coverage() -> int:
    # Ensure the project root is importable even when this file is launched
    # as a plain script (python tools/coverage_feedback.py).
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    # Drop pre-imported submodules (but keep the package objects themselves)
    # so module-level code is re-executed *under* the tracer without creating
    # a second, identity-split copy of the parent package.
    for name in list(sys.modules):
        if name.startswith("ndjson_pipe.") or name.startswith("tests."):
            del sys.modules[name]

    # No ignoredirs: on macOS sys.prefix lives under /Library while the
    # interpreter reports /Library/... via a /tmp-style symlinked realpath in
    # some setups, which made stdlib trace ignore project files wholesale.
    # Counts are filtered to the four tracked files explicitly below.
    tracer = trace.Trace(count=True, trace=False)

    def _run():
        loader = unittest.TestLoader()
        suite = loader.discover(os.path.join(PROJECT_ROOT, "tests"))
        return unittest.TextTestRunner(verbosity=1).run(suite)

    result = tracer.runfunc(_run)
    if not result.wasSuccessful():
        return 1

    # Normalize tracer keys via realpath: macOS can hand back symlinked paths
    # (e.g. /Library/Developer/.../Python3) that differ only textually.
    raw_counts = tracer.results().counts
    counts = {}
    for (filename, lineno), count in raw_counts.items():
        counts[(os.path.realpath(filename), lineno)] = count

    per_file = {}
    for rel in TRACKED_PACKAGES:
        path = os.path.realpath(os.path.join(PROJECT_ROOT, rel))
        executable = set(_executable_lines(path))
        hit = {lineno for (f, lineno), count in counts.items()
               if f == path and count > 0 and lineno in executable}
        per_file[rel] = (len(hit), len(executable), sorted(executable - hit))

    print("\n=== coverage feedback (stdlib trace) ===")
    aggregate_hit = aggregate_total = 0
    for rel, (hit, total, missing) in sorted(per_file.items()):
        pct = 100.0 * hit / max(total, 1)
        aggregate_hit += hit
        aggregate_total += total
        print(f"  {rel:<32} {hit:>4}/{total:<4} {pct:5.1f}%")
        for lineno in missing[:8]:
            print(f"      uncovered line {lineno}: "
                  f"{linecache.getline(os.path.join(PROJECT_ROOT, rel), lineno).strip()[:70]}")
    overall = aggregate_hit / max(aggregate_total, 1)
    print(f"  {'AGGREGATE':<32} {aggregate_hit:>4}/{aggregate_total:<4} "
          f"{100.0 * overall:5.1f}%  (threshold {100.0 * COVERAGE_THRESHOLD:.0f}%)")

    if overall < COVERAGE_THRESHOLD:
        print("FAIL: coverage below threshold; extend the LEGAL corpus / chunk "
              "combinations before touching fixed rejection samples.", file=sys.stderr)
        return 2
    return 0


def _executable_lines(path):
    """Lines that hold the start of an executable statement.

    The stdlib line tracer records a hit on a statement's first line only,
    so this set matches its key space exactly. Standalone docstrings (an
    ``Expr`` wrapping a string constant) are declarative documentation and
    excluded; continuation lines of multi-line statements are excluded the
    same way. Blank and comment-only lines never appear in the AST.
    """
    import ast

    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
                and isinstance(node.value.value, str):
            continue  # standalone docstring
        lines.add(node.lineno)
    return sorted(lines)


if __name__ == "__main__":
    raise SystemExit(run_with_coverage())
