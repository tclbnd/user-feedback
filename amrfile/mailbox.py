"""Mailbox quota accounting."""


def usage(messages, quota_seconds=1800):
    """messages: list of dicts with duration_ms. Returns used seconds and whether over quota."""
    used = sum(m["duration_ms"] for m in messages) / 1000
    return {"used_seconds": round(used, 1), "over_quota": used > quota_seconds,
            "remaining_seconds": max(0, round(quota_seconds - used, 1))}


def oldest_first(messages):
    return sorted(messages, key=lambda m: m["received"])
