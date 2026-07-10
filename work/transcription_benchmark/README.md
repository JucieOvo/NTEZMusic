# MP3 to MIDI 转录基准数据集

> 创建日期：2026-07-10
> 作者：JucieOvo
> 用于 NTEZMusic MP3 → MIDI 转录管线金标准评测

## 数据来源

所有数据来自 **MAESTRO v3.0.0**，一个由 Yamaha Disklavier 自动钢琴录制的演奏音频及其精确配对 performance MIDI 的数据集。

- 官方项目页：https://magenta.withgoogle.com/datasets/maestro
- 官方 CSV：https://storage.googleapis.com/magentadata/datasets/maestro/v3.0.0/maestro-v3.0.0.csv
- Zenodo DOI：https://doi.org/10.5281/zenodo.4654123
- 论文引用：Hawthorne et al., "Enabling Factorized Piano Music Modeling and Generation with the MAESTRO Dataset", ICLR 2019

## 许可

MAESTRO v3.0.0 使用 **CC BY-NC-SA 4.0** 许可协议：

1. 仅用于非商业研究和项目验证。
2. 保留数据集来源及许可说明。
3. MP3 属于 WAV 的转码派生文件，已标明格式转换过程。
4. 后续公开数据或报告时，请引用 MAESTRO 论文及数据集。

## 下载方式

为免下载约 101 GB 的完整数据集，本目录通过 Hugging Face 镜像按 MAESTRO 原始相对路径逐文件下载：

- 镜像地址：https://huggingface.co/datasets/ddPn08/maestro-v3.0.0
- URL 结构：`https://huggingface.co/datasets/ddPn08/maestro-v3.0.0/resolve/main/{MAESTRO相对路径}`

> 该镜像仅作为文件传输渠道，曲目身份与配对关系仍以 MAESTRO 官方 CSV 为准。

## 目录结构

```text
transcription_benchmark/
├── README.md                    # 本文件
├── bach_bwv846/                 # 巴赫 C大调前奏曲与赋格 BWV 846
│   ├── source.wav               # 原始无损 WAV
│   ├── input.mp3                # 由 source.wav 转换的 320kbps MP3
│   ├── reference_performance.midi  # 同演奏的 performance MIDI（金标准）
│   └── metadata.json            # 元数据与校验值
└── chopin_op10_no12/            # 肖邦 革命练习曲 Op.10 No.12
    ├── source.wav               # 原始无损 WAV
    ├── input.mp3                # 由 source.wav 转换的 320kbps MP3
    ├── reference_performance.midi  # 同演奏的 performance MIDI（金标准）
    └── metadata.json            # 元数据与校验值
```

## 选定条目

### 巴赫：《C 大调前奏曲与赋格》BWV 846

- 作曲家：Johann Sebastian Bach
- MAESTRO 标题：Prelude and Fugue in C Major, WTC II, BWV 846
- **元数据异常**：MAESTRO CSV 中标注为 "WTC II"，但 BWV 846 实际属于《平均律钢琴曲集》第一卷（WTC I）。此差异已在 metadata.json 中记录。
- 年份：2014 | 数据划分：train
- MIDI 时长：239.897 秒 | 音频时长：240.943 秒（差异 1.046 秒，为尾部静音）
- MIDI 音符事件：1925 (ON) / 1925 (OFF) | 音高范围：36-84

### 肖邦：《革命练习曲》Op.10 No.12

- 作曲家：Frédéric Chopin
- MAESTRO 标题：Etude Op. 10 No. 12 in C Minor
- 年份：2011 | 数据划分：test
- MIDI 时长：138.185 秒 | 音频时长：139.119 秒（差异 0.934 秒，为尾部静音）
- MIDI 音符事件：2134 (ON) / 2134 (OFF) | 音高范围：24-94

## 转码参数

两个曲目的 MP3 使用完全一致的编码参数：

| 参数 | 值 |
|------|-----|
| 编码器 | libmp3lame |
| 码率模式 | CBR（仅 `-b:a 320k`，不使用 `-q:a`） |
| 目标码率 | 320 kbps |
| 实际平均码率 | 巴赫 320 kbps / 肖邦 320 kbps |
| ffmpeg 命令 | `ffmpeg -y -i source.wav -codec:a libmp3lame -b:a 320k input.mp3` |
| ffmpeg 版本 | 8.0.1-full_build-www.gyan.dev |

## 配对校验结果

两个曲目均通过以下校验：

1. WAV 与 MIDI 路径来自 MAESTRO CSV 同一行，为同一次 Disklavier 演奏录制。
2. MIDI 文件头正确（MThd），格式为 type 1，轨道数 2，TPQ 384。
3. WAV 文件头正确（RIFF），PCM s16le，44100 Hz，立体声。
4. MP3 与 WAV 有效音频时长完全一致，未发生时长截断或拉伸。
5. MIDI 为真实演奏录制（performance MIDI），包含力度变化和非量化时间轴，不是 ASAP 的量化 score MIDI。
6. 音高范围在 0-127 合法区间内，时间轴单调递增。
7. SHA-256 文件校验值已在各曲目的 metadata.json 中记录。

## 使用说明

- `input.mp3` 可直接作为 `audio_to_yaml_converter.py --audio` 的输入。
- `reference_performance.midi` 作为转录结果的金标准对比。
- 评测时以 MIDI 的音符起止时间和力度为基准，与转录输出的 YAML/MIDI 进行匹配。
- 由于两组数据仅包含独奏钢琴，不能独立验证 Demucs 分轨算法或 36 键缩编算法。

## 测试边界

这两组数据仅用于验证真实钢琴音频的 MP3 → MIDI 转录能力。它们不能独立证明：

1. Demucs 对混合音乐（人声+伴奏）分轨的正确性。
2. 后续 36 键缩编算法的语义保留能力。
3. 对其他乐器或非键盘类音乐的泛化能力。
