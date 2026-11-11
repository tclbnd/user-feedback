"""Command line entry point for the event recorder.

Usage (unchanged from the baseline)::

    python3 recorder.py capture.jsonl published/

The pipeline is crash-safe by construction:

1. the capture log is replayed deterministically into a :class:`~mux.MuxPlan`;
2. init sections and segments are written to temp names and atomically renamed;
3. the playlist is atomically replaced only once every referenced file exists;
4. the progress record is the very last write.

Two environment variables inject hard process kills for recovery testing:
``RECORDER_CRASH_AFTER_SEGMENT`` and ``RECORDER_CRASH_BEFORE_PROGRESS``.
"""

import argparse
from pathlib import Path

from ingest import read_capture
from mux import build_plan, publish_plan
from state import record_progress


def record(log: Path, output: Path) -> None:
    session = read_capture(log)
    plan = build_plan(session)
    publish_plan(plan, output)
    record_progress(output, session, plan)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package a dual-track fMP4 capture into an HLS EVENT playlist")
    parser.add_argument("capture", type=Path, help="path to capture.jsonl")
    parser.add_argument("output", type=Path, help="publication directory")
    args = parser.parse_args()
    record(args.capture, args.output)


if __name__ == "__main__":
    main()
