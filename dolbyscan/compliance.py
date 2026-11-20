"""Delivery spec checks for broadcast programmes."""


def check(summary, spec):
    issues = []
    if summary["sample_rate"] != spec.get("sample_rate", 48000):
        issues.append("sample rate")
    if spec.get("channels") and summary["channels"] != spec["channels"]:
        issues.append("channel count")
    if "dialnorm" in spec and summary["dialnorm"] != spec["dialnorm"]:
        issues.append("dialnorm")
    if spec.get("max_bitrate") and (summary.get("bitrate") or 0) > spec["max_bitrate"]:
        issues.append("bitrate")
    return issues
