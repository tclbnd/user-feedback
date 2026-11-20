"""Release sheet checks against the label's delivery spec.

Tag dictionaries produced by :mod:`flacmeta.parse` map an upper-case field
name to the list of values seen for that field, in order of appearance.
"""

REQUIRED = ("TITLE", "ARTIST", "ALBUM", "ISRC")


def _values(tags, name):
    return tags.get(name.upper(), [])


def missing_tags(tags):
    """Return the required field names that have no value at all."""
    return [name for name in REQUIRED if not _values(tags, name)]


def first_value(tags, name):
    """First value for a field, or None when the field is absent."""
    values = _values(tags, name)
    return values[0] if values else None


def track_sheet(items):
    """items: list of (path, probe result) -> rows for the release sheet."""
    rows = []
    for path, info in items:
        tags = info.get("tags", {})
        rows.append({
            "file": path,
            "title": first_value(tags, "TITLE"),
            "artists": list(_values(tags, "ARTIST")),
            "seconds": info.get("duration"),
            "missing": missing_tags(tags),
            "has_cover": bool(info.get("pictures")),
        })
    return rows
