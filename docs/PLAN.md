---
stage: plan
title: SVSEP-MPDR v3 音频算法优化实施规划
author: JucieOvo
date: 2026-07-01
status: draft
---

# SVSEP-MPDR v3 音频算法优化实施规划

## 关联 Superpowers 工作文档 (id: section-sp-ref)

- **Superpowers 计划文档**: @SP:plan/2026-07-01-svsep-mpdr-v3
- **writing-plans 会话日期**: 2026-07-01
- **提炼者**: JucieOvo
- **说明**: 本文档是 Superpowers 计划的人类可读摘要。任务执行细节、测试步骤、子代理提示词见 `docs/superpowers/plans/2026-07-01-svsep-mpdr-v3.md`。

---

## 规划目标 (id: section-1.1)

将 @DESIGN:section-1.2 中定义的 7 个目标拆解为 6 个可由顶级模型子代理逐项执行的任务。规划重点是保持纯音频主链路，不接入演奏者视频、MediaPipe 手部跟踪或音视频融合模块。

### 阶段划分

- 阶段 1: MIDI 输入质量修正 -- 1 个任务，目标：在量化前完成 onset 聚类，减少同一和弦拆格。
- 阶段 2: AI 声部分离审计 -- 1 个任务，目标：让 piano_svsep 输出带有真实分离统计。
- 阶段 3: 主旋律与 MPDR 增强 -- 2 个任务，目标：增强主旋律 DP 与候选评分。
- 阶段 4: 输出冲突与报告完善 -- 2 个任务，目标：增强 token 冲突优先级、CLI 配置和质量报告。

---

## 任务依赖总览 (id: section-1.2)

```text
T-001 → T-003 → T-004 → T-005 → T-006
   ↘
    T-002 ────────────────↗
```

说明：

- T-001 提供 onset 聚类配置与测试基础。
- T-002 可在 T-001 后或与 T-003 前置并行，但 T-006 需要其审计统计稳定。
- T-003 依赖 T-001 的测试文件和配置工厂。
- T-004 依赖 T-003 的主旋律路径更稳定。
- T-005 依赖 T-004 的候选统计。
- T-006 依赖全部新增配置和报告字段。

---

## 检查点 (id: section-1.3)

### 检查点 1: MIDI 输入归一通过 (id: section-1.3-check-1)

- **位置**: T-001 完成后
- **验证**: `tests/test_audio_to_yaml_svsep_mpdr.py` 中 onset 聚类测试通过。
- **阻塞**: 未通过则不得进入主旋律与 MPDR 增强。

### 检查点 2: piano_svsep 审计结构通过 (id: section-1.3-check-2)

- **位置**: T-002 完成后
- **验证**: `tests/test_svsep_hand_separator_audit.py` 全部通过。
- **阻塞**: 未通过则不得把分离统计写入 conversion report。

### 检查点 3: MPDR 核心评分通过 (id: section-1.3-check-3)

- **位置**: T-004 完成后
- **验证**: 主旋律 DP、左手 bass anchor、全局统计相关测试全部通过。
- **阻塞**: 未通过则不得修改 token 冲突优先级。

### 检查点 4: 纯音频主链路确认 (id: section-1.3-check-4)

- **位置**: T-006 完成后
- **验证**: 测试确认 `src/audio_to_yaml_converter.py` 未 import `fusion_engine` 或 `dual_stream_extractor`。
- **阻塞**: 未通过则不得进入真实曲目验证。

---

## 任务摘要 (id: section-2)

### T-001: MIDI onset 聚类与配置 (id: task-T-001)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-002, @DESIGN:change-2, @DESIGN:section-3.2-mod-A |
| **Superpowers 任务** | @SP:plan/2026-07-01-svsep-mpdr-v3#task-1 |
| **描述** | 增加 onset 聚类配置和 `_cluster_midi_note_onsets`，在 MIDI 读取后、量化前修正近起音和弦。 |
| **依赖** | 无 |
| **产物** | `src/audio_to_yaml_converter.py:149`（修改）、`src/audio_to_yaml_converter.py:703`（修改）、`tests/test_audio_to_yaml_svsep_mpdr.py`（新增） |
| **验收标准** | onset 聚类测试通过，非法配置真实报错。 |

### T-002: piano_svsep 分离审计结构 (id: task-T-002)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-003, @DESIGN:change-3, @DESIGN:section-3.2-mod-B |
| **Superpowers 任务** | @SP:plan/2026-07-01-svsep-mpdr-v3#task-2 |
| **描述** | 为 `SvsepHandSeparator.separate` 增加真实分离审计结果，低质量匹配直接报错。 |
| **依赖** | 无 |
| **产物** | `src/svsep_hand_separator.py:24`（修改）、`src/audio_to_yaml_converter.py:886`（修改）、`tests/test_svsep_hand_separator_audit.py`（新增） |
| **验收标准** | 审计 dataclass 测试通过，调用方能写入 svsep 统计。 |

### T-003: 主旋律 DP 评分增强 (id: task-T-003)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-004, @DESIGN:change-4, @DESIGN:section-3.2-mod-C |
| **Superpowers 任务** | @SP:plan/2026-07-01-svsep-mpdr-v3#task-3 |
| **描述** | 扩展主旋律 DP 评分维度，加入 pitch、velocity、duration、强拍、连续性、大跳和重复惩罚。 |
| **依赖** | T-001 |
| **产物** | `src/audio_to_yaml_converter.py:149`（修改）、`src/audio_to_yaml_converter.py:1333`（修改）、`tests/test_audio_to_yaml_svsep_mpdr.py`（修改） |
| **验收标准** | 主旋律路径测试通过，孤立高跳不再压过连续旋律。 |

### T-004: MPDR 候选评分与审计指标增强 (id: task-T-004)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-005, @DESIGN:FR-007, @DESIGN:change-5, @DESIGN:change-7, @DESIGN:section-3.2-mod-D, @DESIGN:section-3.2-mod-E |
| **Superpowers 任务** | @SP:plan/2026-07-01-svsep-mpdr-v3#task-4 |
| **描述** | 增加 bass anchor、遮蔽风险、低频浑浊、重复抑制和全局质量统计。 |
| **依赖** | T-003 |
| **产物** | `src/audio_to_yaml_converter.py:1154`（修改）、`src/audio_to_yaml_converter.py:1526`（修改）、`src/audio_to_yaml_converter.py:2130`（修改） |
| **验收标准** | `bass_anchor_integrity` 等报告字段可由真实统计计算。 |

### T-005: token 冲突优先级增强 (id: task-T-005)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-006, @DESIGN:change-6, @DESIGN:section-3.2-mod-F |
| **Superpowers 任务** | @SP:plan/2026-07-01-svsep-mpdr-v3#task-5 |
| **描述** | 在同一物理 token 冲突时使用主旋律、低音锚点、右手非旋律、左手和声的确定优先级。 |
| **依赖** | T-004 |
| **产物** | `src/audio_to_yaml_converter.py:318`（修改）、`src/audio_to_yaml_converter.py:2047`（修改） |
| **验收标准** | token 冲突测试通过，主旋律和 bass anchor 不被高 keep_score 填充音挤掉。 |

### T-006: CLI 配置接线与最终 smoke 验证 (id: task-T-006)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-001, @DESIGN:FR-007, @DESIGN:NFR-002, @DESIGN:section-3.4-decision-4, @DESIGN:change-1, @DESIGN:change-7 |
| **Superpowers 任务** | @SP:plan/2026-07-01-svsep-mpdr-v3#task-6 |
| **描述** | 将新增配置接入 CLI 与配置构造，更新使用说明，并验证主链路没有 import 视频融合模块。 |
| **依赖** | T-001, T-002, T-003, T-004, T-005 |
| **产物** | `src/audio_to_yaml_converter.py`（CLI 区域修改）、`docs/audio_to_yaml_converter使用说明.md`（修改）、测试文件（修改） |
| **验收标准** | 新增配置可通过 CLI 传入，测试确认未接入 `fusion_engine` 或 `dual_stream_extractor`。 |

---

## 环境与工具 (id: section-3)

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.10 | 运行转换器、测试和本地脚本 |
| pretty_midi | 项目环境版本 | 读取与构造真实 MIDI note |
| PyYAML | 项目环境版本 | 读写 YAML 曲谱 |
| librosa | 项目环境版本 | BPM 与音频特征处理 |
| Demucs | 本机安装版本 | 真实音频分轨 |
| Transkun | 本机安装版本 | 真实钢琴音频转 MIDI |
| music21 | 项目环境版本 | MIDI 到 MusicXML 转换 |
| partitura | 项目环境版本 | MusicXML note_array 构建 |
| piano_svsep | 项目本地版本 | GNN 左右手声部分离 |
| torch / torch_geometric | 本机 CUDA 兼容版本 | piano_svsep 推理 |
| pytest | 项目环境版本 | 单元级真实逻辑测试 |

| 工具 | 版本 | 用途 |
|------|------|------|
| `/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe` | Python 3.10 | Windows 本机测试命令 |
| Bash | Git Bash | 执行非交互式命令 |

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 条目 | 供后续引用 |
|----------|------|-----------|
| task-T-001 | T-001 MIDI onset 聚类与配置 | @PLAN:task-T-001 |
| task-T-002 | T-002 piano_svsep 分离审计结构 | @PLAN:task-T-002 |
| task-T-003 | T-003 主旋律 DP 评分增强 | @PLAN:task-T-003 |
| task-T-004 | T-004 MPDR 候选评分与审计指标增强 | @PLAN:task-T-004 |
| task-T-005 | T-005 token 冲突优先级增强 | @PLAN:task-T-005 |
| task-T-006 | T-006 CLI 配置接线与最终 smoke 验证 | @PLAN:task-T-006 |
| section-1.3-check-1 | MIDI 输入归一通过 | @PLAN:section-1.3-check-1 |
| section-1.3-check-2 | piano_svsep 审计结构通过 | @PLAN:section-1.3-check-2 |
| section-1.3-check-3 | MPDR 核心评分通过 | @PLAN:section-1.3-check-3 |
| section-1.3-check-4 | 纯音频主链路确认 | @PLAN:section-1.3-check-4 |

---

## 设计覆盖矩阵 (id: appendix-b)

| 设计条目 | 锚点 | 对应任务 | 覆盖状态 |
|----------|------|----------|----------|
| 目标与非目标 | @DESIGN:section-1.2 | T-001, T-002, T-003, T-004, T-005, T-006 | 已覆盖 |
| 范围边界 | @DESIGN:section-1.3 | T-006 | 已覆盖 |
| SVSEP-MPDR v3 主链路 | @DESIGN:FR-001 | T-006 | 已覆盖 |
| MIDI onset 聚类与量化校正 | @DESIGN:FR-002 | T-001 | 已覆盖 |
| AI 左右手分离与审计 | @DESIGN:FR-003 | T-002 | 已覆盖 |
| 主旋律 DP 路径追踪 | @DESIGN:FR-004 | T-003 | 已覆盖 |
| MPDR 感知密度候选生成与精排 | @DESIGN:FR-005 | T-004 | 已覆盖 |
| 36 键折叠与 token 冲突消解 | @DESIGN:FR-006 | T-005 | 已覆盖 |
| 输出真实转换报告 | @DESIGN:FR-007 | T-004, T-006 | 已覆盖 |
| 真实执行与失败透明 | @DESIGN:NFR-001 | T-002, T-006 | 已覆盖 |
| 可审计性 | @DESIGN:NFR-002 | T-002, T-004, T-006 | 已覆盖 |
| Windows 本机兼容 | @DESIGN:NFR-003 | T-006 | 已覆盖 |
| 参数可配置 | @DESIGN:NFR-004 | T-001, T-003, T-006 | 已覆盖 |
| 真实曲目验证 | @DESIGN:NFR-005 | T-006 | 已覆盖 |
| MidiEventNormalizer | @DESIGN:section-3.2-mod-A | T-001 | 已覆盖 |
| SvsepHandSeparator | @DESIGN:section-3.2-mod-B | T-002 | 已覆盖 |
| MelodyPathTracker | @DESIGN:section-3.2-mod-C | T-003 | 已覆盖 |
| MpdrCandidateBuilder | @DESIGN:section-3.2-mod-D | T-004 | 已覆盖 |
| MpdrCandidateReranker | @DESIGN:section-3.2-mod-E | T-004 | 已覆盖 |
| GameKeyboardTokenEmitter | @DESIGN:section-3.2-mod-F | T-005 | 已覆盖 |
| 选择 svsep_mpdr 主链路 | @DESIGN:section-3.4-decision-1 | T-006 | 已覆盖 |
| 不提供静默降级 | @DESIGN:section-3.4-decision-2 | T-002, T-006 | 已覆盖 |
| 主旋律保护优先 | @DESIGN:section-3.4-decision-3 | T-003, T-005 | 已覆盖 |
| 视频融合延后并保持纯音频主链路 | @DESIGN:section-3.4-decision-4 | T-006 | 已覆盖 |
| 扩展音频算法配置 | @DESIGN:change-1 | T-001, T-003, T-006 | 已覆盖 |
| 增加 MIDI 事件归一与 onset 聚类 | @DESIGN:change-2 | T-001 | 已覆盖 |
| 增强 piano_svsep 分离审计 | @DESIGN:change-3 | T-002 | 已覆盖 |
| 增强主旋律 DP 追踪 | @DESIGN:change-4 | T-003 | 已覆盖 |
| 增强 MPDR 候选选择 | @DESIGN:change-5 | T-004 | 已覆盖 |
| 增强折叠与 token 冲突统计 | @DESIGN:change-6 | T-005 | 已覆盖 |
| 扩展全局质量评分与报告 | @DESIGN:change-7 | T-004, T-006 | 已覆盖 |
