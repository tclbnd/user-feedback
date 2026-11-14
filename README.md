# 多步表单向导确定性回放
Python3.9+ stdlib. `python3 wizard.py flow.json script.json output.json`
flow.json 定义页面: `{"pages":[{"fp":"p1","fields":[{"name":"a","type":"text"}],"next":{"规则":"p2"}}]}`
script.json 回放计划: `{"steps":[{"expect":"p1","fill":{"a":"x"},"goto":"p2"},...],"resume_from":null}`
- 按页面指纹 fp 定位当前页(非序号); 实际 fp≠expect → abort,`{"aborted_at":k,"expected","actual"}`
- 字段类型: text/select/date/checkbox 各有填写语义; 验证不匹配的预填值记 `field_diff`
- 持久化 `state.json` 已完成步骤; resume_from=k 时前 k 步已提交不得重填(记 skipped)
- 输出 `{"submitted":[{"step":k,"fp","payload"}],"skipped":[k],"aborted_at","field_diff":[...],"final_payload":{...}}`
缺陷基线: 按序号对齐, 分支错位, 无断点恢复.
