#!/usr/bin/env python3
"""collect.py 验收测试: 5 层混合嵌套页、同名独立采集、变异复跑、CLI。"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE5 = os.path.join(ROOT, "examples", "page5.json")

# 真值: DFS 顺序下每条字段的 (name, value, path)
EXPECTED_FIELDS = [
    ("user", "root-user", ""),
    ("csrf", "root-csrf", ""),
    ("amount", "100", "0"),
    ("user", "a-user", "0"),
    ("memo", "g1-memo", "0/0"),
    ("csrf", "gg1-csrf", "0/0/0"),
    ("amount", "200", "0/0/0"),
    ("token", "deep-token", "0/0/0/0"),
    ("memo", "gg2-memo", "0/0/1"),
    ("amount", "300", "1"),
    ("user", "bg1-user", "1/0"),
    ("extra", "bg1-extra", "1/0"),
    ("memo", "bg1-memo", "1/0"),
    ("csrf", "d1-csrf", "2/0"),
    ("csrf", "d2-csrf", "2/1"),
]

EXPECTED_PATHS = [
    "", "0", "0/0", "0/0/0", "0/0/0/0", "0/0/1",
    "1", "1/0", "2", "2/0", "2/1",
]

EXPECTED_DEPTH_COUNTS = {0: 1, 1: 3, 2: 4, 3: 2, 4: 1}


def reference_parse(page):
    """独立参考解析器: 显式栈迭代 + 独立累积, 与 collect.collect 的递归实现
    不同构, 用于交叉验证帧树摘要与字段归属。"""
    stack = [(page, "", 0)]
    ref_frames = []
    ref_fields = []
    while stack:
        node, path, depth = stack.pop(0)
        own = {"url": node.get("url"), "fields": node.get("fields", [])}
        fp = hashlib.sha256(
            json.dumps(own, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:8]
        node_fields = node.get("fields", [])
        ref_frames.append({
            "path": path, "fp": fp, "n_fields": len(node_fields),
            "depth": depth, "url": node.get("url"),
        })
        for i, fld in enumerate(node_fields):
            ref_fields.append({
                "name": fld["name"], "value": fld.get("value"),
                "path": path, "fp": fp, "frame_index": i,
            })
        children = node.get("frames", [])
        for order in range(len(children) - 1, -1, -1):
            child = children[order]
            child_path = str(order) if path == "" else path + "/" + str(order)
            stack.insert(0, (child, child_path, depth + 1))
    return ref_frames, ref_fields


def load_page():
    with open(PAGE5, encoding="utf-8") as fh:
        return json.load(fh)


def get_node(page, path):
    node = page
    if path:
        for part in path.split("/"):
            node = node["frames"][int(part)]
    return node


class FrameTreeTests(unittest.TestCase):

    def setUp(self):
        self.page = load_page()
        self.result = collect.collect(self.page)

    def test_frame_tree_inventory_matches_truth(self):
        paths = [fr["path"] for fr in self.result["frames"]]
        self.assertEqual(paths, EXPECTED_PATHS)
        self.assertEqual(self.result["coverage"]["frames"], 11)
        self.assertEqual(
            self.result["coverage"]["frames_by_depth"], EXPECTED_DEPTH_COUNTS
        )

    def test_field_attribution_matches_truth(self):
        got = [(f["name"], f["value"], f["path"])
               for f in self.result["fields"]]
        self.assertEqual(got, EXPECTED_FIELDS)
        self.assertEqual(self.result["coverage"]["fields"], len(EXPECTED_FIELDS))

    def test_every_field_carries_path_and_fingerprint(self):
        fp_by_path = {fr["path"]: fr["fp"] for fr in self.result["frames"]}
        for fld in self.result["fields"]:
            self.assertIn("path", fld)
            self.assertEqual(len(fld["fp"]), 8)
            self.assertEqual(fld["fp"], fp_by_path[fld["path"]])

    def test_same_name_fields_collected_independently(self):
        # 15 条字段一条不丢; 旧扁平实现按 name 归并只会剩 6 个唯一名字
        self.assertEqual(len(self.result["fields"]), 15)
        names = [f["name"] for f in self.result["fields"]]
        unique_names = set(names)
        self.assertEqual(len(unique_names), 6)
        for name in unique_names:
            records = [f for f in self.result["fields"] if f["name"] == name]
            paths = {f["path"] for f in records}
            if len(records) > 1:
                # 同名记录必须可由帧路径区分, 且 value 互不覆盖
                self.assertEqual(len(records), len(paths))
                self.assertEqual(len(records),
                                 len({f["value"] for f in records}))
        self.assertEqual(
            sorted(self.result["coverage"]["duplicate_names"]),
            sorted(["user", "csrf", "amount", "memo"]),
        )

    def test_matches_independent_reference_parser(self):
        ref_frames, ref_fields = reference_parse(self.page)
        self.assertEqual(
            [(f["path"], f["fp"], f["n_fields"], f["depth"])
             for f in self.result["frames"]],
            [(f["path"], f["fp"], f["n_fields"], f["depth"])
             for f in ref_frames],
        )
        self.assertEqual(
            [(f["name"], f["value"], f["path"], f["fp"], f["frame_index"])
             for f in self.result["fields"]],
            [(f["name"], f["value"], f["path"], f["fp"], f["frame_index"])
             for f in ref_fields],
        )

    def test_fingerprint_isolated_per_frame(self):
        # 孙帧 0/0/1 增删字段: 仅该帧 fp 与字段集变化
        before_fp = {fr["path"]: fr["fp"] for fr in self.result["frames"]}
        mutated = copy.deepcopy(self.page)
        target = get_node(mutated, "0/0/1")
        target["fields"].append({"name": "new_field", "value": "nv"})
        after = collect.collect(mutated)
        after_fp = {fr["path"]: fr["fp"] for fr in after["frames"]}

        # 帧树路径标识完全不变
        self.assertEqual([fr["path"] for fr in after["frames"]],
                         EXPECTED_PATHS)
        changed = [p for p in before_fp if before_fp[p] != after_fp[p]]
        self.assertEqual(changed, ["0/0/1"])

        before_fields = [(f["path"], f["frame_index"], f["name"])
                         for f in self.result["fields"]]
        after_fields = [(f["path"], f["frame_index"], f["name"])
                        for f in after["fields"]]
        # 仅新增一条属于 0/0/1 的字段, 其它帧字段集原样保留
        added = [x for x in after_fields if x not in before_fields]
        removed = [x for x in before_fields if x not in after_fields]
        self.assertEqual(removed, [])
        self.assertEqual(added, [("0/0/1", 1, "new_field")])

    def test_delete_field_in_grandchild_only_changes_that_frame(self):
        mutated = copy.deepcopy(self.page)
        target = get_node(mutated, "0/0/0")
        target["fields"].pop(1)  # 删除 amount=200
        after = collect.collect(mutated)
        before_by_path = {}
        for fld in self.result["fields"]:
            before_by_path.setdefault(fld["path"], []).append(
                (fld["frame_index"], fld["name"]))
        after_by_path = {}
        for fld in after["fields"]:
            after_by_path.setdefault(fld["path"], []).append(
                (fld["frame_index"], fld["name"]))
        for path in EXPECTED_PATHS:
            if path == "0/0/0":
                continue
            self.assertEqual(before_by_path.get(path, []),
                             after_by_path.get(path, []))
        self.assertEqual(
            after_by_path["0/0/0"],
            [(0, "csrf")],
        )

    def test_content_change_keeps_path_identity(self):
        # 帧内 value 变化不改路径; 动态帧编号稳定
        mutated = copy.deepcopy(self.page)
        get_node(mutated, "2/1")["fields"][0]["value"] = "changed"
        after = collect.collect(mutated)
        self.assertEqual([fr["path"] for fr in after["frames"]],
                         EXPECTED_PATHS)
        d2 = [f for f in after["fields"] if f["path"] == "2/1"]
        self.assertEqual(len(d2), 1)
        self.assertEqual(d2[0]["value"], "changed")

    def test_digest_deterministic_and_content_sensitive(self):
        again = collect.collect(copy.deepcopy(self.page))
        self.assertEqual(again["digest"], self.result["digest"])
        mutated = copy.deepcopy(self.page)
        get_node(mutated, "0/0/1")["fields"].append(
            {"name": "x", "value": "y"})
        self.assertNotEqual(collect.collect(mutated)["digest"],
                            self.result["digest"])


class CliTests(unittest.TestCase):

    def test_cli_end_to_end(self):
        out_path = os.path.join(HERE, "out_cli.json")
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "collect.py"),
                 PAGE5, out_path],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(proc.returncode, 0)
            with open(out_path, encoding="utf-8") as fh:
                out = json.load(fh)
            self.assertEqual(len(out["frames"]), 11)
            self.assertEqual(len(out["fields"]), 15)
            expected_cov = collect.collect(load_page())["coverage"]
            # JSON 往返后 dict 的 int 键会变成字符串
            out_cov = dict(out["coverage"])
            out_cov["frames_by_depth"] = {
                int(k): v for k, v in out_cov["frames_by_depth"].items()
            }
            self.assertEqual(out_cov, expected_cov)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
