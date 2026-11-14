import json
import sys
from pathlib import Path


def main(input_path, output_path):
    data = json.loads(Path(input_path).read_text())
    rows = [row for batch in data["batches"] for row in batch if row["op"] == "upsert"]
    Path(output_path).write_text(json.dumps(dict(rows=rows, buckets=[], changes=[])))


if __name__ == "__main__":
    main(*sys.argv[1:])
