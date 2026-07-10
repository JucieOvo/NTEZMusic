# Transkun 原始 88 键 MIDI 真实评测报告

> 评测日期：2026-07-10  
> 作者：JucieOvo  
> 范围：MP3/WAV 经 Demucs 钢琴 stem 到 Transkun 2.0.1 原始 88 键 MIDI  
> 不包含：MIDI 清洗、MusicXML、乐理分析、36 键缩编、YAML 与自动弹奏

## 一、结论

当前已复现的核心故障是**全局时间轴被错误修改**，不是 Transkun 生成了错误旋律。

Transkun 原始 120 BPM MIDI（B）在两首 MAESTRO 真实曲目上的 onset+pitch F1 分别达到：

- 巴赫 BWV 846：`0.9950`；
- 肖邦 Op.10 No.12：`0.9728`。

项目调用 `_fix_midi_tempo()` 后，只修改 tempo 元事件、不重算音符 tick，导致同一批音符在秒时间上被整体拉伸。被污染的 MIDI（C）未经校准时 onset+pitch F1 降至：

- 巴赫：`0.0475`；
- 肖邦：`0.0534`。

把 C 按准确 tempo 比例恢复后，F1 分别恢复到 `0.9950` 和 `0.9728`，与 B 完全一致。B 与 C 的全部 note_on/note_off tick、音高、力度、通道、顺序和数量也逐事件一致，唯一有效差异是 tempo 元事件。

因此：

1. 当前 MP3 到 88 键 MIDI 的“旋律错误”判断不成立；
2. 已确认的主要故障是 `_fix_midi_tempo()` 对绝对时间轴的破坏；
3. Transkun 剩余问题主要是少量漏音和 note offset/时值误差，肖邦比巴赫明显；
4. 现阶段不应使用乐理重排修复该问题，也不应优先更换转录模型。

## 二、真实环境与产物

### 2.1 环境

| 项目 | 实际值 |
|------|--------|
| GPU | NVIDIA GeForce RTX 3090 24GB |
| PyTorch | 2.7.1+cu118 |
| CUDA | 11.8 |
| Transkun | 2.0.1 |
| checkpoint SHA-256 | `50A80010EFFC2A59FFCD068A95CD2B29BD7F23A27A3515BC3CCD209C89A3D44C` |
| mir_eval | 0.8.2 |
| pretty_midi | 0.2.11 |
| mido | 1.3.3 |
| 模型采样率 | 44100 Hz |
| segment hop/size | 8 秒 / 16 秒 |

### 2.2 新生成的不可变原始产物

每首曲目均生成：

```text
evaluation/raw_transkun/notes_est.json
evaluation/raw_transkun/transkun_raw_120bpm.mid
evaluation/raw_transkun/run_manifest.json
evaluation/diagnostic_raw_b.json
evaluation/diagnostic_tempo_c.json
```

巴赫：

`work/transcription_benchmark/bach_bwv846/evaluation/`

肖邦：

`work/transcription_benchmark/chopin_op10_no12/evaluation/`

manifest 中的 MIDI 与 JSON SHA-256 已重新计算并验证一致。

## 三、B 与 C 的直接语义对照

| 曲目 | B tempo | C tempo | B/C note 事件 | note 数 |
|------|---------|---------|---------------|---------|
| 巴赫 | 500000 μs/beat（120 BPM） | 510638 μs/beat（约 117.5 BPM） | 全部逐事件相等 | 1910 |
| 肖邦 | 500000 μs/beat（120 BPM） | 534283 μs/beat（约 112.3 BPM） | 全部逐事件相等 | 2023 |

tempo 覆盖后的秒时间满足：

```text
C_time = B_time × C_tempo_microseconds / 500000
```

恢复 B 时间轴所需的缩放因子为：

```text
Bach   ≈ 500000 / 510638 ≈ 0.97917
Chopin ≈ 500000 / 534283 ≈ 0.93584
```

诊断评测得到的最优因子分别为 `0.9791` 和 `0.9360`，与 MIDI tempo 元数据给出的理论值一致。

## 四、原始 Transkun MIDI 评测

### 4.1 核心指标

| 指标 | 巴赫 B | 肖邦 B |
|------|-------:|-------:|
| 参考音符数 | 1925 | 2134 |
| 估计音符数 | 1910 | 2023 |
| onset+pitch 匹配数 | 1908 | 2022 |
| onset-only F1 | 0.9950 | 0.9728 |
| onset+pitch F1 | 0.9950 | 0.9728 |
| onset+pitch+offset F1 | 0.9372 | 0.8400 |
| velocity-aware F1 | 0.9361 | 0.8270 |
| frame piano-roll accuracy | 0.9070 | 0.6811 |
| onset MAE | 3.41 ms | 4.71 ms |
| onset P95 | 8.07 ms | 11.72 ms |
| offset MAE（完整匹配对） | 14.30 ms | 18.53 ms |
| offset P95（完整匹配对） | 42.24 ms | 46.09 ms |

### 4.2 旋律与节奏判断

两首曲目的 onset-only F1 与 onset+pitch F1 完全相同。这表明：

- 成功对齐的起音几乎全部具有正确音高；
- 不是“节奏对了但旋律音高错了”；
- 估计 MIDI 中新增的错误音符极少；
- 肖邦的主要内容损失是漏掉部分参考音符，而不是把音符转成错误音高。

原始 B 的 onset P95 仅为 8.07 ms 和 11.72 ms，远小于 50 ms 评测容差。Transkun 的起音节奏精度不是当前瓶颈。

真正下降明显的是 offset、velocity-aware 和 frame 指标，尤其是肖邦。这说明后续优化应集中在：

1. 弱音或高密度织体中的漏音；
2. note-off/音符时值估计；
3. 延音踏板 CC64 与音符持续语义的联合评测；
4. 高密度段落中的帧级持续覆盖率。

Transkun 的负 pitch 事件并非非法音高。实际核查确认 `pitch=-64` 等事件对应 MIDI ControlChange，例如 CC64 延音踏板。生成器现已将其标记为 `event_type="cc_event"`，不会与 88 键音符混淆。

## 五、tempo 覆盖造成的指标损害

| 指标 | 巴赫 B | 巴赫 C | 肖邦 B | 肖邦 C |
|------|-------:|-------:|-------:|-------:|
| onset-only F1 | 0.9950 | 0.4980 | 0.9728 | 0.6346 |
| onset+pitch F1 | 0.9950 | 0.0475 | 0.9728 | 0.0534 |
| onset+pitch+offset F1 | 0.9372 | 0.0266 | 0.8400 | 0.0293 |
| frame accuracy | 0.9070 | 0.1470 | 0.6811 | 0.0523 |
| 精确 scale 校准后 onset+pitch F1 | 0.9950 | 0.9950 | 0.9728 | 0.9728 |

C 中较高的 onset-only、极低的 onset+pitch 并不表示模型发生大规模错音。时间轴拉伸后，一些起音会偶然靠近其他参考音符，但其对应音高不同，从而制造“似乎是音高错误”的假象。

## 六、旧诊断结果偏低的原因

`work/transcription_benchmark/diagnose_timing.py` 存在两个诊断错误：

### 6.1 使用最后音符结束时间估算 scale

代码第 17–18 行使用：

```python
scale = reference_last_note_end / estimated_last_note_end
```

最后音符的 offset 会受到漏音、note-off 误差和尾部事件差异影响，不等于 tempo 元数据导致的严格时间比例。数万分之几到千分之一的 scale 误差，在 140–240 秒长曲上会累积成超过 50 ms 的尾部偏差。

这正是旧报告校准后仍只有 `0.3737/0.5408` 的主要原因。使用精细 scale 搜索或 MIDI tempo 的精确比例后，可恢复到 `0.9950/0.9728`。

### 6.2 “P+Ons”字段取错

旧脚本第 39 行使用：

```python
po["F-measure"]
```

该字段仍包含 offset 约束，却被打印为 `P+Ons`。mir_eval 对应的无 offset 字段应为 `F-measure_no_offset`。这解释了旧报告中 onset+pitch 与 onset+pitch+offset F1 完全相同的异常现象。

## 七、修复策略

### P0：停止修改 Transkun 原始 MIDI

`writeMidi()` 生成的 120 BPM MIDI 已经正确编码音频绝对秒时间。主管线不得再对同一文件调用 `_fix_midi_tempo()`。

推荐数据流：

```text
notes_est
  → transkun_raw_120bpm.mid（不可变，供评测与演奏）
  → detected_bpm.json（独立节拍元数据）
```

### P1：如需音乐 BPM，只生成独立派生文件

如果后续乐谱量化确实需要 librosa BPM，应把它作为配置或 sidecar 传递。若必须生成带检测 BPM 的 MIDI，则需要同步重算全部 tick，以保持每个事件的绝对秒时间不变，且不得覆盖原始 B。

### P2：替换旧诊断脚本

后续评测统一使用：

`work/transcription_benchmark/diagnostic_evaluator.py`

不得再使用最后音符时长比作为精确 tempo 校准，也不得把诊断性对齐指标当作 raw 端到端指标。

### P3：剩余质量优化方向

tempo 问题消除后，不建议优先调整旋律模型。下一阶段应针对肖邦进行：

1. 按力度、音区、局部复音密度统计漏音；
2. 对 note-off 和 CC64 踏板事件进行联合分析；
3. 比较 `source.wav` 直送 Transkun 与 Demucs piano stem，判断分轨是否损失弱音和持续音；
4. 以 onset+pitch+offset、frame accuracy 为主要优化指标，不再只看 onset+pitch。

## 八、验证结果

真实测试结果：

- `tests/test_raw_midi_generator.py`：20 passed；
- `tests/test_diagnostic_evaluator.py` + `tests/test_evaluate_transcription.py`：50 passed；
- 四个变更文件 LSP 诊断：0 error，0 warning；
- 所有测试均使用真实音频、真实 Transkun checkpoint、真实 MAESTRO MIDI；
- 未使用 Mock、Stub、假数据、CPU 回退或伪造结果。

## 九、适用边界

本结论来自两首 MAESTRO 独奏钢琴录音，并且当前 B 使用现有 Demucs piano stem 重新推理。它足以证明本项目现有基准失败由 tempo 覆盖引起，但不能直接证明 Transkun 在任意混合编制 MP3 上同样具有 0.97 以上 F1。混合音乐仍需独立评估 Demucs 分离质量。
