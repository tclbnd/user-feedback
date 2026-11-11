"""Crash-recovery acceptance: kill at three points, replay, compare state.

For each crash point the pipeline is run as a *subprocess* that hard-exits
(os._exit, simulating kill -9), then the original input is replayed through
the normal resume path and, separately, a full-scan path. The resulting
records, rejections, conflicts and checkpoint must equal a clean baseline run
byte-for-byte at the content level.
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

from ndjson_pipe.compare import compare_stores
from ndjson_pipe.importer import (
    CRASH_AFTER_COMMIT,
    CRASH_BEFORE_PERSIST,
    CRASH_EXIT_CODE,
    CRASH_IN_TRANSACTION,
    import_source,
)
from ndjson_pipe.store import Store

CRASH_POINTS = (CRASH_BEFORE_PERSIST, CRASH_IN_TRANSACTION, CRASH_AFTER_COMMIT)


def _run_subprocess(db_path: str, source_id: str, file_path: str, point: str, line_no: int,
                    chunk_size: int, full_scan: bool = False) -> int:
    env = dict(os.environ)
    env["PIPE_CRASH_POINT"] = point
    env["PIPE_CRASH_LINE"] = str(line_no)
    # Run an explicit absolute runner script: the interpreter then adds only
    # the trusted project root to sys.path (no reliance on the caller's cwd),
    # while -I still blocks site-packages and user-site contamination.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runner = os.path.join(project_root, "ndjson_pipe", "_subprocess_runner.py")
    cmd = [
        sys.executable,
        "-I",
        runner,
        "import",
        "--db",
        db_path,
        "--source-id",
        source_id,
        "--file",
        file_path,
        "--chunk-size",
        str(chunk_size),
        "--batch-size",
        "3",
    ]
    if full_scan:
        cmd.append("--full-scan")
    proc = subprocess.run(
        cmd, env=env, cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return proc.returncode


def crash_recovery_matrix(
    source_file: str,
    source_id: str,
    workdir: str,
    *,
    chunk_size: int = 7,
    crash_line: int = 3,
) -> Tuple[bool, List[dict]]:
    """Run all three crash points and return (overall_ok, per-point reports)."""
    with open(source_file, "rb") as fh:
        data = fh.read()

    reports = []
    overall_ok = True

    # Clean baseline: normal streaming import with no crash injection.
    baseline_db = os.path.join(workdir, "baseline.db")
    with Store(baseline_db) as store:
        import_source(store, source_id, source_file, chunk_size=chunk_size, batch_size=3)

    for point in CRASH_POINTS:
        report = {"point": point}
        db_path = os.path.join(workdir, f"crash-{point}.db")
        crashed_file = os.path.join(workdir, f"crash-{point}.ndjson")
        with open(crashed_file, "wb") as fh:
            fh.write(data)

        code = _run_subprocess(db_path, source_id, crashed_file, point, crash_line, chunk_size)
        report["exit_code"] = code
        if code != CRASH_EXIT_CODE:
            report["error"] = f"expected hard exit {CRASH_EXIT_CODE}, got {code}"
            overall_ok = False
            reports.append(report)
            continue

        # Replay 1: resume from the durable checkpoint (same file, no full scan).
        resume_code = _run_subprocess(db_path, source_id, crashed_file, "__none__",
                                      crash_line, chunk_size)
        report["resume_exit_code"] = resume_code

        with Store(db_path) as crashed_store, Store(baseline_db) as baseline_store:
            diffs = compare_stores(source_id, baseline_store, crashed_store)
        report["diffs_after_resume"] = diffs
        if diffs:
            overall_ok = False

        # Replay 2: full-scan redelivery of the identical original input.
        scan_code = _run_subprocess(db_path, source_id, crashed_file, "__none__",
                                    crash_line, chunk_size, full_scan=True)
        report["full_scan_exit_code"] = scan_code
        with Store(db_path) as crashed_store, Store(baseline_db) as baseline_store:
            diffs_scan = compare_stores(source_id, baseline_store, crashed_store)
        report["diffs_after_full_scan"] = diffs_scan
        if diffs_scan:
            overall_ok = False

        # Final checkpoint must equal the source length.
        with Store(db_path) as crashed_store:
            report["checkpoint"] = crashed_store.get_checkpoint(source_id)
        report["checkpoint_ok"] = report["checkpoint"] == len(data)
        overall_ok = overall_ok and report["checkpoint_ok"]
        reports.append(report)

    return overall_ok, reports


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Crash-recovery acceptance runner")
    parser.add_argument("--file", required=True)
    parser.add_argument("--source-id", default="acceptance-source")
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--chunk-size", type=int, default=7)
    parser.add_argument("--crash-line", type=int, default=3)
    args = parser.parse_args(argv)

    workdir = args.workdir or tempfile.mkdtemp(prefix="ndjson-crash-")
    os.makedirs(workdir, exist_ok=True)
    ok, reports = crash_recovery_matrix(
        args.file,
        args.source_id,
        workdir,
        chunk_size=args.chunk_size,
        crash_line=args.crash_line,
    )
    json.dump({"ok": ok, "workdir": workdir, "reports": reports},
              sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
