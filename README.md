# 页面内容指纹与改版分层决策
Python3.9+ stdlib. `python3 diff.py old.json new.json output.json`
页面json: `{"tpl":"<骨架>","blocks":[{"id":"main","text":"..."},{"id":"nav","text":"..."}],"attrs":{...}}`
- 结构指纹 fp_struct=blocks id 序列+层级签名; 内容指纹 fp_content=仅 id=main|article|content 块文本规范化哈希
- 规范化: 折叠空白, 属性序无关; 指纹计算分块大小不得影响结果
- 分类: "template"(struct变content不变) "content"(content变) "mixed"(都变) "none"; 仅 template → 不重抓(refetch=false)
- 输出 `{"fp_struct","fp_content","class","refetch":bool,"confidence":0-1,"evidence":{...},"digest"}`
缺陷基线: 整页 sha256, 任何变化→content+重抓.
