"""Caption quality checks shown to the subtitling vendor."""


def reading_speed(cue):
    seconds = (cue["end"] - cue["start"]) / 1000
    chars = len(cue["text"].replace("\n", ""))
    return chars / seconds if seconds > 0 else float("inf")


def report(cues, max_cps=20, max_lines=2, min_gap_ms=80):
    issues = []
    for i, cue in enumerate(cues):
        if reading_speed(cue) > max_cps:
            issues.append({"cue": i, "issue": "too fast"})
        if cue["text"].count("\n") + 1 > max_lines:
            issues.append({"cue": i, "issue": "too many lines"})
        if i and cue["start"] - cues[i - 1]["end"] < min_gap_ms and cue["start"] >= cues[i - 1]["end"]:
            issues.append({"cue": i, "issue": "gap too short"})
    return issues
