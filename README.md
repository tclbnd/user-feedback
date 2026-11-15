# 跨页 canonical 与 hreflang 聚簇的去重裁决

`python3 canon.py input.json output.json`

input.json: `{"pages":[{"url":u,"canonical":u|null,"hreflang":[{"lang":"en","url":v}...]}...],"langs":["en","ja"],"seed":1}`

模型约定：
- canonical 构成有向图:环按确定性规则仲裁(取环内字典序最小页为代表);跨域 canonical 同样生效
- hreflang 声明须互指才算数:A 指 B 的 lang 而 B 不回指 A → 该声明记 conflict,不用于聚簇
- 同 canonical 组+有效 hreflang 对聚为同簇;代表页=簇内字典序最小
- 输出 `{"clusters":[{"rep":u,"members":[u...]}],"verdicts":{u:原因串},"digest":"sha1"}`;输入乱序结果一致

缺陷基线:环按输入序停;单向 hreflang 不校验。
