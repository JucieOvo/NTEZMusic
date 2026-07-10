# 音频转换管线与 svsep_mpdr 声部分离新管线 代码说明书

> **面向对象**：项目开发者（功能原理与代码结构说明，非用户手册）
> **适用版本**：基于 2026-07 代码库
> **作者**：JucieOvo
> **最后更新**：2026-07-11 -- 新增基准评测排名、和弦上限修复说明、行号校准

---

## 目录

0. [基准评测结果](#0-基准评测结果)
1. [整体数据流概览](#1-整体数据流概览)
2. [音频前端管线](#2-音频前端管线)
   - 2.1 DemucsSeparator -- Demucs 真实分轨
   - 2.2 PianoTranscriber -- 钢琴音频转 MIDI
   - 2.3 AudioToYamlPipeline -- 流水线调度器
   - 2.4 BPM 检测逻辑
   - 2.5 MIDI 预处理
3. [svsep_mpdr 新管线](#3-svsep_mpdr-新管线)
   - 3.1 SvsepHandSeparator -- piano_svsep GNN 声部分离
   - 3.2 MidiToYamlConverter.convert 中的路由逻辑
   - 3.3 声部分离后的预处理
   - 3.4 _build_svsep_mpdr_score_events
   - 3.5 _generate_svsep_mpdr_candidates -- 参数扫描
   - 3.6 _build_svsep_mpdr_candidate -- 核心构建
   - 3.7 关键子算法一览
4. [svsep_mpdr 与 attention_weighted 旧管线的差异](#4-svsep_mpdr-与-attention_weighted-旧管线的差异)
5. [配置参数详解与影响](#5-配置参数详解与影响)
6. [潜在脆弱点分析](#6-潜在脆弱点分析)
7. [已知修复记录](#7-已知修复记录)

---

## 0. 基准评测结果

### 0.1 评测环境

在 5 首测试曲目上对所有音高压缩模式进行统一评测，使用相同量化参数（`quantize_beat=0.25`、`max_chord_notes=6`、`allow_accidentals=True`、`out_of_range_policy=octave_fold`）。

### 0.2 排名

| 排名 | 模式 | 平均分 | 说明 |
|:----:|------|:------:|------|
| 1 | `hands_decoupled` | -- | 三重交叉验证 + 和声功能简化 |
| 2 | `attention_weighted` | -- | 小节滑动窗口 + 交叉注意力 |
| 3 | `adaptive_octave_fold` | -- | 和弦统一八度偏移 |
| **4** | **`svsep_mpdr`** | **0.5273** | GNN 分离 + MPDR 预算感知 + 五维精排 |
| 5 | `octave_fold` | -- | 逐音独立六度折叠 |
| -- | `none` | -- | 不压缩 |

### 0.3 svsep_mpdr 逐曲得分

| 曲目 | 得分 |
|------|:----:|
| cruel_angel_thesis | 0.6148 |
| mihang_familiar_world | 0.4392 |
| unravel | 0.6906 |
| idontwanttobegay | 0.4511 |
| one_last_kiss | 0.4406 |

### 0.4 解读

- `svsep_mpdr` 对简单编曲（如 unravel 0.6906）表现最佳，GNN 分离准确率高，MPDR 缩编后左手清晰不浑浊
- 对复杂编曲（如 idontwanttobegay 0.4511）得分偏低，主要瓶颈在于左手选择逻辑不完整（仅保留 bass + fifth，丢失三度/七度色彩音）
- `mihang_familiar_world`（0.4392）此前为唯一不及格候选（因原始和弦超过 `max_chord_notes=6` 导致硬约束失败），已在 `_candidates_to_modifier_safe_tokens` 中修复（详见第 7 节）

---

## 1. 整体数据流概览

### 1.1 端到端流水线

```
音频文件 (.mp3/.wav/...)
    │ (可选)
    ▼
DemucsSeparator.separate()       -- 调用本机 demucs 命令分轨
    │
    ▼  (piano stem)
PianoTranscriber.transcribe()    -- Transkun 神经网络转录
    │
    ▼  (MIDI 文件)
MidiToYamlConverter.convert()    -- MIDI 到 YAML 曲谱转换
    │                                此处根据 pitch_compression_mode 分流
    ▼
YAML 曲谱 + 书面报告
```

### 1.2 MidiToYamlConverter.convert 入口分流

convert() 是管线中**核心决策点**。确认 MIDI 文件存在后，执行：

1. `_read_midi_notes()` -- 读取并量化 MIDI 音符，包含延音合并
2. **分流判断**：
   - 若 `pitch_compression_mode == "svsep_mpdr"` -> 走新管线
   - 否则 -> 走旧管线 `_build_score_events()`
3. `_validate_playback_timing()` -- 校验总播放时长不超过限制

### 1.3 新旧管线的高层对比

| 维度 | 旧管线 (hands_decoupled / attention_weighted) | 新管线 (svsep_mpdr) |
|---------|-----------------------------------------------|----------------------|
| 左右手判定 | 基于统计分割线（三重交叉验证） | piano_svsep GNN 预测 staff 标签 |
| 左手处理 | 和声功能优先级简化 | MPDR 预算感知动态缩编 |
| 右手处理 | 完整保留 + 密度约束 | 保守型内声削弱 |
| 折叠方式 | 统一八度折叠 | 左右手独立折叠，各自参考中心 |
| 候选生成 | 参数扫描 -> 评分 -> 选最优 | 参数扫描 -> 评分 -> 选最优 |
| 核心数据结构 | `grouped_pitches: dict[int, list[int]]` | `MpdrNoteCandidate` 富结构 |

---

## 2. 音频前端管线

### 2.1 DemucsSeparator -- Demucs 真实分轨

**文件**：`src/audio_to_yaml_converter.py`（约第 390-432 行，行号可能因版本迭代偏移）

#### 职责

调用本机 Demucs（通过 `python -m demucs` 子进程）对输入音频执行真实分轨，提取钢琴 stem。

#### 数据流

```
config.audio_path
    │
    ▼
subprocess.run([python, -m, demucs, --name, model, --out, work_dir, audio_path])
    │
    ▼
分离结果目录 /demucs/{model}/{stem}/{piano}.wav
    │
    ▼ 返回 Path
```

#### 关键细节

- 使用 `subprocess.run`，非线程安全的进程调用。`check=False` + 手动校验 return code，这是因为 Demucs 作为外部命令运行。
- **硬校验**：必须在输出目录中真实找到目标 stem wav，否则抛出 `FileNotFoundError`。
- 输出目录结构由 Demucs 自身决定：`{work_dir}/demucs/{model}/{stem}/{piano}.wav`，不可自定义。
- 支持的分轨模型由 `--demucs-model` 参数指定，默认 `htdemucs_6s`。

### 2.2 PianoTranscriber -- 钢琴音频转 MIDI

**文件**：`src/audio_to_yaml_converter.py`（约第 550-680 行）

#### 职责

使用 Transkun 神经网络将钢琴音频转录为 MIDI 文件。支持 segment-based 推理以处理长音频。

#### 数据流

```
Piano stem (.wav)
    │
    ▼
Transkun model (checkpoint .pt)
    │
    ▼
MIDI 文件 -> work_dir/midi/{song_name}_transkun.mid
```

#### 关键细节

- **BPM 自动检测**：音频文件使用 librosa onset 检测真实 BPM，并写入转录 MIDI 的 tempo 轨道
- 转录设备支持 cpu/cuda（`--transcription-device` 参数）
- segment 参数可选（`--transcription-segment-hop-size`、`--transcription-segment-size`），不指定时使用模型默认值
- 若指定 `--input-midi`，则跳过 Demucs 分轨和转录，直接进入压缩阶段

### 2.3 AudioToYamlPipeline -- 流水线调度器

**文件**：`src/audio_to_yaml_converter.py`（`AudioToYamlPipeline` 类）

#### 职责

编排完整的音频到 YAML 转换流程。根据输入类型（音频文件 vs 预生成 MIDI）决定是否执行分轨和转录。

核心调度路径：
1. `--audio` 提供 -> Demucs -> Transkun -> 压缩
2. `--input-midi` 提供 -> 直接压缩

### 2.4 BPM 检测逻辑

- 当 `--audio` 提供且 `--bpm 0`（默认）时，使用 librosa onset 检测真实 BPM
- 检测到的 BPM 写入转录 MIDI 的 tempo 轨道
- 若 MIDI tempo 为 120.0（转录工具默认值），递归查找同名音频并用 `_estimate_audio_bpm()` 检测
- 失败直接抛 `RuntimeError`，**无条件回退**（已在旧版本修复了静默回退 120 的 bug）

### 2.5 MIDI 预处理

**文件**：`src/audio_to_yaml_converter.py`（`_read_midi_notes()` 约第 700-780 行）

#### 职责

读取 MIDI 文件，提取音符事件，进行量化、延音合并和 BPM 标准化。

#### 关键细节

- 使用 `pretty_midi` 库解析 MIDI 文件
- 音符量化到 `quantize_beat` 栅格（默认 0.25 拍 = 十六分音符精度）
- duration 重新计算，最短不小于 `quantize_beat * 0.25`
- 延音合并：`_merge_sustained_midi_notes()` 将重叠的连续音符合并为长音
- 合并间隙阈值由 `merge_sustained_gap_beats` 控制

---

## 3. svsep_mpdr 新管线

### 3.1 SvsepHandSeparator -- piano_svsep GNN 声部分离

**文件**：`src/svsep_hand_separator.py`（全文约 200 行）

#### 职责

使用 piano_svsep（基于 GNN 的谱表预测模型）对 MIDI 钢琴进行左右手声部分离。将 MIDI 文件转为 MusicXML，通过 piano_svsep 预测每个音符的 staff 标签（1=高音谱表/右手，2=低音谱表/左手），再按 staff 拆分为两个独立的 MidiNoteEvent 列表。

#### 数据流

```
MIDI 文件
    │
    ▼
music21: MIDI -> MusicXML (.musicxml)
    │
    ▼
partitura: 加载 MusicXML -> note_array
    │
    ▼
piano_svsep: GNN 推理 -> staff 标签 (1=右手, 2=左手)
    │
    ▼
按 staff 拆分 -> left_midi_notes, right_midi_notes
```

#### 关键技术点

1. **MIDI -> MusicXML 转换**：使用 music21 的 `converter.parse()` 将 MIDI 解析为乐谱对象，再通过 `write("musicxml")` 写入临时文件。music21 会自动推断拍号和调号。

2. **MusicXML -> partitura note_array**：partitura 加载 MusicXML 后提取 `note_array`（结构化 NumPy 数组），包含每个音符的 onset、duration、pitch、voice 等字段。

3. **piano_svsep 推理**：将 note_array 转为 GNN 图结构，通过预训练模型预测每个音符的 staff 标签。

4. **回填匹配**（`_match_svsep_labels_to_original`）：由于 MusicXML 往返过程中 onset 时间会有约 0.2-0.4 拍的偏移，无法直接用时间匹配。采用**双指针搜索策略**--假设音高顺序在原始 MIDI 和 MusicXML 之间一致，按时间顺序遍历两组音符，找到音高一致的匹配对。

5. **硬校验**：
   - `match_ratio >= 0.95` -- 原始 MIDI 回填匹配率
   - 未知 staff 标签（`staff_label == 0`）-> 直接抛异常
   - 回填全量覆盖：`used_orig_indices != len(orig_midi_notes)` 时抛异常。这意味着如果任何音符未能匹配，整个分离失败。

#### 脆弱点

1. **MusicXML 转换一致性**：music21 的 MIDI -> MusicXML 可能在某些 MIDI 文件上失败，尤其是多轨、多拍号或非常规事件。
2. **回填全量覆盖过于严格**：对于有短暂装饰音或边缘音符的 MIDI 文件，任何匹配失败都会导致管线终止。
3. **双指针搜索假设**：假设音高顺序一致，但在某些量化 MIDI 上可能不成立。

### 3.2 MidiToYamlConverter.convert 中的路由逻辑

**文件**：`src/audio_to_yaml_converter.py`（`convert()` 方法）

当 `pitch_compression_mode == "svsep_mpdr"` 时：

1. 加载 SvsepHandSeparator（延迟检查依赖）
2. 调用 `_separate_hands_with_svsep()` 获取左右手分离结果
3. 调用 `_preprocess_svsep_hands()` 预处理（延音合并 + 续打）
4. 调用 `_build_svsep_mpdr_score_events()` 构建候选事件
5. 精排评分后选择最优候选

**关键设计原则**：svsep 分离结果是**权威输入**，不允许旧启发式分割静默介入。分离失败直接报错。

### 3.3 声部分离后的预处理

#### 3.3.1 延音合并

对左右手分别调用 `_merge_sustained_midi_notes()`，与 MIDI 预处理阶段的逻辑相同。由于原始 MIDI 在入口处已合并一次，此处的第二次合并不应产生实质变化，但作为防御性处理保留。

#### 3.3.2 续打击键（Sustain Split）

**文件**：`src/audio_to_yaml_converter.py`（`_split_svsep_sustained_notes()`，约第 1140 行）

对左手低音长音进行"续打"处理：在长音的中点位置插入一个额外的击键事件。这解决了游戏中长低音弦被其他音符遮蔽后听感不足的问题。

**评分影响**：续打不是一个新音符，而是一个"起音提醒"。它**不会**修改原音符，只是追加一个 `MidiNoteEvent` 到左手列表中。

**配置控制**：
- `sustain_split_enabled`：是否开启续打
- `sustain_split_threshold_beats`：最低 duration 阈值
- `sustain_split_max_midpoint_density`：中点密度上限
- `sustain_split_strike_duration_beats`：续打击键的评分 duration

### 3.4 _build_svsep_mpdr_score_events

**文件**：`src/audio_to_yaml_converter.py`（约第 1180-1280 行）

`_generate_svsep_mpdr_candidates` 的顶层调度器。负责：

1. 验证 `mpdr_candidate_count` 不超过候选组合总数
2. 生成参数扫描范围（对若干关键参数做 scale_factor 扫描）
3. 逐个调用 `_build_svsep_mpdr_candidate()` 构建候选
4. 精排评分（`_score_svsep_mpdr_stats()`），选最高分

### 3.5 _generate_svsep_mpdr_candidates -- 参数扫描

**文件**：`src/audio_to_yaml_converter.py`（约第 1220-1300 行）

对 3 个关键参数做 3 级扫描 = 27 组候选。通过 `mpdr_candidate_count` 均匀采样：

```python
variant_configs = [variant_configs[int(i * step)] for i in range(candidate_count)]
```

**注意事项**：采样策略是均匀抽取而非最大化多样性。若参数扫描产生的组合本身有顺序偏置（如 `protection` 变化而其他固定），采样结果可能遗漏重要参数组合。

### 3.6 _build_svsep_mpdr_candidate -- 核心构建

**文件**：约第 1393-1580 行

这是新管线最核心的方法，逐时间片构建左右手候选。

#### 3.6.1 完整流程

```
输入: left_midi_notes, right_midi_notes, config

1. 量化分组: _group_midi_notes_by_index() -> left_groups, right_groups
2. 合并时间片索引集: all_time_indices = sorted(set(left) | set(right))
3. 统计 max_original_chord_notes (原始和弦最大音符数)
4. 提取全局趋势线: _extract_global_trend() -> global_ref
5. 提取主旋律路径: _extract_primary_melody_path() -> melody_by_index

6. 逐时间片处理:
   a. _select_mpdr_right_notes()    -> 右手候选（保守内声削弱）
   b. _select_mpdr_left_notes()     -> 左手候选（预算感知选择）
   c. 累计遮蔽风险 & 统计
   d. _suppress_mpdr_repeated_candidates() -> 抑制延音重复
   e. _fold_mpdr_candidates_unified()     -> 左右手独立八度折叠
   f. _candidates_to_modifier_safe_tokens() -> 转 YAML token
   g. 写入 grouped_notes[time_index] = tokens

7. 计算全局质量分: _score_svsep_mpdr_stats() -> global_score
8. 返回 MpdrBuildResult
```

#### 3.6.2 逐时间片处理的详细逻辑

**全局趋势线** `global_ref`（复用 `_extract_global_trend`）：

使用粗粒度窗口滑动采样音高中位数 -> 非因果双向指数平滑 -> 线性插值回每个时间片。为八度折叠提供浮动的参考中心。

**主旋律路径** `melody_by_index`（`_extract_primary_melody_path`）：

动态规划：在每个右手有音时间片，综合音高突出度、时值、velocity 和相邻连续性，选择最可能的主旋律音符。

#### 3.6.3 右手选择 `_select_mpdr_right_notes`

约第 1806-1890 行

原则：**保守型内声削弱**。

- 主旋律音符始终保留（`is_primary_melody=True` 强制保留）
- 右手和弦 <= 6 个音符时全部保留
- > 6 个时，按 `keep_score` 排序，在预算内选择性保留内声
- 预算公式：`max(base_opacity, len(candidates) * 0.65)`，若当前无左手则加 bonus

#### 3.6.4 左手选择 `_select_mpdr_left_notes`

约第 1891-2000 行

原则：**预算感知最大化效用**。

- **预算计算**：
  ```
  budget = base_opacity - protection_strength * melody_activity + rest_bonus
  budget = clamp(budget, left_opacity_min, left_opacity_max)
  ```
  主旋律活动度高时压缩左手，右手休止时释放更多预算给左手。

- **选择优先级**：
  1. **低音锚点**（bass_anchor）-- 当前时间片左手最低音，带有 `is_bass_anchor` 标记，token 冲突时优先级较高
  2. **五度音** -- 与低音相距 7 个半音的伴奏音，若感知成本不超过预算则保留
  3. 其余音符通过 `_score_mpdr_left_note` 评分，但在当前版本中**尚未被遍历选择**（流程中仅 bass + fifth 被选中，剩余 scored_candidates 未被利用--这是一个明显的**待完成逻辑**）

#### 3.6.5 左手音符效用评分 `_score_mpdr_left_note`

约第 1913-1946 行

```
utility = bass_anchor_weight (若为 bass)
        + harmony_color_weight * 和声色彩评分
        + voice_leading_weight * 声部连接评分
        + velocity_weight * velocity_norm
        + duration_weight * duration_norm
```

- **和声色彩**：计算当前音在 bass 上的音程，大三度/小三度/六度等协和度加权
- **声部连接**：与上一时间片保留左手音的 pitch 连续性（距离越近越高）

#### 3.6.6 遮蔽风险评估 `_compute_mpdr_masking_risk`

约第 2003-2057 行

五个风险来源，乘以当前主旋律活动度：

| 风险类型 | 来源 | 公式 |
|-----------|--------|------|
| 起音碰撞 | 与旋律同时起音 | `onset_collision_penalty` |
| 音区碰撞 | 音高靠近旋律音 | `collision_penalty * (1 - distance / semitones)` |
| 低音浑浊 | 低音区密集 | `low_mud_penalty * (1 + depth)` |
| 密度风险 | 左手自身密度 | `density_penalty * max(0, len - 1)` |
| 时长规避 | 长音覆盖 | `quacking * activity * duration` |

#### 3.6.7 重复抑制 `_suppress_mpdr_repeated_candidates`

约第 1748-1804 行

跨时间片记忆：记录每个手+音高最后保留的音符。左手同音短间隔直接抑制；右手仅在重叠或长音上下文时抑制。主旋律不被抑制。

#### 3.6.8 八度折叠 `_fold_mpdr_candidates_unified`

约第 2228-2280 行

**左右手独立折叠，各自独立参考中心**：
- 左手：参考中心 = `mpdr_left_ref_pitch`，窗口上界 = `mpdr_left_window_high`
- 右手：参考中心 = `max(global_ref[t], mpdr_right_ref_pitch)`，窗口下界 = `mpdr_right_window_low`

折叠算法 `_adaptive_octave_fold_slice_mpdr`（约第 2280-2330 行）：

1. 若和弦完整（所有音能落在窗口内且保持相对音程），选择整体偏移使平均音高最接近参考中心
2. 否则每个音独立选择最近的八度偏移

**v2 改进**：不再让左右手各自漂移，而是使用统一的全曲趋势参考（`global_ref`），只在折叠窗口和参考中心上区分左右手。

#### 3.6.9 Token 生成 `_candidates_to_modifier_safe_tokens`

约第 2366-2406 行

按物理键位去重：提取 token 的基础键身份（如 `+3` 和 `+#3` 都映射到 C 键位的 3），按优先级决定保留哪一个。

优先级：`主旋律 > 低音锚点 > 右手非旋律 > 左手和声 > 其他`

**和弦上限硬约束（2026-07 修复）**：物理键位去重后，按音乐优先级排序并截断至 `config.max_chord_notes` 个。此修复解决了 `mihang_familiar_world` 等密集编曲因原始和弦超过 6 音导致硬约束失败的 bug。所有 20 个候选现在均通过硬约束检查。详见第 7 节。

#### 3.6.10 全局质量分 `_score_svsep_mpdr_stats`

约第 2458-2525 行

五维加权评分，归一化到 0-1：

| 维度 | 权重参数 | 含义 |
|--------|--------------|---------|
| `melody_integrity` | `mpdr_score_melody_weight` | 主旋律保留比例 |
| `masking_avoidance` | `mpdr_score_masking_weight` | 遮蔽规避比例 |
| `harmonic_completeness` | `mpdr_score_harmony_weight` | 0.75 * 左手和声完整度 + 0.25 * 右手保留比例 |
| `bass_continuity` | `mpdr_score_bass_weight` | 低音连续时间片比例 |
| `register_clarity` | `mpdr_score_register_weight` | token 冲突丢弃的反向指标 |

---

### 3.7 关键子算法一览

| 方法 | 行号（近似） | 功能概要 |
|--------|------|-----------------|
| `_extract_global_trend` | ~3157 | 粗粒度窗口采样 + 双向指数平滑 -> 全局趋势线 |
| `_extract_primary_melody_path` | ~1515 | DP 在右手音符中选择主旋律路径 |
| `_select_mpdr_right_notes` | ~1806 | 右手内声保守削弱 |
| `_select_mpdr_left_notes` | ~1891 | 左手预算感知选择（bass + fifth 硬逻辑） |
| `_score_mpdr_left_note` | ~1913 | 左手效用评分（和声 + 声部 + velocity + duration） |
| `_compute_mpdr_masking_risk` | ~2003 | 五维遮蔽风险评估 |
| `_compute_mpdr_perceptual_cost` | ~2059 | 无力度环境感知成本 |
| `_suppress_mpdr_repeated_candidates` | ~1748 | 跨时间片延音重复抑制 |
| `_fold_mpdr_candidates_unified` | ~2228 | 左右手独立八度折叠 |
| `_candidates_to_modifier_safe_tokens` | ~2366 | Token 冲突消解 + 物理键去重 + 和弦上限截断 |
| `_score_svsep_mpdr_stats` | ~2458 | 五维加权全局质量分 |

> **注意**：上表中的行号为当前代码库（2026-07）的近似位置。由于 `audio_to_yaml_converter.py` 全文超过 4200 行且持续迭代，具体行号可能与文档略有偏差，以实际代码中的 `def` 声明为准。

---

## 4. svsep_mpdr 与 attention_weighted 旧管线的差异

### 4.1 架构级差异

| 维度 | svsep_mpdr (新) | attention_weighted (旧) |
|---------|-----------------|--------------------------|
| 声部分离手段 | piano_svsep GNN 预测 staff 标签 | 三重交叉验证分割线（双峰+运动+velocity） |
| 左右手角色 | 权威输入，严格区分 | 启发式分割线，允许模糊 |
| 音符建模 | `MpdrNoteCandidate` 富结构（utility, risk, keep_score 等） | 纯 pitch 列表 |
| 裁剪策略 | 预算感知动态缩编（budget-driven） | 和声优先级静态简化（rule-based） |
| 八度折叠 | 左右手独立参考 + 独立窗口 | 统一折叠 |
| 候选评分 | 五维加权分（旋律/遮蔽/和声/低音/音区清晰度） | 四维加权分（在 reranker 模块中实现） |
| 统计审计 | 丰富的全链路统计（匹配率、预算使用、遮蔽事件等） | 较少审计字段 |

### 4.2 八度折叠差异

**svsep_mpdr**（`_fold_mpdr_candidates_unified`）：
- 右手折叠到 `C3-B5`（下界由 `mpdr_right_window_low` 控制）
- 左手折叠到 `C3-mpdr_left_window_high`（上界可由配置上界压缩）
- 参考中心各自独立：右手接近旋律区，左手靠低音区
- 和弦整体迁移优先于单音独立迁移

**旧管线**（`_adaptive_octave_fold_slice`）：
- 左右手统一折叠到 `C3-B5`
- 不区分左右手参考中心
- 全局趋势线作为唯一参考

### 4.3 左手简化策略差异

**svsep_mpdr**：
- 对每个左手音符独立评分（效用 - 遮蔽风险）
- 选择逻辑：低音锚点强制保留 -> 五度音按预算 -> **剩余音符当前未遍历选择**
- 预算随主旋律活动度动态变化

**旧管线**（`_simplify_by_harmonic_priority`）：
- 基于和声功能优先级排序
- 纯音高 + duration 驱动的静态选择
- 无遮蔽风险感知

### 4.4 对延音的处理差异

**svsep_mpdr**：额外有 `_split_svsep_sustained_notes` 续打击键机制，以及 `_suppress_mpdr_repeated_candidates` 重复抑制机制。

**旧管线**：只有基础的 `_merge_sustained_midi_notes` 延音合并。

---

## 5. 配置参数详解与影响

### 5.1 svsep 相关参数

| 参数 | 类型 | 默认值 | 影响 |
|--------|------|---------|--------|
| `svsep_model_path` | `Path | None` | None | piano_svsep .ckpt 权重路径。为 None 时 svsep_mpdr 报错 |
| `svsep_device` | str | "cpu" | 推理设备，影响 GNN 推理速度 |

### 5.2 续打击键参数

| 参数 | 类型 | 说明 |
|--------|------|---------|
| `sustain_split_enabled` | bool | 是否开启续打 |
| `sustain_split_threshold_beats` | float | 最低 duration 阈值，低于此值的低音不续打 |
| `sustain_split_max_midpoint_density` | int | 中点位置密度上限，密度高时不续打 |
| `sustain_split_strike_duration_beats` | float | 续打击键的评分 duration |

### 5.3 MPDR 核心参数

| 参数 | 类型 | 影响范围 |
|--------|------|--------------|
| `mpdr_melody_protection_strength` | float | 主旋律音符的效用加成、左手预算压缩强度 |
| `mpdr_left_opacity_base` | float | 左手基础感知密度预算，影响左手保留音符数 |
| `mpdr_left_opacity_min` | float | 左手预算下界，防止预算过低导致左手完全消失 |
| `mpdr_left_opacity_max` | float | 左手预算上界，防止预算过高导致左手过于密集 |
| `mpdr_melody_rest_bonus` | float | 右手休止时给左手释放的额外预算 |
| `mpdr_melody_onset_guard_beats` | float | 主旋律起音保护窗口（拍） |
| `mpdr_register_collision_penalty` | float | 音区碰撞惩罚权重 |
| `mpdr_register_collision_semitones` | float | 音区碰撞判定半音窗口 |
| `mpdr_onset_collision_penalty` | float | 起音碰撞惩罚权重 |
| `mpdr_low_mud_pitch` | int | 低音浑浊判定阈值（MIDI 音高） |
| `mpdr_low_mud_penalty` | float | 低音浑浊惩罚权重 |
| `mpdr_density_penalty` | float | 密度惩罚权重 |
| `mpdr_duration_ducking_strength` | float | 长音规避（ducking）强度 |
| `mpdr_bass_anchor_weight` | float | 低音锚点效用权重 |
| `mpdr_harmony_color_weight` | float | 和声色彩效用权重 |
| `mpdr_voice_leading_weight` | float | 声部连接效用权重 |
| `mpdr_velocity_weight` | float | velocity 效用权重 |
| `mpdr_duplicate_penalty` | float | 同八度重复音惩罚 |
| `mpdr_repeat_suppression_beats` | float | 重复抑制搜索窗口 |
| `mpdr_repeat_overlap_tolerance_beats` | float | 重复重叠容差 |
| `mpdr_candidate_count` | int | 参数扫描候选数量上限 |
| `mpdr_scan_delta_ratio` | float | 参数扫描相对扰动比例 |
| `mpdr_right_ref_pitch` | float | 右手独立折叠参考中心 |
| `mpdr_left_ref_pitch` | float | 左手独立折叠参考中心 |
| `mpdr_left_window_high` | int | 左手折叠窗口最高音 |
| `mpdr_right_window_low` | int | 右手折叠窗口最低音 |

### 5.4 MPDR 精排权重参数

| 参数 | 影响 |
|--------|--------|
| `mpdr_score_melody_weight` | 主旋律完整度的权重 |
| `mpdr_score_masking_weight` | 遮蔽规避的权重 |
| `mpdr_score_harmony_weight` | 和声完整度的权重 |
| `mpdr_score_bass_weight` | 低音连续性的权重 |
| `mpdr_score_register_weight` | 音区清晰度的权重 |

**所有精排权重之和必须大于 0**，否则运行时抛异常。

### 5.5 全局参数（对 svsep_mpdr 也生效）

| 参数 | 影响 |
|--------|--------|
| `quantize_beat` | 量化栅格精度，直接影响所有时间片索引和预算计算粒度 |
| `max_chord_notes` | 单拍最大音符数上限 |
| `max_score_events` | score 事件数量上限 |
| `ref_smoothing` | global_trend 指数平滑系数 |
| `global_trend_alpha` | 趋势线平滑系数 |
| `global_trend_window_beats` | 趋势采样窗口 |
| `beat_unit` | 节拍单位，影响感知成本计算 |
| `merge_sustained_notes` | 是否对左右手分别延音合并 |
| `merge_sustained_gap_beats` | 延音合并间隙阈值 |

---

## 6. 潜在脆弱点分析

### 6.1 架构级脆弱点

#### 6.1.1 svsep_mpdr 对 piano_svsep 的强依赖

整个新管线以 GNN 分离结果为绝对权威输入（`_separate_hands_with_svsep` 注释明确写明"不允许旧启发式分割静默介入"）。如果：

- 模型文件损坏或版本不匹配 -> 全管线崩溃
- 推理结果在复杂编曲（和弦密集、复调）中准确率下降 -> 左手右手分配错误，后续 MPDR 缩编在错误声部上执行
- 未知 staff 标签（`staff_label == 0`）-> 直接抛异常阻断

**缓解**：审计验证 `SvsepSeparationAudit.validate()` 提供了质量门控，但代价是脆性失败。

#### 6.1.2 左手选择逻辑不完整

`_select_mpdr_left_notes` 只选择了低音锚点和五度音。其余音符虽然已通过 `_score_mpdr_left_note` 评分，但在当前代码中**没有被遍历消费**（约第 1907-1909 行只有 fallback 兜底）。这意味着：

- 当左手和弦较丰富时（如三和弦 + 转位），仅 bass 和 fifth 被保留
- 七和弦、九和弦的色彩音（三度和七度）全部丢失
- 左手预算参数 `left_opacity_base` 的效果大打折扣

这可能是一个**待实现的功能缺口**，或是设计意图（极简左手风格），需要确认。

#### 6.1.3 MIDI -> MusicXML -> partitura 往返误差

`SvsepHandSeparator.separate()` 使用了 music21 -> MusicXML -> partitura 的间接路径：
- music21 的 MIDI 解析器可能在某些边缘情况（多轨、系统专属消息、元事件）失败
- MusicXML 格式对部分 MIDI 事件（如滑音、弯音）不透明
- partitura 的 `note_array` 与原始 MIDI 的 onset 存在偏移（~0.2-0.4 拍），双指针搜索的策略虽然有效，但假设了音高顺序一致，在某些经过量化的 MIDI 上可能失效

#### 6.1.4 参数扫描组合爆炸

`_generate_svsep_mpdr_candidates` 对 3 个参数做 3 级扫描 = 27 组候选。若未来扩展更多参数，组合会呈指数增长。当前通过 `mpdr_candidate_count` 均匀采样控制，但采样策略是**均匀抽取不是最大化多样性**，可能遗漏重要参数组合。

### 6.2 代码级脆弱点

#### 6.2.1 mpdr_candidate_count 采样逻辑

```python
variant_configs = [variant_configs[int(i * step)] for i in range(candidate_count)]
```

这是对有序列表的均匀采样，但没有 shuffle，若参数扫描产生的组合本身有顺序偏置（如 `protection` 变化而其他固定），采样结果会偏向某几个参数组合，丢失多样性。

#### 6.2.2 _score_svsep_mpdr_stats 中的循环依赖

`harmonic_completeness` = 0.75 * `left_harmonic_completeness` + 0.25 * `right_texture_preservation`。

其中 `left_harmonic_completeness` 基于 `kept_left_utility / total_left_utility`，而 utility 评分中的 `mpdr_harmony_color_weight`、`mpdr_bass_anchor_weight` 等权重本身又是精排的输入参数。这会形成参数间的非线性耦合，调试效率较低。

#### 6.2.3 统计字段的混合赋值

同一个 `conversion_stats` 字典同时在 `_build_svsep_mpdr_candidate` 和 `_build_svsep_mpdr_score_events` 中修改。由于候选生成会调用多次 `_build_svsep_mpdr_candidate`（每次参数扫描一次），统计值会在循环中被覆写：

```python
# 约第 1420 行：在 candidate 内部更新
self.conversion_stats["max_original_chord_notes"] = max(..., max_original_chord)
```

`max_original_chord_notes` 取 max 是安全的（幂等），但 `max_output_chord_notes` 每次候选也会更新。由于候选精排选用的不是最后一个，这些统计值是最后构建的那个候选的，而不是最优候选的。**分析时需要注意这个偏差**。

#### 6.2.4 续打击键密度条件 bug

`_split_svsep_sustained_notes` 中的密度判定使用的是 `density_map`（左右手合计密度），但截止条件是中点密度 > `sustain_split_max_midpoint_density`，这意味着如果右手在该位置有多个音符，左手低音续打可能被意外阻断。

### 6.3 与旧管线的兼容性

#### 6.3.1 统计数据字段冲突

`clipped_notes` 在 svsep_mpdr 中的计算方式与旧管线不同：
- 旧管线：`total_orig - output_tokens`（在 `_build_attention_weighted_grouped_notes` 中）
- svsep_mpdr：`max(0, total_left - kept_left + total_right - kept_right + token_collision_drops)`

这意味着如果同一份报告中混用两种统计解读，`clipped_notes` 的含义不一致。

#### 6.3.2 out_of_range_policy 处理差异

svsep_mpdr 使用 `_fold_mpdr_candidates_unified` 自带八度折叠，即使在 `out_of_range_policy == "error"` 时也能正常输出折叠后音高。旧管线则依赖 `_normalize_pitch_range` 或 `_adaptive_octave_fold_slice`。两者的折叠策略不同（和弦整体迁移 vs 单音独立迁移），同一个 MIDI 输入在两个管线上可能输出不同的音区映射。

---

## 7. 已知修复记录

### 7.1 `_candidates_to_modifier_safe_tokens` 和弦上限硬约束修复（2026-07）

**问题**：`mihang_familiar_world` 因原始和弦超过 `max_chord_notes=6`，在物理键位去重后仍超过上限，导致硬约束失败。该曲目是唯一不及格的 SVSEP-MPDR 候选。

**根因**：旧的 `_candidates_to_modifier_safe_tokens` 仅做物理键位去重，去重后的 token 列表可能超过 `config.max_chord_notes`，直接传递给下游导致超出游戏允许的最大同拍音符数。

**修复**（`audio_to_yaml_converter.py` 约第 2395-2406 行）：

在物理键位去重之后，新增优先级排序截断逻辑：

```python
# 物理键位去重后仍可能超过游戏允许的统一和弦上限。先按音乐优先级
# 保留主旋律、低音锚点和右手骨架，再按音高排序以稳定输出 token。
priority_items = sorted(
    keyed_items.values(),
    key=lambda item: self._mpdr_candidate_priority(item[1]),
    reverse=True,
)[: config.max_chord_notes]
selected_items = sorted(priority_items, key=lambda item: item[1].mapped_pitch)
```

优先级排序规则（`_mpdr_candidate_priority`）：主旋律 > 低音锚点 > 右手非旋律 > 左手和声 > 其他。截断后按 `mapped_pitch` 排序以稳定输出 token 顺序。

**效果**：所有 20 个候选现在均通过硬约束检查，`mihang_familiar_world` 成功输出 YAML 曲谱（得分 0.4392，较此前无法完成有本质改善）。

---

> **说明**：本文档覆盖了 `F:\NTEZMusic\src\audio_to_yaml_converter.py`（全文约 4225 行）和 `F:\NTEZMusic\src\svsep_hand_separator.py`（全文约 200 行）的核心组件。行号标注为 2026-07 代码库的近似位置，由于代码持续迭代，具体行号可能略有偏差，以实际代码中的 `def` 声明为准。部分辅助方法（如 `_build_score_events`、`_compute_mpdr_harmony_color`、`_compute_mpdr_voice_leading` 等）未在本说明中展开，如需进一步分析可补充。
