# SVSEP-MPDR v3 音频算法优化设计

> 作者：JucieOvo  
> 日期：2026-07-01  
> 状态：待审核  
> 阶段：设计  

---

## 一、设计背景

当前项目已经形成从真实音频到游戏 36 键 YAML 曲谱的离线转换管线，核心链路位于 `src/audio_to_yaml_converter.py:1`，并已经包含 Demucs 分轨、Transkun 转录、MIDI 到 YAML 转换、`svsep_mpdr` 模式、MPDR 候选生成、主旋律路径提取和 36 键折叠等雏形。

本次优化的目标不是重新发明一套规则算法，而是在现有 `svsep_mpdr` 基础上，将音频转谱、左右手分离、主旋律跟踪和折叠压缩编统一为一条高上限主链路：

```text
真实音频
  → Demucs 钢琴 stem
  → Transkun 高保真 MIDI
  → onset/BPM/velocity/duration 校正
  → piano_svsep GNN 左右手声部分离
  → 右手主旋律 DP 路径跟踪
  → MPDR 感知密度候选生成与精排
  → C3-B5 统一折叠与 token 冲突消解
  → YAML 曲谱
```

关键原则：

1. **AI 声部分离作为主依据**：以 `piano_svsep` 的 staff 预测作为左右手分离权威来源，不把旧版双峰检测作为静默替代。
2. **主旋律优先级最高**：右手主旋律 DP 路径在后续压缩、折叠和冲突消解中获得最高保护。
3. **左手保留结构而非堆密度**：优先保留低音锚点、三音、七音、导音等和声信息，抑制重复八度、低频浑浊和与主旋律重叠的遮蔽。
4. **真实失败直接暴露**：依赖缺失、模型加载失败、转谱失败、分离结果异常均应直接报错，不写入伪成功 YAML。
5. **纯音频主链路**：虽然项目已有通过演奏者视频跟踪左右手并辅助主旋律判断的音视频聚合逻辑，但本轮不把视频作为必要输入；左右手与主旋律判断必须直接通过音频转谱后的 MIDI、piano_svsep 和 MPDR 完成，以减少视频采集、手部跟踪、镜像校准和遮挡处理带来的阻碍。
6. **所有质量判断可量化**：每次转换输出真实统计，包括主旋律保留率、低音锚点保留率、token 冲突丢弃数、modifier 冲突次数和 MPDR 全局分数。

---

## 二、现有基础与可复用资产

### 2.1 `audio_to_yaml_converter` 主转换器

`src/audio_to_yaml_converter.py:1` 已经定义从真实音频到 YAML 的离线转换流水线。当前可复用资产包括：

- `SVSEP_MPDR_MODE` 常量：`src/audio_to_yaml_converter.py:146`
- 音频转换配置项：`src/audio_to_yaml_converter.py:149`
- `MidiToYamlConverter.convert` 主入口：`src/audio_to_yaml_converter.py:643`
- `svsep_mpdr` 分支入口：`src/audio_to_yaml_converter.py:672`
- `piano_svsep` 分离入口：`src/audio_to_yaml_converter.py:886`
- `svsep_mpdr` score 事件构建：`src/audio_to_yaml_converter.py:1007`
- MPDR 参数候选生成：`src/audio_to_yaml_converter.py:1088`
- 主旋律路径提取：`src/audio_to_yaml_converter.py:1333`
- 左右手候选选择与评分：`src/audio_to_yaml_converter.py:1526`、`src/audio_to_yaml_converter.py:1611`
- MPDR 折叠与 token 输出：`src/audio_to_yaml_converter.py:1935`、`src/audio_to_yaml_converter.py:2047`
- MPDR 全局质量评分：`src/audio_to_yaml_converter.py:2130`

### 2.2 `svsep_hand_separator` AI 声部分离器

`src/svsep_hand_separator.py:1` 已经实现 MIDI → MusicXML → partitura → piano_svsep → staff 标签拆分流程。当前设计将保留这条链路，并增强其审计输出。

关键入口：

- 模块职责说明：`src/svsep_hand_separator.py:1`
- 模型加载：`src/svsep_hand_separator.py:67`
- 分离方法：`src/svsep_hand_separator.py:97`
- 模型推理：`src/svsep_hand_separator.py:218`

### 2.3 既有算法文档

- `docs/hands_decoupled_v2_final.md:1` 记录了基于 velocity/duration 的左右手信号增强型规则算法。
- `docs/proposal_role_decoupled_dlaf.md:1` 记录了角色解耦注意力折叠方案。
- `docs/implementation_chronicle.md:1` 记录了 36 键游戏键盘约束与算法演进历史。

本次设计会吸收这些文档中的有效思想，但不把旧算法作为主链路替代品。

---

## 三、候选路线与选择

### 3.1 路线 A：强化现有 `svsep_mpdr` 主链路

该路线以 `piano_svsep` GNN 左右手分离作为核心依据，在现有 MPDR 候选生成和精排框架上增强转谱校正、主旋律 DP、感知密度预算和统计审计。

优点：

- 最大化复用当前代码资产。
- 利用 AI 声部分离能力处理交叉手、跨音区和复杂钢琴织体。
- 与项目目标“最强方法”一致。

缺点：

- 依赖 PyTorch、piano_svsep、partitura、music21 等真实运行环境。
- 测试需要真实模型权重和真实曲目，不能用 Mock 简化。

### 3.2 路线 B：多模态音视频融合增强

该路线引入 `src/dual_stream_extractor.py:1` 和 `src/fusion_engine.py:1`，使用视觉指尖轨迹辅助左右手与主旋律归属判断。

优点：

- 在有演奏视频时，上限可能高于纯音频。
- 可用真实物理指法信息校正 AI 声部分离。

缺点：

- 范围从音频算法扩大为音视频系统。
- 对视频质量、镜像、遮挡、视角和 MediaPipe 模型依赖较强。
- 不适合作为本次音频算法优化的主链路。

### 3.3 路线 C：完全重写规则型 RD-DLAF

该路线以 `docs/proposal_role_decoupled_dlaf.md:1` 为基础，使用角色解耦规则算法替代 `piano_svsep`。

优点：

- 较少依赖深度学习环境。
- 可解释性强。

缺点：

- 在复杂钢琴曲中，规则声部分配上限低于 GNN。
- 与“最强方法”的目标不一致。

### 3.4 最终选择

选择路线 A：**SVSEP-MPDR v3：AI 声部分离驱动的主旋律保护型感知密度缩编算法**。

路线 B 作为未来可选增强层，不进入本轮主线；路线 C 的角色解耦思想吸收进评分和审计逻辑，不作为替代管线。需要特别强调：这不是忽略已有音视频聚合逻辑，而是本轮有意降低输入门槛，避免把谱面生成绑定到演奏者视频、手部检测、镜像校准和音视频同步质量上。

---

## 四、目标与非目标

### 4.1 目标

1. 构建一条以 `svsep_mpdr` 为核心的高保真音频转谱主链路。
2. 增强 MIDI onset 聚类与量化校正，减少同一和弦被拆散的问题。
3. 增强 `piano_svsep` 左右手分离输出的质量审计。
4. 增强右手主旋律 DP 追踪，综合 pitch、velocity、duration、强拍和连续性。
5. 增强 MPDR 候选生成，使主旋律、低音锚点、和声色彩和遮蔽规避形成统一评分。
6. 增强 36 键折叠与 token 冲突消解，保证主旋律优先。
7. 输出可用于 A/B 对比的真实转换报告。

### 4.2 非目标

1. 不实现视频融合主链路。视觉融合后续可独立设计。
2. 不实现运行时降级方案。模型或依赖不可用时直接失败。
3. 不把演奏者视频、MediaPipe 手部跟踪或音视频聚合作为本轮算法输入；视频融合仅作为后续可选增强，不进入本轮主链路。
4. 不为了兼容旧算法牺牲 `svsep_mpdr` 的主链路清晰性。
5. 不引入 Mock、Stub、伪统计或伪转换结果。
5. 不改变游戏播放器的 SendInput 执行机制，除非后续验证发现 token 输出格式必须调整。

---

## 五、总体架构

```text
AudioToYamlPipeline
  ├─ DemucsSeparator
  ├─ PianoTranscriber
  └─ MidiToYamlConverter
       ├─ MidiEventNormalizer
       ├─ SvsepHandSeparator
       ├─ MelodyPathTracker
       ├─ MpdrCandidateBuilder
       ├─ MpdrCandidateReranker
       └─ GameKeyboardTokenEmitter
```

### 5.1 `MidiEventNormalizer`

职责：读取 Transkun 输出的真实 MIDI note，保留 pitch、start、end、velocity，并在量化前执行 onset 聚类与持续时间归一。

设计要点：

- 以真实 note.start 秒级时间为输入。
- 使用毫秒级 onset 聚类窗口聚合同一物理发音组。
- 聚类后的起音时间统一为组内中心时间。
- 输出仍为真实 MIDI 音符事件，不创建假音符。

### 5.2 `SvsepHandSeparator`

职责：调用 piano_svsep GNN，为每个音符预测 staff，并拆分为 left/right note list。

设计要点：

- 继续使用 MIDI → MusicXML → partitura → GNN 推理路径。
- 输出 left/right notes 的同时，新增分离审计统计。
- 如果分离结果为空或模型不可用，直接抛出错误。

### 5.3 `MelodyPathTracker`

职责：在右手音符集合中提取主旋律路径。

设计要点：

- 使用动态规划，而不是逐拍贪心。
- 评分维度包括音高相对位置、velocity、duration、强拍位置和声部连续性。
- 通过大跳惩罚和重复噪声惩罚降低装饰音误判。

### 5.4 `MpdrCandidateBuilder`

职责：为每个量化时间片生成右手和左手候选，并计算 utility、masking_risk、perceptual_cost、keep_score。

设计要点：

- 右手主旋律候选强保留。
- 右手非旋律候选根据遮蔽风险和密度预算筛选。
- 左手候选优先保留 bass anchor、三音、七音、导音。
- 左手候选压制重复八度、低频浑浊、同音密集重复和与主旋律同起音遮蔽。

### 5.5 `MpdrCandidateReranker`

职责：对少量 MPDR 参数候选进行全局精排。

设计要点：

- 候选数量保持可控，避免参数爆炸。
- 全局分数由主旋律完整度、遮蔽规避、和声完整度、低音连续性和音区清晰度构成。
- 每个候选必须输出真实统计。

### 5.6 `GameKeyboardTokenEmitter`

职责：将最优候选折叠到 C3-B5，并输出游戏 YAML token。

设计要点：

- 保持 36 键 C3-B5 约束。
- token 冲突时按照主旋律、低音锚点、右手非旋律、左手和声填充的顺序保留。
- 统计 token 丢弃与 modifier 冲突。

---

## 六、核心流程

### 6.1 音频到 MIDI

1. 用户提供真实音频。
2. `DemucsSeparator` 提取钢琴 stem。
3. `PianoTranscriber` 使用 Transkun 生成 MIDI。
4. 如果 Transkun 执行失败，终止流程。

### 6.2 MIDI 事件归一与量化

1. 读取所有有效 MIDI note。
2. 按真实起音时间执行 onset clustering。
3. 保留原始 velocity 和 duration。
4. 转为量化时间片索引。
5. 输出 `MidiNoteEvent` 序列和按时间片分组的数据。

### 6.3 AI 左右手分离

1. 将 MIDI 转为 MusicXML。
2. 使用 partitura 加载乐谱并构建 note_array。
3. 调用 piano_svsep 推理 staff。
4. 根据 staff 拆分 left/right。
5. 生成分离质量统计。

### 6.4 主旋律 DP 路径

1. 按时间片聚合右手音符。
2. 为每个右手候选计算基础旋律分。
3. 在时间轴上执行 DP，累积连续性分数。
4. 回溯得到 `melody_by_index`。
5. 后续 MPDR 阶段强保护主旋律路径。

### 6.5 MPDR 候选生成与精排

1. 对主旋律保护强度、左手密度预算、音区遮蔽惩罚等少量参数生成候选。
2. 每个候选独立构建 score events。
3. 计算候选全局质量分。
4. 选择最高分候选。
5. 输出候选统计与最终 YAML。

### 6.6 36 键折叠与输出

1. 依据统一物理窗口 C3-B5 折叠候选音符。
2. 对 token 冲突执行优先级消解。
3. 插入真实休止符事件。
4. 校验 `max_score_events` 与播放时序。
5. 写入 YAML 和 conversion report。

---

## 七、关键算法设计

### 7.1 Onset 聚类校正

目标：解决 Transkun 或真实演奏造成的毫秒级起音误差，使同一物理和弦进入同一个量化时间片。

规则：

- 以起音时间排序。
- 相邻音符起音差在感知同步阈值内时合并为同一 onset group。
- group 起音写为组内时间中心。
- 不改变 pitch、velocity、end，只校正用于量化的起音。

约束：

- 不创建不存在的音符。
- 不吞并跨度过大的分解和弦。
- 聚类阈值必须配置化，不能写死在逻辑中。

### 7.2 主旋律 DP 评分

基础评分：

```text
base_score =
  pitch_position_weight * pitch_position
+ velocity_weight * velocity_norm
+ duration_weight * duration_norm
+ beat_strength_weight * beat_strength
```

转移评分：

```text
transition_score =
  voice_leading_weight * continuity
- large_jump_penalty
- repeated_noise_penalty
```

回溯结果：

```text
melody_by_index: {time_index → MidiNoteEvent}
```

主旋律路径不要求每个时间片都有音符；休止片保持空。

### 7.3 左手 MPDR 评分

左手候选 utility 由以下因素构成：

- bass anchor 权重。
- 三音、七音、导音、张力音和声色彩权重。
- 与上一左手已选音的声部连接权重。
- velocity 与 duration 加权。
- 重复八度惩罚。

masking risk 由以下因素构成：

- 与主旋律同起音。
- 与主旋律音区距离过近。
- 低频浑浊。
- 左手局部密度过高。
- 长左手音覆盖主旋律活动窗口。

### 7.4 感知密度预算

左手预算根据主旋律活跃度动态调整：

```text
left_budget =
  base_opacity
- melody_protection_strength * melody_activity
+ melody_rest_bonus
```

预算受最小值和最大值约束。

设计含义：

- 主旋律活跃时，左手退让。
- 主旋律休止时，左手补足和声厚度。
- 预算仅控制真实候选选择，不制造填充音。

### 7.5 全局候选精排

全局分数：

```text
global_score =
  w_melody * melody_integrity
+ w_masking * masking_avoidance
+ w_harmony * harmonic_completeness
+ w_bass * bass_continuity
+ w_register * register_clarity
```

每个维度必须可从真实统计计算得到。

### 7.6 Token 冲突消解

优先级：

```text
primary_melody
> bass_anchor
> right_non_melody
> left_harmony_color
> duplicate_or_filler
```

如果多个候选映射到相同物理键位，保留优先级和 keep_score 更高者，并记录丢弃统计。

---

## 八、配置策略

本轮设计以现有 `AudioPipelineConfig` 为主体，不引入分散配置文件。

需要新增或重整的配置类别：

1. **onset 聚类配置**
   - onset 聚类窗口。
   - 最大合并跨度。

2. **主旋律 DP 配置**
   - pitch、velocity、duration、beat、continuity 权重。
   - 大跳惩罚。
   - 重复噪声惩罚。

3. **MPDR 预算配置**
   - 左手基础预算、最小预算、最大预算。
   - 主旋律休止 bonus。
   - 遮蔽惩罚。
   - 低频浑浊阈值。

4. **精排配置**
   - 候选数量。
   - 候选参数扫描比例。
   - 全局分数各维度权重。

配置必须遵守：

- 所有权重可追踪。
- 不把 magic number 写在算法内部。
- 参数无效时直接报错。

---

## 九、验证与质量指标

### 9.1 必须输出的转换统计

每次转换至少输出：

- 原始 MIDI 音符数。
- 左手音符数。
- 右手音符数。
- 主旋律候选数。
- 主旋律保留率。
- 低音锚点保留率。
- 左手保留 utility。
- 遮蔽风险累计值。
- token 冲突丢弃数。
- modifier 冲突次数。
- 低频浑浊惩罚次数。
- 最优候选 `global_score`。

### 9.2 A/B 对比曲目

使用项目现有真实曲目进行对比：

- `config/cruel_angel_thesis.yaml`
- `config/animenz_one_last_kiss.yaml`
- `config/beautiful_world.yaml`
- `config/croatian_rhapsody.yaml`

验证不能使用 Mock 曲目替代。

### 9.3 验收标准

1. `svsep_mpdr` 模式可以完成真实曲目的端到端转换。
2. 转换报告包含上述真实统计。
3. 主旋律保留率高于旧版基准。
4. 低音锚点保留率高于旧版基准。
5. token 冲突丢弃数低于或等于旧版基准。
6. 输出 YAML 能被现有播放器加载。
7. 失败场景真实报错，不生成伪成功结果。

---

## 十、风险与边界

### 10.1 piano_svsep 环境风险

风险：PyTorch、torch_geometric、partitura、music21、piano_svsep 任一依赖不完整都会导致分离失败。

处理：直接报错，并在文档和使用说明中列出真实依赖；不提供静默规则替代。

### 10.2 MusicXML 转换对齐风险

风险：MIDI → MusicXML → note_array 后，note id 与原始 MIDI note 的回填可能出现顺序或时值偏差。

处理：增强回填审计，统计无法匹配的 note 数；异常比例过高时阻断。

### 10.3 主旋律误判风险

风险：右手中声部密集时，DP 可能把装饰音或高位和声音误判为旋律。

处理：引入 duration、velocity、强拍和连续性综合评分，降低单纯 pitch 高位的影响。

### 10.4 36 键物理压缩不可逆风险

风险：88 键到 36 键的压缩必然损失信息。

处理：通过可量化优先级保留主旋律、低音锚点和关键和声色彩，所有丢弃进入统计报告。

### 10.5 无延音踏板硬约束

风险：游戏不支持延音踏板，长音听感无法完全还原。

处理：本轮不解决延音踏板能力缺失，只在 MPDR 里避免长左手音遮蔽主旋律。

---

## 十一、预计变更清单

### 11.1 修改 `src/audio_to_yaml_converter.py`

目标位置：`src/audio_to_yaml_converter.py:149`

内容：扩展 `AudioPipelineConfig`，加入 onset 聚类、主旋律 DP、MPDR 审计相关配置。

原因：集中管理算法参数，避免硬编码。

影响：CLI 参数解析、转换报告、MPDR 构建逻辑。

### 11.2 修改 `src/audio_to_yaml_converter.py`

目标位置：`src/audio_to_yaml_converter.py:703`

内容：在读取 MIDI note 后、量化前加入 MIDI 事件归一与 onset 聚类校正。

原因：解决同一和弦因毫秒误差拆散的问题。

影响：所有 pitch compression mode 的输入事件一致性；需要确保旧模式行为可解释。

### 11.3 修改 `src/svsep_hand_separator.py`

目标位置：`src/svsep_hand_separator.py:97`

内容：增强 `separate` 的返回审计信息，或新增独立审计结构。

原因：需要判断 GNN 输出质量，而不仅拿到 left/right list。

影响：`_separate_hands_with_svsep` 调用方需要接收统计并写入 conversion report。

### 11.4 修改 `src/audio_to_yaml_converter.py`

目标位置：`src/audio_to_yaml_converter.py:1333`

内容：增强主旋律 DP 评分，加入强拍、重复噪声、大跳惩罚和可配置权重。

原因：提高右手主旋律跟踪稳定性。

影响：MPDR 主旋律保护与候选精排。

### 11.5 修改 `src/audio_to_yaml_converter.py`

目标位置：`src/audio_to_yaml_converter.py:1611`

内容：重整左手 MPDR 评分，强化 bass anchor、和声色彩、遮蔽风险和感知成本。

原因：使压缩结果更符合主旋律突出与低音结构保留目标。

影响：左手候选数量、低音保留率、和声完整度。

### 11.6 修改 `src/audio_to_yaml_converter.py`

目标位置：`src/audio_to_yaml_converter.py:2130`

内容：扩展全局候选评分统计，输出更多可审计指标。

原因：为 A/B 对比和后续调参提供真实证据。

影响：conversion report 格式。

---

## 十二、后续规划入口

用户审核通过本文档后，下一步进入实现计划阶段，产出详细任务拆分与 `docs/PLAN.md`。

计划阶段应重点拆分为：

1. 配置与报告结构整理。
2. onset 聚类与 MIDI 事件归一。
3. piano_svsep 审计输出增强。
4. 主旋律 DP 增强。
5. MPDR 候选评分增强。
6. 折叠与 token 冲突统计增强。
7. 真实曲目 A/B 验证。
