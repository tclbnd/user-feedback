import argparse
import json
from pathlib import Path

from reservations import process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()

    history = []
    if args.journal.exists():
        history = [json.loads(line) for line in args.journal.read_text().splitlines()]
    incoming = json.loads(args.input.read_text())["events"]
    events = sorted(history + incoming, key=lambda event: event["seq"])
    outcome = process(events)
    with args.journal.open("a", encoding="utf-8") as stream:
        for event in incoming:
            stream.write(json.dumps(event) + "\n")
    args.output.write_text(json.dumps(outcome, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
