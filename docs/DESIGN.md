---
stage: design
title: SVSEP-MPDR v3 音频算法优化设计
author: JucieOvo
date: 2026-07-01
status: draft
---

# SVSEP-MPDR v3 音频算法优化设计

## 关联 Superpowers 工作文档 (id: section-sp-ref)

- **Superpowers 设计文档**: @SP:spec/2026-07-01-audio-algorithm-optimization-design
- **brainstorming 会话日期**: 2026-07-01
- **提炼者**: JucieOvo

---

## 背景与动机 (id: section-1.1)

当前项目已经具备从真实音频到游戏 36 键 YAML 曲谱的离线转换能力，主转换管线位于 `src/audio_to_yaml_converter.py:1`。现有系统包含 Demucs 分轨、Transkun 转录、MIDI 读取、`svsep_mpdr` 模式、piano_svsep 左右手分离、主旋律路径提取、MPDR 候选生成和 36 键 token 输出等基础能力。

本次设计的动机是将这些雏形收敛为一条高上限主链路，重点解决四类问题：

1. 音频转谱后的毫秒级 onset 偏差可能把同一和弦拆散到不同量化格。
2. 纯规则左右手分离在复杂交叉手、跨音区织体和密集钢琴改编中上限不足。
3. 主旋律跟踪不能只依赖最高音，需要综合 velocity、duration、强拍和声部连续性。
4. 88 键到 C3-B5 36 键的压缩不可逆，必须用可审计的优先级保留主旋律、低音锚点和关键和声色彩。

不进行本次设计的后果是：`svsep_mpdr` 仍会停留在“可用雏形”状态，音频转谱、AI 声部分离、主旋律保护和 MPDR 压缩之间缺少统一设计目标与可验证指标。

---

## 目标与非目标 (id: section-1.2)

### 目标

- 目标 1：将 `svsep_mpdr` 定义为本轮音频算法优化的主链路。
- 目标 2：增强 MIDI onset 聚类与量化校正，降低同一物理和弦被拆散的概率。
- 目标 3：以 piano_svsep GNN 输出作为左右手分离主依据，并补充分离质量审计。
- 目标 4：增强右手主旋律 DP 追踪，使其综合音高、力度、时值、强拍和连续性。
- 目标 5：增强 MPDR 候选生成、感知密度预算、全局精排和 token 冲突统计。
- 目标 6：每次真实转换输出可用于 A/B 对比的 conversion report。
- 目标 7：明确本轮主链路不依赖演奏者视频，左右手与主旋律判断均通过音频/MIDI/piano_svsep/MPDR 完成，以减少视频输入、手部跟踪和视觉校准带来的阻碍。

### 非目标

- 不实现视觉融合主链路；`src/fusion_engine.py:1` 和 `src/dual_stream_extractor.py:1` 作为后续可选增强，不纳入本轮主线。
- 不把演奏者视频、MediaPipe 手部跟踪、视觉左右手轨迹作为本轮算法输入；本轮必须直接通过音频转谱后的 MIDI 信号、piano_svsep 和 MPDR 完成左右手与主旋律判断。
- 不实现静默降级；模型、依赖、音频转谱或声部分离失败时必须直接报错。
- 不使用 Mock、Stub、伪成功 YAML 或虚构转换统计。
- 不改变播放器的 SendInput 执行机制；本轮聚焦音频转谱与谱面生成算法。
- 不重写所有旧算法；旧模式作为基准保留，主链路围绕 `svsep_mpdr` 增强。

---

## 范围边界 (id: section-1.3)

### 包含

- `src/audio_to_yaml_converter.py:149` 中音频转换配置与 MPDR 参数。
- `src/audio_to_yaml_converter.py:643` 中 MIDI 到 YAML 主转换入口。
- `src/audio_to_yaml_converter.py:886` 中 piano_svsep 调用入口。
- `src/audio_to_yaml_converter.py:1007` 中 `svsep_mpdr` score 事件构建流程。
- `src/audio_to_yaml_converter.py:1333` 中主旋律路径追踪。
- `src/audio_to_yaml_converter.py:1526` 与 `src/audio_to_yaml_converter.py:1611` 中左右手候选选择。
- `src/audio_to_yaml_converter.py:1935` 与 `src/audio_to_yaml_converter.py:2047` 中折叠、token 输出和冲突消解。
- `src/svsep_hand_separator.py:97` 中 AI 左右手分离输出。

### 不包含

- 不包含视频轨迹提取、MediaPipe 手部追踪、音视频时空融合。
- 不包含读取演奏者视频来辅助主旋律判定或左右手分配；若后续需要音视频融合，应另行设计，不得混入本轮纯音频主链路。
- 不包含播放器按键后端改造。
- 不包含引入新的远程服务或外部 API。
- 不包含训练新的神经网络模型，只使用已有 piano_svsep 推理能力。

### 与现有系统的关系

本设计不废弃现有转换管线，而是将 `svsep_mpdr` 明确为最高质量主模式。`hands_decoupled`、`adaptive_octave_fold` 和 attention weighted 相关旧模式仍可作为 A/B 对比基准。

---

## 关键术语 (id: section-1.4)

| 术语 | 定义 |
|------|------|
| SVSEP-MPDR v3 | 本轮设计的主算法名称，表示基于 piano_svsep 声部分离和 MPDR 感知密度缩编的第三版主链路。 |
| piano_svsep | 用于钢琴谱表/声部预测的 GNN 模型，本轮作为左右手分离主依据。 |
| MPDR | 感知密度缩编策略，用于在 36 键限制内平衡主旋律、低音、和声和遮蔽风险。 |
| onset clustering | 在量化前将毫秒级接近的起音合并为同一物理发声组的校正步骤。 |
| primary melody | 右手音符集合中通过 DP 路径追踪得到的主旋律音符。 |
| bass anchor | 左手当前时间片中的低音结构锚点，通常承载和声根基。 |
| masking risk | 候选音符遮蔽主旋律或造成低频浑浊的风险评分。 |
| register clarity | 输出音区清晰度，用于衡量 token 冲突、音区重叠和物理键位拥挤程度。 |

---

## 功能需求 (id: section-2.1)

### FR-001: 支持 SVSEP-MPDR v3 主链路 (id: FR-001)

- **描述**: 系统必须以 `svsep_mpdr` 为核心主链路完成真实音频或已有 MIDI 到 YAML 曲谱的转换。
- **用户场景**: 用户希望将复杂钢琴音频转换为适配游戏 36 键钢琴的 YAML 曲谱。
- **优先级**: 必须

### FR-002: MIDI onset 聚类与量化校正 (id: FR-002)

- **描述**: 系统必须在 MIDI note 量化前执行可配置的 onset 聚类，减少同一和弦被毫秒级起音误差拆散的问题。
- **用户场景**: Transkun 输出同一和弦中不同音符存在微小起音差时，用户仍希望输出为同一 YAML 时间片。
- **优先级**: 必须

### FR-003: AI 左右手分离与审计 (id: FR-003)

- **描述**: 系统必须调用 piano_svsep 进行左右手分离，并输出真实分离统计。
- **用户场景**: 用户处理交叉手或复杂钢琴织体时，需要比纯音高规则更可靠的左右手分离。
- **优先级**: 必须

### FR-004: 主旋律 DP 路径追踪 (id: FR-004)

- **描述**: 系统必须在右手音符集合中通过 DP 追踪主旋律路径，综合 pitch、velocity、duration、强拍和连续性。
- **用户场景**: 用户希望压缩后仍保留曲目最可辨识的主旋律线。
- **优先级**: 必须

### FR-005: MPDR 感知密度候选生成与精排 (id: FR-005)

- **描述**: 系统必须生成多个 MPDR 参数候选，并基于真实统计选择全局分数最高的候选。
- **用户场景**: 用户希望算法自动平衡主旋律突出、左手厚度、低音连续和和声色彩。
- **优先级**: 必须

### FR-006: 36 键折叠与 token 冲突消解 (id: FR-006)

- **描述**: 系统必须将最终候选折叠到 C3-B5，并按主旋律优先级消解 token 冲突。
- **用户场景**: 用户需要输出能被现有游戏钢琴播放器加载的 YAML 曲谱。
- **优先级**: 必须

### FR-007: 输出真实转换报告 (id: FR-007)

- **描述**: 系统必须输出 conversion report，包含主旋律保留率、低音锚点保留率、token 丢弃数、modifier 冲突和 MPDR 全局分数等指标。
- **用户场景**: 用户需要基于真实数据审核算法质量并做 A/B 对比。
- **优先级**: 必须

---

## 非功能需求 (id: section-2.2)

### NFR-001: 真实执行与失败透明 (id: NFR-001)

- **类型**: 可用性
- **描述**: 所有外部依赖和模型推理必须真实执行；失败时直接报错。
- **量化指标**: 不允许在失败情况下生成 YAML 或伪 conversion report。

### NFR-002: 可审计性 (id: NFR-002)

- **类型**: 可维护性
- **描述**: 每个转换结果必须包含足以追踪质量的统计指标。
- **量化指标**: conversion report 至少包含原始音符数、左右手音符数、主旋律保留率、低音锚点保留率、token 丢弃数和 global_score。

### NFR-003: Windows 本机兼容 (id: NFR-003)

- **类型**: 兼容性
- **描述**: 设计必须适配 Windows 11 本机环境和 CUDA 推理条件。
- **量化指标**: 所有路径、命令和依赖说明必须适配 Windows 路径格式。

### NFR-004: 参数可配置 (id: NFR-004)

- **类型**: 可维护性
- **描述**: onset 聚类阈值、主旋律 DP 权重、MPDR 预算和精排权重必须通过配置控制。
- **量化指标**: 不在核心算法内部新增不可解释 magic number。

### NFR-005: 真实曲目验证 (id: NFR-005)

- **类型**: 可用性
- **描述**: 验证必须基于项目已有真实曲目和真实依赖。
- **量化指标**: 至少使用 `config/cruel_angel_thesis.yaml`、`config/animenz_one_last_kiss.yaml`、`config/beautiful_world.yaml` 中的曲目产物做 A/B 对比。

---

## 约束条件 (id: section-2.3)

### 技术约束

- 必须使用真实 Demucs、Transkun、piano_svsep、partitura、music21、pretty_midi 推理与解析链路。
- 必须保留游戏键盘 C3-B5 36 键范围约束。
- 不允许使用 Mock、Stub 或伪造统计。
- 不允许静默 fallback 到旧规则算法。
- Python 文件注释与新增文档说明必须使用中文，作者字段统一为 JucieOvo。

### 资源约束

- 本设计依赖本机 GPU 环境，尤其是 piano_svsep 与 Transkun 推理。
- 若 CUDA / PyTorch / torch_geometric 环境不可用，任务应阻断并报告真实原因。

### 合规约束

- 本项目为本机离线音频算法与游戏曲谱生成，不应调用未授权外部服务。
- 不应绕过游戏反作弊或引入后台注入逻辑；本轮只生成 YAML 曲谱。

---

## 架构概览 (id: section-3.1)

本次设计涉及 6 个核心模块。数据流向：真实音频或 MIDI → MIDI 事件归一 → AI 左右手分离 → 主旋律追踪 → MPDR 候选生成与精排 → 36 键 token 输出。

```text
+-------------------+
| Audio/MIDI Input  |
+---------+---------+
          |
          v
+-------------------+     +--------------------+
| MidiEventNormalizer| --> | SvsepHandSeparator |
+---------+---------+     +----------+---------+
          |                          |
          v                          v
+-------------------+     +--------------------+
| MelodyPathTracker | --> | MpdrCandidateBuilder|
+---------+---------+     +----------+---------+
          |                          |
          v                          v
+-------------------+     +--------------------+
| MpdrCandidateReranker |->| GameKeyboardTokenEmitter |
+-------------------+     +--------------------+
```

---

## 模块职责 (id: section-3.2)

### 模块 A: MidiEventNormalizer (id: section-3.2-mod-A)

- **职责**: 读取真实 MIDI note 并在量化前进行 onset 聚类与持续时间归一。
- **对外接口**: 接收 pretty_midi note 事件和配置，返回归一后的 `MidiNoteEvent` 序列。
- **依赖**: `pretty_midi`、`AudioPipelineConfig`。

### 模块 B: SvsepHandSeparator (id: section-3.2-mod-B)

- **职责**: 使用 piano_svsep GNN 对 MIDI 音符进行左右手 staff 分离。
- **对外接口**: 接收 MIDI 路径、工作目录和 BPM，返回左右手音符列表及分离审计统计。
- **依赖**: `music21`、`partitura`、`piano_svsep`、`torch`、`torch_geometric`。

### 模块 C: MelodyPathTracker (id: section-3.2-mod-C)

- **职责**: 在右手音符集合中使用动态规划追踪主旋律路径。
- **对外接口**: 接收按时间片分组的右手音符，返回 `melody_by_index`。
- **依赖**: MPDR 主旋律评分配置。

### 模块 D: MpdrCandidateBuilder (id: section-3.2-mod-D)

- **职责**: 为每个时间片生成左右手候选音符，并计算 utility、masking_risk、perceptual_cost、keep_score。
- **对外接口**: 接收左右手分组、主旋律路径和配置，返回候选构建结果。
- **依赖**: `MelodyPathTracker` 输出、MPDR 预算配置。

### 模块 E: MpdrCandidateReranker (id: section-3.2-mod-E)

- **职责**: 对多组 MPDR 参数候选进行全局评分并选择最优候选。
- **对外接口**: 接收候选结果列表，返回 `global_score` 最高的结果及统计。
- **依赖**: MPDR 精排权重配置。

### 模块 F: GameKeyboardTokenEmitter (id: section-3.2-mod-F)

- **职责**: 将候选音符折叠到 C3-B5，并输出 modifier 安全的 YAML token。
- **对外接口**: 接收已评分候选，返回 YAML score events 和 token 冲突统计。
- **依赖**: 现有 `_pitch_to_token`、36 键范围常量、播放时序校验。

---

## 关键流程 (id: section-3.3)

### 流程 1: 音频到 MIDI 转谱 (id: section-3.3-flow-1)

1. 触发条件：用户输入真实音频且未提供 `input_midi_path`。
2. `DemucsSeparator` 提取钢琴 stem。
3. `PianoTranscriber` 调用 Transkun 生成 MIDI。
4. 异常路径：Demucs 或 Transkun 失败时直接抛出错误，不生成 YAML。

### 流程 2: MIDI 事件归一与 onset 聚类 (id: section-3.3-flow-2)

1. 触发条件：系统获得真实 MIDI 文件。
2. `MidiEventNormalizer` 读取 pitch、start、end、velocity。
3. 按 onset 聚类配置合并毫秒级接近的同一物理发声组。
4. 将聚类后的起音转换为量化时间片。
5. 异常路径：无有效 note 时直接报错。

### 流程 3: AI 左右手分离 (id: section-3.3-flow-3)

1. 触发条件：`pitch_compression_mode` 为 `svsep_mpdr`。
2. `SvsepHandSeparator` 将 MIDI 转为 MusicXML。
3. 使用 partitura 构建 note_array 和异构图。
4. 调用 piano_svsep 推理 staff 标签。
5. 回填为 left/right `MidiNoteEvent`。
6. 异常路径：模型缺失、推理失败、左右手结果为空或异常比例过高时直接报错。

### 流程 4: 主旋律路径追踪 (id: section-3.3-flow-4)

1. 触发条件：右手音符集合已分组。
2. 为每个右手候选计算基础旋律评分。
3. 在时间轴上执行 DP 累积连续性分数。
4. 回溯得到 `melody_by_index`。
5. 异常路径：右手为空时直接返回空路径并由上层报告质量风险。

### 流程 5: MPDR 候选生成与全局精排 (id: section-3.3-flow-5)

1. 触发条件：左右手分离和主旋律路径均已完成。
2. 生成少量参数候选。
3. 对每个候选构建左右手输出候选。
4. 计算全局评分。
5. 选择最高分候选。
6. 异常路径：没有任何有效候选时直接报错。

### 流程 6: 36 键折叠与 YAML 输出 (id: section-3.3-flow-6)

1. 触发条件：最优 MPDR 候选已确定。
2. 将候选折叠到 C3-B5。
3. 按优先级消解 token 冲突。
4. 插入真实休止符事件。
5. 校验 score event 数量和播放时序。
6. 输出 YAML 与 conversion report。

---

## 关键决策 (id: section-3.4)

### 决策 1: 选择 `svsep_mpdr` 作为主链路 (id: section-3.4-decision-1)

- **背景**: 用户明确要求“使用最强大的方法”。
- **选项**:
  - A: 强化 `svsep_mpdr` -- AI 声部分离上限高，复用现有代码资产；依赖环境复杂。
  - B: 引入音视频融合 -- 上限更高但范围扩大，依赖视频质量。
  - C: 重写规则型 RD-DLAF -- 可解释性强，但复杂曲目上限低。
- **选择**: A
- **理由**: 与音频算法目标最匹配，且当前代码已经具备可增强基础。
- **后果**: 本轮实现依赖真实 PyTorch / piano_svsep 环境，验证成本较高。

### 决策 2: 不提供静默降级 (id: section-3.4-decision-2)

- **背景**: 全局规则禁止 Mock、Stub、伪成功和降级掩盖错误。
- **选项**:
  - A: 模型失败时回到规则算法 -- 可用性高，但会掩盖真实失败。
  - B: 模型失败时直接报错 -- 失败透明，便于定位环境和模型问题。
- **选择**: B
- **理由**: 真实执行优先于演示成功，避免输出低质量曲谱却被误认为成功。
- **后果**: 首次环境配置门槛更高。

### 决策 3: 主旋律保护优先于和声厚度 (id: section-3.4-decision-3)

- **背景**: 36 键限制下不可能完整保留 88 键全部信息。
- **选项**:
  - A: 尽量保留更多音符 -- 密度高但容易遮蔽主旋律。
  - B: 优先保护主旋律和低音锚点 -- 可辨识度高但会丢失部分填充和声。
- **选择**: B
- **理由**: 游戏谱面最重要的是主旋律辨识度和低音结构。
- **后果**: 某些密集和声会被主动裁剪，但裁剪必须进入报告。

### 决策 4: 将视频融合延后并保持纯音频主链路 (id: section-3.4-decision-4)

- **背景**: 项目已有 `src/fusion_engine.py:1` 和 `src/dual_stream_extractor.py:1`，其逻辑通过演奏者视频中的手部轨迹辅助左右手分配和主旋律判断；用户明确希望本轮直接通过音频进行分析，以减少视频输入、视觉跟踪、镜像校准、遮挡处理和 MediaPipe 依赖带来的阻碍。
- **选项**:
  - A: 本轮纳入音视频融合 -- 潜在上限高，但范围扩大，并要求视频质量、视角、手部检测和音视频同步均满足条件。
  - B: 本轮聚焦纯音频主链路 -- 通过真实音频转谱、MIDI velocity/duration、piano_svsep 与 MPDR 完成左右手和主旋律分析，范围清晰且输入门槛更低。
- **选择**: B
- **理由**: 本轮目标是降低使用阻碍并提升音频算法本身的上限；视频融合应作为后续可选增强，不应成为生成谱面的必要条件。
- **后果**: 有视频输入时的物理指法信息暂不使用；所有主旋律和左右手判断质量必须由音频/MIDI/piano_svsep/MPDR 链路承担。

---

## 模块间接口 (id: section-4.1)

### 接口: MidiEventNormalizer → SvsepHandSeparator (id: section-4.1-iface-1)

- **调用方向**: `MidiToYamlConverter` 内部先归一 MIDI note，再将 MIDI 路径传给 `SvsepHandSeparator`。
- **方法**: 同步离线调用。
- **输入**: MIDI 文件路径、BPM、工作目录、模型路径、设备类型。
- **输出**: 左手音符列表、右手音符列表、分离审计统计；失败时抛出异常。

### 接口: SvsepHandSeparator → MelodyPathTracker (id: section-4.1-iface-2)

- **调用方向**: `MidiToYamlConverter` 将右手音符分组后传给主旋律追踪器。
- **方法**: 内存数据传递。
- **输入**: `{time_index → [MidiNoteEvent]}` 形式的右手分组。
- **输出**: `{time_index → MidiNoteEvent}` 形式的主旋律路径。

### 接口: MelodyPathTracker → MpdrCandidateBuilder (id: section-4.1-iface-3)

- **调用方向**: MPDR 构建器读取主旋律路径作为强保护约束。
- **方法**: 内存数据传递。
- **输入**: 左右手分组、主旋律路径、MPDR 配置。
- **输出**: 每个时间片的候选集合及候选评分。

### 接口: MpdrCandidateBuilder → MpdrCandidateReranker (id: section-4.1-iface-4)

- **调用方向**: 候选构建器生成多个候选结果，由精排器选择最优。
- **方法**: 内存数据传递。
- **输入**: `MpdrBuildResult` 列表。
- **输出**: 最优 `MpdrBuildResult` 及全局质量指标。

### 接口: MpdrCandidateReranker → GameKeyboardTokenEmitter (id: section-4.1-iface-5)

- **调用方向**: token 输出器读取最优候选中的分组音符。
- **方法**: 内存数据传递。
- **输入**: 最优候选的 grouped notes 和 token 输出配置。
- **输出**: YAML score events 与 token 冲突统计。

---

## 外部接口 (id: section-4.2)

### 外部工具: Demucs (id: section-4.2-ext-1)

- **提供方**: 本机安装的 Demucs 命令行工具。
- **用途**: 从真实音频中提取钢琴 stem。
- **协议**: 本地命令行调用。
- **降级策略**: 不降级；失败时直接报错。

### 外部工具: Transkun (id: section-4.2-ext-2)

- **提供方**: 本机安装的 Transkun 钢琴转录工具。
- **用途**: 将钢琴 stem 转录为 MIDI。
- **协议**: 本地 Python/命令行调用。
- **降级策略**: 不降级；失败时直接报错。

### 外部模型: piano_svsep (id: section-4.2-ext-3)

- **提供方**: 项目本地 `piano_svsep` 模型与依赖。
- **用途**: 对 MIDI 乐谱图进行 staff / voice 预测。
- **协议**: 本地 PyTorch 推理。
- **降级策略**: 不降级；模型缺失或推理失败时直接报错。

---

## 已知风险 (id: section-5.1)

### 风险 1: piano_svsep 环境不可用 (id: section-5.1-risk-1)

- **描述**: PyTorch、torch_geometric、partitura、music21、piano_svsep 或模型权重缺失会导致分离失败。
- **影响**: `svsep_mpdr` 主链路无法运行。
- **概率**: 中
- **缓解措施**: 在计划阶段加入环境验证任务，失败时输出明确依赖和路径错误。

### 风险 2: MusicXML 回填对齐偏差 (id: section-5.1-risk-2)

- **描述**: MIDI 转 MusicXML 后，note_array 与原始 MIDI note 的匹配可能出现偏差。
- **影响**: 左右手分离标签可能回填到错误音符。
- **概率**: 中
- **缓解措施**: 增加匹配审计统计，无法匹配比例超过阈值时阻断。

### 风险 3: 主旋律误判 (id: section-5.1-risk-3)

- **描述**: 右手密集和声、装饰音或高音填充可能被误判为主旋律。
- **影响**: MPDR 会错误保护非主旋律音，导致真正旋律被削弱。
- **概率**: 中
- **缓解措施**: 引入 duration、velocity、强拍、连续性和大跳惩罚综合评分。

### 风险 4: 36 键压缩信息损失 (id: section-5.1-risk-4)

- **描述**: 88 键到 36 键的压缩必然造成信息丢失。
- **影响**: 密集曲目中部分和声和装饰音会丢失。
- **概率**: 高
- **缓解措施**: 通过主旋律、低音锚点和和声色彩优先级控制损失，并在报告中记录丢弃原因。

### 风险 5: 无延音踏板导致听感断裂 (id: section-5.1-risk-5)

- **描述**: 游戏钢琴不支持真实延音踏板。
- **影响**: 长音和踏板效果无法完整还原。
- **概率**: 高
- **缓解措施**: 本轮不解决平台能力缺失，仅避免长左手音遮蔽主旋律。

---

## 未决问题 (id: section-5.2)

### 问题 1: piano_svsep 模型权重路径 (id: section-5.2-q-1)

- **描述**: 实施前需要确认本机实际可用的 piano_svsep 权重路径。
- **阻塞规划**: 否
- **负责人**: JucieOvo

### 问题 2: onset 聚类阈值初始值 (id: section-5.2-q-2)

- **描述**: 需要在计划阶段用真实曲目确定 onset 聚类窗口的初始配置。
- **阻塞规划**: 否
- **负责人**: JucieOvo

### 问题 3: A/B 对比基准模式 (id: section-5.2-q-3)

- **描述**: 需要确认以 `hands_decoupled`、旧 `svsep_mpdr` 还是 `adaptive_octave_fold` 作为主要对比基准。
- **阻塞规划**: 否
- **负责人**: JucieOvo

---

## 假设列表 (id: section-5.3)

### 假设 1: 本机具备真实 CUDA 推理环境 (id: section-5.3-a-1)

- **假设内容**: 本机可以运行 Transkun 和 piano_svsep 的真实推理。
- **如果假设不成立**: 本轮实现仍可完成，但验证阶段会阻塞在环境检查。
- **验证方式**: 计划阶段执行真实依赖检查与最小推理验证。

### 假设 2: 现有 MIDI 回填逻辑可扩展 (id: section-5.3-a-2)

- **假设内容**: `src/svsep_hand_separator.py:239` 附近的原始 MIDI 回填逻辑可以增强审计而不需要整体重写。
- **如果假设不成立**: 需要在计划阶段拆出独立 note matching 模块。
- **验证方式**: 使用真实 MIDI 比对 note count、pitch/start/end 匹配比例。

### 假设 3: 现有播放器可加载新增输出 (id: section-5.3-a-3)

- **假设内容**: 只要 YAML schema 不变，`src/piano_auto_player.py:140` 之后的加载与播放逻辑可以继续使用。
- **如果假设不成立**: 需要在后续计划中增加播放器兼容性修复任务。
- **验证方式**: 使用输出 YAML 通过现有 loader 做真实加载测试。

---

## 计划变更项 (id: section-6.1)

### 变更: 扩展音频算法配置 (id: change-1)

@DESIGN:section-3.2-mod-A

- **操作类型**: 修改
- **目标**: `src/audio_to_yaml_converter.py:149`
- **内容**: 扩展 `AudioPipelineConfig`，增加 onset 聚类、主旋律 DP、MPDR 审计与精排相关参数。
- **原因**: 统一管理算法参数，避免硬编码。
- **影响**: CLI 参数解析、配置校验、conversion report。

### 变更: 增加 MIDI 事件归一与 onset 聚类 (id: change-2)

@DESIGN:FR-002

- **操作类型**: 修改
- **目标**: `src/audio_to_yaml_converter.py:703`
- **内容**: 在 MIDI note 量化前加入 onset clustering 与事件归一流程。
- **原因**: 避免同一物理和弦被毫秒级误差拆散。
- **影响**: `_read_midi_notes`、量化分组、所有后续压缩模式的输入一致性。

### 变更: 增强 piano_svsep 分离审计 (id: change-3)

@DESIGN:FR-003

- **操作类型**: 修改
- **目标**: `src/svsep_hand_separator.py:97`
- **内容**: 增强左右手分离输出，增加匹配数量、无法匹配比例、左右手数量等审计统计。
- **原因**: 确保 AI 声部分离结果可验证。
- **影响**: `src/audio_to_yaml_converter.py:886` 的调用返回结构和 conversion report。

### 变更: 增强主旋律 DP 追踪 (id: change-4)

@DESIGN:FR-004

- **操作类型**: 修改
- **目标**: `src/audio_to_yaml_converter.py:1333`
- **内容**: 将主旋律评分扩展为 pitch、velocity、duration、强拍、连续性、大跳惩罚和重复噪声惩罚的组合。
- **原因**: 降低高位和声音或装饰音误判为主旋律的概率。
- **影响**: MPDR 主旋律保护、候选评分、精排统计。

### 变更: 增强 MPDR 候选选择 (id: change-5)

@DESIGN:FR-005

- **操作类型**: 修改
- **目标**: `src/audio_to_yaml_converter.py:1526`、`src/audio_to_yaml_converter.py:1611`
- **内容**: 重整左右手候选 utility、masking_risk、perceptual_cost 和 keep_score 的计算。
- **原因**: 在主旋律突出、低音锚点保留和和声色彩之间取得更优平衡。
- **影响**: 输出音符密度、左手保留率、遮蔽风险。

### 变更: 增强折叠与 token 冲突统计 (id: change-6)

@DESIGN:FR-006

- **操作类型**: 修改
- **目标**: `src/audio_to_yaml_converter.py:1935`、`src/audio_to_yaml_converter.py:2047`
- **内容**: 明确 token 冲突优先级，记录丢弃候选和 modifier 冲突统计。
- **原因**: 让 36 键压缩损失可审计。
- **影响**: YAML 输出、conversion report。

### 变更: 扩展全局质量评分与报告 (id: change-7)

@DESIGN:FR-007

- **操作类型**: 修改
- **目标**: `src/audio_to_yaml_converter.py:2130`
- **内容**: 增加主旋律保留率、低音锚点保留率、遮蔽规避、和声完整度、音区清晰度等统计。
- **原因**: 为真实 A/B 对比和调参提供证据。
- **影响**: conversion report 字段、验证脚本或人工审核流程。

---

## 受影响范围 (id: section-6.2)

| 文件 | 范围 | 变更类型 | 关联变更 ID |
|------|------|----------|------------|
| `src/audio_to_yaml_converter.py:149` | `AudioPipelineConfig` | 修改 | change-1 |
| `src/audio_to_yaml_converter.py:703` | MIDI note 读取与量化前处理 | 修改 | change-2 |
| `src/svsep_hand_separator.py:97` | `SvsepHandSeparator.separate` | 修改 | change-3 |
| `src/audio_to_yaml_converter.py:1333` | 主旋律路径追踪 | 修改 | change-4 |
| `src/audio_to_yaml_converter.py:1526` | 右手候选选择 | 修改 | change-5 |
| `src/audio_to_yaml_converter.py:1611` | 左手候选选择 | 修改 | change-5 |
| `src/audio_to_yaml_converter.py:1935` | MPDR 折叠 | 修改 | change-6 |
| `src/audio_to_yaml_converter.py:2047` | token 冲突消解 | 修改 | change-6 |
| `src/audio_to_yaml_converter.py:2130` | 全局质量评分 | 修改 | change-7 |
| `docs/superpowers/specs/2026-07-01-audio-algorithm-optimization-design.md` | Superpowers 设计文档 | 新增 | change-1, change-7 |
| `docs/DESIGN.md` | i2aspec 设计文档 | 新增 | change-1, change-7 |

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 章节 | 供 PLAN 引用 |
|----------|------|-------------|
| section-1.1 | 背景与动机 | @DESIGN:section-1.1 |
| section-1.2 | 目标与非目标 | @DESIGN:section-1.2 |
| section-1.3 | 范围边界 | @DESIGN:section-1.3 |
| section-1.4 | 关键术语 | @DESIGN:section-1.4 |
| FR-001 | SVSEP-MPDR v3 主链路 | @DESIGN:FR-001 |
| FR-002 | MIDI onset 聚类与量化校正 | @DESIGN:FR-002 |
| FR-003 | AI 左右手分离与审计 | @DESIGN:FR-003 |
| FR-004 | 主旋律 DP 路径追踪 | @DESIGN:FR-004 |
| FR-005 | MPDR 感知密度候选生成与精排 | @DESIGN:FR-005 |
| FR-006 | 36 键折叠与 token 冲突消解 | @DESIGN:FR-006 |
| FR-007 | 输出真实转换报告 | @DESIGN:FR-007 |
| NFR-001 | 真实执行与失败透明 | @DESIGN:NFR-001 |
| NFR-002 | 可审计性 | @DESIGN:NFR-002 |
| NFR-003 | Windows 本机兼容 | @DESIGN:NFR-003 |
| NFR-004 | 参数可配置 | @DESIGN:NFR-004 |
| NFR-005 | 真实曲目验证 | @DESIGN:NFR-005 |
| section-2.3 | 约束条件 | @DESIGN:section-2.3 |
| section-3.1 | 架构概览 | @DESIGN:section-3.1 |
| section-3.2-mod-A | MidiEventNormalizer | @DESIGN:section-3.2-mod-A |
| section-3.2-mod-B | SvsepHandSeparator | @DESIGN:section-3.2-mod-B |
| section-3.2-mod-C | MelodyPathTracker | @DESIGN:section-3.2-mod-C |
| section-3.2-mod-D | MpdrCandidateBuilder | @DESIGN:section-3.2-mod-D |
| section-3.2-mod-E | MpdrCandidateReranker | @DESIGN:section-3.2-mod-E |
| section-3.2-mod-F | GameKeyboardTokenEmitter | @DESIGN:section-3.2-mod-F |
| section-3.3-flow-1 | 音频到 MIDI 转谱 | @DESIGN:section-3.3-flow-1 |
| section-3.3-flow-2 | MIDI 事件归一与 onset 聚类 | @DESIGN:section-3.3-flow-2 |
| section-3.3-flow-3 | AI 左右手分离 | @DESIGN:section-3.3-flow-3 |
| section-3.3-flow-4 | 主旋律路径追踪 | @DESIGN:section-3.3-flow-4 |
| section-3.3-flow-5 | MPDR 候选生成与全局精排 | @DESIGN:section-3.3-flow-5 |
| section-3.3-flow-6 | 36 键折叠与 YAML 输出 | @DESIGN:section-3.3-flow-6 |
| section-3.4-decision-1 | 选择 svsep_mpdr 主链路 | @DESIGN:section-3.4-decision-1 |
| section-3.4-decision-2 | 不提供静默降级 | @DESIGN:section-3.4-decision-2 |
| section-3.4-decision-3 | 主旋律保护优先 | @DESIGN:section-3.4-decision-3 |
| section-3.4-decision-4 | 视频融合延后 | @DESIGN:section-3.4-decision-4 |
| section-4.1-iface-1 | Normalizer 到 Svsep 接口 | @DESIGN:section-4.1-iface-1 |
| section-4.1-iface-2 | Svsep 到 Melody 接口 | @DESIGN:section-4.1-iface-2 |
| section-4.1-iface-3 | Melody 到 MPDR Builder 接口 | @DESIGN:section-4.1-iface-3 |
| section-4.1-iface-4 | MPDR Builder 到 Reranker 接口 | @DESIGN:section-4.1-iface-4 |
| section-4.1-iface-5 | Reranker 到 TokenEmitter 接口 | @DESIGN:section-4.1-iface-5 |
| section-4.2-ext-1 | Demucs 外部工具 | @DESIGN:section-4.2-ext-1 |
| section-4.2-ext-2 | Transkun 外部工具 | @DESIGN:section-4.2-ext-2 |
| section-4.2-ext-3 | piano_svsep 外部模型 | @DESIGN:section-4.2-ext-3 |
| section-5.1-risk-1 | piano_svsep 环境风险 | @DESIGN:section-5.1-risk-1 |
| section-5.1-risk-2 | MusicXML 对齐风险 | @DESIGN:section-5.1-risk-2 |
| section-5.1-risk-3 | 主旋律误判风险 | @DESIGN:section-5.1-risk-3 |
| section-5.1-risk-4 | 36 键压缩信息损失 | @DESIGN:section-5.1-risk-4 |
| section-5.1-risk-5 | 无延音踏板风险 | @DESIGN:section-5.1-risk-5 |
| section-5.2-q-1 | piano_svsep 模型权重路径 | @DESIGN:section-5.2-q-1 |
| section-5.2-q-2 | onset 聚类阈值初始值 | @DESIGN:section-5.2-q-2 |
| section-5.2-q-3 | A/B 对比基准模式 | @DESIGN:section-5.2-q-3 |
| section-5.3-a-1 | 本机 CUDA 推理环境假设 | @DESIGN:section-5.3-a-1 |
| section-5.3-a-2 | MIDI 回填逻辑可扩展假设 | @DESIGN:section-5.3-a-2 |
| section-5.3-a-3 | 播放器可加载新增输出假设 | @DESIGN:section-5.3-a-3 |
| change-1 | 扩展音频算法配置 | @DESIGN:change-1 |
| change-2 | 增加 MIDI 事件归一与 onset 聚类 | @DESIGN:change-2 |
| change-3 | 增强 piano_svsep 分离审计 | @DESIGN:change-3 |
| change-4 | 增强主旋律 DP 追踪 | @DESIGN:change-4 |
| change-5 | 增强 MPDR 候选选择 | @DESIGN:change-5 |
| change-6 | 增强折叠与 token 冲突统计 | @DESIGN:change-6 |
| change-7 | 扩展全局质量评分与报告 | @DESIGN:change-7 |
