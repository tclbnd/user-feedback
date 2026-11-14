# 时钟回拨下单调序列号生成
Python3.9+ stdlib. `python3 idgen.py req.json output.json`
req.json: `{"clock":[0,1,2,1.5,1.4,50,49,100],"batch":2,"state":"state.json"}`
- ID = ts_ms*2^12 + seq(0..4095); 时钟回拨时 ts 位不得回退(用 last_ts 补), seq 耗尽等待下一 ms
- 回拨 >500ms 用漂移位(时间位+偏移)接管并记 alert; state 持久化 last_ts,重启读回保证不回退
- 输出 `{"ids":[...],"meta":[{ts,seq,src:"normal|wait|drift"}],"alerts":[...]}`
缺陷基线: 直接用当前时钟,回拨重复ID,无持久化.
