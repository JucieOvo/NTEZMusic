# 乐谱语义感知缩编管线技术方案

> 面向后续实际代码编写的详细设计文档
>
> 编写日期：2026-07-09
>
> 项目根目录：`F:\NTEZMusic`
>
> 作者：JucieOvo

---

## 目录

1. [方案目标](#一方案目标)
2. [核心判断](#二核心判断)
3. [运行时边界](#三运行时边界)
4. [总体管线设计](#四总体管线设计)
5. [新增模块规划](#五新增模块规划)
6. [乐谱语义中间表示](#六乐谱语义中间表示)
7. [机翻 MIDI 合规化策略](#七机翻-midi-合规化策略)
8. [五线谱生成与校验策略](#八五线谱生成与校验策略)
9. [专业参考谱验证策略](#九专业参考谱验证策略)
10. [36 键乐理缩编策略](#十36-键乐理缩编策略)
11. [与现有管线的接入方式](#十一与现有管线的接入方式)
12. [配置参数设计](#十二配置参数设计)
13. [报告与中间产物设计](#十三报告与中间产物设计)
14. [验证门槛](#十四验证门槛)
15. [MVP 实施阶段](#十五mvp-实施阶段)
16. [风险与处理原则](#十六风险与处理原则)
17. [后续代码编写清单](#十七后续代码编写清单)

---

## 一、方案目标

### 1.1 总目标

在现有 `音频 / MIDI -> YAML` 工具链中新增一条“乐谱语义感知缩编管线”，将机器转录得到的 MIDI 事件先规整为可解释、可校验、可导出的合规五线谱语义结构，再基于该结构完成面向《异环》36 键键盘的乐理缩编。

新的目标管线为：

```text
用户音频 / 用户 MIDI
    ↓
音频转录或 MIDI 读取
    ↓
机翻 MIDI 清洗与规范化
    ↓
节拍网格、小节、拍号、调性、声部、和声、乐句推断
    ↓
合规五线谱语义中间层
    ↓
可选：导出 MusicXML / MIDI / 审计报告
    ↓
可选：用户本地专业参考谱验证
    ↓
36 键乐理缩编
    ↓
现有 YAML 曲谱
    ↓
现有自动弹奏器
```

### 1.2 需要解决的问题

当前项目已有 `attention_weighted` 与 `svsep_mpdr` 等缩编策略，但这些策略主要基于 MIDI 数值和启发式评分，尚未形成完整的乐谱语义层。因此存在以下问题：

1. 机器转录 MIDI 的起音、时值和和弦聚合存在数值偏差。
2. MIDI 事件本身不包含可靠的乐句、小节、声部、和声功能等语义。
3. 当前压缩主要解决“落入 36 键范围”和“听感相似”，但不保证缩编策略符合人类乐理经验。
4. 现有 `attention_weighted` 文档已指出缺少调性检测，所有音高被近似平等处理。
5. 现有 `svsep_mpdr` 文档已指出左手选择逻辑尚未完整实现 bass anchor、harmony color、voice leading、velocity、duration 等维度。

### 1.3 新方案的工程定位

本方案不是替换现有管线，而是在现有 MIDI 到 YAML 之间新增一层：

```text
ScoreRegularizationLayer
```

该层负责把“有噪声的机器 MIDI”提升为“合规乐谱语义”。后续缩编不再只依据原始音高列表，而是依据主旋律、低音骨架、和声功能、声部走向、乐句边界等信息做取舍。

---

## 二、核心判断

### 2.1 MIDI 不是五线谱

机器转录 MIDI 是演奏事件集合，而不是正式乐谱。它记录的是：

```text
音高、起音时间、结束时间、力度、轨道、通道
```

但正式五线谱还需要：

```text
拍号、调号、小节线、声部、谱表、音名拼写、时值、休止符、连音线、乐句结构、和声结构
```

因此，不能把本方案理解成简单格式转换：

```text
MIDI -> MusicXML
```

而应理解成：

```text
noisy MIDI -> symbolic score reconstruction -> legal staff notation
```

### 2.2 合规五线谱需要两类约束

#### 2.2.1 记谱硬约束

这类约束必须严格满足，否则不允许进入后续缩编：

1. 每个小节的总时值必须等于拍号容量。
2. 每个声部内部不允许非法重叠。
3. 空白时间必须由休止符补齐。
4. 跨小节长音必须拆分并使用连音线表达。
5. 音符时值必须落在允许集合中。
6. MusicXML 必须可以成功导出并回读。
7. 输出到 36 键之前必须保留原始 MIDI 对齐关系，便于审计。

#### 2.2.2 乐理软约束

这类约束用于评分和候选选择：

1. 旋律线应尽量连续。
2. 低音骨架应尽量稳定。
3. 和弦拼写应符合调性。
4. 临时记号不应过度混乱。
5. 乐句边界应靠近休止、长音、和声终止或小节周期。
6. 声部之间不应频繁交叉。
7. 缩编后应保留根音、三度、七度等关键和声信息。

### 2.3 GPT 的角色边界

本项目运行时不接入 LLM。GPT 只在开发阶段作为乐理规则来源和工程化顾问参与。

开发期：

```text
GPT 辅助整理乐理规则
GPT 协助设计评分函数
GPT 协助审查算法是否符合乐理经验
GPT 协助把人类经验转为可编码约束
```

运行期：

```text
无 LLM API
无 Prompt
无联网推理
无非确定性音乐判断
```

最终软件必须是确定性管线。同一输入、同一配置、同一环境下应得到一致输出。

---

## 三、运行时边界

### 3.1 输入边界

运行时允许以下输入：

1. 用户音频文件：`mp3`、`wav`、`flac` 等。
2. 用户已有 MIDI 文件。
3. 用户本地专业参考谱文件，作为可选验证集。

参考谱可以是：

```text
MIDI
MusicXML
```

第一阶段暂不直接支持 PDF 乐谱识别。PDF 乐谱识别属于光学音乐识别任务，建议后续独立规划。

### 3.2 输出边界

运行时输出包括：

1. 清洗后的 MIDI。
2. 合规化后的 MusicXML。
3. 乐谱语义分析 JSON。
4. 转录质量审计报告。
5. 36 键缩编 YAML。
6. 缩编过程审计报告。

### 3.3 禁止行为

运行时禁止：

1. 用伪造默认值掩盖拍号、调性、声部等推断失败。
2. 在无法生成合规五线谱时继续输出伪成功 YAML。
3. 在缺失必要中间结果时直接跳过验证。
4. 使用 Mock、Stub 或伪参考谱代替真实输入。
5. 使用 LLM 作为最终转换结果来源。

---

## 四、总体管线设计

### 4.1 完整运行时流程

```text
AudioToYamlPipeline
    │
    ├─ DemucsSeparator
    │      输入音频，输出 piano stem
    │
    ├─ PianoTranscriber
    │      输入 piano stem，输出 raw MIDI
    │
    ├─ MidiEventCanonicalizer
    │      输入 raw MIDI，输出 cleaned note events
    │
    ├─ BeatGridEstimator
    │      输入 cleaned note events，输出 tempo map、beat grid、bar grid
    │
    ├─ ScoreQuantizer
    │      输入 note events + beat grid，输出 quantized score events
    │
    ├─ ScoreSemanticAnalyzer
    │      输入 quantized score events，输出 key、meter、voices、harmony、phrases
    │
    ├─ StaffNotationBuilder
    │      输入 score semantics，输出 MusicXML / music21 Score
    │
    ├─ ReferenceScoreAuditor（可选）
    │      输入系统谱 + 用户参考谱，输出转录审计报告
    │
    ├─ TheoryAware36KeyArranger
    │      输入乐谱语义，输出 36 键 score events
    │
    └─ YamlScoreWriter
           输入 36 键 score events，输出现有 YAML
```

### 4.2 第一阶段最小管线

为了降低风险，第一阶段可以先跳过音频前端，直接从 MIDI 开始：

```text
input-midi
    ↓
MidiEventCanonicalizer
    ↓
BeatGridEstimator
    ↓
ScoreQuantizer
    ↓
StaffNotationBuilder
    ↓
TheoryAware36KeyArranger
    ↓
YAML
```

音频转录仍复用现有 `PianoTranscriber`。这样可以先验证乐谱语义层与缩编层是否可行。

---

## 五、新增模块规划

### 5.1 `score_model.py`

职责：定义乐谱语义中间表示的数据模型。

建议包含：

```text
CanonicalNoteEvent
BeatGrid
BarGrid
QuantizedNoteEvent
VoiceAssignment
ChordAnalysis
PhraseBoundary
ScoreSemanticModel
ReductionDecision
```

### 5.2 `midi_event_canonicalizer.py`

职责：清洗机器转录 MIDI。

主要功能：

1. 读取 MIDI 音符。
2. 合并同音重叠。
3. 合并延音导致的碎片。
4. 聚类近邻起音。
5. 过滤极短噪声音。
6. 输出可审计的 `CanonicalNoteEvent`。

### 5.3 `beat_grid_estimator.py`

职责：从清洗后的音符事件中重建节拍网格与小节网格。

主要功能：

1. 估计全局 BPM 或平滑 tempo map。
2. 生成候选拍号。
3. 对每种拍号建立候选小节网格。
4. 根据起音贴合度、低音强拍、和声变化、乐句停顿评分。
5. 选择最优 beat grid。

### 5.4 `score_quantizer.py`

职责：将浮点时间规整为合法乐谱时值。

主要功能：

1. 将起音对齐到 beat grid。
2. 将时值映射到允许的记谱时值集合。
3. 处理跨小节连音。
4. 插入必要休止符。
5. 生成每个声部内合法的量化事件。

### 5.5 `score_semantic_analyzer.py`

职责：分析调性、和声、声部、乐句等音乐语义。

主要功能：

1. 调性识别。
2. 音名拼写建议。
3. 左右手与谱表分配。
4. 声部分配。
5. 和弦识别。
6. 主旋律识别。
7. 低音骨架识别。
8. 乐句边界识别。

### 5.6 `staff_notation_builder.py`

职责：从乐谱语义模型生成可导出的五线谱对象。

主要功能：

1. 生成 `music21.stream.Score`。
2. 写入拍号、调号、小节、谱表、声部。
3. 写入音符、休止符、连音线。
4. 导出 MusicXML。
5. 回读 MusicXML 做一致性校验。

### 5.7 `reference_score_auditor.py`

职责：用用户本地提供的专业参考谱验证系统转录质量。

主要功能：

1. 解析参考 MIDI / MusicXML。
2. 将参考谱转换为同一内部表示。
3. 执行系统谱与参考谱对齐。
4. 输出音高、起音、时值、声部、和声、乐句差异。
5. 给出是否建议继续缩编的判断。

### 5.8 `theory_aware_36key_arranger.py`

职责：基于乐谱语义执行 36 键缩编。

主要功能：

1. 保留主旋律。
2. 保留低音骨架。
3. 保留关键和声色彩音。
4. 删除重复八度和低价值填充音。
5. 将音符移动到 C3-B5 范围。
6. 最小化声部跳进。
7. 控制单和弦最大按键数。
8. 输出可转为现有 YAML 的事件序列。

---

## 六、乐谱语义中间表示

### 6.1 `CanonicalNoteEvent`

用于表示清洗后的真实音符事件。

字段建议：

```text
note_id                 音符唯一编号
pitch                   MIDI 音高
start_second            原始起音秒
end_second              原始结束秒
start_beat_raw          原始起音拍
end_beat_raw            原始结束拍
velocity                MIDI 力度
source_track            来源轨道
source_channel          来源通道
merge_sources           被合并的原始音符编号
confidence              清洗后保留置信度
```

### 6.2 `BeatGrid`

用于表示推断出的拍点系统。

字段建议：

```text
bpm                     全局 BPM
tempo_map               可选 tempo 曲线
beat_unit               拍单位
time_signature          拍号
beat_positions_second   每个拍点的秒位置
beat_positions_raw      每个拍点的原始拍位置
confidence              节拍网格置信度
```

### 6.3 `BarGrid`

用于表示小节结构。

字段建议：

```text
bar_index               小节编号
start_beat              小节起始拍
end_beat                小节结束拍
strong_beats            小节内强拍位置
candidate_score         当前小节网格评分
```

### 6.4 `QuantizedNoteEvent`

用于表示已规整到乐谱网格的音符。

字段建议：

```text
note_id                 继承 CanonicalNoteEvent 的 ID
pitch                   MIDI 音高
spelled_pitch           音名拼写
bar_index               所属小节
voice_id                声部编号
staff_id                谱表编号
quantized_start_beat    量化起音
quantized_duration      量化时值
notation_value          记谱时值名称
tie_start               是否连音起点
tie_continue            是否连音延续
tie_stop                是否连音终点
is_rest                 是否休止符
```

### 6.5 `ChordAnalysis`

用于表示和声分析结果。

字段建议：

```text
bar_index               小节编号
beat_position           拍点位置
root_pitch_class        根音音级
chord_quality           和弦性质
chord_tones             和弦内音
non_chord_tones         非和声音
function_label          和声功能标签
confidence              和声识别置信度
```

### 6.6 `PhraseBoundary`

用于表示乐句边界。

字段建议：

```text
boundary_beat           边界拍点
boundary_bar            边界小节
boundary_type           起句 / 收句 / 半终止 / 完全终止 / 弱边界
evidence                证据列表
confidence              置信度
```

### 6.7 `ScoreSemanticModel`

总装结构。

字段建议：

```text
canonical_notes         清洗后音符
beat_grid               节拍网格
bar_grid                小节网格
quantized_notes         量化音符
key_signature           调号
time_signature          拍号
voice_assignments       声部分配
chord_analyses          和声分析
phrase_boundaries       乐句边界
validation_report       合规校验结果
```

---

## 七、机翻 MIDI 合规化策略

### 7.1 问题来源

机器转录 MIDI 常见问题包括：

1. 起音浮动：同一和弦内多个音符起音可能相差几十毫秒。
2. 时值浮动：真实长音可能被切碎，短音可能被拉长。
3. 踏板残留：延音造成同音重叠和持续时间异常。
4. 漏音：弱音、内声部、快速经过音容易缺失。
5. 多音：泛音、噪声或模型误判生成额外音符。
6. BPM 偏差：转录 MIDI 的 tempo 轨道可能与真实音乐结构不一致。
7. 乐句缺失：MIDI 本身不表达乐句。

### 7.2 同音重叠合并

同一 pitch 在时间上重叠或间隔极短时，应判断是否属于同一实际音符。

合并条件建议：

```text
same_pitch = True
gap_seconds <= same_pitch_merge_gap_seconds
overlap_seconds >= 0 或 gap 很小
```

合并策略：

```text
new_start = min(start)
new_end = max(end)
new_velocity = max 或 velocity 加权平均
merge_sources = 原始 note_id 列表
```

### 7.3 和弦起音聚类

同一和弦内音符起音略有偏差时，需要聚类到同一乐谱起点。

聚类窗口建议：

```text
onset_cluster_window_seconds = 0.03 到 0.08
```

聚类中心建议使用 velocity 加权平均，但要保留每个原始起音用于审计。

### 7.4 异常短音过滤

极短且低力度的音符可能是噪声。

过滤必须谨慎，不能删除真正装饰音。建议第一阶段只过滤同时满足以下条件的音符：

```text
duration_seconds < min_noise_duration_seconds
velocity < min_noise_velocity
附近没有相同旋律运动证据
不是强拍上的低音
不是和弦根音候选
```

如果无法确定，应保留并标记低置信度，而不是删除。

### 7.5 节拍网格重建

节拍网格不应只依赖 MIDI tempo 轨道。建议根据 onset 重新估计。

候选生成：

```text
候选 BPM 范围：根据现有 BPM 附近扩展
候选拍号：2/4、3/4、4/4、6/8
候选 phase：不同强拍偏移
```

评分指标：

```text
onset_grid_error        起音贴合拍点的误差
chord_compactness       和弦内音符是否集中
bass_strong_beat_score  低音是否常在强拍
harmony_change_score    和声变化是否常在小节开头
rest_boundary_score     休止或长音是否贴合乐句边界
notation_complexity     量化后是否产生过多复杂时值
```

总评分示例：

```text
BeatGridScore =
    - 0.35 * onset_grid_error
    + 0.20 * chord_compactness
    + 0.15 * bass_strong_beat_score
    + 0.15 * harmony_change_score
    + 0.10 * rest_boundary_score
    - 0.05 * notation_complexity
```

### 7.6 时值量化

第一阶段建议允许以下时值：

```text
全音符
二分音符
四分音符
八分音符
十六分音符
附点二分
附点四分
附点八分
跨小节连音线
```

暂不优先支持复杂 tuplets。若检测到三连音特征明显，再在后续版本添加。

量化必须满足：

```text
每个声部每小节总时值合法
音符不能越过小节线而不拆分
休止符必须补齐空白
```

---

## 八、五线谱生成与校验策略

### 8.1 推荐技术路线

建议使用 `music21` 作为五线谱对象构建与 MusicXML 导出的主要工具。

原因：

1. 支持 `stream.Score`、`Part`、`Measure`、`Voice` 等乐谱结构。
2. 支持调性分析、拍号、调号、音名、时值、休止符、连音线。
3. 支持 MusicXML 导出。
4. 项目现有 `piano_svsep` 管线已经涉及 MusicXML 生态，技术路线兼容。

### 8.2 构建步骤

```text
ScoreSemanticModel
    ↓
创建 music21.stream.Score
    ↓
创建右手 Part 与左手 Part
    ↓
写入 TimeSignature 与 KeySignature
    ↓
按 BarGrid 创建 Measure
    ↓
按 VoiceAssignment 创建 Voice
    ↓
插入 Note / Rest / Tie
    ↓
导出 MusicXML
    ↓
回读 MusicXML
    ↓
一致性校验
```

### 8.3 合规校验器

需要实现 `ScoreNotationValidator`。

硬校验项：

1. 所有小节时值完整。
2. 所有声部内部无非法重叠。
3. 所有跨小节音符都有连音处理。
4. 所有休止符合法填充。
5. 所有音符都有 staff / voice 归属。
6. MusicXML 可以导出。
7. 导出后可以回读。
8. 回读后的音符数量、音高集合、量化时值与导出前一致。

软评分项：

1. 临时记号密度。
2. 声部交叉次数。
3. 旋律跳进强度。
4. 低音线连续度。
5. 和声变化稳定性。
6. 乐句边界规律性。

---

## 九、专业参考谱验证策略

### 9.1 定位

专业参考谱是用户本地提供的验证集，用于判断当前音频转录和乐谱合规化结果是否可信。

它不参与运行时默认流程，除非用户显式传入：

```text
--reference-midi
--reference-musicxml
```

### 9.2 对齐流程

```text
系统生成 ScoreSemanticModel
    │
    ├─ 转为对齐用 note sequence
    │
用户参考谱
    │
    ├─ 解析为 reference note sequence
    │
    ↓
小节级对齐
    ↓
拍点级对齐
    ↓
音符级匹配
    ↓
差异统计
    ↓
审计报告
```

### 9.3 匹配指标

#### 9.3.1 音高准确性

```text
pitch_precision = 匹配正确音符数 / 系统输出音符数
pitch_recall = 匹配正确音符数 / 参考谱音符数
pitch_f1 = 2 * precision * recall / (precision + recall)
```

#### 9.3.2 起音偏差

统计匹配音符的 onset 差异：

```text
mean_onset_error_beats
median_onset_error_beats
p95_onset_error_beats
```

#### 9.3.3 时值偏差

统计匹配音符的 duration 差异：

```text
mean_duration_error_beats
median_duration_error_beats
p95_duration_error_beats
```

#### 9.3.4 和弦一致性

按量化拍点比较音高集合：

```text
chord_jaccard = |system_pitches ∩ reference_pitches| / |system_pitches ∪ reference_pitches|
```

#### 9.3.5 声部一致性

比较 staff / voice 归属是否接近参考谱。

#### 9.3.6 乐句一致性

比较系统推断的 phrase boundary 与参考谱中休止、长音、终止式位置的接近程度。

### 9.4 审计结论

审计报告应给出：

```text
PASS        转录质量足以进入乐理缩编
WARNING     存在偏差，但可继续缩编，并在报告中提示
FAIL        转录质量不足，不建议继续缩编
```

FAIL 条件示例：

```text
pitch_f1 < reference_min_pitch_f1
p95_onset_error_beats > reference_max_onset_error_beats
chord_jaccard_mean < reference_min_chord_jaccard
```

---

## 十、36 键乐理缩编策略

### 10.1 目标约束

输出必须满足《异环》键盘硬约束：

```text
MIDI 范围：C3-B5，即 48-83
最大同时按键数：由 max_chord_notes 控制，默认 6
无延音踏板
无力度输出
```

### 10.2 乐理保留优先级

缩编时建议使用如下优先级：

```text
主旋律
> 低音骨架
> 和弦根音
> 三度
> 七度
> 关键经过音
> 五度
> 六度 / 九度色彩音
> 重复八度
> 装饰音 / 填充音
```

### 10.3 音符评分函数

每个候选音符应获得综合评分：

```text
NoteKeepScore =
    melody_weight          * melody_score
  + bass_weight            * bass_anchor_score
  + chord_weight           * chord_function_score
  + voice_weight           * voice_leading_score
  + phrase_weight          * phrase_position_score
  + duration_weight        * duration_score
  + velocity_weight        * velocity_score
  - duplication_penalty    * octave_duplicate_score
  - density_penalty        * local_density_score
```

第一阶段建议默认权重：

```text
melody_weight = 0.25
bass_weight = 0.20
chord_weight = 0.20
voice_weight = 0.15
phrase_weight = 0.08
duration_weight = 0.07
velocity_weight = 0.05
```

### 10.4 和声功能评分

和弦内音评分建议：

| 和声角色 | 建议分值 | 说明 |
|----------|----------|------|
| 根音 | 1.00 | 定义和声基础，尤其低音声部优先保留 |
| 三度 | 0.90 | 决定大三 / 小三性质 |
| 七度 | 0.85 | 决定属七、导向感与紧张度 |
| 低音非根音 | 0.75 | 可能表达转位或低音线进行 |
| 九度 / 六度 | 0.60 | 色彩音，密度允许时保留 |
| 五度 | 0.45 | 和声稳定但信息量较低 |
| 重复八度 | 0.10 | 优先删除 |
| 非和声音 | 视上下文 | 经过音、辅助音、倚音需结合旋律判断 |

### 10.5 36 键映射策略

候选映射不应只做独立八度折叠，而应结合声部连续性。

每个音符可生成多个候选：

```text
pitch + 12k, k ∈ [-6, 6], 且 48 <= pitch + 12k <= 83
```

候选选择代价：

```text
MappingCost =
    range_cost
  + voice_leap_cost
  + harmony_spacing_cost
  + melody_register_cost
  + bass_register_cost
  + hand_overlap_cost
```

目标：

```text
在满足 C3-B5 与 max_chord_notes 的前提下，最小化全曲声部跳进和和声变形。
```

### 10.6 和弦密度控制

当同一时间片超过 `max_chord_notes` 时，按 `NoteKeepScore` 排序保留。

但必须加入保护规则：

1. 主旋律音不可轻易删除。
2. 小节强拍低音不可轻易删除。
3. 如果当前和弦有三度，优先保留三度而非五度。
4. 七和弦场景下，七度优先级高于五度。
5. 重复八度优先删除。

### 10.7 乐句感知缩编

乐句边界附近需要特别处理：

1. 收句长音应尽量保留。
2. 终止式低音应尽量保留。
3. 乐句开头的主题动机应尽量保留。
4. 乐句内部快速装饰可按密度裁剪。

---

## 十一、与现有管线的接入方式

### 11.1 新压缩模式

建议新增：

```text
--pitch-compression-mode score_aware_theory
```

现有模式继续保留：

```text
none
octave_fold
adaptive_octave_fold
hands_decoupled
attention_weighted
svsep_mpdr
```

### 11.2 `MidiToYamlConverter.convert()` 分流

当前 `convert()` 已根据 `pitch_compression_mode` 分流。后续应新增：

```text
if config.pitch_compression_mode == "score_aware_theory":
    return self._build_score_aware_theory_events(midi_path, config)
```

### 11.3 与 `svsep_mpdr` 的关系

`score_aware_theory` 不必完全替代 `svsep_mpdr`。

可以设计为：

```text
score_aware_theory 使用乐谱语义层做主流程
svsep_mpdr 的 GNN staff 预测结果可作为声部分配证据
attention_weighted 的评分思想可作为 note keep score 的一部分
reranker 可继续负责候选精排
```

### 11.4 与现有 YAML 输出的关系

新模式最终仍输出当前自动弹奏器可识别的 YAML，不改变 `piano_auto_player.py` 的输入协议。

---

## 十二、配置参数设计

建议新增参数：

```text
--export-musicxml PATH
--reference-midi PATH
--reference-musicxml PATH
--score-quantize-unit FLOAT
--score-allowed-time-values TEXT
--score-meter-candidates TEXT
--score-key-detection-mode TEXT
--score-enable-triplets
--score-min-note-confidence FLOAT
--score-max-onset-error-beats FLOAT
--score-reference-min-pitch-f1 FLOAT
--score-reference-min-chord-jaccard FLOAT
--score-phrase-detection-mode TEXT
--score-arrangement-report PATH
```

第一阶段建议默认值：

```text
score_quantize_unit = 0.25
score_meter_candidates = "4/4,3/4,2/4,6/8"
score_key_detection_mode = "global"
score_enable_triplets = False
score_min_note_confidence = 0.30
score_max_onset_error_beats = 0.125
score_reference_min_pitch_f1 = 0.85
score_reference_min_chord_jaccard = 0.70
score_phrase_detection_mode = "heuristic"
```

---

## 十三、报告与中间产物设计

### 13.1 建议目录结构

每次运行在 `work-dir` 下输出：

```text
work/song/
├── midi/
│   ├── raw_transcription.mid
│   ├── cleaned.mid
│   └── quantized.mid
├── score/
│   ├── score_semantics.json
│   ├── generated.musicxml
│   └── generated_score_audit.json
├── audit/
│   ├── transcription_alignment_report.json
│   ├── transcription_alignment_report.md
│   ├── notation_validation_report.json
│   └── arrangement_report.md
└── yaml/
    └── output.yaml
```

### 13.2 乐谱合规报告

报告内容：

1. 推断 BPM。
2. 推断拍号。
3. 推断调号。
4. 总小节数。
5. 总音符数。
6. 被合并音符数。
7. 被过滤音符数。
8. 量化误差统计。
9. 声部分配统计。
10. 合规校验结果。

### 13.3 缩编报告

报告内容：

1. 输入音符数。
2. 输出音符数。
3. 保留率。
4. 主旋律保留率。
5. 低音骨架保留率。
6. 和弦关键音保留率。
7. 删除的重复八度数量。
8. 八度迁移数量。
9. 最大和弦密度。
10. 输出是否满足 C3-B5。

---

## 十四、验证门槛

### 14.1 五线谱合规验证

进入 36 键缩编前必须满足：

```text
MusicXML 导出成功
MusicXML 回读成功
每小节时值完整
所有音符有 staff 分配
所有音符有 voice 分配
量化误差不超过阈值
```

### 14.2 参考谱验证

如果用户提供参考谱，则建议满足：

```text
pitch_f1 >= 0.85
mean_onset_error_beats <= 0.125
chord_jaccard_mean >= 0.70
```

否则输出 WARNING 或 FAIL。

### 14.3 36 键输出验证

输出 YAML 前必须满足：

```text
所有 MIDI 音高在 48-83
所有 token 可被现有 NotationParser 解析
所有和弦音数 <= max_chord_notes
无空事件
总播放时长合法
YAML 可以被 PianoConfigLoader 回读
```

---

## 十五、MVP 实施阶段

### 15.1 第一阶段：文档与数据模型

目标：建立后续代码开发基础。

任务：

1. 完成本方案文档。
2. 新建乐谱语义数据模型。
3. 明确新模式参数。
4. 明确报告格式。

### 15.2 第二阶段：MIDI 清洗与节拍网格

目标：把 noisy MIDI 转为稳定事件序列。

任务：

1. 实现 `MidiEventCanonicalizer`。
2. 实现 `BeatGridEstimator`。
3. 输出 cleaned MIDI 与 beat grid 报告。

### 15.3 第三阶段：五线谱合规化

目标：生成可导出、可回读、可校验的 MusicXML。

任务：

1. 实现 `ScoreQuantizer`。
2. 实现 `StaffNotationBuilder`。
3. 实现 `ScoreNotationValidator`。
4. 输出 MusicXML 与合规报告。

### 15.4 第四阶段：乐理语义分析

目标：提取缩编所需乐理信息。

任务：

1. 实现调性识别。
2. 实现声部分配。
3. 实现和弦识别。
4. 实现主旋律与低音骨架识别。
5. 实现乐句边界识别。

### 15.5 第五阶段：36 键乐理缩编

目标：生成乐理合理的 36 键 YAML。

任务：

1. 实现 `TheoryAware36KeyArranger`。
2. 接入现有 YAML 生成逻辑。
3. 实现缩编报告。
4. 与 `attention_weighted`、`svsep_mpdr` 做对比。

### 15.6 第六阶段：参考谱验证

目标：用用户本地专业谱验证系统输出。

任务：

1. 实现参考谱解析。
2. 实现小节级、拍点级、音符级对齐。
3. 实现审计指标。
4. 输出验证报告。

---

## 十六、风险与处理原则

### 16.1 复杂节拍风险

第一阶段不强制支持复杂变拍号、自由速度、复杂 tuplets。

处理原则：

```text
检测到无法可靠规整时，直接报错或输出 FAIL 报告，不伪造成功结果。
```

### 16.2 漏音不可恢复风险

机器转录漏掉的音符无法凭空恢复。

处理原则：

```text
只标记疑似缺失位置，不自动补写不存在的音符。
```

### 16.3 多余音过滤风险

误删装饰音会损害音乐表达。

处理原则：

```text
第一阶段只过滤极短、极弱、低置信度且无乐理支撑的音符。
```

### 16.4 调性误判风险

调性误判会影响音名拼写与和声分析。

处理原则：

```text
输出调性置信度；低置信度时使用中性拼写策略并报告 WARNING。
```

### 16.5 五线谱视觉排版风险

本方案第一阶段关注 MusicXML 语义合法，不追求出版级排版质量。

处理原则：

```text
先保证结构正确，再考虑视觉美观。
```

---

## 十七、后续代码编写清单

后续实际代码编写建议按以下顺序执行：

1. 新增 `src/score_model.py`。
2. 新增 `src/midi_event_canonicalizer.py`。
3. 新增 `src/beat_grid_estimator.py`。
4. 新增 `src/score_quantizer.py`。
5. 新增 `src/staff_notation_builder.py`。
6. 新增 `src/score_notation_validator.py`。
7. 新增 `src/score_semantic_analyzer.py`。
8. 新增 `src/theory_aware_36key_arranger.py`。
9. 新增 `src/reference_score_auditor.py`。
10. 修改 `src/audio_to_yaml_converter.py`，接入 `score_aware_theory` 模式。
11. 新增真实测试用例，优先覆盖 MIDI 输入到 MusicXML 输出。
12. 新增真实测试用例，覆盖 MusicXML 回读校验。
13. 新增真实测试用例，覆盖 36 键输出约束。
14. 更新 CLI 文档与使用说明。

建议第一轮代码只实现：

```text
MIDI -> cleaned notes -> beat grid -> quantized score -> MusicXML -> validation report
```

等这一段稳定后，再进入：

```text
semantic analysis -> theory-aware 36-key arrangement -> YAML
```

这样可以避免一次性修改过多核心逻辑，降低回归风险。

---

## 结论

本方案将现有项目从“音高压缩型 MIDI 转 YAML”升级为“乐谱语义感知型 36 键编配”。核心思想是先把机器转录 MIDI 规整成合规五线谱语义，再依据主旋律、低音骨架、和声功能、声部进行和乐句结构完成缩编。

运行时不依赖 LLM。GPT 只在开发阶段用于提炼乐理经验，最终所有规则必须固化为确定性代码、可验证报告和硬约束校验器。

后续代码实施应从数据模型和 MIDI 合规化开始，逐步接入 MusicXML 导出、乐理分析和 36 键缩编，不建议一开始直接改动现有核心转换逻辑。
