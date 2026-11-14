# 多轨自动混音电平平衡
`python3 automix.py req.json output.json`
req.json: `{"tracks":[{name,pcm}],"target_lufs":-16,"fs":16000}`
- 以名称含 `vox` 的轨为人声侧链，对其余轨施加随起动、释放变化的避让包络。对白段避让至少 8 dB，持续静音后恢复到 0 dB 避让。
- 输入可附 `"params":{"block":256}` 指定内部处理分块大小，改变分块不得改变增益包络。
- 峰值限制为 -1 dBFS，保留 `{"out":"mix.wav","gains":{name:db},"peak_db":v,"lufs":v}`，并在 `envelope.music_duck_db` 输出每帧音乐避让量（正 dB 表示衰减）。
缺陷基线: 等增益相加,无限幅无避让.
