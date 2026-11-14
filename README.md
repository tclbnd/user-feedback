# 字幕逐帧时间窗与碰撞布局
`python3 sub.py req.json output.json`
req.json: `{"fps":25,"dur":4,"subs":[{"t0":0,"t1":2,"text":"A"},{"t0":1,"t1":3,"text":"B长..."}],"w":320,"h":240,"font":16}`
- 每帧激活集按 [t0,t1) 时间窗; 重叠时 y 向上堆叠(底部优先),行高=font*1.3
- 超宽行按词边界断行(拉丁)或任意断(CJK); 仍超则逐级缩字号至下限10px,记 degrade
- 输出 `{"frames":[{f,"rects":[{text,x,y,w,h,font}]}],"degrade":n,"overlap_px":0}`
缺陷基线: 全部同位置堆叠(像素重叠),不断行.
