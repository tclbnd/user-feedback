"""设备时间戳对齐（README 规定的 JSON 接口）。

用法: python3 align.py input.json output.json
输入 devices 按设备列出同一采集序列的 sync（候选同步标记）与 probes
（待标定时间戳），时间单位秒。仅依赖本地数据完成校准，不假设设备间
网络或共享时钟。完整的闪光/声脉冲检测、跨重启全局参考时钟与媒体重采样
实现在 clockcal 包内，本文件提供其 JSON 入口。
"""
import argparse
import json
from pathlib import Path

from clockcal.model import align


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    data = json.loads(a.input.read_text())
    result = align(data)
    a.output.write_text(
        json.dumps(result, allow_nan=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
