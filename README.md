# 嵌套文档帧上下文归属采集
Python3.9+ stdlib. `python3 collect.py page.json output.json`
page.json 描述 DOM+frame 树: `{"url":"p","fields":[{"name":"user","value":"x"}],"frames":[{...递归}]}`.
- 字段记录须带完整帧路径 `path:"0/2/1"`(按出现序编号)与所在文档指纹(该 frame 序列化 sha256 前8)
- 跨层同名字段独立采集不覆盖; 输出 `{"frames":[{"path","fp","n_fields"}],"fields":[{"name","value","path","fp"}],"coverage":{"frames":n,"fields":n},"digest":规范化摘要}`
- 帧路径标识与帧内内容无关; 孙帧字段变化只影响该 path 子集.
缺陷基线: 扁平遍历, 字段无 path, 同名覆盖.
