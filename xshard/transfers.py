"""Account transfers routed to shards, and the end-of-day balance report."""
import zlib


def shard_of(account, shards):
    names = sorted(shards)
    return names[zlib.crc32(account.encode()) % len(names)]


def transfer_writes(src, dst, amount, shards, placement=None):
    placement = placement or {}
    s = placement.get(src) or shard_of(src, shards)
    d = placement.get(dst) or shard_of(dst, shards)
    writes = {}
    writes.setdefault(s, {})[src] = writes.get(s, {}).get(src, 0) - amount
    writes.setdefault(d, {})[dst] = writes.get(d, {}).get(dst, 0) + amount
    return writes


def balance_report(shards):
    rows = []
    for name in sorted(shards):
        for account, value in sorted(shards[name].data.items()):
            rows.append({"shard": name, "account": account, "balance": value})
    return {"rows": rows, "total": sum(r["balance"] for r in rows)}
