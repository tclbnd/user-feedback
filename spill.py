"""Join strategy for a build side that does not fit in the hash table."""
import json

from hashjoin import key_of


def nested_loop(joiner, left_path, right_path):
    """Join block by block when the build side does not fit in the hash table."""
    matched, block = set(), []

    def flush():
        hit = [False] * len(block)
        for i, row in enumerate(read_rows(left_path)):
            lk = key_of(row, joiner.left_col)
            for n, other in enumerate(block):
                if lk != key_of(other, joiner.right_col):
                    continue
                hit[n] = True
                if joiner.kind == "semi" and i in matched:
                    break
                if joiner.kind in ("inner", "left", "right"):
                    joiner.emit({"l": row, "r": other})
                elif joiner.kind == "semi":
                    joiner.emit(row)
                matched.add(i)
        if joiner.kind == "right":
            for n, other in enumerate(block):
                if not hit[n]:
                    joiner.emit({"l": None, "r": other})

    for row in read_rows(right_path):
        block.append(row)
        if len(block) == joiner.work_mem:
            flush()
            block = []
    if block:
        flush()
    for i, row in enumerate(read_rows(left_path)):
        if i not in matched:
            if joiner.kind == "left":
                joiner.emit({"l": row, "r": None})
            elif joiner.kind == "anti":
                joiner.emit(row)


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
