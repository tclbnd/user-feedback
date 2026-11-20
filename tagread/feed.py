"""Fields the podcast feed takes from a tag summary."""


def first(summary, frame_id):
    values = summary["text"].get(frame_id) or []
    return values[0] if values else None


def feed_item(summary):
    return {
        "title": first(summary, "TIT2"),
        "author": ", ".join(summary["text"].get("TPE1") or []),
        "album": first(summary, "TALB"),
        "genre": first(summary, "TCON"),
        "explicit": summary["user"].get("EXPLICIT", "no").lower() in ("yes", "true", "1"),
    }
