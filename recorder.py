import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from ingest import read_capture
from mux import make_playlist
from playlist import publish
from state import record_progress


def record(log: Path, output: Path) -> None:
    session = read_capture(log)
    with TemporaryDirectory() as stage:
        stage = Path(stage)
        make_playlist(session, stage)
        publish(stage, output)
    record_progress(output, session)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    record(args.capture, args.output)


if __name__ == "__main__":
    main()
