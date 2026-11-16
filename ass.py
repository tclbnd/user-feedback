#!/usr/bin/env python3
r"""ASS karaoke progress sampler (offline, per-frame).

Input : {"events":[{"layer","start","end","text","style"}], "samples":[t_ms]}
Output: {"samples":[{"t","lines":[{"layer","text","spans":[...]}]}...]}

Tag subset (braced text not matching the subset is dropped):
  {\kN} {\kfN} {\koN}  karaoke segment, N centiseconds of fill time for
                       the text run following the tag; the fill fraction is
                       the same for all three, the letter only selects the
                       render style (plain sweep / smooth fill / outline).
  {\r}               reset inline flags AND cancel any pending karaoke
                       segment; restores the event's own style.
  {\i1|\i0} {\b1|\b0} {\u1|\u0}  inline style on/off, persist until 0/\r.
  \n hard line break, \h non-breaking space, {{ / }} literal braces.
"""
import json
import sys

NBSP = " "
KARA_TAGS = ("kf", "ko", "k")          # longest first so kf wins over k
STYLE_KEYS = ("i", "b", "u")


def base_style(event_style):
    """Style the event starts with (per-event style object, else all off)."""
    st = {k: 0 for k in STYLE_KEYS}
    if isinstance(event_style, dict):
        for k in STYLE_KEYS:
            st[k] = 1 if event_style.get(k, 0) else 0
    return st


def tokenize(text):
    """Split raw event text into (kind, value) tokens.

    kind "tag": value is the brace body (outer braces consumed).
    kind "text": raw text with escapes and literal-brace markers kept
    verbatim for later resolution.  Doubled braces {{ }} become literal
    single braces; unbalanced braces are treated as literal text.
    """
    tokens = []
    buf = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            if i + 1 < n and text[i + 1] == "{":      # {{ -> literal {
                buf.append("{")
                i += 2
                continue
            depth, j = 1, i + 1
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth:                                # unbalanced: literal
                buf.append(text[i:])
                break
            if buf:
                tokens.append(("text", "".join(buf)))
                buf = []
            tokens.append(("tag", text[i + 1:j]))
            i = j + 1
        elif ch == "}":
            if i + 1 < n and text[i + 1] == "}":      # }} -> literal }
                buf.append("}")
                i += 2
            else:
                buf.append(ch)                        # lone }: literal
                i += 1
        else:
            buf.append(ch)
            i += 1
    if buf:
        tokens.append(("text", "".join(buf)))
    return tokens


def eval_tag(body):
    """Return an action for one brace body, or None when outside the subset."""
    if body == "\\r":
        return ("reset", None)
    for name in KARA_TAGS:
        prefix = "\\" + name
        tail = body[len(prefix):]
        if body.startswith(prefix) and tail.isdigit():
            mode = name[1] if len(name) > 1 else "k"
            return ("kara", mode, int(tail) * 10)    # centiseconds -> ms
    if len(body) == 3 and body[1] in STYLE_KEYS and body[2] in "01":
        return ("style", body[1], int(body[2]))
    return None


def resolve_escapes(s):
    return s.replace("\\n", "\n").replace("\\h", NBSP)


def parse_event(text, event_style, start, t):
    """Build plain text plus styled spans for one event sampled at time t."""
    style = base_style(event_style)
    pending = None          # dict(mode, begin, dur) awaiting its text run
    raw_spans = []          # [run, style_copy, kara_info_or_None, boundary]
    boundary = False        # a tag was consumed since the last text token

    def push(run):
        info = None
        if pending is not None:
            mode = pending["mode"]
            begin, dur = pending["begin"], pending["dur"]
            elapsed = t - start - begin
            if dur <= 0:
                # zero-length slot: done the instant its boundary is reached
                fill = 1.0 if elapsed >= 0 else 0.0
            else:
                fill = elapsed / dur
                if fill < 0:
                    fill = 0.0
                elif fill > 1:
                    fill = 1.0
            # same timing for k / kf / ko (mode only selects the render style);
            # clamping makes every earlier segment 1 and every later one 0
            info = {"mode": mode, "fill": round(float(fill), 3)}
        raw_spans.append([run, dict(style), info, boundary])

    for kind, value in tokenize(text):
        if kind == "text":
            if value:
                push(value)
            boundary = False                      # text closes the boundary
            continue
        boundary = True                           # every tag splits spans
        action = eval_tag(value)
        if action is None:
            continue                                         # drop unknown tag
        if action[0] == "reset":
            style = base_style(event_style)
            pending = None                                   # cancel timing
        elif action[0] == "style":
            style[action[1]] = action[2]
        else:  # kara: segments chain in text order off the previous one
            begin = pending["begin"] + pending["dur"] if pending else 0
            pending = {"mode": action[1], "begin": begin, "dur": action[2]}

    # merge adjacent text tokens that no tag separated AND that carry the same
    # style+kara; then resolve escapes so each \n / \h stays one character.
    merged = []
    for run, st, kara, broke in raw_spans:
        if (merged and not broke and merged[-1][1] == st
                and merged[-1][2] == kara):
            merged[-1][0] += run
        else:
            merged.append([run, st, kara])

    spans, parts = [], []
    for run, st, kara in merged:
        run = resolve_escapes(run)
        parts.append(run)
        spans.append({"text": run, "style": st, "kara": kara})
    return "".join(parts), spans


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    samples = []
    for t in data["samples"]:
        lines = []
        for idx, e in enumerate(data["events"]):
            if e["start"] <= t < e["end"]:                  # half-open window
                txt, spans = parse_event(e["text"], e.get("style"),
                                         e["start"], t)
                lines.append({"layer": e["layer"], "text": txt,
                              "spans": spans,
                              "_key": (e["layer"], e["start"], idx)})
        lines.sort(key=lambda x: x["_key"])
        for line in lines:
            del line["_key"]
        samples.append({"t": t, "lines": lines})
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"samples": samples}, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
