# MP3 → MIDI 基准数据下载与准备方案

> 编写日期：2026-07-10
> 作者：JucieOvo
> 状态：待技术总监审批

## 一、目标

为 NTEZMusic 的 MP3 → MIDI 转录管线准备两组真实钢琴演奏基准数据。每组数据必须包含同一次 Yamaha Disklavier 演奏录制得到的音频和 performance MIDI，以避免不同演奏版本之间的速度、力度、反复段落和自由速度差异污染评测结果。

原始数据采用 MAESTRO v3.0.0。下载后保留无损 WAV，并从同一 WAV 转换出 MP3 作为项目输入。对应 performance MIDI 作为转录金标准。

## 二、选定条目

### 2.1 巴赫：《C 大调前奏曲与赋格》BWV 846

MAESTRO 元数据中的标题为 `Prelude and Fugue in C Major, WTC II, BWV 846`。BWV 846 实际属于《平均律钢琴曲集》第一卷，`WTC II` 是数据集元数据中的标题异常。本条目包含前奏曲与赋格完整演奏，不应再标注为“仅前奏曲”。

| 字段 | 值 |
|------|-----|
| 作曲家 | Johann Sebastian Bach |
| 标题 | Prelude and Fugue in C Major, WTC II, BWV 846 |
| 年份 | 2014 |
| 数据划分 | train |
| MAESTRO CSV 时长 | 239.887580625 秒 |
| WAV 路径 | `2014/MIDI-UNPROCESSED_16-18_R1_2014_MID--AUDIO_16_R1_2014_wav--1.wav` |
| MIDI 路径 | `2014/MIDI-UNPROCESSED_16-18_R1_2014_MID--AUDIO_16_R1_2014_wav--1.midi` |

### 2.2 肖邦：《革命练习曲》Op.10 No.12

| 字段 | 值 |
|------|-----|
| 作曲家 | Frédéric Chopin |
| 标题 | Etude Op. 10 No. 12 in C Minor |
| 年份 | 2011 |
| 数据划分 | test |
| MAESTRO CSV 时长 | 138.094146094 秒 |
| WAV 路径 | `2011/MIDI-Unprocessed_07_R1_2011_MID--AUDIO_R1-D3_03_Track03_wav.wav` |
| MIDI 路径 | `2011/MIDI-Unprocessed_07_R1_2011_MID--AUDIO_R1-D3_03_Track03_wav.midi` |

## 三、数据来源与许可

### 3.1 权威元数据

- MAESTRO 官方项目页：<https://magenta.withgoogle.com/datasets/maestro>
- MAESTRO v3.0.0 官方 CSV：<https://storage.googleapis.com/magentadata/datasets/maestro/v3.0.0/maestro-v3.0.0.csv>
- Zenodo DOI：<https://doi.org/10.5281/zenodo.4654123>

### 3.2 文件下载来源

官方 Google Cloud Storage 和 Zenodo 主要提供完整压缩包，不提供便利的曲目级单文件入口。为避免下载约 101 GB 的完整数据集，计划使用 Hugging Face 上按 MAESTRO 原始目录展开的 `ddPn08/maestro-v3.0.0` 镜像，按元数据中的相对路径下载四个目标文件。

镜像 URL 结构为：

`https://huggingface.co/datasets/ddPn08/maestro-v3.0.0/resolve/main/{MAESTRO相对路径}`

该镜像仅作为文件传输渠道，曲目身份与配对关系仍以 MAESTRO 官方 CSV 为准。

### 3.3 许可要求

MAESTRO v3.0.0 使用 CC BY-NC-SA 4.0：

1. 仅用于非商业研究和项目验证。
2. 保留数据集来源及许可说明。
3. MP3 属于 WAV 的转码派生文件，需要标明发生过格式转换。
4. 后续公开数据或报告时，应引用 MAESTRO 论文及数据集。

## 四、目标目录结构

计划写入以下目录：

```text
work/transcription_benchmark/
├── README.md
├── bach_bwv846/
│   ├── source.wav
│   ├── input.mp3
│   ├── reference_performance.midi
│   └── metadata.json
└── chopin_op10_no12/
    ├── source.wav
    ├── input.mp3
    ├── reference_performance.midi
    └── metadata.json
```

`source.wav` 用作无损输入对照；`input.mp3` 用作当前 MP3 → MIDI 管线的实际输入；`reference_performance.midi` 是与音频精确配对的真实演奏 MIDI；`metadata.json` 保存原始文件路径、年份、数据划分、时长、来源 URL、下载时间及本地校验值。

## 五、执行顺序

1. 从 MAESTRO 官方 CSV 再次读取并锁定两个条目的音频和 MIDI 相对路径。
2. 验证四个 Hugging Face 单文件 URL 可访问，并记录响应状态、文件大小和最终重定向地址。
3. 创建 `work/transcription_benchmark/` 及两个曲目子目录。
4. 分别下载两个 WAV 和两个 performance MIDI，不下载完整 MAESTRO 数据集。
5. 对下载结果计算 SHA-256，并写入各自的 `metadata.json`。
6. 使用本机 ffmpeg 从每个 `source.wav` 转换出 MP3。两个曲目采用完全一致的编码参数，避免测试条件不一致。
7. 使用 ffprobe 和 MIDI 解析器读取真实时长，比较 WAV、MP3 和 MIDI 的总时长。
8. 检查 MIDI 是否可解析、是否包含音符事件、音高是否合法、时间轴是否单调。
9. 将数据来源、许可、转码说明和校验结果写入目录级 `README.md`。

## 六、完整性与配对校验

每首曲目必须同时满足以下条件才能用于测试：

1. WAV、MP3 和 MIDI 均可正常打开。
2. WAV 与 MIDI 文件路径来自 MAESTRO 官方 CSV 的同一行。
3. WAV 与 MIDI 总时长差异处于合理的尾部静音范围内；不得通过裁切伪造时长一致。
4. MP3 与 WAV 的有效音频时长一致。
5. MIDI 包含真实音符和力度事件，不得误用 ASAP 的量化 score MIDI。
6. SHA-256、文件大小、来源 URL 和原始 MAESTRO 相对路径均已记录。
7. 巴赫曲目的本地名称必须明确写为“前奏曲与赋格”，并记录 MAESTRO 的 `WTC II` 元数据异常。

## 七、风险与处理原则

### 7.1 第三方镜像风险

Hugging Face 仓库不是 MAESTRO 官方主托管。执行时必须通过官方 CSV 核对路径、时长和作品信息，并保存本地 SHA-256。若镜像文件与官方元数据不一致，应立即报错并停止，不允许改用来源不明的替代文件。

### 7.2 文件体积与网络风险

WAV 文件预计为数十至数百 MB。下载失败时不得保留残缺文件作为成功结果；必须检查实际文件格式和大小后再进入转码。

### 7.3 测试边界

这两组数据仅用于验证真实钢琴音频的 MP3 → MIDI 转录能力。它们不能独立证明 Demucs 对混合音乐分轨的正确性，也不能证明后续 36 键缩编算法正确。

## 八、审批点

审批后才执行以下操作：

1. 创建 `work/transcription_benchmark/` 目录。
2. 下载两个 WAV 和两个 performance MIDI。
3. 调用 ffmpeg 转换两个 MP3。
4. 解析并校验音频、MIDI 与元数据。

在获得明确的“开始”或等价批准前，不执行上述下载、转码和验证操作。
