def convert(params):
    pairs = []
    for kv in params.split():
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        if k == "cqm":
            continue
        pairs.append(k + "=" + v.replace(":", ","))
    return ":".join(pairs)
