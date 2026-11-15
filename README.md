# 跨页 canonical 与 hreflang 聚簇的去重裁决

`python3 canon.py input.json output.json`

input.json: `{"pages":[{"url":u,"canonical":u|null,"hreflang":[{"lang":"en","url":v}...]}...],"langs":["en","ja"],"seed":1}`

output.json: `{"clusters":[{"rep":u,"members":[u...]}],"verdicts":{u:原因串},"digest":"sha1"}`
- `clusters` 按 `rep` 升序，`members` 升序，`rep == members[0]`（字典序最小者）
- `verdicts` 逐页给出该页的聚簇归属与仲裁原因，`;` 分隔
- `digest` 对 `clusters` 的稳定字符串表示取 sha1，是聚簇摘要的指纹

模型约定：
- canonical 构成有向图:环按确定性规则仲裁(取环内字典序最小页为代表);跨域 canonical 同样生效
- hreflang 声明须互指才算数:A 指 B 的 lang 而 B 不回指 A → 该声明记 conflict,不用于聚簇
- 同 canonical 组+有效 hreflang 对聚为同簇;代表页=簇内字典序最小
- 输入乱序结果一致

## 裁决规则

### canonical

每页最多一个 canonical 目标，故 canonical 边构成函数图：每个连通分量要么终止于
一个终页（也可以是输入之外的 URL），要么终止于恰好一个环。

- **环**：取环内字典序最小的 URL 作为分量代表，与遍历起点、输入顺序无关。
- **指向环的页**：并入该环所在分量，`verdicts` 中同时记录
  `cycle-min:[...]-><最小页>`，使仲裁可审计。
- **跨域**：canonical 与 hreflang 都可跨域生效，不做域内限制。
- **指向输入之外**：记为 `note:canonical-external:<url>`，不是冲突；该页单独成簇。

### hreflang

声明 `A: lang=L -> B` 仅在 B 回指 A 时生效（互指）：

| 情形 | 处理 | verdict 片段 |
| --- | --- | --- |
| 互指（含语言标签不一致） | 聚簇；不一致只记录 | `hreflang:L<->B` / `hreflang:L<->B(lang-mismatch:back=...)` |
| 单向（B 不回指 A） | 不聚簇 | `conflict:hreflang-one-way:L->B` |
| 目标不在输入内 | 无法验互指，不聚簇 | `conflict:hreflang-external-target:L->B` |
| 同页同语言指多个不同目标 | 仍按互指聚簇，额外记录 | `conflict:hreflang-lang-duplicate:L->B1\|B2` |
| 显式自指（`L -> A`） | 合法，不产生边 | `hreflang-self:L` |
| 结构损坏 | 忽略该条目 | `conflict:hreflang-not-a-list` / `conflict:hreflang-malformed-entry` |

语言标签大小写不敏感，`en_US` 与 `en-US` 视为同一写法；语言标签本身不参与聚簇判定
（`en` 与 `en-US` 也不会因标签差异拆簇），因此互指即聚簇，语言维度只用于逐页 `lang:` 报告。

### 自相矛盾与重复记录

- 同一 URL 出现多条记录时合并：canonical 取字典序最小的非自指目标，若声明里同时含
  自指与外指，记 `conflict:canonical-self-and-target:<targets>`；多个外指记
  `conflict:canonical-multiple:<a>|<b>`。
- 所有仲裁理由都进入 `verdicts`，冲突用例可逐条复核。

### 与输入无关

代表页、环代表、冲突取舍都只依赖 URL / 语言取值：

- 页面与 hreflang 条目的输入次序不影响输出；
- `langs` 配置不改变 `clusters` / `digest`，仅校验格式。

## 验证

```
python3 -m unittest -v test_canon     # 单元 + 属性 + 命令行测试
python3 acceptance.py                 # 端到端验收，走真实 CLI
```

- `scenarios.py` 手工裁决 31 组场景（环 canonical、单向 hreflang、跨域指向、语言维、
  重复记录、退化输入），是参考裁决。
- `acceptance.py` 用真实子进程逐组校验聚簇与冲突理由，再用乱序输入 + 2 组语言配置复跑，
  校验 `clusters` 与 `digest` 完全一致。

缺陷基线（输入序停环、单向 hreflang 照收）在这 31 组中 22 组不一致，可作为修复有效性的反证。
