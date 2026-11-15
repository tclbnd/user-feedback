import argparse, hashlib, json
from pathlib import Path

def process(data):
    hops_map = data["hops"]; results = []
    for req in data["requests"]:
        url = req["url"]; hops = []; seen = {url}
        term, final = "ok", url
        while url in hops_map and len(hops) < data["max_hops"]:
            h = hops_map[url]; loc = h["location"]
            hops.append({"url": url, "status": h["status"], "location": loc})
            if loc.startswith("/"):
                base = url.split("/", 3)
                url = base[0] + "//" + base[2] + loc
            else:
                url = loc
            if url in seen:
                term, final = "loop", url
                break
            seen.add(url)
            final = url
        else:
            if len(hops) >= data["max_hops"]:
                term = "limit"
        results.append({"id": req["id"], "final": final, "terminal": term, "hops": hops})
    digest = hashlib.sha256(json.dumps(results, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"results": results, "trace_digest": digest}

def main():
    a = argparse.ArgumentParser(); a.add_argument("input", type=Path); a.add_argument("output", type=Path)
    r = a.parse_args()
    r.output.write_text(json.dumps(process(json.loads(r.input.read_text())), ensure_ascii=False) + "\n")
main()
