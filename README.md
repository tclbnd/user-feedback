# 掩蔽驱动音频水印嵌入与盲检测
`python3 wm.py req.json output.json`
req.json: `{"pcm":"in.wav","bits":[1,0,1,...],"seed":7,"frame":1024}`
- 16bit mono wav; 每帧嵌 1 bit: PN 序列(seed) ±符号=bit; 能量分配到掩蔽频带(谱能量高区强度×2,安静帧×0.5)
- 检测: 同步头定位(前8帧已知pattern)+逐帧相关; 阈值 |corr|>0.3
- 输出 `{"out":"wm.wav","embed":[{frame,bit,energy}],"detect":{"bits":[...],"conf":[...]}}`; 同 seed 可复现.
缺陷基线: 等能量直接叠加(可闻),无同步,检测不相关.
