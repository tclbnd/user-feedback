# 页面内容指纹与改版分层决策

只依赖 Python 3.9+ 标准库。整页哈希会把模板微调变成"全量重抓"，这里把指纹
分成**模板层**和**内容层**，让模板改版与正文改版分别落判，各自带证据与置信度。

```bash
python3 diff.py  old.json new.json out/decision.json   # 单页对比
python3 scan.py  out/repo out/scan.json --verify       # 仓库扫描 + 摘要一致性自检
python3 evaluate.py                                    # 验收报告（不通过则退出码非 0）
python3 run_all.py                                     # 一键：造数据 → 扫描 → 验收
```

页面 JSON：`{"tpl":"<骨架>","blocks":[{"id":"main","text":"..."},...],"attrs":{...}}`
`tpl` 里用 `{{block_id}}` 标出每个 block 在骨架中的位置。

## 分层

| 层 | 指纹 | 比什么 |
| --- | --- | --- |
| 结构 struct | `fp_struct` | 规范化 DOM 大纲（标签 + 排序后的属性）+ 非内容块的骨架 |
| 框架 chrome | `fp_chrome` | 显式判定为导航/页脚等框架块的文本 + 模板静态文本 |
| 模板 template | `fp_template` | struct + chrome 的联合承诺，即"模板变了没有" |
| 内容 content | `fp_content` | 正文区域（由文档结构定位）的规范化文本 |

分类与决策：

| class | 含义 | refetch | 入档 |
| --- | --- | --- | --- |
| `none` | 各层都没动 | 否 | 无 |
| `template` | 只是模板变、正文没变 | **否** | 模板层 |
| `content` | 正文变了 | 是 | 内容层（+结构变了则模板层） |
| `mixed` | 两层都变 | 是（一次抓取同时刷新两层） | 两层 |

仅模板变化**不触发内容层重抓**，这是本方案的核心诉求。

## 正文区域怎么定位

按可靠性从高到低逐级回退，实际用的策略与置信度都写进 `evidence.content_region`：

1. `declared+container` —— 块 id/role 显式声明为正文，再用骨架定位其容器；
2. `dom_container` —— 按 `main`/`article`/role/容器类词找到结构容器，取文本量最大者；
3. `text_mass` —— 完全没有语义线索时，按**顶层分节**的文本量择一（不按单个块挑，
   否则分块方式会改变判定）。

## 归一化与不变量

- 文本：NFKC、去零宽字符、所有 Unicode 空白折叠；内容层进一步只保留"两个 ASCII
  词字符之间"的空格，因此**分块边界不会影响正文哈希**。
- 属性：按名排序、`class` 词排序、`style` 声明排序，属性序扰动不影响指纹。
- 注释、doctype、`script`/`style` 文本不参与指纹。
- 哈希是 SHA-256 流式喂入，**分块大小（feeding chunk）不影响任何摘要**。
- 扫描摘要 `digest` 只由指纹与判定组成，同一仓库重复扫描（目录顺序、分块方式不同）
  必然一致。

## 输出（单页）

```json
{"class":"template","refetch":false,"archive":true,"archive_layers":["template"],
 "confidence":0.77,"confidence_drivers":{...},
 "evidence":{"layers":{...},"content_region":{...},"blocks":{...},
             "relocation":{...},"layer_confidence":{...},"notes":[]},
 "fingerprints":{"old":{...},"new":{...}},
 "fp_struct":"...","fp_chrome":"...","fp_template":"...","fp_content":"...","digest":"..."}
```

`evidence.layers` 保留每层的旧/新摘要与是否变化；`evidence.blocks` 是块级 diff；
`evidence.relocation` 记录"正文只是换了位置"的掩码结果；`evidence.layer_confidence`
是**每层各自**的置信度，`confidence` 是整体判定置信度。

## 两道防误判的护栏

- **仅移位**：同一段正文换了块 id 或换到别的容器，掩码后正文与其**顺序**都一致时，
  判为结构变化而不是正文变化，避免无谓重抓。
- **区域不可靠**：正文占比异常、区域定位本身有歧义时，`content` 判决会被降级并在
  `evidence.notes` 里写明原因，不会静默给出高风险结论。

## 验收

`python3 evaluate.py` 的 8 项检查（40 页标注集训练/验收 + 不变量）：

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 模板/正文/混合分类准确率（≥95%） | 40/40 = 100% |
| 2 | 抖动稳定性：空白/属性序/重新分块等 10 种 × 10 版式 | 100/100 摘要不变 |
| 3 | 字节喂入大小不变性 | 400 次比较全部一致 |
| 4 | 块级重新分块不变性 | 120 页全部一致 |
| 5 | 交叉组合扫描：4 类编辑 × 10 变体 × 10 版式 | 400/400 判定正确 |
| 6 | 分层判别力（负对照） | 40/40 |
| 7 | 同仓库两次扫描摘要一致 | 一致 |
| 8 | 每个判定都带分层证据与置信度 | 通过 |

40 页标注集覆盖 10 种真实版式：语义化 `main`/`article`、纯 class 词、中文 id、
无任何语义线索、ARIA `role`、双栏干扰、深层嵌套等，因此通过并不依赖单一骨架。

## 文件

| 文件 | 作用 |
| --- | --- |
| `fingerprints.py` | 归一化、DOM 解析、正文区域定位、四层指纹 |
| `engine.py` | 分层判定、护栏、置信度、重抓/入档决策 |
| `diff.py` | 单页对比 CLI |
| `scan.py` | 仓库扫描 + 摘要一致性自检 |
| `corpus.py` | 10 版式 × 4 类标注集与抖动用例生成 |
| `evaluate.py` | 验收报告 |
| `run_all.py` | 一键全流程 |

缺陷基线（原 `diff.py`）：整页 sha256，任何变化 → `content` + 重抓，模板微调即全量重抓。
