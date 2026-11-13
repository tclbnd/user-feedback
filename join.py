"""命令行入口：复合键外存连接（inner / left）。

用法：
    python3 join.py left.csv right.csv result --type left --memory-mib 64
    python3 join.py left.csv right.csv result --crash-before-publish

结果目录只包含 rows.csv 与 _SUCCESS；目标目录已存在时拒绝覆盖。任何 I/O
异常或中断都会回收本次暂存且绝不发布半成品；--crash-before-publish 模拟
“发布前崩溃”，退出码 97，目标目录不会被创建，重跑可安全清理并重新发布。
"""
import argparse
import json
import signal
import sys
from pathlib import Path

from skewjoin.engine import JoinEngine


def main():
    parser = argparse.ArgumentParser(description="分区外存哈希连接（含热点分段）")
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--type", choices=["inner", "left"], default="inner")
    parser.add_argument("--memory-mib", type=int, default=64)
    parser.add_argument("--crash-before-publish", action="store_true")
    parser.add_argument("--spill-root", type=Path, default=None,
                        help="暂存目录根（默认取结果目录的父目录）")
    parser.add_argument("--max-open-fds", type=int, default=None,
                        help="同时打开的临时文件句柄上限，默认 16")
    args = parser.parse_args()

    engine = JoinEngine(
        args.left, args.right, args.output, args.type,
        memory_mib=args.memory_mib,
        crash_before_publish=args.crash_before_publish,
        spill_root=args.spill_root,
        max_open=args.max_open_fds,
    )

    def _on_signal(signum, frame):  # 中断同样要保证暂存被回收
        engine.staging.destroy()
        sys.exit(130)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        metrics = engine.run()
    except FileExistsError as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False),
              file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        # run() 的 finally 已销毁暂存，不会留下不完整结果或临时文件。
        print(json.dumps(dict(error="%s: %s" % (type(exc).__name__, exc)),
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
