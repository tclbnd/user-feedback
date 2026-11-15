import argparse
import json
from pathlib import Path


def process(data):
    documents = []
    for document in data["documents"]:
        label = document.get("http_charset") or "utf-8"
        body = bytes.fromhex(document["body_hex"])
        try:
            text = body.decode(label, errors="replace")
        except LookupError:
            label = "utf-8"
            text = body.decode(label, errors="replace")
        documents.append({"id": document["id"], "text": text, "encoding": label})
    return {"documents": documents}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = process(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
