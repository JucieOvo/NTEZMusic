# NTEZMusic MP3 to 88 键 MIDI 质量诊断 Brief v2

> 目标读者：具备自动钢琴转录、MAESTRO、mir_eval、MIDI 时序与踏板语义经验的专家模型  
> 编写日期：2026-07-11  
> 作者：JucieOvo  
> 状态：原始 Transkun MIDI 已完成真实复评，待专家诊断剩余质量问题  
> 版本：v2.0  
> 替代版本：`docs/mp3_to_midi_diagnosis_brief_for_expert_model.md` v1.0

---

## 一、执行摘要

NTEZMusic 的音频前端负责把真实音频转换为 88 键钢琴 MIDI。当前分析范围严格截止到 Transkun 88 键 MIDI，不涉及 MusicXML、乐理分析、36 键缩编、YAML 或自动弹奏。

主管线为：

```text
MP3/WAV
    -> Demucs 4.0.1 钢琴分轨
    -> Transkun 2.0.1 转录
    -> writeMidi() 生成 120 BPM 原始 MIDI（B）
    -> librosa 检测全曲平均 BPM
    -> _fix_midi_tempo() 覆盖 tempo 元事件，生成播放版本（C）
```

v1 Brief 曾根据 C 与 MAESTRO 参考 MIDI 的直接评测，认为 Transkun 存在严重时间偏移和大量旋律错误。重新保留并评测 tempo 覆盖前的原始 MIDI（B）后，该判断被推翻。

真实结果如下：

| 指标 | 巴赫 B | 肖邦 B |
|------|-------:|-------:|
| onset+pitch F1 | **0.9950** | **0.9728** |
| onset+pitch+offset F1 | 0.9372 | 0.8400 |
| velocity-aware F1 | 0.9361 | 0.8270 |
| frame piano-roll accuracy | 0.9070 | 0.6811 |
| onset P95 | 8.07 ms | 11.72 ms |

被平均 BPM 覆盖后的 C 未经时间恢复时，onset+pitch F1 只有巴赫 0.0475、肖邦 0.0534。但 B 与 C 的全部 note_on/note_off tick、音高、力度、通道、顺序及数量逐事件完全相同，唯一有效差异是 tempo 元事件。按准确 tempo 比例恢复 C 后，F1 会完整恢复到 B 的 0.9950 和 0.9728。

因此，本项目当前作出以下判断与决策：

1. Transkun 原始输出不存在大规模旋律音高错误；
2. Transkun 的 onset 节奏精度已经较高；
3. C 的低 raw F1 来源于全曲统一变速，不代表转录失败；
4. 项目接受全局平均速度造成的绝对时间尺度变化，不要求修复该行为；
5. 剩余研究重点转为漏音、note-off/时值、踏板语义和帧级持续覆盖率；
6. 专家不应再提出以修复 tempo 覆盖为首要目标的方案。

---

## 二、产品决策与诊断边界

### 2.1 已接受的全局速度局限

项目允许根据 librosa 检测的全曲平均 BPM，对整首 MIDI 的播放速度进行统一缩放。

该行为具有以下性质：

- 不改变 MIDI pitch；
- 不改变音符数量；
- 不改变 note_on/note_off tick；
- 不改变音符顺序；
- 不改变局部相对节奏比例；
- 会改变全部事件的绝对秒时间和总播放时长。

当前两首基准曲目中：

| 曲目 | 原始 B tempo | C tempo | 全局时间变化 |
|------|--------------|---------|--------------|
| 巴赫 | 120 BPM | 约 117.5 BPM | 播放时长约增加 2.1% |
| 肖邦 | 120 BPM | 约 112.3 BPM | 播放时长约增加 6.8% |

这是已知并接受的产品局限，不作为本轮待修复缺陷。

### 2.2 评测口径

后续必须区分两种用途：

| 产物 | 用途 | 允许的评测结论 |
|------|------|----------------|
| B：tempo 未覆盖的 Transkun 原始 MIDI | 评估模型转录质量 | 可报告 raw onset/pitch/offset/velocity/frame 指标 |
| C：平均 BPM 覆盖后的播放 MIDI | 实际统一速度播放 | 不得直接与原演奏绝对时间做 50ms raw 对齐并声称模型失败 |

C 如需与参考 MIDI 做诊断对照，必须先按其 tempo 元数据恢复准确的全局时间比例。校准结果只能说明 C 与 B 的等价关系，不能代替 B 的 raw 指标。

### 2.3 明确排除的范围

本 Brief 不讨论：

- 88 键 MIDI 到 36 键游戏键盘的缩编；
- 乐理重排、主旋律保护、和声简化；
- MusicXML、piano_svsep 或 GNN 手部分离；
- YAML token 与自动弹奏；
- 是否取消平均 BPM 覆盖。

---

## 三、真实环境与基准数据

### 3.1 软件与硬件

| 组件 | 版本或规格 |
|------|------------|
| OS | Windows 11 |
| GPU | NVIDIA GeForce RTX 3090 24GB |
| PyTorch | 2.7.1+cu118 |
| CUDA | 11.8 |
| Transkun | 2.0.1 |
| Transkun checkpoint SHA-256 | `50A80010EFFC2A59FFCD068A95CD2B29BD7F23A27A3515BC3CCD209C89A3D44C` |
| mir_eval | 0.8.2 |
| pretty_midi | 0.2.11 |
| mido | 1.3.3 |
| Demucs | 4.0.1 |
| 模型采样率 | 44100 Hz |
| Transkun segment hop | 8 秒 |
| Transkun segment size | 16 秒 |

### 3.2 真实曲目

| 属性 | 巴赫 BWV 846 | 肖邦 Op.10 No.12 |
|------|--------------|-----------------|
| 数据集 | MAESTRO v3.0.0 | MAESTRO v3.0.0 |
| 输入音频 | 现有 Demucs piano stem | 现有 Demucs piano stem |
| 参考 MIDI 音符数 | 1925 | 2134 |
| 估计 MIDI 音符数 | 1910 | 2023 |
| 参考音高范围 | 36–84 | 24–94 |
| B tempo | 120 BPM | 120 BPM |
| C tempo | 约 117.5 BPM | 约 112.3 BPM |

### 3.3 基准文件

巴赫目录：

```text
work/transcription_benchmark/bach_bwv846/
```

肖邦目录：

```text
work/transcription_benchmark/chopin_op10_no12/
```

每首曲目的关键产物：

```text
reference_performance.midi
pipeline/demucs/htdemucs_6s/input/piano.wav
pipeline/midi/input.mid                         # C
evaluation/raw_transkun/notes_est.json
evaluation/raw_transkun/transkun_raw_120bpm.mid # B
evaluation/raw_transkun/run_manifest.json
evaluation/diagnostic_raw_b.json
evaluation/diagnostic_tempo_c.json
```

---

## 四、A/B/C 定义与证据链

### 4.1 产物定义

| 标识 | 定义 |
|------|------|
| A | MAESTRO Disklavier `reference_performance.midi` |
| B | Transkun `notes_est` 经 `writeMidi()` 直接生成的 120 BPM 原始 MIDI |
| C | B 的 tempo 元事件被 `_fix_midi_tempo()` 覆盖后的播放 MIDI |

### 4.2 B 与 C 的逐事件一致性

真实核查结果：

| 曲目 | B/C note_on+note_off 事件数 | 事件是否完全一致 |
|------|-----------------------------:|------------------|
| 巴赫 | 3820 | 是 |
| 肖邦 | 4046 | 是 |

比较字段包括：

- track；
- 绝对 tick；
- message type；
- MIDI note；
- velocity；
- channel；
- 事件顺序。

B 与 C 的差异仅为：

```text
Bach:   500000 μs/beat -> 510638 μs/beat
Chopin: 500000 μs/beat -> 534283 μs/beat
```

因此可以排除 `_fix_midi_tempo()` 改写旋律音高、漏删音符或改变 tick 节奏的可能性。

### 4.3 CC 事件语义

Transkun `notes_est` 中存在负 pitch，例如 `pitch=-64`。该值不是非法钢琴音高，而是 Transkun 对 MIDI ControlChange 的内部编码：

```text
pitch > 0  -> MIDI note
pitch <= 0 -> ControlChange(abs(pitch))
```

其中 `-64` 对应 CC64 延音踏板。

真实事件统计：

| 曲目 | 总事件 | 钢琴音符 | CC 事件 |
|------|-------:|---------:|--------:|
| 巴赫 | 1964 | 1910 | 54 |
| 肖邦 | 2201 | 2023 | 178 |

后续专家不得把负 pitch 当作模型生成非法音高的证据。

---

## 五、原始 Transkun MIDI 真实指标

### 5.1 音符级与帧级指标

| 指标 | 巴赫 B | 肖邦 B |
|------|-------:|-------:|
| onset-only Precision | 0.9990 | 0.9995 |
| onset-only Recall | 0.9912 | 0.9475 |
| onset-only F1 | 0.9950 | 0.9728 |
| onset+pitch Precision | 0.9990 | 0.9995 |
| onset+pitch Recall | 0.9912 | 0.9475 |
| onset+pitch F1 | **0.9950** | **0.9728** |
| onset+pitch+offset F1 | 0.9372 | 0.8400 |
| velocity-aware F1 | 0.9361 | 0.8270 |
| frame piano-roll Precision | 0.9640 | 0.9247 |
| frame piano-roll Recall | 0.9387 | 0.7211 |
| frame piano-roll Accuracy | 0.9070 | 0.6811 |

### 5.2 匹配与残差

| 指标 | 巴赫 B | 肖邦 B |
|------|-------:|-------:|
| 参考音符数 | 1925 | 2134 |
| 估计音符数 | 1910 | 2023 |
| onset+pitch 匹配数 | 1908 | 2022 |
| onset+pitch+offset 匹配数 | 1797 | 1746 |
| onset MAE | 3.41 ms | 4.71 ms |
| onset P95 | 8.07 ms | 11.72 ms |
| offset MAE（完整匹配对） | 14.30 ms | 18.53 ms |
| offset P95（完整匹配对） | 42.24 ms | 46.09 ms |

### 5.3 当前可得出的音乐内容结论

两首曲目的 onset-only F1 与 onset+pitch F1 完全相同，且估计侧 Precision 接近 1。这说明：

1. 成功检测到的起音几乎都具有正确音高；
2. 大规模半音错判、八度错判和谐波假音并不存在；
3. 肖邦的主要内容问题是 Recall 下降，即部分参考音符未被输出；
4. onset 时间误差远小于 50 ms，起音节奏不是主要瓶颈；
5. offset、frame recall 和 velocity-aware 指标明显较低，应作为剩余优化重点。

必须注意：onset+pitch 匹配数差异只能说明检测覆盖率，不能直接确定漏音发生在弱音、内声部、低音还是高密度和弦。该分类仍需进一步实验。

---

## 六、平均 BPM 覆盖的准确影响

### 6.1 未校准 C 指标

| 指标 | 巴赫 C | 肖邦 C |
|------|-------:|-------:|
| onset-only F1 | 0.4980 | 0.6346 |
| onset+pitch F1 | 0.0475 | 0.0534 |
| onset+pitch+offset F1 | 0.0266 | 0.0293 |
| frame piano-roll Accuracy | 0.1470 | 0.0523 |

这些值不代表 Transkun 模型质量，只反映 C 与 A 使用了不同的绝对时间尺度。

### 6.2 精确全局恢复

恢复 B 时间轴的理论比例：

```text
Bach   = 500000 / 510638 ≈ 0.97917
Chopin = 500000 / 534283 ≈ 0.93584
```

诊断评测器得到：

| 曲目 | 最优 scale | 恢复后 onset+pitch F1 | B raw F1 |
|------|-----------:|-----------------------:|---------:|
| 巴赫 | 0.9791 | 0.9950 | 0.9950 |
| 肖邦 | 0.9360 | 0.9728 | 0.9728 |

恢复后的指标与 B 完全一致，未发现额外局部 tempo 漂移。

### 6.3 为什么 C 容易制造“旋律错误”假象

C 的时间轴被整体拉伸后，一部分估计 onset 会偶然靠近其他参考音符的 onset。onset-only 匹配会把这些偶然邻近视为时间匹配，但对应 pitch 不同，因此出现：

```text
onset-only F1 中等
onset+pitch F1 极低
```

这不能证明旋律音高错误。必须先恢复全局时间比例或直接评测 B。

---

## 七、对 v1 Brief 的更正

### 7.1 旧的 0.3737/0.5408 不是可靠的校准后上限

旧脚本 `work/transcription_benchmark/diagnose_timing.py` 使用：

```python
scale = reference_last_note_end / estimated_last_note_end
```

最后音符结束时间会受漏音、note-off 和尾部事件差异影响，不能精确代表 MIDI tempo 覆盖比例。千分之一量级的 scale 误差在长曲尾部会累积成超过 50 ms 的偏差。

使用 tempo 元数据理论比例或 0.0001 精细 scale 搜索后，校准 F1 可恢复到 0.9950/0.9728。

### 7.2 旧脚本的“P+Ons”字段取值错误

旧脚本打印：

```python
po["F-measure"]
```

但该字段包含 offset 约束。mir_eval 的无 offset 字段是：

```python
po["F-measure_no_offset"]
```

因此 v1 中 onset+pitch 与 onset+pitch+offset 完全相同的表格不应继续引用。

### 7.3 仍然有效的 v1 证据

以下事实继续有效：

- 两首真实 MAESTRO 管线均成功运行；
- Demucs stem、MIDI、YAML 等产物可解析；
- `_fix_midi_tempo()` 会覆盖 tempo 元事件；
- C 的直接评测会产生系统性时间偏移；
- 禁止把 YAML 结构通过等同于音乐质量通过。

---

## 八、当前真正需要专家回答的问题

### 8.1 漏音定位

**Q1**：肖邦 B 的 onset+pitch Recall 为 0.9475，而 Precision 为 0.9995。如何对约 112 个未匹配参考音符进行可解释分类？至少考虑：

- 参考 velocity；
- 音区；
- 同时复音数；
- 与主声部或内声部的关系；
- 起音间隔；
- 是否位于快速重复音、琶音或高密度和弦；
- 是否靠近 Transkun 16 秒 segment 边界；
- Demucs stem 中对应频率能量是否被削弱。

请给出不依赖 Mock 的逐音诊断方法和统计图设计。

### 8.2 note-off 与时值

**Q2**：巴赫和肖邦的 onset+pitch F1 很高，但 onset+pitch+offset F1 分别降至 0.9372 和 0.8400。应如何区分：

- 模型 note-off 检测偏差；
- 踏板造成的声学持续与键盘释放时间差异；
- MAESTRO Disklavier note-off 与音频衰减尾部不一致；
- Demucs 对衰减尾音的损伤；
- 快速重复音导致的音符边界切分问题？

### 8.3 CC64 踏板感知评测

**Q3**：当前 note-level offset 指标与 frame piano-roll 指标没有把 Transkun 输出的 CC64 踏板事件用于延长音符。对于真实钢琴转录，应否同时报告：

1. key-release note offset；
2. pedal-extended acoustic offset；
3. CC64 事件级 Precision/Recall/F1；
4. 应用踏板后的 frame-level piano-roll 指标？

请说明推荐协议及其与 mir_eval 标准指标的关系。

### 8.4 Demucs 与 MP3 消融

**Q4**：MAESTRO 本身是独奏钢琴，Demucs 理论上没有分离其他乐器的收益。是否应进行以下真实消融：

| 输入 | Demucs | 目的 |
|------|--------|------|
| `source.wav` | 否 | 无损独奏钢琴上限 |
| `input.mp3` | 否 | 单独评估 MP3 编码影响 |
| `source.wav` | 是 | 单独评估 Demucs 对衰减和弱音的影响 |
| `input.mp3` | 是 | 复现当前前端 |

重点观察 onset+pitch Recall、offset F1、frame recall 和 CC64，而不是再次诊断全局 tempo。

### 8.5 面向任意混合 MP3 的泛化

**Q5**：当前 0.9950/0.9728 来自 MAESTRO 独奏钢琴，不能直接推广到带人声、鼓和其他乐器的混合音频。对于真实混合 MP3，如何建立分层评测：

```text
原始混合音频
    -> Demucs piano stem 质量
    -> Transkun 音符质量
    -> 踏板与时值质量
```

请给出每层应使用的真实指标和最小测试集。

---

## 九、建议的最小后续实验

### E1：漏音分桶

对 B 中未匹配参考音符按 velocity、音区、局部复音密度、时值、segment 边界距离分桶。

目标：判断肖邦约 5% Recall 损失的主导条件。

### E2：踏板感知 offset 评测

解析 A 与 B 的 CC64，分别计算 key-release 与 pedal-extended 音符区间。

目标：判断 offset/frame 指标下降中有多少来自踏板语义差异。

### E3：Demucs 消融

使用同一 checkpoint 和分段参数，完成 WAV/MP3 与 Demucs 开关的四组真实推理。

目标：判断弱音漏检和持续时值损失是否由 Demucs 或 MP3 引入。

### E4：高密度片段案例分析

从肖邦中选择漏音密度最高的若干真实片段，联合展示：

- 原始 waveform 或频谱；
- Demucs stem；
- 参考 piano roll；
- Transkun piano roll；
- CC64 曲线；
- velocity 和局部复音数。

目标：形成可供模型、音频前处理或阈值优化使用的直接证据。

---

## 十、现有工具与验证状态

### 10.1 工具

原始 MIDI 生成：

```text
work/transcription_benchmark/raw_midi_generator.py
```

诊断评测：

```text
work/transcription_benchmark/diagnostic_evaluator.py
```

完整评测报告：

```text
docs/transkun_raw_midi_evaluation_report.md
```

### 10.2 已完成验证

- 原始生成器真实测试：20 passed；
- 诊断评测与旧评测回归：50 passed；
- 相关 Python 文件 LSP：0 error，0 warning；
- 两首曲目均使用 RTX 3090 和真实 checkpoint 重新推理；
- B 的 tempo 已验证为 120 BPM；
- B/C note 事件逐项一致；
- manifest 输出哈希与实际文件一致；
- 未使用 Mock、Stub、假数据或 CPU 回退。

---

## 十一、要求专家遵守的结论边界

1. 不得再把 C 的 0.0475/0.0534 当作 Transkun raw 转录质量；
2. 不得把 0.3737/0.5408 当作精确时间校准后的模型上限；
3. 不得把负 pitch 的 CC64 事件解释成非法钢琴音高；
4. 不得提出修复或移除全局平均速度作为首要方案，该局限已被产品接受；
5. 不得把 onset-only 与 onset+pitch 的差值直接称为旋律错误率；
6. 不得把对齐后的诊断指标当作 raw 端到端指标；
7. 不得讨论 88 键到 36 键缩编或乐理重排；
8. 不得使用 Mock、Stub、伪造数据、伪成功结果或降级环境；
9. 不得从两首 MAESTRO 独奏钢琴直接推断任意混合 MP3 的质量；
10. 所有新结论必须附带真实产物路径、评测参数和可复现实验。

---

## 十二、建议专家交付格式

```text
1. 剩余质量问题排序
   1.1 漏音主导条件
   1.2 note-off/时值误差来源
   1.3 CC64 与踏板扩展语义

2. 真实消融实验矩阵
   2.1 WAV/MP3 × Demucs 开关
   2.2 每项实验的固定变量与观测指标
   2.3 证伪条件

3. 逐音与分段诊断设计
   3.1 漏音分桶
   3.2 高密度段落案例
   3.3 segment 边界统计

4. 最小优化建议
   4.1 不改变全局平均速度决策
   4.2 优先提高 Recall、offset F1 和 frame recall
   4.3 每项建议的回归风险与验证方式

5. 适用边界
   5.1 MAESTRO 独奏钢琴结论
   5.2 任意混合 MP3 所需的额外证据
```
