# 多轨互相关相位对齐
`python3 align.py req.json output.json`
req.json: `{"tracks":[{"name":"ref","pcm":"ref.wav"},{"name":"t1","pcm":"t1.wav"}],"fs":16000,"max_lag_ms":20}`
WAV=16bit mono. 以 ref 为准逐轨估计整数+亚采样延迟(互相关峰+抛物线插值);
- 相关峰歧义(|次峰/主峰|>0.9)或 SNR<-5dB 段标 unreliable
- 输出 `{"tracks":[{name,lag_ms,frac_ms,reliable,conf}],"mix":"mix.wav","notch_db_improve":v}`
缺陷基线: 零延迟直接混音.
