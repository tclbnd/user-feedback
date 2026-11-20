"""Release sheet checks against the label's delivery spec."""

REQUIRED = ("TITLE", "ARTIST", "ALBUM", "ISRC")


def missing_tags(tags):
    present = {k.upper() for k in tags}
    return [k for k in REQUIRED if k not in present]


def track_sheet(items):
    """items: list of (path, probe result) -> rows for the release sheet."""
    rows = []
    for path, info in items:
        tags = info.get("tags", {})
        rows.append({"file": path, "title": tags.get("TITLE"), "seconds": info.get("duration"),
                     "missing": missing_tags(tags), "has_cover": bool(info.get("pictures"))})
    return rows
