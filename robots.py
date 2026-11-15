import argparse, json
from pathlib import Path

def process(data):
    rules = []
    for i, line in enumerate(data["robots"].splitlines()):
        line = line.split("#", 1)[0].strip()
        if line.startswith("Disallow:"):
            rules.append((i + 1, line[9:].strip(), False))
        elif line.startswith("Allow:"):
            rules.append((i + 1, line[6:].strip(), True))
    from urllib.parse import urlparse
    out = []
    for u in data["urls"]:
        path = urlparse(u).path
        hit = None
        for ln, pat, allow in rules:
            if pat and path.startswith(pat):
                hit = (ln, pat, allow)
                break
        out.append({"url": u, "allowed": hit[2] if hit else True,
                    "rule": ("Disallow:" if hit and not hit[2] else "Allow:") + hit[1] if hit else None,
                    "line": hit[0] if hit else None})
    return {"decisions": out, "crawl_delay": None, "skipped": []}

def main():
    a = argparse.ArgumentParser(); a.add_argument("input", type=Path); a.add_argument("output", type=Path)
    r = a.parse_args()
    r.output.write_text(json.dumps(process(json.loads(r.input.read_text())), ensure_ascii=False) + "\n")
main()
