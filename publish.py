import json
import sys
from pathlib import Path


def main(request_path, warehouse_path):
    request = json.loads(Path(request_path).read_text())
    warehouse = Path(warehouse_path)
    warehouse.mkdir(parents=True, exist_ok=True)
    pointer = warehouse / "CURRENT.json"
    pointer.write_text(json.dumps({
        "version": request["expected_version"] + 1,
        "manifest": "unfinished.json",
    }) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
