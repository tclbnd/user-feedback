# 嵌套文档帧上下文归属采集
Python3.9+ stdlib. `python3 collect.py page.json output.json`
page.json 描述 DOM+frame 树: `{"url":"p","fields":[{"name":"user","value":"x"}],"frames":[{...递归}]}`.

## 约定
- 根文档帧路径为 `""`; 子帧按在父帧 `frames` 中的出现顺序从 0 编号,
  完整路径如 `0/2/1`（根→第0帧→第2帧→第1帧）。路径仅取决于树结构位置,
  与帧内 url/fields 内容无关, 帧内字段增删不改任何帧的路径标识。
- 动态加载帧在出现时按顺序编号, 同结构复跑编号稳定（见 `2/0`、`2/1`）。
- 文档指纹 `fp`: 对每个帧 **自身** 文档（`url` + 本帧 `fields`, 剔除嵌套
  `frames`）规范化序列化后取 sha256 前 8 位。各帧上下文独立, 孙帧变化只
  改变孙帧自己的 fp, 不波及其它帧。
- 字段身份是 `(完整帧路径, 帧内序号 frame_index)`, 不再按 name 归并;
  跨层同名字段独立采集不覆盖, 每条记录携带 `path` 与所在文档 `fp`。

## 输出结构
```json
{
  "frames":   [{"path": "", "url": "...", "fp": "cd7de0d5",
                "n_fields": 2, "depth": 0}],
  "fields":   [{"name": "user", "value": "x", "path": "0/2",
                "fp": "ab12cd34", "frame_index": 0}],
  "coverage": {"frames": 11, "fields": 15,
               "frames_by_depth": {"0": 1, "1": 3},
               "duplicate_names": ["amount", "csrf", "memo", "user"]},
  "digest": "<对完整结果规范化序列化后的 sha256, 64 位>"
}
```
`frames`/`fields` 均为 DFS 先序顺序; `duplicate_names` 列出跨帧重名字段
（同帧重名保留但不列入）; `digest` 确定性且对内容变化敏感。

## 测试与演示
- `python3 -m unittest discover -s tests -v`: 10 项验收测试, 含与独立参考
  解析器（显式栈实现, 与主实现递归不同构）的帧树摘要交叉校验。
- `python3 tests/demo_mutation.py`: 孙帧 `0/0/1` 增删字段复跑演示, 验证仅
  该帧 fp/字段集变化、帧路径与动态帧编号不变。
- 夹具 `examples/page5.json`: 5 层（depth 0–4）混合嵌套页, 11 帧 15 字段,
  `user/csrf/amount/memo` 跨多层同名。

## 缺陷修复对照
缺陷基线: 扁平遍历, 字段无 path, 同名覆盖（15 条字段仅保留 6 条, 4 个
`csrf` 只剩 1 个, 帧计数恒为 0）。现实现按帧树递归建立独立文档上下文,
同名字段全部独立采集。
