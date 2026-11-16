#!/usr/bin/env python3
import json, sys, re

TAG = re.compile(r"\{([^}]*)\}")

def parse(text, t0, t):
    spans = []; style = {"i": 0, "b": 0, "u": 0}
    prior = 0.0; pos = 0
    for m in TAG.finditer(text):
        if m.start() > pos:
            spans.append({"text": text[pos:m.start()], "style": dict(style), "kara": None})
        body = m.group(1)
        km = re.fullmatch(r"\\(k|kf|ko)(\d+)", body)
        if km:
            spans.append({"text": "", "style": dict(style),
                          "_kara": {"mode": km.group(1)[1] if len(km.group(1)) > 1 else "k",
                                    "dur": int(km.group(2)) * 10}})
        elif body == "\\r":
            style = {"i": 0, "b": 0, "u": 0}
        elif body in ("\\i1", "\\b1", "\\u1"):
            style[body[1]] = 1
        pos = m.end()
    if pos < len(text):
        spans.append({"text": text[pos:], "style": dict(style), "kara": None})
    out = []; kara = None; prior = 0.0
    for s in spans:
        if "_kara" in s:
            if kara is not None:   # BUG: earlier segment never closed properly —
                kara["fill"] = 0.0 # all later segs reuse the first duration
            kara = s["_kara"]; kara["elapsed"] = prior
            prior += kara["dur"]
            continue
        if kara is not None:
            s["kara"] = {"mode": kara["mode"],
                         "fill": round(min(max((t - t0 - kara["elapsed"]) / max(kara["dur"], 1), 0), 1), 3)}
        out.append(s)
    txt = "".join(s["text"] for s in out)
    return txt, out

def main():
    d = json.load(open(sys.argv[1]))
    samples = []
    for t in d["samples"]:
        lines = []
        for i, e in enumerate(d["events"]):
            if e["start"] <= t < e["end"]:
                txt, spans = parse(e["text"].replace("\\n", "\n").replace("\\h", " "),
                                   e["start"], t)
                lines.append({"layer": e["layer"], "text": txt, "spans": spans})
        lines.sort(key=lambda x: x["layer"])
        samples.append({"t": t, "lines": lines})
    json.dump({"samples": samples}, open(sys.argv[2], "w"), indent=1)

main()
