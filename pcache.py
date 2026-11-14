import json
import sys
from pathlib import Path


def split_predicate(term):
    """把 'dept=eng' / 'level>5' 拆成 (字段, 操作符, 右值)。"""
    if "=" in term:
        field, value = term.split("=", 1)
        return field, "=", value
    if ">" in term:
        field, value = term.split(">", 1)
        return field, ">", value
    raise ValueError("unsupported predicate: %r" % term)


def evaluate(row, rules):
    """按规则顺序求值。

    规则内谓词按数组顺序短路检查（不匹配立即停止），首个全部命中的规则
    决定 allow，无规则命中返回 False。
    等号比字符串，大于号比整数，缺字段不命中。
    返回 (结论, 本次求值实际读取的字段集合)。
    """
    read_fields = set()
    for rule in rules:
        matched = True
        for term in rule["pred"]:
            field, op, expected = split_predicate(term)
            read_fields.add(field)
            if field not in row:
                matched = False
                break
            if op == "=":
                if str(row[field]) != expected:
                    matched = False
                    break
            else:  # op == ">"
                if not int(row[field]) > int(expected):
                    matched = False
                    break
        if matched:
            return rule["allow"], frozenset(read_fields)
    return False, frozenset(read_fields)


def build_dep_index(cache):
    """字段 -> 最终缓存中依赖该字段的行 ID，键与值均按升序输出。"""
    index = {}
    for row_id in sorted(cache):
        for field in cache[row_id]["deps"]:
            index.setdefault(field, set()).add(row_id)
    return {field: sorted(ids) for field, ids in sorted(index.items())}


def main(input_path, output_path):
    data = json.loads(Path(input_path).read_text())
    rows = [query["sub"] for query in data["queries"]]
    rules = data["rules"]

    # 缓存条目: row_id -> {"result": bool, "deps": frozenset[str]}
    cache = {}
    invalidations = {}
    hits = 0
    misses = 0
    results = []

    def fill(row_id):
        result, deps = evaluate(rows[row_id], rules)
        cache[row_id] = {"result": result, "deps": deps}
        return result

    # 启动时对每行填缓存但不输出结果，也不计命中/未命中。
    for row_id in range(len(rows)):
        fill(row_id)

    for change in data["changes"]:
        if change["kind"] == "attr":
            row_id = change["sub_i"]
            key = change["key"]
            rows[row_id][key] = change["to"]
            # 仅使本行、且结论依赖该字段的缓存失效；无关更正保持命中。
            entry = cache.get(row_id)
            if entry is not None and key in entry["deps"]:
                del cache[row_id]
                invalidations[key] = invalidations.get(key, 0) + 1
        else:  # kind == "rule": 规则变更，旧结论全部不可信，重建索引后重算。
            for rule in rules:
                if rule["id"] == change["id"]:
                    if "pred" in change:
                        rule["pred"] = change["pred"]
                    if "allow" in change:
                        rule["allow"] = change["allow"]
                    break
            else:
                raise KeyError("unknown rule id: %r" % change["id"])
            invalidations["rule"] = (
                invalidations.get("rule", 0) + len(cache)
            )
            cache.clear()

        # 更正逐个生效，每个更正后按 queries 顺序执行全体查询。
        for row_id in range(len(rows)):
            entry = cache.get(row_id)
            if entry is not None:
                hits += 1
                results.append(entry["result"])
            else:
                misses += 1
                results.append(fill(row_id))

    result = dict(
        results=results,
        hits=hits,
        misses=misses,
        invalidations=dict(sorted(invalidations.items())),
        dep_index=build_dep_index(cache),
    )
    Path(output_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main(*sys.argv[1:])
