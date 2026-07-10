# NTEZMusic MP3->MIDI 转录基准预检报告

> 生成时间：2026-07-10 UTC
> 作者：JucieOvo
> 检查类型：只读真实验收预检（未运行任何模型推理）
> 目标：验证是否可以安全启动两次 GPU 模型推理（Demucs + Transkun）并完成 mir_eval 评测

---

## 总体结论

| 项目 | 状态 |
|------|------|
| **总体就绪** | **ALL_PASS** |
| 阻塞项 | 0 |
| 可启动 GPU 推理 | 是 |

---

## 1. 运行环境

### 1.1 Python

| 属性 | 值 | 状态 |
|------|-----|------|
| 版本 | 3.10.11 | PASS |
| 可执行文件 | `C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe` | -- |

### 1.2 Torch / CUDA / GPU

| 属性 | 值 | 状态 |
|------|-----|------|
| torch 版本 | 2.7.1+cu118 | PASS |
| CUDA 可用 | True | PASS |
| CUDA 版本 | 11.8 | -- |
| GPU | NVIDIA GeForce RTX 3090 (24 GB VRAM) | PASS |
| cuDNN | 90100 | PASS |
| GPU 数量 | 1 | -- |

> 警告：pynvml 废弃警告（不影响运行，torch 2.7.1 仍兼容 pynvml）。

### 1.3 磁盘空间

| 磁盘 | 总容量 | 可用 | 状态 |
|------|--------|------|------|
| F: (项目所在) | 931.5 GB | **171.2 GB** | PASS |
| C: (torch hub 缓存) | -- | **55.1 GB** | PASS |

> 两首曲目预估共需约 1 GB（含 demucs stem、transkun MIDI、中间产物）。磁盘空间充裕。

---

## 2. 关键 CLI 工具

| 工具 | 版本 | CLI 可用 | 状态 |
|------|------|----------|------|
| ffmpeg | 8.0.1-full_build-www.gyan.dev | 是 | PASS |
| ffprobe | 8.0.1-full_build-www.gyan.dev | 是 | PASS |
| `python -m demucs.separate --help` | 4.0.1 | 是 | PASS |
| `python -m transkun.transcribe --help` | 2.0.1 | 是 | PASS |

### 2.1 demucs 命令验证输出（节选）

```
usage: demucs.separate [-h] [-s SIG | -n NAME] [--repo REPO] [-v] [-o OUT]
                       [--filename FILENAME] [-d DEVICE] [--shifts SHIFTS]
                       ...
  -n NAME, --name NAME  Pretrained model name or signature. Default is htdemucs.
  -d DEVICE, --device DEVICE
                        Device to use, default is cuda if available else cpu
```

### 2.2 transkun 命令验证输出（节选）

```
usage: transcribe.py [-h] [--weight WEIGHT] [--conf CONF] [--device [DEVICE]]
                     [--segmentHopSize SEGMENTHOPSIZE]
                     [--segmentSize SEGMENTSIZE]
                     audioPath outPath
  --device [DEVICE]     The device used to perform computations, DEFAULT: cpu
```

---

## 3. Python 包依赖

| 包名 | 版本 | 用途 | 状态 |
|------|------|------|------|
| torch | 2.7.1+cu118 | GPU 推理后端 | PASS |
| demucs | 4.0.1 | 音频分轨（钢琴分离） | PASS |
| transkun | 2.0.1 | 钢琴音频 -> MIDI 转录 | PASS |
| mir_eval | 0.8.2 | 转录评测指标计算 | PASS |
| pretty_midi | 0.2.11 | MIDI 解析与写入 | PASS |
| numpy | 1.26.4 | 数组运算 | PASS |
| librosa | 0.11.0 | 音频分析 / BPM 检测 | PASS |
| soundfile | 0.13.1 | 音频文件读写 | PASS |
| soxr | 1.0.0 | transkun 音频重采样 | PASS |
| scipy | 1.15.3 | 信号处理 | PASS |
| pyyaml | 6.0.2 | YAML 配置解析 | PASS |

---

## 4. 模型权重与缓存

### 4.1 Demucs htdemucs_6s

| 属性 | 值 | 状态 |
|------|-----|------|
| 缓存状态 | **已缓存** | PASS |
| 缓存路径 | `C:\Users\15311\.cache\torch\hub\checkpoints\5c90dfd2-34c22ccb.th` | -- |
| 文件大小 | 52.4 MB | -- |
| 下载源 | `https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/5c90dfd2-34c22ccb.th` | -- |
| 首轮是否触发下载 | **否（已缓存）** | -- |

> 命令输出证据：缓存目录存在，`5c90dfd2-34c22ccb.th` 文件存在且大小为 52.4 MB。

### 4.2 Transkun 内置权重

| 属性 | 值 | 状态 |
|------|-----|------|
| 权重路径 | `C:\Users\15311\...\site-packages\transkun\pretrained\2.0.pt` | PASS |
| 权重大小 | 53.8 MB | -- |
| 配置路径 | `C:\Users\15311\...\site-packages\transkun\pretrained\2.0.conf` | PASS |
| 下载需求 | **无（pip install transkun 即自带）** | -- |

> Transkun 默认使用 `pkg_resources.resource_filename(__name__, "pretrained/2.0.pt")` 加载内置权重，无需任何网络下载。`audio_to_yaml_converter.py` 中的 `_load_transkun_model()` 也使用此路径作为回退。

---

## 5. 基准媒体文件

### 5.1 巴赫 BWV 846

| 文件 | SHA-256 | 大小 | 时长 | 状态 |
|------|---------|------|------|------|
| `input.mp3` | `e72a975a...ab82758` | 9.2 MB | 240.94 s | PASS |
| `reference_performance.midi` | `c3ae7cb3...3682ea8` | 17.1 KB | 239.90 s (1925 notes) | PASS |
| `source.wav` | `0ea770bb...0c2fa14` | 40.5 MB | -- | PASS |
| `metadata.json` | `5d0e28c2...2a37e2b0` | 3.0 KB | -- | PASS |

### 5.2 肖邦 Op.10 No.12

| 文件 | SHA-256 | 大小 | 时长 | 状态 |
|------|---------|------|------|------|
| `input.mp3` | `996605f9...68756d` | 5.3 MB | 139.12 s | PASS |
| `reference_performance.midi` | `a2834d23...a896dd` | 20.7 KB | 138.19 s (2134 notes) | PASS |
| `source.wav` | `5c6f0113...0a7b68` | 23.4 MB | -- | PASS |
| `metadata.json` | `341eee2b...adb1922` | 2.8 KB | -- | PASS |

> 所有 SHA-256 与各曲目 `metadata.json` 中记录值一致。音频与 MIDI 均来自 MAESTRO v3.0.0 同一条目（同一次 Disklavier 演奏录制），时长差异在尾部静音范围内。

---

## 6. 评测脚本验证

| 检查项 | 结果 | 状态 |
|--------|------|------|
| 模块导入 | `evaluate_transcription.py` 导入成功 | PASS |
| `extract_notes_from_midi()` | 巴赫 MIDI 成功提取 **1925 个音符** | PASS |
| 音符频率范围 | 65.4 - 1046.5 Hz (MIDI 36-84) | PASS |

---

## 7. 风险与备注

1. **pynvml 废弃警告**：torch 2.7.1 仍兼容，不影响运行。后续可安装 `nvidia-ml-py` 消除警告。
2. **Demucs htdemucs_6s 已缓存**：首轮运行不会触发网络下载。当前缓存 365.8 MB（含 htdemucs 标准模型 80.2 MB + 6s 模型 52.4 MB + alexnet 233.1 MB）。
3. **Transkun 默认设备为 CPU**：运行转录时需要显式指定 `--device cuda`，或通过 `audio_to_yaml_converter.py` 的 `--transcription-device cuda` 参数。
4. **两曲均为独奏钢琴**：MAESTRO 数据集仅包含 Disklavier 录制的钢琴独奏，Demucs 分轨在此场景下预期行为是 input=output 几乎无损通过（钢琴轨即为原音频）。
5. **评测标准**：`evaluate_transcription.py` 使用 mir_eval MIREX 标准容差（onset 50ms, pitch 50 cents, offset_ratio 0.2），与论文一致。

---

## 8. 阻塞项

**无阻塞项。** 所有检查项均为 PASS。

---

## 9. 下一步操作建议

```powershell
# 1. 巴赫 BWV 846 - Demucs 分轨 + Transkun 转录 + YAML 压缩
python src/audio_to_yaml_converter.py `
  --audio "work/transcription_benchmark/bach_bwv846/input.mp3" `
  --output-yaml "work/transcription_benchmark/bach_bwv846/transcribed.yaml" `
  --work-dir "work/transcription_benchmark/bach_bwv846/work" `
  --song-name "Bach BWV 846" `
  --bpm 0 `
  --transcription-device cuda `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals --out-of-range-policy octave_fold

# 2. 肖邦 Op.10 No.12 - 同上
python src/audio_to_yaml_converter.py `
  --audio "work/transcription_benchmark/chopin_op10_no12/input.mp3" `
  --output-yaml "work/transcription_benchmark/chopin_op10_no12/transcribed.yaml" `
  --work-dir "work/transcription_benchmark/chopin_op10_no12/work" `
  --song-name "Chopin Op.10 No.12" `
  --bpm 0 `
  --transcription-device cuda `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals --out-of-range-policy octave_fold

# 3. 运行评测
python work/transcription_benchmark/evaluate_transcription.py `
  --ref-midi "work/transcription_benchmark/bach_bwv846/reference_performance.midi" `
  --est-midi "work/transcription_benchmark/bach_bwv846/work/transkun/xxx.mid" `
  --output-json "work/transcription_benchmark/bach_bwv846/eval_result.json" `
  --output-md "work/transcription_benchmark/bach_bwv846/eval_report.md"
```
