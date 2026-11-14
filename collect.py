#!/usr/bin/env python3
"""嵌套文档帧上下文归属采集。

页面模型 page.json::

    {"url": "p",
     "fields": [{"name": "user", "value": "x"}],
     "frames": [ ... 同构递归 ... ]}

设计要点
--------
1. 帧树 (frame tree): 每个 frame 节点是一份独立文档, 拥有独立的解析上下文。
   根文档路径为 ""; 子帧按在父帧 frames 中的出现顺序从 0 编号,
   拼接成完整帧路径, 如 "0/2/1" 表示 根 -> 第0帧 -> 第2帧 -> 第1帧。
   路径只由树结构位置决定, 与帧内内容 (url/fields) 无关, 因此帧内字段
   增删不会改变任何帧的路径标识。
2. 文档指纹 (fp): 对每个帧 *自身* 的文档内容 (url + 本帧 fields, 剔除嵌套
   frames) 做规范化序列化后取 sha256 前 8 位。各帧上下文独立, 孙帧变化
   只改变孙帧自身的 fp, 不波及其它帧。
3. 同名字段不覆盖: 字段的身份是 (完整帧路径, 帧内序号), 不再按 name 归并。
   每条字段记录都携带完整 path 与所在文档 fp, 跨帧同名输入各自独立采集。
"""
import hashlib
import json
import sys


def _canonical(obj):
    """规范化 JSON 序列化: sort_keys + 紧凑分隔符, 保证字节级稳定。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def doc_fingerprint(node):
    """计算单个帧自身文档的指纹 (sha256 前 8 位)。

    只取该帧自己的 url 与 fields, 显式剔除嵌套 frames —— 嵌套帧是独立
    文档, 拥有各自的指纹, 不计入父帧文档内容。
    """
    own_doc = {
        "url": node.get("url"),
        "fields": node.get("fields", []),
    }
    return hashlib.sha256(_canonical(own_doc).encode("utf-8")).hexdigest()[:8]


def collect(page):
    """递归遍历帧树, 采集帧清单、逐字段归属与覆盖报告。

    返回::

        {"frames":   [{"path", "url", "fp", "n_fields", "depth"}, ...],
         "fields":   [{"name", "value", "path", "fp", "frame_index"}, ...],
         "coverage": {"frames", "fields", "frames_by_depth",
                      "duplicate_names"},
         "digest":   "<规范化采集结果的 sha256>"}
    """
    frames = []
    fields = []
    # name -> 出现该名字的不同帧路径集合, 用于覆盖报告中的重名诊断
    name_paths = {}

    def walk(node, path, depth):
        # 每帧独立解析上下文: 指纹只基于本帧文档
        fp = doc_fingerprint(node)
        node_fields = node.get("fields", [])
        frames.append({
            "path": path,
            "url": node.get("url"),
            "fp": fp,
            "n_fields": len(node_fields),
            "depth": depth,
        })

        for idx, field in enumerate(node_fields):
            name = field["name"]
            fields.append({
                "name": name,
                "value": field.get("value"),
                "path": path,           # 完整帧路径, 根文档为 ""
                "fp": fp,               # 所在文档指纹
                "frame_index": idx,     # 帧内稳定序号
            })
            name_paths.setdefault(name, set()).add(path)

        # 子帧按出现顺序编号; 路径标识仅取决于结构位置, 与内容无关
        for order, child in enumerate(node.get("frames", [])):
            child_path = str(order) if path == "" else path + "/" + str(order)
            walk(child, child_path, depth + 1)

    walk(page, "", 0)

    by_depth = {}
    for fr in frames:
        by_depth[fr["depth"]] = by_depth.get(fr["depth"], 0) + 1

    duplicate_names = sorted(
        name for name, paths in name_paths.items() if len(paths) > 1
    )

    result = {
        "frames": frames,
        "fields": fields,
        "coverage": {
            "frames": len(frames),
            "fields": len(fields),
            "frames_by_depth": by_depth,
            "duplicate_names": duplicate_names,
        },
    }
    # 规范化摘要: 对完整采集结果做稳定序列化后取 sha256
    result["digest"] = hashlib.sha256(
        _canonical(result).encode("utf-8")
    ).hexdigest()
    return result


# 兼容旧入口名
def run(page):
    return collect(page)


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python3 collect.py page.json output.json")
    with open(sys.argv[1], encoding="utf-8") as fh:
        page = json.load(fh)
    result = collect(page)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()
