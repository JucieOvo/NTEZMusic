# 音频转换管线与 svsep_mpdr 声部分离新管线 代码说明书

> **面向对象**：项目开发者（功能原理与代码结构说明，非用户手册）
> **适用版本**：基于 2026-07 代码库
> **作者**：JucieOvo

---

## 目录

1. [整体数据流概览](#1-整体数据流概览)
2. [音频前端管线](#2-音频前端管线)
   - 2.1 DemucsSeparator — Demucs 真实分轨
   - 2.2 PianoTranscriber — 钢琴音频转 MIDI
   - 2.3 AudioToYamlPipeline — 流水线调度器
   - 2.4 BPM 检测逻辑
   - 2.5 MIDI 预处理
3. [svsep_mpdr 新管线](#3-svsep_mpdr-新管线)
   - 3.1 SvsepHandSeparator — piano_svsep GNN 声部分离
   - 3.2 MidiToYamlConverter.convert 中的路由逻辑
   - 3.3 声部分离后的预处理
   - 3.4 _build_svsep_mpdr_score_events
   - 3.5 _generate_svsep_mpdr_candidates — 参数扫描
   - 3.6 _build_svsep_mpdr_candidate — 核心构建
   - 3.7 关键子算法一览
4. [svsep_mpdr 与 attention_weighted 旧管线的差异](#4-svsep_mpdr-与-attention_weighted-旧管线的差异)
5. [配置参数详解与影响](#5-配置参数详解与影响)
6. [潜在脆弱点分析](#6-潜在脆弱点分析)

---

## 1. 整体数据流概览

### 1.1 端到端流水线

```
音频文件 (.mp3/.wav/...)
    │ (可选)
    ▼
DemucsSeparator.separate()       ── 调用本机 demucs 命令分轨
    │
    ▼  (piano stem)
PianoTranscriber.transcribe()    ── Transkun 神经网络转录
    │
    ▼  (MIDI 文件)
MidiToYamlConverter.convert()    ── MIDI 到 YAML 曲谱转换
    │                                此处根据 pitch_compression_mode 分流
    ▼
YAML 曲谱 + 书面报告
```

### 1.2 MidiToYamlConverter.convert 入口分流

convert() 是管线中**核心决策点**。确认 MIDI 文件存在后，执行：

1. `_read_midi_notes()` — 读取并量化 MIDI 音符，包含延音合并
2. **分流判断**：
   - 若 `pitch_compression_mode == "svsep_mpdr"` → 走新管线
   - 否则 → 走旧管线 `_build_score_events()`
3. `_validate_playback_timing()` — 校验总播放时长不超过限制

### 1.3 新旧管线的高层对比

| 维度 | 旧管线 (hands_decoupled / attention_weighted) | 新管线 (svsep_mpdr) |
|---------|-----------------------------------------------|----------------------|
| 左右手判定 | 基于统计分割线（三重交叉验证） | piano_svsep GNN 预测 staff 标签 |
| 左手处理 | 和声功能优先级简化 | MPDR 预算感知动态缩编 |
| 右手处理 | 完整保留 + 密度约束 | 保守型内声削弱 |
| 折叠方式 | 统一八度折叠 | 左右手独立折叠，各自参考中心 |
| 候选生成 | 参数扫描 → 评分 → 选最优 | 参数扫描 → 评分 → 选最优 |
| 核心数据结构 | `grouped_pitches: dict[int, list[int]]` | `MpdrNoteCandidate` 富结构 |

---

## 2. 音频前端管线

### 2.1 DemucsSeparator — Demucs 真实分轨

**文件**：`src/audio_to_yaml_converter.py` 第 390-432 行

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
- 输出目录结构由 Demucs 自身决定：`{work_dir}/demucs/{model}/{audio_stem}/{stem}.wav`。

#### 脆弱点

- 依赖本机已安装 demucs 且可用，无版本检查。
- 子进程无超时控制，长音频可能被挂起。
- stem 路径拼接假设了 Demucs 的固定输出结构，版本升级可能破坏。

---

### 2.2 PianoTranscriber — 钢琴音频转 MIDI

**文件**：`src/audio_to_yaml_converter.py` 第 435-645 行

#### 职责

将 Demucs 分离出的钢琴 stem 通过 Transkun（Neural Semi-CRF Transformer V2）神经网络转录为 MIDI 文件。

#### 数据流

```
piano stem wav
    │
    ├─ _load_audio_samples()  → (raw_audio, sr)
    ├─ _load_transkun_model() → (model, device)
    │
    ▼
soxr.resample()  ── 适配到 model.fs
    │
    ▼
model.transcribe(audio_tensor, step, segmentSize)
    │
    ▼
writeMidi(notes_est) → .mid 文件
    │
    └─ _fix_midi_tempo()  ── 用 librosa 检测 BPM 并写入 MIDI tempo
```

#### 关键细节

- **权重加载**：支持用户指定 `.pt` 权重文件 + 同目录 `.conf`；否则使用 transkun 内置默认。
- **采样率适配**：使用 `soxr.resample()`（soxr 是 transkun 的传递依赖），非 librosa 重采样。
- **设备控制**：`transcription_device` 配置为 `"cuda"` 但 CUDA 不可用时，静默回退 CPU 并打印提示。
- **BPM 修正**：`_fix_midi_tempo()` 使用 `_estimate_audio_bpm()` 检测 BPM（librosa），然后通过 `mido` 修改 MIDI 文件的 `set_tempo` 元消息。这是为了后续 `--input-midi` 复用路径不需要重新检测 BPM。

#### _load_transkun_model 细节

1. 搜索权重路径：优先 config 指定，否则内置 pretrained
2. 使用 `moduleconf.parseFromFile` 加载配置 → 实例化 `TransKun` 类
3. 检查 checkpoint 中 key 名（`best_state_dict` / `state_dict`）
4. `strict=False` 加载，允许新旧版本权重兼容

#### 脆弱点

- `soxr` 作为传递依赖，单独 try-import，安装不完整时直接报错。
- `transkun.Data.writeMidi` 在 Windows 路径下可能因 `\` 转义出问题。
- 默认假设所有曲目 4/4 拍，无拍号检测。

---

### 2.3 AudioToYamlPipeline — 流水线调度器

**文件**：`src/audio_to_yaml_converter.py` 第 3676-3795 行

#### 职责

串联三个核心组件（DemucsSeparator → PianoTranscriber → MidiToYamlConverter），执行配置校验、BPM 自动检测、YAML 写盘、YAML 回读校验以及书面报告生成。

#### run() 流程

```
1. _validate_config()
2. BPM 自动检测（若 config.bpm <= 0）
3. 分轨 + 转录（跳过若 input_midi_path 已提供）
4. convert()
5. yaml.safe_dump() 写入
6. PianoConfigLoader().load() 回读校验
7. _write_report()
```

#### BPM 自动检测逻辑

见下文 2.4 节。

---

### 2.4 BPM 检测逻辑

涉及两个函数：

#### 2.4.1 `_estimate_audio_bpm(audio_path)`

**文件**：第 95-128 行

算法：
1. `librosa.load(sr=22050, mono=True)` 加载音频
2. `librosa.onset.onset_strength()` 计算 onset 强度包络
3. `librosa.beat.beat_track()` 动态规划 beat tracker
4. 返回 `round(tempo, 1)`

**健壮性校验**：
- tempo 必须为有限正数
- beats 列表不能为空
- 失败直接抛 `RuntimeError`，**无条件回退**（已在旧版本修复了静默回退 120 的 bug）

#### 2.4.2 `_fix_midi_tempo(midi_path, audio_path)`

**文件**：第 593-628 行

作用：Transkun 转录输出的 MIDI tempo 固定为 120，本函数用音频检测的真实 BPM 覆盖 MIDI 文件中的 `set_tempo` 元消息。使用 `mido` 库操作。

流程：
1. `_estimate_audio_bpm()` 检测 BPM
2. 遍历所有 track 查找 `set_tempo` 消息，替换 tempo 值
3. 若没有找到，在 track[0] 开头插入

#### 2.4.3 BPM 自动检测入口（AudioToYamlPipeline）

**文件**：第 3704-3712 行 和 第 3728-3763 行

规则：
- `config.bpm <= 0` 时触发自动检测
- 先尝试从 MIDI 的 `get_tempo_changes()` 读取
- 若 MIDI tempo 为 120.0（转录工具默认值），递归查找同名音频并用 `_estimate_audio_bpm()` 检测
- 音频文件则直接 `_estimate_audio_bpm()`

**同名音频查找** `_find_matching_audio()`（第 48-92 行）：

搜索 MIDI 文件目录、父目录、项目根目录（含 `src/` 的目录），查找与 MIDI 文件名前缀匹配的音频文件。前缀匹配是双向的，匹配长度越长的优先级越高。

---

### 2.5 MIDI 预处理

#### 2.5.1 `_merge_sustained_midi_notes` — 合并延音拆片

**文件**：第 764-837 行

**问题来源**：
- 延音踏板产生的同音重叠
- 转录模型将同一个长音拆成多个短音片段

**算法**（贪心流式合并）：

```
midi_notes 已按 (pitch, start_beat) 排序
遍历每个音符：
    若 pitch != current_pitch → 结束当前合并，开始新组
    若 start_beat <= current_end + gap_beats → 扩展合并区间
        保留 max(velocity), max(end_beat)
    否则 → 结束当前合并，开始新组
```

**关键参数**：`merge_sustained_gap_beats` — 合并间隙阈值（拍）。大于此间隙的同音被认为是独立音符而非延音片段。

**在 svsep_mpdr 管线中的使用**：
- 在 `convert()` 中，svsep 分离出左右手后**分别对左右手**执行延音合并
- 合并前先按 `(pitch, start_beat)` 排序，确保同音相邻

#### 2.5.2 `_cluster_midi_note_onsets` — 起音聚类

**文件**：第 839-923 行

**问题来源**：
- 演奏中同时按下的和弦，转录后各音 onset 有毫秒级偏差
- 导致同一个和弦被分配到不同的量化格中

**算法**（velocity 加权平均聚类）：

```
1. 按 start_beat 排序
2. 贪心分组：
   - 当前音符与组首差值 <= max_span_beats
   - 且当前音符与组末差值 <= window_beats
   → 归入同组
3. 每组 > 1 个音符时：
   - cluster_start = velocity 加权平均 start_beat
   - 各音符 end_beat 按同样 delta 平移
   - duration 重新计算，最短不小于 quantize_beat * 0.25
```

**注意**：此方法目前在旧管线中广泛使用；在 svsep_mpdr 新管线中是否启用取决于 `onset_cluster_enabled` 配置。当启用时，起音聚类在 MIDI 读取阶段全局执行，**不按左右手独立聚类**。

---

## 3. svsep_mpdr 新管线

### 3.1 SvsepHandSeparator — piano_svsep GNN 声部分离

**文件**：`src/svsep_hand_separator.py` 第 37-479 行

#### 3.1.1 职责

接收原始 MIDI 文件，通过中间 MusicXML 格式路由到 piano_svsep GNN 模型，预测每个音符的 staff 标签（1=高音谱表/右手，2=低音谱表/左手），然后按标签拆分为两个独立的 `MidiNoteEvent` 列表，并附带完整的审计统计。

#### 3.1.2 完整数据流

```
MIDI 文件
    │
    ▼
music21.converter.parse() → MusicXML 文件 (.musicxml)
    │
    ▼
partitura.load_score() → Score 对象
    │
    ├─ remove_ties_acros_barlines()     移除跨小节连音线
    ├─ 移除 Beam（符杠）、Rest（休止符）、Tuplet（连音）
    ├─ 移除 GraceNote（装饰音）
    │
    ▼
score[0].note_array() → 结构化 note_array
    │
    ├─ hetero_graph_from_note_array()    构建异构图节点+边
    ├─ get_vocsep_features()             提取声部特征
    ├─ get_measurewise_pot_edges()       小节范围潜在边
    ├─ get_pot_chord_edges()             和弦潜在边
    │
    ▼
HeteroScoreGraph → score_graph_to_pyg() → pyg Batch
    │
    ▼
model.predict_step() → (pred_voices, pred_staff, _)
    │
    ▼
pred_staff: 每个音符的 staff 标签（0/1/2）
    │
    ▼
使用双指针策略与原始 MIDI 音符回填 velocity/duration
    │
    ├─ staff=1 → 右手 (right_hand_notes)
    └─ staff=2 → 左手 (left_hand_notes)
    │
    ▼
SvsepSeparationResult(left_notes, right_notes, audit)
```

#### 3.1.3 GNN 推理细节

模型为 `piano_svsep.models.pl_models.PLPianoSVSep`，通过 `load_from_checkpoint()` 延迟加载（首次 `separate()` 调用时加载）。

输入异构图的组成：
- **节点**：每个音符一个节点，特征来自 `get_vocsep_features()`（音高、时值、位置等声部特征）
- **边**：同一起音时间的 edge、measurewise 潜在边、和弦潜在边
- **标签**：无（推理模式）

输出：
- `pred_voices`: voice 预测（每个音符属于哪个声部）
- `pred_staff`: staff 预测（0=无分配，1=高音谱表，2=低音谱表）
- 约定：staff=1 → 右手，staff=2 → 左手

#### 3.1.4 双指针回填策略

**问题**：MusicXML 往返过程中 onset 时间会有 ~0.2-0.4 拍的偏移，无法直接用时间匹配。但音高顺序在两者间是一致的。

**算法**（`_match_note_by_pitch_and_order`，第 268-296 行）：

```
对每个 partitura 音符 (按顺序)：
    从上次匹配位置开始在原始 MIDI 列表中搜索同音高且未使用的音符
    窗口大小 50，若窗口内未找到则全局搜索
    → 贪心匹配，O(n * window) 复杂度
```

**为什么必须回填**：
- partitura 的 note_array 丢失 velocity 信息
- MusicXML 转换后 duration 可能因连音符号变化
- 回填保证了最终 MidiNoteEvent 的 velocity 和 timing 来自**原始 MIDI**

#### 3.1.5 审计验证 (SvsepSeparationAudit)

`separate()` 末尾构造审计对象并调用 `validate()`：

- `match_ratio >= 0.95` — 原始 MIDI 回填匹配率
- `left_note_count + right_note_count > 0` — 非空
- `unknown_staff_count == 0` — 无未分配音符

**任何一项不满足则抛 `ValueError`，阻断下游流程。**

#### 3.1.6 脆弱点

1. **MusicXML 转换一致性**：music21 的 MIDI → MusicXML 可能在某些 MIDI 文件上失败，尤其是多轨、多拍号或非常规事件。
2. **GNN 输入预处理**：移除 Beam/Rest/Tuplet/GraceNote 的操作是针对 piano_svsep 模型的预处理要求，这些操作直接修改了 partitura Score 对象，可能在某些乐谱上出错。
3. **回填全量覆盖**：`used_orig_indices != len(orig_midi_notes)` 时抛异常。这意味着如果任何音符未能匹配，整个分离失败。这对于有短暂装饰音或边缘音符的 MIDI 文件过于严格。
4. **模型文件依赖**：`model_path` 指向 `.ckpt` 文件，必须与代码使用的 piano_svsep 版本匹配。`strict=False` 可容忍部分兼容，但仍是脆弱点。

---

### 3.2 MidiToYamlConverter.convert 中的路由逻辑

**文件**：第 665-725 行

```python
if config.pitch_compression_mode == SVSEP_MPDR_MODE:    # "svsep_mpdr"
    left_notes, right_notes = self._separate_hands_with_svsep(midi_path, config)
    
    # 左右手分别延音合并
    if config.merge_sustained_notes:
        left_notes = self._merge_sustained_midi_notes(...)
        right_notes = self._merge_sustained_midi_notes(...)
    
    # 构建 YAML score 事件
    score_events = self._build_svsep_mpdr_score_events(
        left_midi_notes=...,
        right_midi_notes=...,
        config=config,
    )
else:
    # 旧管线: 直接 _build_score_events(midi_notes, config)
    score_events = self._build_score_events(midi_notes, config)
```

**注意**：起音聚类 `_cluster_midi_note_onsets` 在 `_read_midi_notes()` 中执行，在两个分支中都生效。

---

### 3.3 声部分离后的预处理

#### 3.3.1 `_separate_hands_with_svsep`

**文件**：第 1033-1074 行

简单封装：实例化 `SvsepHandSeparator`，调用 `separate()`，收集统计信息。配置缺失或分离失败直接抛异常。

#### 3.3.2 `_split_svsep_sustained_notes` — 左手长低音续打击键

**文件**：第 1076-1161 行

**问题背景**：游戏环境没有延音踏板，长低音在播放层被截断为 1ms 短击后，低音和声会立即消失，在最需要和声支撑的片段丢失持续性。

**算法**（条件筛选）：

对左手音符逐个检查：
1. 必须是当前时间片左手**最低音**（bass）
2. `duration_beats >= sustain_split_threshold_beats`
3. 中点位置的同拍音符数 `<= sustain_split_max_midpoint_density`（避免叠在密集区域）

若满足条件，在原 note 的中间位置插入一个续打击键，继承 pitch 和 velocity，duration 为固定短值。

**评分影响**：续打不是一个新音符，而是一个"起音提醒"。它**不会**修改原音符，只是追加一个 `MidiNoteEvent` 到左手列表中。

---

### 3.4 _build_svsep_mpdr_score_events — 主调度

**文件**：第 1163-1242 行

**流程**：

```
1. 调用 _split_svsep_sustained_notes()    左手长低音续打
2. 调用 _generate_svsep_mpdr_candidates()  参数扫描生成多个候选
3. max(candidates, key=lambda c: c.global_score)  选出最优
4. 将最优候选的 grouped_notes 转为 YAML score 事件
```

#### YAML 事件生成逻辑（第 1221-1241 行）：

```
按时间片顺序遍历 grouped_notes：
    若前一个时间片与当前有间隔 → 插入休止符事件 ("0", beat)
    当前时间片 tokens → 单音直接输出，多音用 [token1 token2 ...] 输出
    统计 max_output_chord_notes、output_note_count
    若 score_events 超过 max_score_events → 抛出异常
```

**统计上报**：
- `remapped_notes` / `octave_moved_notes`：被八度折叠迁移的音符
- `collisions_avoided`：因键位冲突被丢弃的音符
- `clipped_notes`：总裁剪量（左手丢弃 + 右手丢弃 + token 冲突）

---

### 3.5 _generate_svsep_mpdr_candidates — 参数扫描

**文件**：第 1244-1301 行

#### 扫描策略

对三个核心参数做 3 级网格扫描（每个参数 3 个值：原始值 × (1 ± delta_ratio)）：

1. `mpdr_melody_protection_strength` — 主旋律保护强度
2. `mpdr_left_opacity_base` — 左手基础感知密度预算
3. `mpdr_register_collision_penalty` — 音区碰撞惩罚

若扫描出的组合总数超过 `mpdr_candidate_count`，则**均匀采样**到上限以内（第 1288-1290 行）：

```python
if len(variant_configs) > config.mpdr_candidate_count:
    step = len(variant_configs) / config.mpdr_candidate_count
    variant_configs = [variant_configs[int(i * step)] for i in range(candidate_count)]
```

每个变体配置调用 `_build_svsep_mpdr_candidate()`，构建出 `MpdrBuildResult` 加入候选池。

---

### 3.6 _build_svsep_mpdr_candidate — 核心构建

**文件**：第 1303-1493 行

这是新管线最核心的方法，逐时间片构建左右手候选。

#### 3.6.1 完整流程

```
输入: left_midi_notes, right_midi_notes, config

1. 量化分组: _group_midi_notes_by_index() → left_groups, right_groups
2. 合并时间片索引集: all_time_indices = sorted(set(left) | set(right))
3. 统计 max_original_chord_notes (原始和弦最大音符数)
4. 提取全局趋势线: _extract_global_trend() → global_ref
5. 提取主旋律路径: _extract_primary_melody_path() → melody_by_index

6. 逐时间片处理:
   a. _select_mpdr_right_notes()    → 右手候选（保守内声削弱）
   b. _select_mpdr_left_notes()     → 左手候选（预算感知选择）
   c. 累计遮蔽风险 & 统计
   d. _suppress_mpdr_repeated_candidates() → 抑制延音重复
   e. _fold_mpdr_candidates_unified()     → 左右手独立八度折叠
   f. _candidates_to_modifier_safe_tokens() → 转 YAML token
   g. 写入 grouped_notes[time_index] = tokens

7. 计算全局质量分: _score_svsep_mpdr_stats() → global_score
8. 返回 MpdrBuildResult
```

#### 3.6.2 逐时间片处理的详细逻辑

**全局趋势线** `global_ref`（第 1515-1541 行复用 `_extract_global_trend`）：

使用粗粒度窗口滑动采样音高中位数 → 非因果双向指数平滑 → 线性插值回每个时间片。为八度折叠提供浮动的参考中心。

**主旋律路径** `melody_by_index`（从 1515 行开始的 `_extract_primary_melody_path`）：

动态规划：在每个右手有音时间片，综合音高突出度、时值、velocity 和相邻连续性，选择最可能的主旋律音符。

#### 3.6.3 右手选择 `_select_mpdr_right_notes`

第 1716-1799 行

原则：**保守型内声削弱**。

- 主旋律音符始终保留（`is_primary_melody=True` 强制保留）
- 右手和弦 <= 6 个音符时全部保留
- > 6 个时，按 `keep_score` 排序，在预算内选择性保留内声
- 预算公式：`max(base_opacity, len(candidates) * 0.65)`，若当前无左手则加 bonus

#### 3.6.4 左手选择 `_select_mpdr_left_notes`

第 1801-1911 行

原则：**预算感知最大化效用**。

- **预算计算**（第 1836-1837 行）：
  ```
  budget = base_opacity - protection_strength * melody_activity + rest_bonus
  budget = clamp(budget, left_opacity_min, left_opacity_max)
  ```
  主旋律活动度高时压缩左手，右手休止时释放更多预算给左手。

- **选择优先级**：
  1. **低音锚点**（bass_anchor）— 当前时间片左手最低音，带有 `is_bass_anchor` 标记，token 冲突时优先级较高
  2. **五度音**（第 1897-1906 行）— 与低音相距 7 个半音的伴奏音，若感知成本不超过预算则保留
  3. 其余音符通过 `_score_mpdr_left_note` 评分，但在当前版本中**尚未被遍历选择**（流程中仅 bass + fifth 被选中，剩余 scored_candidates 未被利用——这是一个明显的**待完成逻辑**）

#### 3.6.5 左手音符效用评分 `_score_mpdr_left_note`

第 1913-1946 行

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

第 2003-2057 行

五个风险来源，乘以当前主旋律活动度：

| 风险类型 | 来源 | 公式 |
|-----------|--------|------|
| 起音碰撞 | 与旋律同时起音 | `onset_collision_penalty` |
| 音区碰撞 | 音高靠近旋律音 | `collision_penalty × (1 - distance / semitones)` |
| 低音浑浊 | 低音区密集 | `low_mud_penalty × (1 + depth)` |
| 密度风险 | 左手自身密度 | `density_penalty × max(0, len - 1)` |
| 时长规避 | 长音覆盖 | `quacking × activity × duration` |

#### 3.6.7 重复抑制 `_suppress_mpdr_repeated_candidates`

第 1658-1714 行

跨时间片记忆：记录每个手+音高最后保留的音符。左手同音短间隔直接抑制；右手仅在重叠或长音上下文时抑制。主旋律不被抑制。

#### 3.6.8 八度折叠 `_fold_mpdr_candidates_unified`

第 2138-2188 行

**左右手独立折叠，各自独立参考中心**：
- 左手：参考中心 = `mpdr_left_ref_pitch`，窗口上界 = `mpdr_left_window_high`
- 右手：参考中心 = `max(global_ref[t], mpdr_right_ref_pitch)`，窗口下界 = `mpdr_right_window_low`

折叠算法 `_adaptive_octave_fold_slice_mpdr`（第 2190-2237 行）：

1. 若和弦完整（所有音能落在窗口内且保持相对音程），选择整体偏移使平均音高最接近参考中心
2. 否则每个音独立选择最近的八度偏移

**v2 改进**：不再让左右手各自漂移，而是使用统一的全曲趋势参考（`global_ref`），只在折叠窗口和参考中心上区分左右手。

#### 3.6.9 Token 生成 `_candidates_to_modifier_safe_tokens`

第 2276-2309 行

按物理键位去重：提取 token 的基础键身份（如 `+3` 和 `+#3` 都映射到 C 键位的 3），按优先级决定保留哪一个。

优先级：`主旋律 > 低音锚点 > 右手非旋律 > 左手和声 > 其他`

#### 3.6.10 全局质量分 `_score_svsep_mpdr_stats`

第 2361-2426 行

五维加权评分，归一化到 0-1：

| 维度 | 权重参数 | 含义 |
|--------|--------------|---------|
| `melody_integrity` | `mpdr_score_melody_weight` | 主旋律保留比例 |
| `masking_avoidance` | `mpdr_score_masking_weight` | 遮蔽规避比例 |
| `harmonic_completeness` | `mpdr_score_harmony_weight` | 0.75 × 左手和声完整度 + 0.25 × 右手保留比例 |
| `bass_continuity` | `mpdr_score_bass_weight` | 低音连续时间片比例 |
| `register_clarity` | `mpdr_score_register_weight` | token 冲突丢弃的反向指标 |

---

### 3.7 关键子算法一览

| 方法 | 行号 | 功能概要 |
|--------|------|-----------------|
| `_extract_global_trend` | 3157 | 粗粒度窗口采样 + 双向指数平滑 → 全局趋势线 |
| `_extract_primary_melody_path` | 1515 | DP 在右手音符中选择主旋律路径 |
| `_select_mpdr_right_notes` | 1716 | 右手内声保守削弱 |
| `_select_mpdr_left_notes` | 1801 | 左手预算感知选择（bass + fifth 硬逻辑） |
| `_score_mpdr_left_note` | 1913 | 左手效用评分（和声 + 声部 + velocity + duration） |
| `_compute_mpdr_masking_risk` | 2003 | 五维遮蔽风险评估 |
| `_compute_mpdr_perceptual_cost` | 2059 | 无力度环境感知成本 |
| `_suppress_mpdr_repeated_candidates` | 1658 | 跨时间片延音重复抑制 |
| `_fold_mpdr_candidates_unified` | 2138 | 左右手独立八度折叠 |
| `_candidates_to_modifier_safe_tokens` | 2276 | Token 冲突消解 + 物理键去重 |
| `_score_svsep_mpdr_stats` | 2361 | 五维加权全局质量分 |

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
- 选择逻辑：低音锚点强制保留 → 五度音按预算 → **剩余音符当前未遍历选择**
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
| `svsep_model_path` | `Path \| None` | None | piano_svsep .ckpt 权重路径。为 None 时 svsep_mpdr 报错 |
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

整个新管线以 GNN 分离结果为绝对权威输入（`_separate_hands_with_svsep` 第 1042 行注释明确写明"不允许旧启发式分割静默介入"）。如果：

- 模型文件损坏或版本不匹配 → 全管线崩溃
- 推理结果在复杂编曲（和弦密集、复调）中准确率下降 → 左手右手分配错误，后续 MPDR 缩编在错误声部上执行
- 未知 staff 标签（`staff_label == 0`）→ 直接抛异常阻断

**缓解**：审计验证 `SvsepSeparationAudit.validate()` 提供了质量门控，但代价是脆性失败。

#### 6.1.2 左手选择逻辑不完整

`_select_mpdr_left_notes` 只选择了低音锚点和五度音。其余音符虽然已通过 `_score_mpdr_left_note` 评分，但在当前代码中**没有被遍历消费**（第 1907-1909 行只有 fallback 兜底）。这意味着：

- 当左手和弦较丰富时（如三和弦 + 转位），仅 bass 和 fifth 被保留
- 七和弦、九和弦的色彩音（三度和七度）全部丢失
- 左手预算参数 `left_opacity_base` 的效果大打折扣

这可能是一个**待实现的功能缺口**，或是设计意图（极简左手风格），需要确认。

#### 6.1.3 MIDI → MusicXML → partitura 往返误差

`SvsepHandSeparator.separate()` 使用了 music21 → MusicXML → partitura 的间接路径：
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

`harmonic_completeness` = 0.75 × `left_harmonic_completeness` + 0.25 × `right_texture_preservation`。

其中 `left_harmonic_completeness` 基于 `kept_left_utility / total_left_utility`，而 utility 评分中的 `mpdr_harmony_color_weight`、`mpdr_bass_anchor_weight` 等权重本身又是精排的输入参数。这会形成参数间的非线性耦合，调试效率较低。

#### 6.2.3 统计字段的混合赋值

同一个 `conversion_stats` 字典同时在 `_build_svsep_mpdr_candidate` 和 `_build_svsep_mpdr_score_events` 中修改。由于候选生成会调用多次 `_build_svsep_mpdr_candidate`（每次参数扫描一次），统计值会在循环中被覆写：

```python
# 第 1330 行：在 candidate 内部更新
self.conversion_stats["max_original_chord_notes"] = max(..., max_original_chord)
```

`max_original_chord_notes` 取 max 是安全的（幂等），但 `max_output_chord_notes` 在第 1230-1231 行每次候选也会更新。由于候选精排选用的不是最后一个，这些统计值是最后构建的那个候选的，而不是最优候选的。**分析时需要注意这个偏差**。

#### 6.2.4 续打击键密度条件 bug

`_split_svsep_sustained_notes` 中第 1140 行的密度判定使用的是 `density_map`（左右手合计密度），但截止条件是中点密度 > `sustain_split_max_midpoint_density`，这意味着如果右手在该位置有多个音符，左手低音续打可能被意外阻断。

### 6.3 与旧管线的兼容性

#### 6.3.1 统计数据字段冲突

`clipped_notes` 在 svsep_mpdr 中的计算方式与旧管线不同：
- 旧管线：`total_orig - output_tokens`（在 `_build_attention_weighted_grouped_notes` 中）
- svsep_mpdr：`max(0, total_left - kept_left + total_right - kept_right + token_collision_drops)`

这意味着如果同一份报告中混用两种统计解读，`clipped_notes` 的含义不一致。

#### 6.3.2 out_of_range_policy 处理差异

svsep_mpdr 使用 `_fold_mpdr_candidates_unified` 自带八度折叠，即使在 `out_of_range_policy == "error"` 时也能正常输出折叠后音高。旧管线则依赖 `_normalize_pitch_range` 或 `_adaptive_octave_fold_slice`。两者的折叠策略不同（和弦整体迁移 vs 单音独立迁移），同一个 MIDI 输入在两个管线上可能输出不同的音区映射。

---

> **说明**：本文档覆盖了 `F:\NTEZMusic\src\audio_to_yaml_converter.py` 和 `F:\NTEZMusic\src\svsep_hand_separator.py` 的核心组件。由于 `audio_to_yaml_converter.py` 全文 4128 行，部分辅助方法（如 `_build_score_events`、`_compute_mpdr_harmony_color`、`_compute_mpdr_voice_leading` 等）未在本说明中展开，如需进一步分析可补充。
