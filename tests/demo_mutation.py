#!/usr/bin/env python3
"""验收场景演示: 孙帧字段增删后复跑, 仅该帧字段集/指纹变化。"""
import copy
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import collect  # noqa: E402


def get_node(page, path):
    node = page
    for part in path.split("/"):
        node = node["frames"][int(part)]
    return node


def field_sets(out):
    grouped = {}
    for fld in out["fields"]:
        grouped.setdefault(fld["path"], []).append(
            (fld["frame_index"], fld["name"], fld["value"]))
    return grouped


def main():
    page = json.load(open(os.path.join(ROOT, "examples", "page5.json"),
                          encoding="utf-8"))
    base = collect.collect(page)
    base_fp = dict((fr["path"], fr["fp"]) for fr in base["frames"])

    # 变异 1: 孙帧 0/0/1 增加一个字段, 经 CLI 复跑
    mut = copy.deepcopy(page)
    get_node(mut, "0/0/1")["fields"].append(
        {"name": "new_field", "value": "nv"})
    mut_page = os.path.join(HERE, "page5_mut.json")
    mut_out = os.path.join(HERE, "out_mut.json")
    with open(mut_page, "w", encoding="utf-8") as fh:
        json.dump(mut, fh)
    subprocess.run([sys.executable, os.path.join(ROOT, "collect.py"),
                    mut_page, mut_out], check=True)
    with open(mut_out, encoding="utf-8") as fh:
        after = json.load(fh)
    after_fp = dict((fr["path"], fr["fp"]) for fr in after["frames"])

    same_paths = [fr["path"] for fr in after["frames"]] == \
        [fr["path"] for fr in base["frames"]]
    print("paths identical after mutation:", same_paths)
    changed_fp = [p for p in base_fp if base_fp[p] != after_fp[p]]
    print("fp changed only in:", changed_fp)

    bs, af = field_sets(base), field_sets(after)
    diff = [p for p in bs if bs[p] != af.get(p)]
    print("field sets changed only in:", diff)
    print("0/0/1 before:", bs["0/0/1"])
    print("0/0/1 after :", af["0/0/1"])

    # 变异 2: 删除同帧字段后再跑, 动态帧编号 2/0, 2/1 保持稳定
    mut2 = copy.deepcopy(page)
    get_node(mut2, "0/0/1")["fields"].pop()
    after2 = collect.collect(mut2)
    dyn = [fr["path"] for fr in after2["frames"]
           if fr["path"].startswith("2")]
    print("after delete, dynamic frame paths still:", dyn)
    print("digest base != mutated:", base["digest"] != after["digest"])

    os.remove(mut_page)
    os.remove(mut_out)


if __name__ == "__main__":
    main()
