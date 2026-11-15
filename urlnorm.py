import argparse, json
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode

def norm(url, pol):
    p = urlparse(url.strip())
    scheme = p.scheme.lower(); host = p.hostname or ""
    port = p.port
    netloc = host + (":" + str(port) if port else "")
    qs = parse_qsl(p.query, keep_blank_values=True)
    qs = [(k, v) for k, v in qs if k not in pol.get("params", [])]
    qs.sort()
    path = p.path or "/"
    frag = "#" + p.fragment if p.fragment else ""
    return scheme + "://" + netloc + path + ("?" + urlencode(qs) if qs else "") + frag

def process(data):
    canon = {u: norm(u, data["policy"]) for u in data["urls"]}
    uniq = sorted(set(canon.values()))
    return {"canonical": canon, "unique": uniq,
            "dropped": len(canon) - len(uniq),
            "stats": {"total": len(data["urls"]), "unique": len(uniq)}}

def main():
    a = argparse.ArgumentParser(); a.add_argument("input", type=Path); a.add_argument("output", type=Path)
    r = a.parse_args()
    r.output.write_text(json.dumps(process(json.loads(r.input.read_text())), ensure_ascii=False) + "\n")
main()
