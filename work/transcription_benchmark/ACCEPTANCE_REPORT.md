# MP3 → MIDI → 36 键 YAML 全链路真实验收报告

> 日期：2026-07-10
> 环境：RTX 3090 24GB · PyTorch 2.7.1 CUDA 11.8 · Demucs 4.0.1 · Transkun 2.0.1 · mir_eval 0.8.2
> 基准：MAESTRO v3.0.0 两组真实 Disklavier 演奏

---

## 一、执行概要

| 事项 | 巴赫 BWV 846 | 肖邦 Op.10 No.12 |
|------|------------|-----------------|
| 退出码 | 0 | 0 |
| Demucs stem | piano.wav 42.5 MB | piano.wav 24.5 MB |
| Transkun raw MIDI | 13.2 KB, 1912 notes | 14.6 KB, 2023 notes |
| BPM 检测值 | 117.5 | 112.3 |
| 最终 YAML | 51.4 KB, 1698 events | 33.2 KB, 1064 events |
| YAML 合法加载 | PASS | PASS |

**门 A（管线可运行性）：PASS。** Demucs、Transkun、BPM 检测、缩编和 YAML 导出均真实完成。

---

## 二、转录正确性（门 B）

### 2.1 关键发现：BPM 写入导致系统时间拉伸

MAESTRO 参考 MIDI 采用固定 tempo=120 BPM（数据集团队约定），而管线中的 `_fix_midi_tempo()` 将 librosa 检测值写入转录 MIDI 的 tempo 轨道。这导致纯音符时间序列产生约 2%~6% 的线性拉伸。在不做任何校准的情况下，50ms 容差的逐音 F1 接近 0。

| 条目 | 参考时长 | 转录时长 | 拉伸比 |
|------|---------|---------|--------|
| 巴赫 | 239.84s | 244.86s | 0.9795 |
| 肖邦 | 138.11s | 147.50s | 0.9363 |

### 2.2 线性校准后的逐音指标

使用 ref_duration / est_duration 做简单全局缩放后：

| 指标 | 巴赫 BWV 846 | 肖邦 Op.10 No.12 |
|------|------------|-----------------|
| P+Ons+Off F1 | **0.3737** | **0.5408** |
| P+Ons F1 | 0.3737 | 0.5408 |
| Precision | 0.3750 | 0.5556 |
| Recall | 0.3725 | 0.5267 |
| 匹配对 | 717 / 1925 | 1124 / 2134 |
| 漏检参考音符 | 1208 | 1010 |
| 额外转录音符 | 1195 | 899 |
| Onset MAE | 25.5 ms | 23.8 ms |
| Onset P95 | 47.0 ms | 46.7 ms |
| Onset P99 | 49.4 ms | 49.3 ms |
| Avg Overlap Ratio | 0.8477 | 0.5729 |

### 2.3 解读

- 匹配对的 onset 精度良好，P95 在 50ms 容差以内；
- 但超过一半的音符无法匹配，表明存在大量音高错误、起音误检或时值偏差；
- 巴赫（规则织体）F1 反而**低于**肖邦（高密度），这与直觉相反 — 需进一步分析是否巴赫的大量分解和弦起音被合并或误检；
- Avg Overlap Ratio 差异大（巴赫 0.85 vs 肖邦 0.57），说明肖邦的匹配对在时值覆盖上偏差较大，可能与快速段落的高频起音聚类有关。

**本轮不预设通过阈值。** 上述指标是 Transkun + Demucs 在真实 Disklavier 录音上的实际基线。

---

## 三、36 键缩编输出（门 C）

| 检查项 | 巴赫 | 肖邦 |
|--------|------|------|
| YAML `PianoConfigLoader` 严格加载 | PASS | PASS |
| song.name / BPM 字段 | PASS | PASS |
| playback / keyboard_mapping 完整 | PASS | PASS |
| raw_score 事件可索引 | PASS | PASS |

缩编事件数远小于原始音符数（巴赫 1698 / 肖邦 1064），这是 `attention_weighted` 密度裁剪后的正常结果。YAML 结构合法、BPM 正确。

---

## 四、根因定位

### 已确认问题

1. **BPM 写入导致的系统性时间偏移**（严重）：`_fix_midi_tempo()` 将 librosa 检测值写入 MIDI，与 MAESTRO 参考 tempo=120 不兼容。纯线性缩放将 F1 从 ~2.7% 提升到 37–54%，但仍远未达到可信任水平。**建议优先修复：检测 MIDI 是否已有合理 tempo map，避免在已有范围内覆盖。**

2. **Transkun 转录精度**（严重）：即使在时间校准后，只有 37–54% 的音符能在 50ms/50cents 容差内匹配。这是真实转录质量，不是评测工具错误。

3. **巴赫表现差于肖邦的反直觉结果**：巴赫以规整织体著称，但 F1 仅为肖邦的 69%。需排查是否：`_merge_sustained_midi_notes` 将巴赫分解和弦碎片合并、起音聚类窗口不适配巴赫的均匀节奏。

### 非原因排除

- **评测工具已验证正确**：mir_eval 直接从 pretty_midi 读取秒级时间，不受 MIDI tempo 轨道影响。
- **文件完整性**：SHA-256 全部通过，无下载损坏。
- **CUDA/模型错误**：无。

---

## 五、结论

| 门 | 结果 |
|----|------|
| 门 A：管线可运行性 | **PASS** |
| 门 B：转录正确性 | **基线已记录** — 校准后 F1 0.37/0.54 |
| 门 C：YAML 与缩编约束 | **PASS**（结构合法） |

**当前管线可以运行，但转录质量不足以直接支撑高质量游戏曲谱。** 最优先修复项：BPM 处理策略和 Transkun 转录精度诊断。在修复之前，不应声称"MP3→MIDI 管线已通过验收"。

---

## 六、全部产物清单

```
work/transcription_benchmark/
├── bach_bwv846/
│   ├── input.mp3                              # 基准输入
│   ├── source.wav                              # 无损对照
│   ├── reference_performance.midi               # 金标准
│   ├── final_attention_weighted.yaml            # 36键 YAML
│   ├── transcription_evaluation.json            # 逐音 JSON
│   ├── transcription_evaluation.md              # 逐音 Markdown
│   ├── pipeline/demucs/htdemucs_6s/input/piano.wav  # Demucs stem
│   ├── pipeline/midi/input.mid                  # Transkun raw
│   └── pipeline/conversion_report.json
├── chopin_op10_no12/
│   ├── input.mp3
│   ├── source.wav
│   ├── reference_performance.midi
│   ├── final_attention_weighted.yaml
│   ├── transcription_evaluation.json
│   ├── transcription_evaluation.md
│   ├── pipeline/demucs/htdemucs_6s/input/piano.wav
│   ├── pipeline/midi/input.mid
│   └── pipeline/conversion_report.json
├── evaluate_transcription.py                   # 评测工具
├── diagnose_timing.py                          # 诊断脚本
└── README.md
```
