# YUV↔RGB 色彩转换修复

Python 3.9+，标准库。运行 `python3 convert.py input.json output.json`。

input.json：
```json
{"standard": "bt601"|"bt709"|"bt2020",
 "rgb_range": "full"|"limited",
 "yuv_range": "full"|"limited",
 "ops": [{"direction": "rgb-to-yuv"|"yuv-to-rgb", "pixels": [[r,g,b], ...]}]}
```

语义：

- YUV 采样点用 Y、Cb、Cr 三个整数表示。full 范围 Y∈[0,255]，limited 范围 Y∈[16,235]、Cb/Cr∈[16,240]（中心 128）。RGB full 为 [0,255]，limited 为 [16,235]。
- 先按标准矩阵在**归一化 full 范围**内做线性变换，再按目标范围缩放，四舍五入到最近整数（半整数向上）并截断到 [0,255]。
- 矩阵：`bt601`(Kr=0.299,Kb=0.114)、`bt709`(Kr=0.2126,Kb=0.0722)、`bt2020`(Kr=0.2627,Kb=0.0593)，Cb/Cr 增益按 0.5/(1-Kb) 与 0.5/(1-Kr)。
- output.json：`{"ops":[{"pixels":[[...]]}], "warnings":[...]}`。
- **跨标准检测**：若 ops 中出现两个不同 standard 的 rgb↔yuv 往返链（request 里另带 `"chain": [{"standard":..,"direction":..},..]`），或同请求声明的输入 standard 与像素元数据 `"meta": {"standard":..}` 不一致，`warnings` 追加 `"mixed-standard"`。正常单标准请求 warnings 为空数组。
- 支持 `"roundtrip": true`：对每个 op 先按声明方向转换，再逆变换回原空间，输出附加 `"roundtrip": {"max_err": n}`（逐通道绝对差最大值）。

坏掉的地方（待修）：limited↔full 的缩放被放到了矩阵乘法**之前**且作用在 YUV 端、bt709 路径实际使用了 bt601 的 Kr/Kb、Cb/Cr 增益用的是 0.5/(1+K) 的错误公式。修好后边界色样应得到标准真值。
