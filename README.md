# decode.py

运行 `python3 decode.py input.json output.json`。输入输出为本地 UTF-8 JSON，仅使用标准库。

## 解码规则

候选编码按顺序尝试：BOM → HTTP `http_charset` → HTML `meta_charset` → `probe` →
`fallback`。识别 UTF-8、UTF-16 LE/BE、UTF-32 LE/BE 的 BOM，先去掉 BOM 再严格解码。
每个规范编码（`codecs.lookup(...).name`）最多尝试一次；未知标签跳过并在 `attempts`
记为 `unknown`。仅当严格解码成功才接受。全部失败时按 UTF-8 恢复：保留可解码片段，
每个错误字节写入 `bad_bytes` 并在文本中以一个 U+FFFD 表示，`source` 为 `recovery`、
`encoding` 为 `null`。

输出 `documents` 按 `id` 排序，每项含 `id`、`text`、`encoding`、`source`、
`bad_bytes`、`attempts`；`summary` 含 `by_source`（bom/http/meta/probe/fallback/
recovery 计数）与 `bad_bytes_total`。

## 本地验收（43 条样本）

```
python3 make_samples.py     # 生成 input_a.json / input_b.json（两套 fallback）
python3 decode.py input_a.json output_a.json
python3 decode.py input_b.json output_b.json
python3 verify.py           # 断言冲突声明、端序、别名、截断、未知标签、
                            # 恢复、重排可重现性与两套 fallback 的结果
```

`byte_probe.py` 用于实测各 codec 对问题字节串的接受/拒绝情况，辅助构造边界样本。
