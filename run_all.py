#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键复跑全部验收: 造样例 -> 剪辑 -> 评测 -> 基线对照 -> 边界/参数测试。"""
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = [
    ("特征/FFT 自检与信号探针", "tests_check.py"),
    ("合成验收样例", "make_fixtures.py"),
    ("人工指标/逐段决策/分块不变性评测", "evaluate.py"),
    ("纯电平门限基线对照", "compare_baseline.py"),
    ("边界条件与确定性测试", "test_edge.py"),
    ("原始 req schema 兼容性测试", "test_compat.py"),
    ("剪切策略参数行为测试", "test_params.py"),
]


def main():
    for title, script in STEPS:
        print("\n" + "#" * 70)
        print("# %s  (%s)" % (title, script))
        print("#" * 70)
        rc = subprocess.call([sys.executable, os.path.join(HERE, script)])
        if rc != 0:
            print("步骤失败: %s rc=%d" % (script, rc))
            return rc
    print("\n全部验收步骤通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
