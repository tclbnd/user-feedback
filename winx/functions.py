"""Window functions evaluated over the frame (aggregates and value functions)
or over the ordered partition (ranking functions)."""


def agg(name, values):
    vals = [v for v in values if v is not None]
    if name == "count":
        return len(vals)
    if name == "count_star":
        return len(values)
    if not vals:
        return None
    if name == "sum":
        return sum(vals)
    if name == "avg":
        return sum(vals) / len(vals)
    if name == "min":
        return min(vals)
    if name == "max":
        return max(vals)
    raise ValueError(name)


def value_fn(name, values, n=None):
    if not values:
        return None
    if name == "first_value":
        return values[0]
    if name == "last_value":
        return values[-1]
    if name == "nth_value":
        return values[n - 1] if 0 < n <= len(values) else None
    raise ValueError(name)


def ranking(name, keys, i):
    if name == "row_number":
        return i + 1
    if name == "rank":
        return 1 + sum(1 for k in keys[:i] if k != keys[i])
    if name == "dense_rank":
        return 1 + len({k for k in keys[:i] if k != keys[i]})
    raise ValueError(name)
