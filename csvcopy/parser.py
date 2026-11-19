"""Splitting CSV input into records and fields.

Each field comes back as (text, quoted) so the loader can apply the NULL
rules, which depend on whether the field was quoted.
"""


def records(data, opts):
    for line in data.split("\n"):
        if line == "":
            continue
        yield split(line, opts)


def split(line, opts):
    fields = []
    for raw in line.split(opts["delimiter"]):
        q = opts["quote"]
        if len(raw) >= 2 and raw[0] == q and raw[-1] == q:
            fields.append((raw[1:-1], True))
        else:
            fields.append((raw, False))
    return fields
