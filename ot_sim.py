import json
import sys
from pathlib import Path


def main(input_path, output_path):
    data = json.loads(Path(input_path).read_text())
    finals = []
    for order in data["orders"]:
        text = data["doc"]
        for batch in order:
            for op in data["batches"][batch]:
                pos = op["pos"]
                if op["op"] == "ins":
                    text = text[:pos] + op["text"] + text[pos:]
                else:
                    text = text[:pos] + text[pos + op["len"]:]
        finals.append(text)
    result = dict(final=finals, converged=len(set(finals)) == 1,
                  dedupe=dict(unique=0, duplicates=0), audit=[])
    Path(output_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main(*sys.argv[1:])
