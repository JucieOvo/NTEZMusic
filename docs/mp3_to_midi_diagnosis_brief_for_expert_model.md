# NTEZMusic MP3 to MIDI 转录质量诊断 Brief

> **历史版本提示**：本文件为 v1.0，部分诊断结论已被原始 Transkun MIDI 的真实复评推翻。最新版本见 `docs/mp3_to_midi_diagnosis_brief_for_expert_model_v2.md`。本文件仅保留用于追溯旧实验与旧假设。

> 目标读者：具备 AMT（Automatic Music Transcription）、mir_eval 评测与 MIDI 时序处理经验的专家模型
> 编写日期：2026-07-10
> 作者：JucieOvo
> 状态：待外部模型诊断
> 版本：v1.0

---

## 一、执行摘要

NTEZMusic 是一个游戏内钢琴自动弹奏项目，其核心管线为：

```
真实钢琴录音 MP3 (MAESTRO Disklavier)
    -> Demucs 4.0.1 钢琴分轨
    -> Transkun 2.0.1 转录
    -> BPM 检测 + tempo 写入
    -> attention_weighted 36 键缩编
    -> YAML 曲谱
```

在两组真实 MAESTRO v3.0.0 录音（巴赫 BWV 846、肖邦 Op.10 No.12）上完成全链路真实运行后：

- 管线退出码为 0，所有中间产物可解析，YAML 结构约束 PASS。
- 但原始转录评测（未经任何校准）的 onset+pitch F1 仅为约 0.05（肖邦），表明 Transkun 输出与 MAESTRO 参考 MIDI 之间存在严重的系统性时间偏移。
- 做简单的全局线性时间拉伸校准后，F1 提升至巴赫 0.3737、肖邦 0.5408，但仍有超过一半音符无法在 50ms/50cents 容差内匹配。
- 巴赫（规则织体）的校准后 F1 竟低于肖邦（高密度），与直觉相反。

本 Brief 将管线设计、真实运行数据、源码证据、已证实事实与未验证假设完整呈现，供外部模型进行严谨根因诊断、实验设计并提出最小修复方案。

---

## 二、项目目标与硬约束

### 2.1 项目目标

1. 将任意钢琴演奏音频（MP3/WAV）转化为可被 36 键虚拟键盘自动弹奏的 YAML 曲谱。
2. 缩编后的曲谱必须满足键盘映射约束（MIDI 48-83 内、单和弦最大 6 音、左手最大 3 音）。
3. 转录质量需达到可支撑游戏体验的水平（当前未达成）。

### 2.2 硬约束

| 约束 | 说明 |
|------|------|
| 禁止 Mock/Stub/假数据 | 所有评测必须基于真实运行产物 |
| 禁止降级 | 不允许因 CUDA 不可用切换 CPU、不允许因模型缺失伪造结果 |
| 禁止未授权修改代码 | 诊断阶段只读分析，修复前需方案审批 |
| 真实环境 | RTX 3090 24GB、PyTorch 2.7.1 CUDA 11.8、Demucs 4.0.1、Transkun 2.0.1、mir_eval 0.8.2 |
| 评测对象 | `pipeline/midi/input.mid`（Transkun raw 输出，被 `_fix_midi_tempo` 修改后的版本）vs MAESTRO `reference_performance.midi` |

---

## 三、真实数据与环境

### 3.1 基准数据

| 属性 | 巴赫 BWV 846 | 肖邦 Op.10 No.12 |
|------|------------|-----------------|
| 数据集 | MAESTRO v3.0.0, train split, 2014 | MAESTRO v3.0.0, test split, 2011 |
| 输入格式 | MP3, CBR 320kbps, libmp3lame | 同 |
| 参考格式 | performance MIDI (Disklavier), type 1, 384 TPQN | 同 |
| 参考音符数 (note_on) | 1925 | 2134 |
| 参考时长 | 239.897s (MIDI), 240.943s (WAV) | 138.185s (MIDI), 139.119s (WAV) |
| 音高范围 (MIDI) | 36-84 | 24-94 |
| MP3 SHA-256 | E72A975A... (见 metadata.json) | 996605F9... (见 metadata.json) |
| 参考 MIDI SHA-256 | C3AE7CB3... (见 metadata.json) | A2834D23... (见 metadata.json) |
| 配对验证 | WAV/MIDI 同一次演奏，时长差 1.046s（尾部静音） | 同，时长差 0.934s（尾部静音） |

> MAESTRO 参考 MIDI 的默认 tempo 为 120 BPM（数据集团队约定），这是一个关键背景。

### 3.2 运行环境

| 组件 | 版本/规格 |
|------|----------|
| GPU | NVIDIA RTX 3090 24GB |
| OS | Windows 11 |
| PyTorch | 2.7.1, CUDA 11.8 |
| Demucs | 4.0.1 (htdemucs_6s) |
| Transkun | 2.0.1 (pretrained/2.0.pt) |
| pretty_midi | >=0.2.10 |
| mir_eval | 0.8.2 |
| librosa | 用于 BPM 检测 |
| ffmpeg | 8.0.1-full_build |

### 3.3 全部产物路径

```
work/transcription_benchmark/
├── bach_bwv846/
│   ├── input.mp3                              # 基准 MP3 输入
│   ├── source.wav                              # 无损对照
│   ├── reference_performance.midi               # 金标准 (1925 notes)
│   ├── final_attention_weighted.yaml            # 36键 YAML (1698 events)
│   ├── pipeline/demucs/htdemucs_6s/input/piano.wav  # Demucs stem (42.5 MB)
│   ├── pipeline/midi/input.mid                  # Transkun raw (13.2 KB, 1912 notes)
│   └── pipeline/conversion_report.json          # 含 raw_note_count=1846, BPM=117.5
├── chopin_op10_no12/
│   ├── input.mp3
│   ├── source.wav
│   ├── reference_performance.midi               # 金标准 (2134 notes)
│   ├── final_attention_weighted.yaml            # 36键 YAML (1064 events)
│   ├── transcription_evaluation.json            # 原始未校准评测 (F1=0.0534)
│   ├── transcription_evaluation.md
│   ├── pipeline/demucs/htdemucs_6s/input/piano.wav  # Demucs stem (24.5 MB)
│   ├── pipeline/midi/input.mid                  # Transkun raw (14.6 KB, 2023 notes)
│   └── pipeline/conversion_report.json          # 含 raw_note_count=1726, BPM=112.3
├── evaluate_transcription.py                    # 评测工具
├── diagnose_timing.py                           # 校准前后对比诊断脚本
└── README.md
```

> 注意：巴赫的 `transcription_evaluation.json` 当前未生成。肖邦的已存在，内容为**原始未校准**评测结果。

---

## 四、精确管线描述

### 4.1 全链路执行命令

巴赫 BWV 846：

```powershell
python src/audio_to_yaml_converter.py `
  --audio "work/transcription_benchmark/bach_bwv846/input.mp3" `
  --output-yaml "work/transcription_benchmark/bach_bwv846/final_attention_weighted.yaml" `
  --work-dir "work/transcription_benchmark/bach_bwv846/pipeline" `
  --song-name "巴赫 C大调前奏曲与赋格 BWV 846" `
  --bpm 0 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6 --left-max-chord-notes 3 `
  --phrase-gap-beats 0.5 --global-trend-alpha 0.05 --global-trend-window-beats 4.0 `
  --transcription-device cuda
```

肖邦 Op.10 No.12：同上，替换输入路径和曲名。

### 4.2 阶段划分

| 阶段 | 说明 | 关键路径 |
|------|------|---------|
| S1: Demucs 分轨 | htdemucs_6s 将 MP3 分离为 piano + other | `pipeline/demucs/htdemucs_6s/input/piano.wav` |
| S2: Transkun 转录 | `model.transcribe(audio_tensor)` -> notes_est -> `writeMidi()` | `pipeline/midi/input.mid` |
| S3: Tempo 修正 | `_fix_midi_tempo()`：librosa BPM 检测 -> mido 覆盖 tempo 轨道 | 同上 MIDI（原地修改） |
| S4: MIDI 清洗 | `_read_midi_notes()`：秒转拍（用 config.bpm）、`_merge_sustained_midi_notes()`、`_cluster_midi_note_onsets()` | 仅影响缩编输入，不影响 raw 评测 |
| S5: 缩编 | `attention_weighted` 36 键压缩 -> YAML | `final_attention_weighted.yaml` |
| S6: 评测 | `evaluate_transcription.py`：pretty_midi 读取 -> mir_eval 逐音比对 | `transcription_evaluation.json` |

---

## 五、指标协议与关键发现

### 5.1 评测工具与容差

评测脚本 `work/transcription_benchmark/evaluate_transcription.py` 使用 `mir_eval.transcription`：

- onset tolerance: 50 ms
- pitch tolerance: 50 cents
- offset_ratio: 0.2, offset_min_tolerance: 50 ms
- 从 pretty_midi 提取 intervals (秒)、pitches (Hz)、velocities
- 不对 MIDI 做任何时间轴拉伸或 DTW 对齐

### 5.2 原始未校准指标

**肖邦 Op.10 No.12**（唯一现有未校准 JSON）：

| 指标 | Onset+Pitch | Onset+Pitch+Offset |
|------|------------|-------------------|
| Precision | 0.0549 | 0.0302 |
| Recall | 0.0520 | 0.0286 |
| F1 | **0.0534** | **0.0293** |
| 匹配对 | 61 / 2134 | |
| 漏检 | 2073 | |
| 额外 | 1962 | |
| Onset MAE (匹配对) | 24.9 ms | |
| Onset RMSE | 29.7 ms | |

**巴赫**：未生成原始未校准 JSON（当前仅存在 `diagnose_timing.py` 的校准前/后输出）。

### 5.3 线性校准后指标

`diagnose_timing.py` 使用 `scale = ref_duration / est_duration` 做全局线性拉伸后：

| 指标 | 巴赫 BWV 846 | 肖邦 Op.10 No.12 |
|------|------------|-----------------|
| 参考时长 | 239.84s | 138.11s |
| 转录时长 | 244.86s | 147.50s |
| 拉伸比 | 0.9795 | 0.9363 |
| P+Ons+Off F1 | **0.3737** | **0.5408** |
| P+Ons F1 | 0.3737 | 0.5408 |
| Precision | 0.3750 | 0.5556 |
| Recall | 0.3725 | 0.5267 |
| 匹配对 | 717 / 1925 | 1124 / 2134 |
| 漏检 | 1208 | 1010 |
| 额外 | 1195 | 899 |
| Onset MAE | 25.5 ms | 23.8 ms |
| Onset P95 | 47.0 ms | 46.7 ms |
| Avg Overlap Ratio | 0.8477 | 0.5729 |

### 5.4 指标分层解读

**重要：不得混淆两类指标**

1. **原始未校准指标**（肖邦 F1=0.0534）：反映 Transkun raw MIDI（含 tempo 覆盖）与 MAESTRO 参考的直接比对。这是转录系统端到端未经任何后处理的真实表现。
2. **线性校准后指标**（巴赫 F1=0.3737, 肖邦 F1=0.5408）：仅做全局时间拉伸后的结果。这不能当作"转录质量"，而是一个诊断工具——它隔离了系统时间偏移后，展示剩余的 pitch/onset 匹配能力。

即使在时间校准后，仍有 46%-63% 的音符无法匹配，表明问题不仅限于时间偏移。

---

## 六、源码关键证据

### 6.1 Tempo 写入流程（核心问题来源）

位于 `src/audio_to_yaml_converter.py:446-522`，`PianoTranscriber.transcribe()` 方法：

```python
# src/audio_to_yaml_converter.py Line 512-520
output_midi = writeMidi(notes_est)          # Transkun 输出写入 MIDI
output_midi.write(str(midi_path))

# 6. 若需自动检测 BPM，对输出 MIDI 修正 tempo 轨道
if config.audio_path is not None:           # --audio 模式必然触发
    self._fix_midi_tempo(midi_path=midi_path, audio_path=config.audio_path)
```

触发条件：当用户通过 `--audio` 传入 MP3 时，`config.audio_path is not None` 为 True，因此**必然**调用 `_fix_midi_tempo`。若通过 `--input-midi` 跳过转录，则 `config.audio_path` 为 None，不触发。

位于 `src/audio_to_yaml_converter.py:596-631`，`_fix_midi_tempo()` 方法：

```python
# Line 608-628
detected_bpm = _estimate_audio_bpm(audio_path=audio_path)  # librosa 检测
midi_file = mido.MidiFile(str(midi_path))
tempo_meta = mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(detected_bpm), time=0)

# 遍历轨道，覆盖已有 set_tempo 消息
for track in midi_file.tracks:
    for message in track:
        if message.type == "set_tempo":
            message.tempo = tempo_meta.tempo   # 直接覆盖
            has_tempo = True
            break
if not has_tempo:
    midi_file.tracks[0].insert(0, tempo_meta)  # 无 tempo 则插入

midi_file.save(str(midi_path))
```

此方法**无条件覆盖**已有 tempo 元事件。它不检查 MIDI 是否已有合理的 tempo map（如 MAESTRO 的固定 120 BPM）。

### 6.2 mir_eval 时间处理：关键澄清

评测脚本 `evaluate_transcription.py` 通过 `pretty_midi` 解析 MIDI：

```python
# line 110-120 (extract_notes_from_midi)
pm = pretty_midi.PrettyMIDI(midi_path)
for instrument in pm.instruments:
    for note in instrument.notes:
        # note.start 和 note.end 已由 pretty_midi 将 tick -> tempo map -> 秒
        intervals.append([note.start, note.end])
        pitches_hz.append(midi_pitch_to_hz(note.pitch))
```

`pretty_midi` 内部将 MIDI tick 通过 tempo map 转换为秒。`mir_eval.transcription` 接收的 intervals 已经是秒值。

**因此以下两句话均不准确，需要精确表述：**

- 错误表述："mir_eval 不受 MIDI tempo 轨道影响"——mir_eval 本身确实不解析 tempo，但它接收的秒级输入**已经**被 pretty_midi 的 tempo map 转换所影响。
- 正确表述："mir_eval 本身不直接解析 MIDI tempo 元数据，但其输入的秒值已经包含了 pretty_midi 按 tempo map 从 tick 到秒的转换结果。当 `_fix_midi_tempo` 修改了 MIDI 的 tempo 轨道后，pretty_midi 会使用新的 tempo map 重新计算每个音符的起止秒值，导致所有 onset/offset 发生非线性（实际为全局线性拉伸）偏移。"

### 6.3 raw MIDI 与缩编的分界

- **评测对象**：`pipeline/midi/input.mid` —— 即 Transkun 输出 + `_fix_midi_tempo` 覆盖后的 MIDI 文件。它是 `transcribe()` 方法的最终返回值。
- **缩编输入转换**（位于 `audio_to_yaml_converter.py:785-820`，`_read_midi_notes()`）：此函数仅在 `MidiToYamlConverter.convert()` 内部调用，它将 `note.start` (秒) 按 `60.0/config.bpm` 转换为拍，执行 `_merge_sustained_midi_notes`（同音延音合并）和 `_cluster_midi_note_onsets`（起音聚类），然后送入缩编管线。**这些操作仅影响缩编输入，不影响 raw 转录评测。**

---

## 七、已证实事实、待验证假设与数据缺口

### 7.1 已证实事实

| 编号 | 事实 | 证据 |
|------|------|------|
| F1 | 管线退出码为 0，两首曲目均完成全链路 | ACCEPTANCE_REPORT.md |
| F2 | `_fix_midi_tempo` 将 librosa 检测 BPM 写入 MIDI，覆盖已有 tempo | 源码 Line 596-631 |
| F3 | MAESTRO 参考 MIDI 默认 tempo=120 BPM | MAESTRO 数据集约定 |
| F4 | BPM 写入导致系统时间偏移：巴赫拉伸比 0.9795 (BPM 117.5), 肖邦 0.9363 (BPM 112.3) | diagnose_timing.py 输出 |
| F5 | 原始未校准肖邦 onset+pitch F1=0.0534，校准后 F1=0.5408 | transcription_evaluation.json + diagnose_timing.py |
| F6 | 校准后匹配对 onset P95 约 47ms（< 50ms 容差） | ACCEPTANCE_REPORT.md |
| F7 | 巴赫校准后 F1 (0.3737) < 肖邦 (0.5408)，与直觉相反 | ACCEPTANCE_REPORT.md |
| F8 | 巴赫 conversion_report 中 raw_note_count=1846（参考 1925），肖邦 raw_note_count=1726（参考 2134） | conversion_report.json |
| F9 | conversion_report 中肖邦 `octave_moved_notes=9531` 远大于 raw_note_count，说明八度折叠被多次触发 | 肖邦 conversion_report.json |
| F10 | `_read_midi_notes` 中的 `_merge_sustained_midi_notes` 使用 `merge_sustained_gap_beats=0.25` | config + conversion_report.json |
| F11 | 当前巴赫未生成原始未校准 `transcription_evaluation.json` | 文件不存在 |
| F12 | Demucs stem 可正常解码，大小合理（巴赫 42.5MB, 肖邦 24.5MB） | ACCEPTANCE_REPORT.md |
| F13 | 转录音符数（Transkun raw 1912/2023）与 conversion_report raw_note_count（1846/1726）不一致，说明从 MIDI 解析时存在过滤 | 交叉比对 ACCEPTANCE_REPORT + conversion_report |

### 7.2 待验证假设

| 编号 | 假设 | 需要验证的内容 |
|------|------|--------------|
| H1 | BPM 检测值（巴赫 117.5, 肖邦 112.3）存在偏差 | 对原始音频用多条 onset 检测算法交叉验证 |
| H2 | MAESTRO 参考 tempo=120 是固定值，但实际演奏可能有微妙 tempo 波动 | 用 pretty_midi 分析参考 MIDI 的 tempo map |
| H3 | 巴赫的分解和弦在 Transkun 中可能产生大量碎音，随后被 `_merge_sustained_midi_notes` 误合并 | 比对 raw MIDI 与参考 MIDI 巴赫段落的音高分布 |
| H4 | `_fix_midi_tempo` 的线性 BPM 修改不足以反映真实 tempo 波动，导致匹配对存在残留偏移 | 对比参考 MIDI 的 tempo 曲线与转录 tempo |
| H5 | `_cluster_midi_note_onsets` 的起音聚类窗口可能对巴赫的均匀节奏不适配 | 分析巴赫转录音符的 onset 间隔分布 |
| H6 | Demucs 分轨可能引入伪影（phase 失真、谐波损失），导致 Transkun 转录更多假音 | 做消融：原始 WAV 直接送 Transkun vs Demucs stem |
| H7 | Transkun 的 onset 精度在 50ms 容差内接近边界（P95=47ms），需要更精细的评测 | 用 20ms 容差重跑评测 |
| H8 | conversion_report 的 raw_note_count < 转录音符数是因为 MIDI 解析时过滤了 duration<=0 的音符或鼓轨 | 检查 `_read_midi_notes` 的过滤条件（Line 800: `note.end <= note.start`） |

### 7.3 数据缺口

| 编号 | 缺口 | 影响 |
|------|------|------|
| G1 | 巴赫原始未校准 `transcription_evaluation.json` 缺失 | 无法比较巴赫与肖邦在未校准条件下的 onset 偏移量 |
| G2 | 消融：原始 WAV（不经 Demucs）直送 Transkun 的转录结果缺失 | 无法量化 Demucs 分轨对转录精度的影响 |
| G3 | 消融：禁用 `_fix_midi_tempo`（保留 Transkun 默认 120 BPM）的转录结果缺失 | 无法量化 tempo 覆盖对匹配的独立影响 |
| G4 | 参考 MIDI 的实际 tempo map 分析缺失 | 无法判断线性拉伸是否足够，还是需要 tempo 曲线对齐 |
| G5 | `_merge_sustained_midi_notes` 和 `_cluster_midi_note_onsets` 对巴赫的影响量化缺失 | 无法判断巴赫偏低 F1 是否由清洗步骤导致 |
| G6 | 缩编后 YAML 与原始 MIDI 的音符召回率对比缺失 | 无法分离"转录不良"与"缩编裁剪"对最终事件数的影响 |
| G7 | 未评测 velocity 对齐 | mir_eval transcription_velocity 未被调用 |

---

## 八、要求外部模型回答的具体问题

### 8.1 根因诊断

**Q1**：`_fix_midi_tempo` 将 librosa 检测 BPM 覆盖到 MIDI 后，pretty_midi 按新 tempo map 将 tick 转为秒。这个转换的性质是什么？是严格的全局线性缩放，还是因为不同轨道的 tick 密度不同会产生非线性扭曲？若为线性缩放，为什么校准后的 onset F1（巴赫 0.3737 vs 肖邦 0.5408）仍远低于预期？

**Q2**：巴赫（规则织体，F1=0.3737）校准后表现差于肖邦（高密度，F1=0.5408）的可能根因有哪些？请从以下角度分别提出假设并给出验证方法：
- Transkun 对不同织体类型的建模偏差（巴赫的分解和弦 vs 肖邦的密集和弦）
- `_merge_sustained_midi_notes` 与 `_cluster_midi_note_onsets` 对巴赫均匀节奏的副作用
- MAESTRO 参考 tempo=120 与真实演奏 tempo 波动之间的残差
- Demucs 分轨对巴赫复调声部的影响

**Q3**：校准后匹配对的 onset P95 为 47ms（接近 50ms 容差边界），但仍有 46-63% 音符完全无法匹配。剩余不匹配音符的主要失败模式是什么？是：
- onset 偏差超过 50ms？
- pitch 偏差超过 50 cents（半音误判）？
- 转录完全漏检（silence/harmonic confusion）？
- 转录插入假音（非乐音被识别为音符）？
请设计可验证的诊断方案。

### 8.2 实验设计

**Q4**：请按优先级排序，提出 3-5 个最小化真实消融实验，每个实验需明确：
- 修改哪个变量（如禁用 tempo 写入、跳过 Demucs、调整清洗参数）
- 保留哪些变量（其他条件不变）
- 期望观察什么指标变化
- 该变化能证实或证伪什么假设

**Q5**：本项目使用透明文件隔离（每曲目独立 `pipeline/` 目录），所有产物留存。在现有架构下，推荐什么策略确保消融实验的完全可复现性（包括随机种子、模型版本快照、操作系统级记录）？

### 8.3 评测方法

**Q6**：当前评测使用 50ms/50cents 的事件级 mir_eval。对于实际演奏中存在的微妙 tempo 波动（rubato、渐快渐慢），50ms 窗口是否合适？请判断是否需要引入以下评测方法并给出理由：
- Beat-level 对齐评测（先按拍点对齐再逐音比对）
- DTW（动态时间规整）对齐后的逐音评测
- Chroma-level 或 frame-level 评测作为补充

**Q7**：如何区分"转录 onset 偏移"与"评测对齐失败"？即，当前线性拉伸校准后仍有大量不匹配音符，如何判断这些是真实转录质量差（Transkun 问题）而非评测对齐策略不足（mir_eval 的 match_notes 贪心匹配对真实 tempo 波动不鲁棒）？

### 8.4 修复方案

**Q8**：假设确认 `_fix_midi_tempo` 的单值 BPM 覆盖是系统性时间偏移的主要来源，请给出两种修复方案的优劣势比较：
- 方案 A：检测 MIDI 是否已有合理 tempo map，若有则保留不覆盖
- 方案 B：不修改转录 MIDI 的 tempo，改为在缩编阶段单独传入 config.bpm
- 方案 C：用参考音频的真实 onset 检测替代 librosa，或将 MAESTRO 已知 120 BPM 作为先验

**Q9**：针对巴赫织体问题，若确认 `_merge_sustained_midi_notes` 和 `_cluster_midi_note_onsets` 是瓶颈，推荐的最小改动方案是什么（参数调整 vs 逻辑重构）？

---

## 九、建议交付格式

请按以下结构输出诊断报告：

```
1. 根因分析（2-4 页）
   1.1 Tempo 写入机制及其对 mir_eval 的精确影响路径
   1.2 巴赫 vs 肖邦 F1 反转的成因（需提供验证方法，非仅假设）
   1.3 剩余不匹配音符的失败模式分类与定量估计

2. 消融实验设计（1-2 页）
   2.1 实验矩阵（变量 x 曲目 x 指标）
   2.2 每项实验的执行命令（基于现有脚本）
   2.3 预期结果与对应的证伪条件

3. 最小修复方案（1-2 页）
   3.1 排序后的修复步骤（仅描述设计，不要求完整代码）
   3.2 每步修复的预期指标改善幅度
   3.3 回归风险与验证方法

4. 开放性建议
   4.1 是否需要引入 DTW/beat-level 评测
   4.2 是否需要独立评测 Demucs 的分离质量
   4.3 长期改进方向（模型微调、数据增强等）
```

---

## 十、禁止事项

1. **不得声称问题已修复**：本 Brief 描述的是当前观测状态，修复尚未实施。
2. **不得虚构未观测的指标**：如巴赫的未校准 F1、velocity 评测等均未运行，不得给出虚构值。
3. **不得给出未经验证的实现代码**：本 Brief 寻求诊断和方案设计，而非可直接运行的修复补丁。
4. **不得把线性校准后分数当作原始转录结果**：校准后 F1 是诊断工具，不是转录系统的直接表现。
5. **不得把 YAML 结构 PASS 等同音乐质量 PASS**：门 C（YAML 加载、token 映射、键数约束）通过只说明输出格式合法，不说明音乐内容正确。
6. **不得使用 emoji 或夸张语气**：诊断报告需保持技术严谨。
7. **不得提出需要 Mock/Stub/降级的实验方案**：所有消融必须基于真实模型、真实数据和真实环境运行。
