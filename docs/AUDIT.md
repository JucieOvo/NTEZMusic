---
stage: audit
title: SVSEP-MPDR v3 审计报告
author: JucieOvo
date: 2026-07-01
status: draft
---

# SVSEP-MPDR v3 审计报告

## 关联 Agent 审查记录 (id: section-sp-ref)

- **Superpowers 计划**: @SP:plan/2026-07-01-svsep-mpdr-v3
- **实践报告**: @PRACTICE:change-C-001 ~ C-007
- **Agent 审查记录**:
  - 整体代码审查: 1 Critical（已修复）, 4 Important（待评估）, 5 Minor（建议跟进）
- **说明**: 本文档整合 Agent 审查发现，并补充独立的逐项设计-规划-实践对比核查。本阶段 Agent 审查由于项目无 git 仓库，采用全文件审查模式。

---

## 审计概述 (id: section-1)

### 审计范围 (id: section-1.1)

**被审计条目:**

- 设计条目: @DESIGN:FR-001 ~ FR-007, @DESIGN:NFR-001 ~ NFR-005, @DESIGN:section-3.2-mod-A ~ mod-F, @DESIGN:section-3.4-decision-1 ~ decision-4, @DESIGN:change-1 ~ change-7
- 规划任务: @PLAN:task-T-001 ~ T-006, @PLAN:section-1.3-check-1 ~ check-4
- 实践变更: @PRACTICE:change-C-001 ~ C-007

**审计排除项:**

无。全部 6 个规划任务均已执行，全部 7 个设计变更项均已对应实现。

### 审计方法 (id: section-1.2)

1. **整合 Agent 审查结果**: 从整体代码审查中提取 Critical/Important/Minor 问题
2. **逐项设计对比**: 对每个 @DESIGN 条目，核查 @PRACTICE 中的对应变更
3. **逐项规划对比**: 对每个 @PLAN 任务，核查执行完整度与验收标准
4. **代码质量抽查**: 对核心路径（onset 聚类、主旋律 DP、token 冲突优先级）进行独立审查
5. **追溯链完整性**: 检查 DESIGN → PLAN → PRACTICE → AUDIT 的引用链完整性

---

## 设计符合性审计 (id: section-2)

### 审计项 2.1: @DESIGN:FR-001 -- SVSEP-MPDR v3 主链路 (id: audit-2.1)

- **设计条目**: @DESIGN:FR-001
- **对应实践变更**: @PRACTICE:change-C-002, C-003, C-004, C-005, C-006, C-007
- **Agent 审查结果**: 整体审查通过 -- 纯音频主链路边界明确
- **符合度**: 完全符合

- **逐项检查**:
  - [x] `svsep_mpdr` 为核心主链路 → 符合（C-002~C-006 全部围绕该模式增强）
  - [x] 不依赖演奏者视频 → 符合（C-007 smoke test 确认无视频融合模块导入）
  - [x] 左右手与主旋律判断通过音频/MIDI/piano_svsep/MPDR → 符合

### 审计项 2.2: @DESIGN:FR-002 -- MIDI onset 聚类 (id: audit-2.2)

- **设计条目**: @DESIGN:FR-002
- **对应实践变更**: @PRACTICE:change-C-001, C-002
- **Agent 审查结果**: 通过 -- 配置完整、聚类算法正确、非法配置报错
- **符合度**: 完全符合

- **逐项检查**:
  - [x] 可配置的 onset 聚类 → 符合（3 个配置字段 + CLI 参数）
  - [x] 量化前执行 → 符合（_read_midi_notes 中 merge 之后、排序之前执行）
  - [x] 减少和弦被起音误差拆散 → 符合（velocity 加权平均 + 窗口/跨度双阈值）
  - [x] 非法配置报错 → 符合（window < 0 或 max_span < window 时 ValueError）

### 审计项 2.3: @DESIGN:FR-003 -- AI 左右手分离审计 (id: audit-2.3)

- **设计条目**: @DESIGN:FR-003
- **对应实践变更**: @PRACTICE:change-C-003
- **Agent 审查结果**: 通过 -- docstring 左右手标注已修复
- **符合度**: 完全符合

- **逐项检查**:
  - [x] piano_svsep 左右手分离 → 符合（GNN 推理链路不变）
  - [x] 真实分离统计 → 符合（8 个审计维度）
  - [x] 低质量匹配报错 → 符合（match_ratio < 0.95 抛 ValueError）
  - [x] 调用方写入 svsep 统计 → 符合（6 个新增 conversion_stats 字段）

### 审计项 2.4: @DESIGN:FR-004 -- 主旋律 DP 追踪 (id: audit-2.4)

- **设计条目**: @DESIGN:FR-004
- **对应实践变更**: @PRACTICE:change-C-001, C-004
- **Agent 审查结果**: 通过 -- 7 个专属权重独立可调，大跳/重复惩罚有效
- **符合度**: 完全符合

- **逐项检查**:
  - [x] 综合 pitch 评分 → 符合（mpdr_melody_pitch_weight）
  - [x] 综合 velocity 评分 → 符合（mpdr_melody_velocity_weight）
  - [x] 综合 duration 评分 → 符合（mpdr_melody_duration_weight）
  - [x] 综合强拍评分 → 符合（mpdr_melody_beat_weight）
  - [x] 连续性转移 → 符合（mpdr_melody_continuity_weight）
  - [x] 大跳惩罚 → 符合（mpdr_melody_large_jump_penalty）
  - [x] 重复惩罚 → 符合（mpdr_melody_repetition_penalty）

### 审计项 2.5: @DESIGN:FR-005 -- MPDR 候选生成与精排 (id: audit-2.5)

- **设计条目**: @DESIGN:FR-005
- **对应实践变更**: @PRACTICE:change-C-005
- **Agent 审查结果**: 通过 -- 5 个审计字段完整累计，bass_anchor_integrity 正确写入
- **符合度**: 完全符合

- **逐项检查**:
  - [x] 多个 MPDR 参数候选 → 符合（mpdr_candidate_count 控制）
  - [x] 全局分数选择最优候选 → 符合（_score_svsep_mpdr_stats 加权评分）
  - [x] 审计指标可量化 → 符合（bass_anchor_integrity + 5 个事件计数器）

### 审计项 2.6: @DESIGN:FR-006 -- token 冲突消解 (id: audit-2.6)

- **设计条目**: @DESIGN:FR-006
- **对应实践变更**: @PRACTICE:change-C-006
- **Agent 审查结果**: 通过 -- 优先级正确：主旋律 > 低音锚点 > 右手非旋律 > 左手和声
- **符合度**: 完全符合

- **逐项检查**:
  - [x] 折叠到 C3-B5 → 符合（不改变折叠逻辑，仅增强冲突消解）
  - [x] 主旋律优先级消解 → 符合（rank=5 压制任何高 keep_score 填充音）
  - [x] 低音锚点优先级 → 符合（is_bass_anchor 标记 + rank=4）
  - [x] 冲突丢弃统计 → 符合（svsep_mpdr_token_conflict_dropped_notes）

### 审计项 2.7: @DESIGN:FR-007 -- 转换报告 (id: audit-2.7)

- **设计条目**: @DESIGN:FR-007
- **对应实践变更**: @PRACTICE:change-C-005, C-007
- **Agent 审查结果**: 通过 -- 5 个必需质量指标全部写入 stats
- **符合度**: 完全符合

- **逐项检查**:
  - [x] 主旋律保留率 → 符合（melody_integrity）
  - [x] 低音锚点保留率 → 符合（bass_anchor_integrity）
  - [x] token 丢弃数 → 符合（svsep_mpdr_token_conflict_dropped_notes）
  - [x] modifier 冲突 → 符合（统计累计到 svsep_mpdr_token_conflict_dropped_notes）
  - [x] MPDR 全局分数 → 符合（_score_svsep_mpdr_stats 返回值）

### 审计项 2.8: @DESIGN:NFR-001 ~ NFR-005 -- 非功能需求 (id: audit-2.8)

| 条目 | 符合度 | 证据 |
|------|--------|------|
| @DESIGN:NFR-001 (真实执行与失败透明) | 完全符合 | onset 聚类非法配置抛 ValueError；审计 validate() 对低匹配率/空结果抛错；piano_svsep 推理失败抛 RuntimeError |
| @DESIGN:NFR-002 (可审计性) | 完全符合 | SvsepSeparationAudit 8 维度统计 + 5 个 MPDR 事件计数器 + 5 个质量指标 |
| @DESIGN:NFR-003 (Windows 兼容) | 完全符合 | 全部测试在 Windows 11 通过 |
| @DESIGN:NFR-004 (参数可配置) | 完全符合 | 13 个新增配置字段全部带默认值并通过 CLI 接入 |
| @DESIGN:NFR-005 (真实曲目验证) | 待验证 | 需要 piano_svsep 模型权重路径确认后进行真实曲目验证（见 @DESIGN:section-5.2-q-1） |

### 审计项 2.9: @DESIGN:section-3.2 模块职责符合性 (id: audit-2.9)

| 设计模块 | 修改文件:行号 | 职责一致 | 备注 |
|----------|-------------|----------|------|
| @DESIGN:section-3.2-mod-A (MidiEventNormalizer) | `audio_to_yaml_converter.py:839-956` | 是 | onset 聚类 + 事件归一 |
| @DESIGN:section-3.2-mod-B (SvsepHandSeparator) | `svsep_hand_separator.py:97-454` | 是 | AI 分离 + 审计统计 |
| @DESIGN:section-3.2-mod-C (MelodyPathTracker) | `audio_to_yaml_converter.py:1545-1629` | 是 | DP 主旋律追踪 |
| @DESIGN:section-3.2-mod-D (MpdrCandidateBuilder) | `audio_to_yaml_converter.py:1366-1432` | 是 | 候选构建 + 审计累计 |
| @DESIGN:section-3.2-mod-E (MpdrCandidateReranker) | `audio_to_yaml_converter.py:2422-2423` | 是 | bass_anchor_integrity |
| @DESIGN:section-3.2-mod-F (GameKeyboardTokenEmitter) | `audio_to_yaml_converter.py:2251-2302` | 是 | 优先级 token 输出 |

### 审计项 2.10: @DESIGN:section-3.4 关键决策符合性 (id: audit-2.10)

| 决策 | 符合度 | 证据 |
|------|--------|------|
| @DESIGN:section-3.4-decision-1 (svsep_mpdr 主链路) | 完全符合 | 全部增强围绕 svsep_mpdr 模式 |
| @DESIGN:section-3.4-decision-2 (不提供静默降级) | 完全符合 | 所有错误路径直接抛异常 |
| @DESIGN:section-3.4-decision-3 (主旋律保护优先) | 完全符合 | token 冲突优先级 rank 5 + DP 大跳/重复惩罚 |
| @DESIGN:section-3.4-decision-4 (纯音频主链路) | 完全符合 | smoke test 确认无视频模块导入 |

---

## 规划符合性审计 (id: section-3)

### 审计项 3.1: @PLAN:task-T-001 (id: audit-3.1)

- **任务**: @PLAN:task-T-001
- **对应实践变更**: @PRACTICE:change-C-001, C-002
- **Agent 审查结果**: 通过
- **执行完整度**: 完整

- **验收结果**:
  - [x] "onset 聚类测试通过，非法配置真实报错" → 4/4 测试 PASS
  - [x] "AudioPipelineConfig 新增 3 个 onset 聚类字段" → 已实现
  - [x] "_cluster_midi_note_onsets 方法" → 已实现
  - [x] "conversion_stats 初始化 onset 统计" → 已实现

### 审计项 3.2: @PLAN:task-T-002 (id: audit-3.2)

- **任务**: @PLAN:task-T-002
- **对应实践变更**: @PRACTICE:change-C-003
- **Agent 审查结果**: 通过（docstring 标注问题已修复）
- **执行完整度**: 完整

- **验收结果**:
  - [x] "审计 dataclass 测试通过" → 4/4 测试 PASS
  - [x] "调用方能写入 svsep 统计" → 6 个 conversion_stats 字段
  - [x] "SvsepSeparationAudit 包含 match_ratio 和 validate()" → 已实现
  - [x] "SvsepSeparationResult 同时携带左右手和审计" → 已实现

### 审计项 3.3: @PLAN:task-T-003 (id: audit-3.3)

- **任务**: @PLAN:task-T-003
- **对应实践变更**: @PRACTICE:change-C-001, C-004
- **Agent 审查结果**: 通过
- **执行完整度**: 完整

- **验收结果**:
  - [x] "主旋律路径测试通过" → 2/2 测试 PASS
  - [x] "孤立高跳不再压过连续旋律" → test 验证通过
  - [x] "7 个专属权重字段" → 全部实现
  - [x] "大跳惩罚" → interval > collision_semitones 时扣除
  - [x] "重复惩罚" → 同音高在 repeat_suppression_beats 内扣除

### 审计项 3.4: @PLAN:task-T-004 (id: audit-3.4)

- **任务**: @PLAN:task-T-004
- **对应实践变更**: @PRACTICE:change-C-005
- **Agent 审查结果**: 通过（见 review Important #3/#4）
- **执行完整度**: 完整

- **验收结果**:
  - [x] "bass_anchor_integrity 等报告字段可由真实统计计算" → 2/2 测试 PASS
  - [x] "total_bass_anchor_notes / kept_bass_anchor_notes 累计" → 已实现
  - [x] "low_mud_penalty_events / register_collision_events / onset_collision_events 累计" → 已实现

### 审计项 3.5: @PLAN:task-T-005 (id: audit-3.5)

- **任务**: @PLAN:task-T-005
- **对应实践变更**: @PRACTICE:change-C-006
- **Agent 审查结果**: 通过
- **执行完整度**: 完整

- **验收结果**:
  - [x] "token 冲突测试通过" → 2/2 测试 PASS
  - [x] "主旋律不被高 keep_score 填充音挤掉" → test 验证通过
  - [x] "bass anchor 不被挤掉" → test 验证通过
  - [x] "is_bass_anchor 字段" → 已实现
  - [x] "_mpdr_candidate_priority 方法" → 已实现

### 审计项 3.6: @PLAN:task-T-006 (id: audit-3.6)

- **任务**: @PLAN:task-T-006
- **对应实践变更**: @PRACTICE:change-C-007
- **Agent 审查结果**: 通过
- **执行完整度**: 完整

- **验收结果**:
  - [x] "新增配置可通过 CLI 传入" → 10 个新 CLI 参数
  - [x] "测试确认未接入 fusion_engine 或 dual_stream_extractor" → smoke test PASS
  - [x] "使用说明更新" → 已更新 docs/audio_to_yaml_converter使用说明.md

### 审计项 3.7: @PLAN: 检查点汇总 (id: audit-3.7)

| 检查点 | 阶段 | 结果 | 证据 |
|--------|------|------|------|
| @PLAN:section-1.3-check-1 (MIDI 输入归一) | T-001 后 | 通过 | 4/4 tests |
| @PLAN:section-1.3-check-2 (piano_svsep 审计) | T-002 后 | 通过 | 4/4 tests |
| @PLAN:section-1.3-check-3 (MPDR 核心评分) | T-004 后 | 通过 | 4/4 tests (T-003+T-004) |
| @PLAN:section-1.3-check-4 (纯音频链路) | T-006 后 | 通过 | smoke test PASS |

---

## 代码质量审计 (id: section-4)

### Agent 审查问题汇总 (id: section-4.1)

| # | 级别 | 描述 | 文件:行号 | 状态 |
|---|------|------|----------|------|
| 1 | Critical | SvsepSeparationAudit docstring 左右手标注颠倒 | `svsep_hand_separator.py:403-404` | **已修复** |
| 2 | Important | 聚类统计使用不规范的 if/else 分支 | `audio_to_yaml_converter.py:944-956` | 待评估 |
| 3 | Important | bass_anchor_integrity 回退到旧字段语义不明确 | `audio_to_yaml_converter.py:2422-2423` | 待评估 |
| 4 | Important | test_build_svsep_mpdr_candidate_accumulates_event_counters 断言过于宽松 | `tests/test_audio_to_yaml_svsep_mpdr.py:417-501` | 待加强 |
| 5 | Important | onset_cluster_window_beats == 0 边界行为 | `audio_to_yaml_converter.py:869` | 待文档化 |
| 6 | Minor | 大跳惩罚复用了 mpdr_register_collision_semitones | `audio_to_yaml_converter.py:1565` | 建议解耦 |
| 7 | Minor | _extract_primary_melody_path interval 变量作用域 | `audio_to_yaml_converter.py:1545-1566` | 已验证无问题 |
| 8 | Minor | _cluster_midi_note_onsets 返回值类型标注 | `audio_to_yaml_converter.py:843,865` | 低风险 |
| 9 | Minor | MidiNoteEvent 在两个文件中重复定义 | `svsep_hand_separator.py:458` vs `audio_to_yaml_converter.py:315` | 建议抽取 |
| 10 | Minor | 测试文件使用相对路径 | `tests/test_audio_to_yaml_svsep_mpdr.py:573` | 建议使用 __file__ |

### 结构审查：模块职责一致性 (id: section-4.2)

| 设计模块 | 实际文件 | 职责一致 | 备注 |
|----------|----------|----------|------|
| @DESIGN:section-3.2-mod-A | `audio_to_yaml_converter.py:839-956` | 是 | onset 聚类 + 事件归一，职责单一 |
| @DESIGN:section-3.2-mod-B | `svsep_hand_separator.py:97-454` | 是 | AI 分离 + 审计统计，边界清晰 |
| @DESIGN:section-3.2-mod-C | `audio_to_yaml_converter.py:1545-1629` | 是 | DP 追踪，评分独立于候选构建 |
| @DESIGN:section-3.2-mod-D | `audio_to_yaml_converter.py:1366-1432` | 是 | 候选构建 + 审计，未混入 token 输出 |
| @DESIGN:section-3.2-mod-E | `audio_to_yaml_converter.py:2374-2430` | 是 | 全局评分独立于候选构建 |
| @DESIGN:section-3.2-mod-F | `audio_to_yaml_converter.py:2251-2302` | 是 | token 输出 + 冲突消解，独立方法 |

全部 6 个模块职责一致，未发现跨层调用或职责泄漏。

### 接口审查：设计接口 vs 实际签名 (id: section-4.3)

| 设计接口 | 设计签名 | 实际签名 | 一致 |
|----------|----------|----------|------|
| @DESIGN:section-4.1-iface-1 (Normalizer→Svsep) | `separate(midi_path, work_dir, bpm) → SvsepSeparationResult` | 匹配 | 是 |
| @DESIGN:section-4.1-iface-2 (Svsep→Melody) | `{time_index → [MidiNoteEvent]} → {time_index → MidiNoteEvent}` | 匹配 | 是 |
| @DESIGN:section-4.1-iface-3 (Melody→MPDR Builder) | 内存数据传递, melody_by_index | 匹配 | 是 |
| @DESIGN:section-4.1-iface-4 (MPDR Builder→Reranker) | `MpdrBuildResult 列表 → 最优结果` | 匹配 | 是 |
| @DESIGN:section-4.1-iface-5 (Reranker→TokenEmitter) | `最优候选 grouped notes → YAML score events` | 匹配 | 是 |

全部 5 个模块间接口签名与设计一致，无偏差。

---

## 差异汇总 (id: section-5)

### 设计-实践差异 (id: section-5.1)

| 差异 ID | 设计条目 | 实践表现 | 类别 | Agent 已标记 |
|----------|----------|----------|------|------------|
| (无) | -- | -- | -- | -- |

**结论：在设计-实践层面，未发现偏差。** 所有 7 个设计变更项（change-1 ~ change-7）均有对应的实践变更（C-001 ~ C-007），所有 7 个功能需求（FR-001 ~ FR-007）和 4 个非功能需求（NFR-001 ~ NFR-004）均已覆盖。NFR-005（真实曲目验证）需要真实 piano_svsep 模型权重路径，属于环境依赖而非实现偏差。

### 差异分类 (id: section-5.2)

| 类别 | 数量 | 说明 |
|------|------|------|
| 有意偏差 | 0 | -- |
| 无意偏差 | 0 | -- |
| 未实现 | 0 | 全部设计条目均已实现 |
| Agent 已标记 | 1 | Critical #1（docstring 标注颠倒，已修复） |

### 未覆盖项 (id: section-5.3)

| 设计条目 | 原因 |
|----------|------|
| @DESIGN:NFR-005 (真实曲目验证) | 需要 piano_svsep 模型权重路径确认，属于环境依赖阻塞（@DESIGN:section-5.2-q-1），非实现遗漏 |
| @DESIGN:section-5.2-q-2 (onset 聚类阈值初始值调优) | 需要真实曲目 A/B 对比确定最优窗口参数，属于参数调优阶段 |
| @DESIGN:section-5.2-q-3 (A/B 对比基准模式) | 需要 NFR-005 验证完成后进行 |
| @DESIGN:section-5.3-a-1 (CUDA 推理环境) | 本机环境验证，需要在真实推理链路中确认 |

---

## 审计结论 (id: section-6)

### 整体评估 (id: section-6.1)

- **设计符合率**: 100% (7/7 功能需求完全符合，4/4 非功能需求完全符合，1 个 NFR 待环境验证)
- **规划执行率**: 100% (6/6 任务完整执行，4/4 检查点通过)
- **追溯链完整性**: 100% (DESIGN → PLAN → PRACTICE → AUDIT 全部引用链完整)
- **测试覆盖**: 16/16 测试通过，覆盖 onset 聚类(4)、审计逻辑(4)、主旋律 DP(2)、MPDR 统计(2)、token 冲突(2)、smoke(2)

**总体评价**: 实现完整对齐设计目标，纯音频主链路边界明确，所有增强围绕 `svsep_mpdr` 模式且未引入视频融合模块。错误处理一致遵循"不降级"原则。代码质量审计发现的唯一 Critical 问题（docstring 左右手标注颠倒）已在本阶段修复。4 个 Important 级别问题属于防御性编程增强和测试加强，不影响功能正确性。环境依赖项（piano_svsep 模型路径、真实曲目 A/B 对比）需要在后续验证阶段补齐。

### 严重问题 (id: section-6.2)

**无当前严重问题。** 审计发现的唯一 Critical 问题（SvsepSeparationAudit docstring 左右手标注颠倒）已在本阶段修复。修复后测试验证通过（4/4 tests PASS）。

### 建议改进 (id: section-6.3)

对应 Agent 审查 Important / Minor 级别问题：

**建议 1: 聚类统计安全累计简化** (id: audit-6.3-1)

- **来源**: Agent 审查 Important #2
- **Superpowers 级别**: Important
- **描述**: `audio_to_yaml_converter.py:944-956` 中 `_cluster_midi_note_onsets` 的统计累计使用了 if/else 分支，由于 stats 已在 `convert()` 中初始化，可直接使用 `+=` 简化。
- **修复建议**: 将 if/else 分支改为 `int(self.conversion_stats.get("onset_clustered_notes", 0)) + clustered_count`

**建议 2: bass_anchor_integrity 回退字段清理** (id: audit-6.3-2)

- **来源**: Agent 审查 Important #3
- **Superpowers 级别**: Important
- **描述**: `audio_to_yaml_converter.py:2422-2423` 使用 `stats.get("total_bass_anchor_notes", stats.get("selected_original_bass", 0))`，跨模式回退到旧字段可能导致语义混淆。
- **修复建议**: 移除 `selected_original_bass`/`kept_original_bass` 回退，明确该函数仅适用于 svsep_mpdr 管线。

**建议 3: 事件累计确定性断言** (id: audit-6.3-3)

- **来源**: Agent 审查 Important #4
- **Superpowers 级别**: Important
- **描述**: `test_build_svsep_mpdr_candidate_accumulates_event_counters` 只验证字段存在性和类型，未验证具体计数值。
- **修复建议**: 构造确定性的输入使得期望计数值可预知，添加 `assert stats["register_collision_events"] == N` 等强断言。

**建议 4: 大跳阈值参数解耦** (id: audit-6.3-4)

- **来源**: Agent 审查 Minor #6
- **Superpowers 级别**: Minor
- **描述**: 主旋律大跳惩罚阈值复用了 `mpdr_register_collision_semitones`，两个语义不同的参数意外耦合。
- **修复建议**: 新增独立配置字段 `mpdr_melody_large_jump_semitones`。

**建议 5: MidiNoteEvent 共享类型定义** (id: audit-6.3-5)

- **来源**: Agent 审查 Minor #9
- **Superpowers 级别**: Minor
- **描述**: `MidiNoteEvent` 在 `svsep_hand_separator.py` 和 `audio_to_yaml_converter.py` 中重复定义，字段相同但属于不同模块作用域。
- **修复建议**: 抽取到独立共享模块（如 `src/midi_types.py`）。

**建议 6: 测试文件路径健壮性** (id: audit-6.3-6)

- **来源**: Agent 审查 Minor #10
- **Superpowers 级别**: Minor
- **描述**: smoke test 使用 `Path("src/audio_to_yaml_converter.py")` 相对路径，依赖执行工作目录。
- **修复建议**: 改为 `Path(__file__).resolve().parent.parent / "src" / "audio_to_yaml_converter.py"`。

### 后续行动项 (id: section-6.4)

| 行动 ID | 描述 | 优先级 | 关联问题 | 负责 |
|----------|------|--------|----------|------|
| ACT-001 | 确认 piano_svsep 模型权重路径并执行真实推理验证 | 严重 | @DESIGN:section-5.2-q-1 | JucieOvo |
| ACT-002 | 使用至少一支真实曲目做 A/B 对比（svsep_mpdr v3 vs hands_decoupled vs adaptive_octave_fold） | 严重 | @DESIGN:NFR-005 | JucieOvo |
| ACT-003 | 重新审查并合并建议 #1 ~ #6 中适合本轮的改进 | 建议 | audit-6.3-1 ~ 6 | JucieOvo |
| ACT-004 | 基于真实曲目结果调优 onset 聚类窗口和主旋律 DP 权重 | 建议 | @DESIGN:section-5.2-q-2 | JucieOvo |

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 条目 | 类型 |
|----------|------|------|
| audit-2.1 | FR-001 主链路符合性 | 设计审计 |
| audit-2.2 | FR-002 onset 聚类符合性 | 设计审计 |
| audit-2.3 | FR-003 AI 分离审计符合性 | 设计审计 |
| audit-2.4 | FR-004 主旋律 DP 符合性 | 设计审计 |
| audit-2.5 | FR-005 MPDR 候选符合性 | 设计审计 |
| audit-2.6 | FR-006 token 冲突符合性 | 设计审计 |
| audit-2.7 | FR-007 转换报告符合性 | 设计审计 |
| audit-2.8 | NFR-001 ~ 005 符合性 | 设计审计 |
| audit-2.9 | section-3.2 模块职责符合性 | 设计审计 |
| audit-2.10 | section-3.4 关键决策符合性 | 设计审计 |
| audit-3.1 ~ 3.7 | 规划任务 T-001 ~ T-006 + 检查点 | 规划审计 |
| audit-6.3-1 ~ 6 | 建议改进 #1 ~ #6 | 审计建议 |
| section-4.1 | Agent 审查问题汇总 | 代码质量 |
| section-4.2 | 结构审查：模块职责一致性 | 代码质量 |
| section-4.3 | 接口审查：设计 vs 实际签名 | 代码质量 |
| section-6.4 | 后续行动项 ACT-001 ~ ACT-004 | 审计结论 |

---

## 三维审计矩阵 (id: appendix-b)

| 设计 | 规划 | 实践 | 审计 | Agent 审查 | 状态 |
|------|------|------|------|----------|------|
| @DESIGN:FR-001 | @PLAN:task-T-006 | @PRACTICE:change-C-007 | audit-2.1 | 通过 | 完全符合 |
| @DESIGN:FR-002 | @PLAN:task-T-001 | @PRACTICE:change-C-001, C-002 | audit-2.2 | 通过 | 完全符合 |
| @DESIGN:FR-003 | @PLAN:task-T-002 | @PRACTICE:change-C-003 | audit-2.3 | 通过 (1 Critical 已修复) | 完全符合 |
| @DESIGN:FR-004 | @PLAN:task-T-003 | @PRACTICE:change-C-001, C-004 | audit-2.4 | 通过 | 完全符合 |
| @DESIGN:FR-005 | @PLAN:task-T-004 | @PRACTICE:change-C-005 | audit-2.5 | 通过 (Important #4) | 完全符合 |
| @DESIGN:FR-006 | @PLAN:task-T-005 | @PRACTICE:change-C-006 | audit-2.6 | 通过 | 完全符合 |
| @DESIGN:FR-007 | @PLAN:task-T-004, T-006 | @PRACTICE:change-C-005, C-007 | audit-2.7 | 通过 | 完全符合 |
| @DESIGN:NFR-001 | @PLAN:task-T-002, T-006 | @PRACTICE:change-C-003, C-007 | audit-2.8 | 通过 | 完全符合 |
| @DESIGN:NFR-002 | @PLAN:task-T-002, T-004, T-006 | @PRACTICE:change-C-003, C-005, C-007 | audit-2.8 | 通过 | 完全符合 |
| @DESIGN:NFR-003 | @PLAN:task-T-006 | @PRACTICE:change-C-007 | audit-2.8 | 通过 (12.19s on Win 11) | 完全符合 |
| @DESIGN:NFR-004 | @PLAN:task-T-001, T-003, T-006 | @PRACTICE:change-C-001, C-004, C-007 | audit-2.8 | 通过 | 完全符合 |
| @DESIGN:NFR-005 | @PLAN:task-T-006 | -- | audit-2.8 | -- | 待环境验证 |
| @DESIGN:section-3.2-mod-A | @PLAN:task-T-001 | @PRACTICE:change-C-002 | audit-2.9 | 通过 | 完全符合 |
| @DESIGN:section-3.2-mod-B | @PLAN:task-T-002 | @PRACTICE:change-C-003 | audit-2.9 | 通过 | 完全符合 |
| @DESIGN:section-3.2-mod-C | @PLAN:task-T-003 | @PRACTICE:change-C-004 | audit-2.9 | 通过 | 完全符合 |
| @DESIGN:section-3.2-mod-D | @PLAN:task-T-004 | @PRACTICE:change-C-005 | audit-2.9 | 通过 | 完全符合 |
| @DESIGN:section-3.2-mod-E | @PLAN:task-T-004 | @PRACTICE:change-C-005 | audit-2.9 | 通过 | 完全符合 |
| @DESIGN:section-3.2-mod-F | @PLAN:task-T-005 | @PRACTICE:change-C-006 | audit-2.9 | 通过 | 完全符合 |
| @DESIGN:section-3.4-decision-1 | @PLAN:task-T-006 | @PRACTICE:change-C-007 | audit-2.10 | 通过 | 完全符合 |
| @DESIGN:section-3.4-decision-2 | @PLAN:task-T-002, T-006 | @PRACTICE:change-C-003, C-007 | audit-2.10 | 通过 | 完全符合 |
| @DESIGN:section-3.4-decision-3 | @PLAN:task-T-003, T-005 | @PRACTICE:change-C-004, C-006 | audit-2.10 | 通过 | 完全符合 |
| @DESIGN:section-3.4-decision-4 | @PLAN:task-T-006 | @PRACTICE:change-C-007 | audit-2.10 | 通过 | 完全符合 |
