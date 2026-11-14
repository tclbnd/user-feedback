# 公平份额抢占调度与记账一致
Python3.9+ stdlib. `python3 fair.py sim.json output.json`
sim.json: `{"jobs":[{"id","w":1-8,"cost":s,"arrive":t}],"horizon":60,"quantum":1}`
- 份额=w/Σw; 高份额作业到达时抢占(暂停)低份额运行者; 被抢占记 preempt_count 且保存检查点
- 记账分段: start/pause/resume/finish 事件流; 有效时间=Σ(resume-start)段; 账单=cost*rate
- 同作业 preempt_count>3 后抢占免疫 20s(防反复); 重放事件流账单不重复
- 输出 `{"events":[{t,job,kind}],"bills":{id:effective_sec},"preempts":{id:n},"finish_order":[id],"util":Σbusy/horizon}`
缺陷基线: 抢占直接 kill,记满时长,无事件流.
