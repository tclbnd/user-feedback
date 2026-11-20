"""Inbox retention: which voice notes expire and how much storage they free."""
from datetime import datetime, timedelta


def expired(notes, now, days=30, keep_starred=True):
    """notes: dicts with id, received (ISO), starred, bytes."""
    limit = datetime.fromisoformat(now) - timedelta(days=days)
    out = []
    for n in notes:
        if keep_starred and n.get("starred"):
            continue
        if datetime.fromisoformat(n["received"]) < limit:
            out.append(n["id"])
    return out


def freed_bytes(notes, ids):
    wanted = set(ids)
    return sum(n["bytes"] for n in notes if n["id"] in wanted)
