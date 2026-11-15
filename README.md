# 分块下载断点续传与校验重组
Python3.9+ stdlib. `python3 dl.py req.json output`
req.json: `{"source":"src.bin","chunk":1024,"concurrency":4,"state":"state.json","fail_at":[0.5,0.8],"corrupt_chunks":[3]}`
- 源文件在 req 同目录; 按 chunk 分块编号从0; state.json 持久化已完成的块及其 sha256
- 模拟断流: 进度达 fail_at 各点抛异常退出(码3), 续跑只取缺失块; 注入 corrupt_chunks 的块源端字节翻转,校验失败后隔离重取该块(重取给正确数据)
- 源不支持 Range(源文件伴生 src.bin.norange): 退化为整体重传并在 output `mode="full"`
- 输出 output: 最终文件逐字节=源, 且写 `{"mode":"chunked|full","chunks":[{i,sha,retried}],"stats":{"retries":n,"bytes":n},"alerts":[...]}`
缺陷基线: 断流后从头重下, 无块级校验, corrupt 不检测, Range 不支持时不退化.
