"""Release sheet checks against the label's delivery spec."""

REQUIRED = ("TITLE", "ARTIST", "ALBUM", "ISRC")


def _field(tags, name):
    """All values for a field in appearance order (tags map to lists)."""
    return tags.get(name, [])


def missing_tags(tags):
    present = {key.upper() for key in tags}
    return [key for key in REQUIRED if key not in present]


def track_sheet(items):
    """items: list of (path, probe result) -> rows for the release sheet.

    Repeated tags stay in order: ``artists`` carries every ARTIST value and
    ``title`` keeps all TITLE values instead of silently dropping the later
    ones.
    """
    rows = []
    for path, info in items:
        tags = info.get("tags", {})
        rows.append({
            "file": path,
            "title": _field(tags, "TITLE"),
            "artists": _field(tags, "ARTIST"),
            "seconds": info.get("duration"),
            "missing": missing_tags(tags),
            "has_cover": bool(info.get("pictures")),
        })
    return rows
