def process(events):
    active = {}
    published = []
    epochs = {}
    results = []
    comparisons = 0
    for event in events:
        seq, operation = event["seq"], event["op"]
        result = {"seq": seq, "status": operation}
        if operation == "reserve":
            key, start, end = event["key"], event["start"], event["end"]
            blockers = []
            for lease in active.values():
                if lease["key"] != key:
                    continue
                comparisons += 1
                if start <= lease["end"] and lease["start"] <= end:
                    blockers.append(lease["lease"])
            if blockers:
                result.update(status="conflict", blockers=sorted(blockers))
            else:
                epochs[key] = epochs.get(key, 0) + 1
                active[seq] = dict(key=key, lease=seq, epoch=epochs[key], start=start, end=end,
                                   expires=event["at"] + event["ttl"])
                result.update(status="reserved", lease=seq, epoch=epochs[key])
        elif operation in ("release", "publish"):
            lease = active.get(event["lease"])
            if lease is None:
                result["status"] = "stale"
            elif operation == "release":
                del active[event["lease"]]
                result["status"] = "released"
            else:
                published.append(lease)
                result["status"] = "published"
        results.append(result)

    def by_key(lease):
        return lease["key"], lease["lease"]

    return {"results": results, "active": sorted(active.values(), key=by_key),
            "published": sorted(published, key=by_key), "epochs": epochs,
            "stats": {"overlap_checks": comparisons}}
