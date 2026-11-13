# -*- coding: utf-8 -*-
"""版本化分位数摘要的命令行薄入口（向后兼容原命令形式）。

示例：
    python3 quantiles.py ROOT apply demo.json
    python3 quantiles.py ROOT query --start 0 --end 16
    python3 quantiles.py ROOT migrate --algorithm q2
    python3 quantiles.py ROOT stats
"""
import sys

from qsummary.cli import main

if __name__ == "__main__":
    sys.exit(main())
