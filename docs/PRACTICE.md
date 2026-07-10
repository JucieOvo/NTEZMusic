---
stage: practice
title: SVSEP-MPDR v3 实践报告
author: JucieOvo
date: 2026-07-01
status: draft
---

# SVSEP-MPDR v3 实践报告

## 关联 Agent 工作记录 (id: section-sp-ref)

- **Git 分支**: 不适用（项目未初始化 git 仓库）
- **Superpowers 计划**: @SP:plan/2026-07-01-svsep-mpdr-v3
- **执行方式**: subagent-driven-development（6 个子代理逐任务执行）
- **提炼者**: JucieOvo
- **说明**: 本文档是 Superpowers 计划执行的人类可读摘要。完整代码 diff 无法通过 git 获取，变更明细见本文档第 5 节产物清单。

---

## 执行范围 (id: section-1.1)

### 覆盖的规划任务

- @PLAN:task-T-001: MIDI onset 聚类与配置
- @PLAN:task-T-002: piano_svsep 分离审计结构
- @PLAN:task-T-003: 主旋律 DP 评分增强
- @PLAN:task-T-004: MPDR 候选评分与审计指标增强
- @PLAN:task-T-005: token 冲突优先级增强
- @PLAN:task-T-006: CLI 配置接线与最终 smoke 验证

### 未覆盖（推迟）

无。全部 6 个规划任务均已执行。

### 执行时间线

| 时间 | 事件 |
|------|------|
| 2026-07-01 | T-001 启动并完成 |
| 2026-07-01 | T-002 启动并完成 |
| 2026-07-01 | T-003 启动并完成 |
| 2026-07-01 | T-004 启动并完成 |
| 2026-07-01 | T-005 启动并完成 |
| 2026-07-01 | T-006 启动并完成 |
| 2026-07-01 | 全部 16 项测试通过，实践完成 |

### 执行顺序

按依赖拓扑排序逐任务串行执行，未出现阻塞或回退：

```
T-001 -> T-002 -> T-003 -> T-004 -> T-005 -> T-006
```

T-001 与 T-002 理论上可并行，但遵循 subagent-driven-development 规程串行执行以避免潜在文件冲突。

---

## 执行环境 (id: section-1.2)

| 依赖 | 规划版本 | 实际版本 | 偏差 |
|------|----------|----------|------|
| Python | 3.10 | 3.10.11 | 无 |
| pretty_midi | 项目环境版本 | 项目环境版本 | 无 |
| PyYAML | 项目环境版本 | 项目环境版本 | 无 |
| pytest | 项目环境版本 | 8.3.4 | 无 |
| torch | 本机 CUDA 兼容版本 | 本机 CUDA 兼容版本 | 无 |
| torch_geometric | 本机 CUDA 兼容版本 | 本机 CUDA 兼容版本 | 无 |
| piano_svsep | 项目本地版本 | 项目本地版本 | 无 |

---

## 执行记录 (id: section-2)

### C-001: 扩展 AudioPipelineConfig 配置字段 (id: change-C-001)

- **关联任务**: @PLAN:task-T-001, @PLAN:task-T-003
- **关联设计**: @DESIGN:change-1, @DESIGN:FR-002, @DESIGN:FR-004
- **变更意图** (形式 A):
  - **操作类型**: 修改
  - **目标**: `src/audio_to_yaml_converter.py:215-311`
  - **内容**: 在 AudioPipelineConfig dataclass 中新增 onset 聚类配置（3 个字段）和主旋律 DP 评分权重（7 个字段），全部带默认值。
  - **原因**: 统一管理算法参数，避免硬编码魔法数字，使参数可通过 CLI 调节。
  - **影响**: CLI 参数解析、AudioPipelineConfig 构造、各消费方法。

- **变更内容** (形式 B):

```diff
## `src/audio_to_yaml_converter.py:215-217` -- docstring 新增
+ onset_cluster_enabled (bool): 是否启用 MIDI 起音聚类
+ onset_cluster_window_beats (float): 起音聚类窗口拍数
+ onset_cluster_max_span_beats (float): 起音聚类最大跨度拍数

## `src/audio_to_yaml_converter.py:227-233` -- docstring 新增
+ mpdr_melody_pitch_weight (float): 主旋律音高显著性权重
+ mpdr_melody_duration_weight (float): 主旋律时值权重
+ mpdr_melody_velocity_weight (float): 主旋律力度权重
+ mpdr_melody_beat_weight (float): 主旋律强拍匹配权重
+ mpdr_melody_continuity_weight (float): 主旋律连续性转移权重
+ mpdr_melody_large_jump_penalty (float): 主旋律大跳惩罚权重
+ mpdr_melody_repetition_penalty (float): 主旋律同音重复惩罚权重

## `src/audio_to_yaml_converter.py:293-295` -- dataclass 字段（onset 聚类）
+ onset_cluster_enabled: bool = True
+ onset_cluster_window_beats: float = 0.08
+ onset_cluster_max_span_beats: float = 0.12

## `src/audio_to_yaml_converter.py:305-311` -- dataclass 字段（主旋律权重）
+ mpdr_melody_pitch_weight: float = 1.0
+ mpdr_melody_duration_weight: float = 0.35
+ mpdr_melody_velocity_weight: float = 0.45
+ mpdr_melody_beat_weight: float = 0.20
+ mpdr_melody_continuity_weight: float = 0.70
+ mpdr_melody_large_jump_penalty: float = 0.35
+ mpdr_melody_repetition_penalty: float = 0.20
```

- **影响范围**:
  - `src/audio_to_yaml_converter.py:215-217,227-233` (docstring)
  - `src/audio_to_yaml_converter.py:293-295` (onset 聚类字段)
  - `src/audio_to_yaml_converter.py:305-311` (主旋律权重字段)
  - `src/audio_to_yaml_converter.py:839-956` (_cluster_midi_note_onsets 消费)
  - `src/audio_to_yaml_converter.py:1562-1571` (_extract_primary_melody_path 消费)
  - `src/audio_to_yaml_converter.py:1626-1629` (_score_primary_melody_note 消费)
  - `src/audio_to_yaml_converter.py:4112-4121` (main 构造)

- **验证方式**: 构造 AudioPipelineConfig 实例，断言所有新字段具有预期默认值并通过配置校验。

---

### C-002: 实现 MIDI onset 聚类方法 (id: change-C-002)

- **关联任务**: @PLAN:task-T-001
- **关联设计**: @DESIGN:change-2, @DESIGN:FR-002, @DESIGN:section-3.3-flow-2

- **变更意图** (形式 A):
  - **操作类型**: 新增
  - **目标**: `src/audio_to_yaml_converter.py:839-956`
  - **内容**: 新增 `_cluster_midi_note_onsets` 方法，在 MIDI note 读取后、量化前对毫秒级接近的起音执行按 velocity 加权平均聚类。同时修改 `_read_midi_notes` 调用链路并在 `conversion_stats` 中初始化聚类统计字段。
  - **原因**: 避免同一物理和弦被 Transkun 转录的毫秒级起音误差拆散到不同量化格。
  - **影响**: `_read_midi_notes` 返回值、所有后续压缩模式的输入一致性。

- **变更内容** (形式 B):

```diff
## `src/audio_to_yaml_converter.py:761-762` -- _read_midi_notes 调用点
+ clustered_notes = self._cluster_midi_note_onsets(midi_notes=sorted_notes, config=config)
+ return tuple(sorted(clustered_notes, key=lambda item: (item.start_beat, item.pitch)))

## `src/audio_to_yaml_converter.py:839-956` -- 新增方法
+ def _cluster_midi_note_onsets(self, midi_notes, config):
+     - 若 onset_cluster_enabled=False 或输入为空，原样返回
+     - onset_cluster_window_beats < 0 时抛 ValueError
+     - onset_cluster_max_span_beats < onset_cluster_window_beats 时抛 ValueError
+     - 按 start_beat 排序扫描：span <= max_span 且 gap <= window → 同组
+     - cluster_start_beat = velocity 加权平均（velocity 总和=0 时用算术平均）
+     - 每个 note 的 end_beat 按 delta 平移，duration 下限 = quantize_beat * 0.25
+     - 返回按 (start_beat, pitch) 排序的 tuple
+     - 安全累计 conversion_stats["onset_clustered_notes"] 和 ["onset_clustered_groups"]

## `src/audio_to_yaml_converter.py:693-694` -- conversion_stats 初始化
+ "onset_clustered_notes": 0,
+ "onset_clustered_groups": 0,
```

- **影响范围**:
  - `src/audio_to_yaml_converter.py:693-694` (stats 初始化)
  - `src/audio_to_yaml_converter.py:761-762` (调用点)
  - `src/audio_to_yaml_converter.py:839-956` (方法实现)

- **验证方式**: `tests/test_audio_to_yaml_svsep_mpdr.py` 中 4 项 onset 聚类测试全部通过，覆盖近起音和弦合并、分解和弦保留、非法配置报错、真实 pretty_midi 文件读取链路。

---

### C-003: 新增 piano_svsep 分离审计结构 (id: change-C-003)

- **关联任务**: @PLAN:task-T-002
- **关联设计**: @DESIGN:change-3, @DESIGN:FR-003, @DESIGN:section-3.2-mod-B

- **变更意图** (形式 A):
  - **操作类型**: 新增 + 修改
  - **目标**: `src/svsep_hand_separator.py:390-439` (新增 dataclass), `src/svsep_hand_separator.py:97-386` (separate 方法)
  - **内容**: 新增 `SvsepSeparationAudit` 和 `SvsepSeparationResult` 两个冻结 dataclass。修改 `separate()` 返回类型从 `tuple[list, list]` 改为 `SvsepSeparationResult`，在推理成功后构造审计对象并调用 `audit.validate()`。修改 `audio_to_yaml_converter._separate_hands_with_svsep` 适配新返回结构，将审计统计写入 `conversion_stats`。
  - **原因**: 让 piano_svsep 声部分离返回可验证的真实统计，使低质量匹配直接报错而非静默产出不可信结果。
  - **影响**: `src/audio_to_yaml_converter.py:1055-1073`、conversion report。

- **变更内容** (形式 B):

```diff
## `src/svsep_hand_separator.py:390-439` -- 新增 dataclass
+ @dataclass(frozen=True)
+ class SvsepSeparationAudit:
+     total_note_array_count: int
+     predicted_staff_count: int
+     matched_original_note_count: int
+     unmatched_original_note_count: int
+     left_note_count: int
+     right_note_count: int
+     unknown_staff_count: int
+     match_ratio: property → 匹配数 / 总数 (0-1)
+     validate(): match_ratio < 0.95 抛 ValueError；
+                左右手全空抛 ValueError；
+                unknown_staff > 0 抛 ValueError

+ @dataclass(frozen=True)
+ class SvsepSeparationResult:
+     left_notes: list[MidiNoteEvent]
+     right_notes: list[MidiNoteEvent]
+     audit: SvsepSeparationAudit

## `src/svsep_hand_separator.py:97` -- separate() 返回值
- 返回 tuple[list, list]
+ 返回 SvsepSeparationResult，内部构造审计对象并调用 validate()

## `src/audio_to_yaml_converter.py:1055-1073` -- 调用方适配
- left_notes, right_notes = separator.separate(...)
+ result = separator.separate(...)
+ result.left_notes / result.right_notes / result.audit
+ conversion_stats 新增 6 个 svsep 审计字段
```

- **影响范围**:
  - `src/svsep_hand_separator.py:390-454` (新增 dataclass)
  - `src/svsep_hand_separator.py:97-386` (separate 修改)
  - `src/audio_to_yaml_converter.py:1055-1073` (调用点适配)

- **验证方式**: `tests/test_svsep_hand_separator_audit.py` 中 4 项纯逻辑测试全部通过，覆盖正常分布通过校验、低匹配比例被拒、空左右手被拒、未知 staff 标签被拒。

---

### C-004: 增强主旋律 DP 评分 (id: change-C-004)

- **关联任务**: @PLAN:task-T-003
- **关联设计**: @DESIGN:change-4, @DESIGN:FR-004, @DESIGN:section-3.2-mod-C

- **变更意图** (形式 A):
  - **操作类型**: 修改
  - **目标**: `src/audio_to_yaml_converter.py:1562-1629`
  - **内容**: `_score_primary_melody_note` 从混用 `mpdr_melody_protection_strength`/`mpdr_voice_leading_weight` 等非专属权重，改为使用 4 个专属字段（pitch / duration / velocity / beat）。`_extract_primary_melody_path` 转移分数改用 `mpdr_melody_continuity_weight`，并加入大跳惩罚（interval > collision_semitones）和重复惩罚（同音高在 `repeat_suppression_beats` 内连续出现）。
  - **原因**: 降低高位和声音或装饰音误判为主旋律的概率，让 DP 更偏好连续旋律线。
  - **影响**: MPDR 主旋律保护、候选评分、精排统计。

- **变更内容** (形式 B):

```diff
## `src/audio_to_yaml_converter.py:1562` -- 转移连续性权重
- + config.mpdr_voice_leading_weight * continuity_score
+ + config.mpdr_melody_continuity_weight * continuity_score

## `src/audio_to_yaml_converter.py:1565-1566` -- 大跳惩罚
+ if interval > config.mpdr_register_collision_semitones:
+     candidate_score -= config.mpdr_melody_large_jump_penalty

## `src/audio_to_yaml_converter.py:1569-1571` -- 重复惩罚
+ if note.pitch == previous_note.pitch:
+     time_gap = abs(note.start_beat - previous_note.start_beat)
+     if time_gap <= config.mpdr_repeat_suppression_beats:
+         candidate_score -= config.mpdr_melody_repetition_penalty

## `src/audio_to_yaml_converter.py:1626-1629` -- 基础评分改用专属字段
- config.mpdr_melody_protection_strength * pitch_score
- + config.mpdr_duration_weight * duration_score
- + config.mpdr_velocity_weight * velocity_score
- + config.mpdr_voice_leading_weight * beat_score
+ config.mpdr_melody_pitch_weight * pitch_score
+ + config.mpdr_melody_duration_weight * duration_score
+ + config.mpdr_melody_velocity_weight * velocity_score
+ + config.mpdr_melody_beat_weight * beat_score
```

- **影响范围**:
  - `src/audio_to_yaml_converter.py:1562` (连续性)
  - `src/audio_to_yaml_converter.py:1565-1566` (大跳)
  - `src/audio_to_yaml_converter.py:1569-1571` (重复)
  - `src/audio_to_yaml_converter.py:1626-1629` (基础评分)

- **验证方式**: `tests/test_audio_to_yaml_svsep_mpdr.py` 中 2 项主旋律 DP 测试通过。`test_extract_primary_melody_prefers_longer_louder_continuous_path` 验证连续旋律线优于短促高装饰音；`test_extract_primary_melody_penalizes_isolated_high_jump` 验证大跳惩罚排除孤立高跳装饰音。

---

### C-005: 增强 MPDR 候选统计与审计指标 (id: change-C-005)

- **关联任务**: @PLAN:task-T-004
- **关联设计**: @DESIGN:change-5, @DESIGN:change-7, @DESIGN:FR-005, @DESIGN:FR-007

- **变更意图** (形式 A):
  - **操作类型**: 修改
  - **目标**: `src/audio_to_yaml_converter.py:1366-1432,2422-2423`
  - **内容**: 在 `_build_svsep_mpdr_candidate` 的 stats 字典中新增 `total_bass_anchor_notes`、`kept_bass_anchor_notes`、`low_mud_penalty_events`、`register_collision_events`、`onset_collision_events` 共 5 个审计字段。在主循环中按真实数据累计：每个有左手音符的时间片按最低音累计 bass anchor 计数，每个左手音符按 melody pitch 距离、起音同步性和低音浑浊条件累计惩罚事件。在 `_score_svsep_mpdr_stats` 中新增 `bass_anchor_integrity` 指标写入。
  - **原因**: 让主旋律遮挡、低音锚点保留、和声遮蔽和频段浑浊等维度可量化审计。
  - **影响**: conversion report 字段、A/B 对比和调参依据。

- **变更内容** (形式 B):

```diff
## `src/audio_to_yaml_converter.py:1366-1374` -- stats 初始化
+ "svsep_mpdr_token_conflict_dropped_notes": 0,
+ "total_bass_anchor_notes": 0,
+ "kept_bass_anchor_notes": 0,
+ "low_mud_penalty_events": 0,
+ "register_collision_events": 0,
+ "onset_collision_events": 0,

## `src/audio_to_yaml_converter.py:1402-1404` -- bass anchor 累计
+ stats["total_bass_anchor_notes"] += 1          # 每个有左手的时间片
+ if any(c.note.pitch == bass_pitch ...):         # 最低音被保留
+     stats["kept_bass_anchor_notes"] += 1

## `src/audio_to_yaml_converter.py:1426-1432` -- 惩罚事件累计
+ 当 masking_risk > 0 且音高接近主旋律 → register_collision_events
+ 当左手与主旋律同时间片起音 → onset_collision_events
+ 当左手低音 <= mpdr_low_mud_pitch 且密度 >= 3 → low_mud_penalty_events

## `src/audio_to_yaml_converter.py:2422-2423` -- bass_anchor_integrity
+ total_bass_anchor = stats.get("total_bass_anchor_notes", ...)
+ kept_bass_anchor = stats.get("kept_bass_anchor_notes", ...)
+ bass_anchor_integrity = kept / total (total=0 时为 1.0)
+ stats["bass_anchor_integrity"] = round(...)
```

- **影响范围**:
  - `src/audio_to_yaml_converter.py:1366-1374` (stats 初始化)
  - `src/audio_to_yaml_converter.py:1402-1404` (bass anchor 累计)
  - `src/audio_to_yaml_converter.py:1426-1432` (惩罚事件累计)
  - `src/audio_to_yaml_converter.py:1469-1470` (token 冲突丢弃累计)
  - `src/audio_to_yaml_converter.py:2422-2423` (bass_anchor_integrity)

- **验证方式**: `tests/test_audio_to_yaml_svsep_mpdr.py` 中 2 项 MPDR 统计测试通过。`test_score_svsep_mpdr_stats_reports_bass_anchor_integrity` 验证 bass_anchor_integrity=0.75 正确写入；`test_build_svsep_mpdr_candidate_accumulates_event_counters` 验证各事件计数器端到端累计正确。

---

### C-006: 增强 token 冲突优先级 (id: change-C-006)

- **关联任务**: @PLAN:task-T-005
- **关联设计**: @DESIGN:change-6, @DESIGN:FR-006, @DESIGN:section-3.2-mod-F, @DESIGN:section-3.4-decision-3

- **变更意图** (形式 A):
  - **操作类型**: 修改
  - **目标**: `src/audio_to_yaml_converter.py:356-368,2251-2302`
  - **内容**: 在 `MpdrNoteCandidate` 中新增 `is_bass_anchor: bool = False` 字段。新增 `_mpdr_candidate_priority` 静态方法，按主旋律(5) > 低音锚点(4) > 右手非旋律(3) > 左手和声(2) > 其他(1) 的优先级排序。修改 `_candidates_to_modifier_safe_tokens` 的冲突消解从仅比较 keep_score 改为比较 `_mpdr_candidate_priority` 元组。在 `_select_mpdr_left_notes` 中为 bass_candidate 标记 `is_bass_anchor=True`。
  - **原因**: 36 键压缩不可逆，主旋律和低音锚点不应被高 keep_score 的填充音挤掉。
  - **影响**: YAML 输出质量、token 冲突统计、conversion report。

- **变更内容** (形式 B):

```diff
## `src/audio_to_yaml_converter.py:356,368` -- MpdrNoteCandidate
+ is_bass_anchor (bool): 是否为左手低音锚点
+ is_bass_anchor: bool = False

## `src/audio_to_yaml_converter.py:1881-1893` -- bass_candidate 标记
+ bass_candidate 重建为 MpdrNoteCandidate(..., is_bass_anchor=True)

## `src/audio_to_yaml_converter.py:2251-2270` -- 新增优先级方法
+ @staticmethod
+ def _mpdr_candidate_priority(candidate):
+     主旋律 → rank=5
+     低音锚点 → rank=4
+     右手非旋律 → rank=3
+     左手和声 → rank=2
+     其他 → rank=1
+     同 rank 按 (keep_score, utility, -pitch) 区分

## `src/audio_to_yaml_converter.py:2302` -- 冲突消解
- if existing is None or candidate.keep_score > existing[1].keep_score:
+ if existing is None or _mpdr_candidate_priority(candidate) > _mpdr_candidate_priority(existing[1]):
```

- **影响范围**:
  - `src/audio_to_yaml_converter.py:356,368` (MpdrNoteCandidate 字段)
  - `src/audio_to_yaml_converter.py:1881-1893` (bass_candidate 标记)
  - `src/audio_to_yaml_converter.py:2251-2270` (优先级方法)
  - `src/audio_to_yaml_converter.py:2298-2302` (冲突消解)
  - `src/audio_to_yaml_converter.py:1469-1470` (冲突丢弃统计)

- **验证方式**: `tests/test_audio_to_yaml_svsep_mpdr.py` 中 2 项 token 冲突测试通过。`test_candidates_to_modifier_safe_tokens_prefers_primary_melody_on_same_token` 验证主旋律（keep_score=1.0）优先于填充音（keep_score=10.0）占用同一 token；`test_candidates_to_modifier_safe_tokens_prefers_bass_anchor_over_left_harmony` 验证低音锚点优先于同声部高 keep_score 和声。

---

### C-007: CLI 配置接线与 smoke 验证 (id: change-C-007)

- **关联任务**: @PLAN:task-T-006
- **关联设计**: @DESIGN:change-1, @DESIGN:change-7, @DESIGN:FR-001, @DESIGN:NFR-002, @DESIGN:section-3.4-decision-4

- **变更意图** (形式 A):
  - **操作类型**: 修改
  - **目标**: `src/audio_to_yaml_converter.py:4024-4035,4112-4121` + `docs/audio_to_yaml_converter使用说明.md`
  - **内容**: 在 `parse_arguments()` 中新增 10 个 CLI 参数（`--disable-onset-cluster`、`--onset-cluster-window-beats`、`--onset-cluster-max-span-beats` 及 7 个 `--mpdr-melody-*` 权重参数）。在 `main()` 的 `AudioPipelineConfig(...)` 构造中接入全部新增参数。在 `docs/audio_to_yaml_converter使用说明.md` 中新增 "SVSEP-MPDR v3 新增参数" 章节。追加 2 项 smoke test：验证纯音频主链路未导入 `fusion_engine`/`dual_stream_extractor`，验证 `_score_svsep_mpdr_stats` 写入全部 5 个必需质量指标。
  - **原因**: 使新增配置可通过 CLI 传入，保持纯音频主链路边界，为真实 A/B 对比和调参提供可审计证据。
  - **影响**: CLI 使用体验、配置构造、使用说明文档。

- **变更内容** (形式 B):

```diff
## `src/audio_to_yaml_converter.py:4024-4035` -- CLI 参数新增
+ --disable-onset-cluster            (flag, 禁用起音聚类)
+ --onset-cluster-window-beats       (float, 默认 0.08)
+ --onset-cluster-max-span-beats     (float, 默认 0.12)
+ --mpdr-melody-pitch-weight         (float, 默认 1.0)
+ --mpdr-melody-duration-weight      (float, 默认 0.35)
+ --mpdr-melody-velocity-weight      (float, 默认 0.45)
+ --mpdr-melody-beat-weight          (float, 默认 0.20)
+ --mpdr-melody-continuity-weight    (float, 默认 0.70)
+ --mpdr-melody-large-jump-penalty   (float, 默认 0.35)
+ --mpdr-melody-repetition-penalty   (float, 默认 0.20)

## `src/audio_to_yaml_converter.py:4112-4121` -- config 构造
+ onset_cluster_enabled=not arguments.disable_onset_cluster,
+ onset_cluster_window_beats=..., onset_cluster_max_span_beats=...,
+ mpdr_melody_pitch_weight=..., mpdr_melody_duration_weight=...,
+ mpdr_melody_velocity_weight=..., mpdr_melody_beat_weight=...,
+ mpdr_melody_continuity_weight=..., mpdr_melody_large_jump_penalty=...,
+ mpdr_melody_repetition_penalty=...,
```

- **影响范围**:
  - `src/audio_to_yaml_converter.py:4024-4035` (CLI)
  - `src/audio_to_yaml_converter.py:4112-4121` (config 构造)
  - `docs/audio_to_yaml_converter使用说明.md` (文档更新)
  - `tests/test_audio_to_yaml_svsep_mpdr.py` (smoke tests)

- **验证方式**: 2 项 smoke test 通过。`test_svsep_mpdr_design_does_not_import_video_fusion_modules` 验证源文件不包含视频融合模块导入；`test_score_report_contains_required_svsep_mpdr_metric_names` 验证 5 个必需质量指标（melody_integrity、bass_anchor_integrity、masking_avoidance、harmonic_completeness、register_clarity）全部写入 stats。

---

## 偏差记录 (id: section-3)

### 与规划的偏差 (id: section-3.1)

**无偏差**。全部 6 个任务按规划顺序、依赖关系和内容要求执行完成，未出现计划外变更、顺序调整、任务拆分/合并或任务跳过。

### 与设计的偏差 (id: section-3.2)

**无偏差**。所有实现与 DESIGN.md 中定义的模块职责、接口约定、关键决策和变更目标一致。具体对齐情况见附录 B 执行覆盖矩阵。

---

## 验证结果 (id: section-4)

### 验收标准结果 (id: section-4.1)

| 任务 | 验收标准 | 结果 | 证据 |
|------|----------|------|------|
| @PLAN:task-T-001 | onset 聚类测试通过，非法配置真实报错 | 通过 | 4/4 测试 PASS |
| @PLAN:task-T-002 | 审计 dataclass 测试通过，调用方能写入 svsep 统计 | 通过 | 4/4 测试 PASS |
| @PLAN:task-T-003 | 主旋律路径测试通过，孤立高跳不压过连续旋律 | 通过 | 2/2 测试 PASS |
| @PLAN:task-T-004 | bass_anchor_integrity 等报告字段可由真实统计计算 | 通过 | 2/2 测试 PASS |
| @PLAN:task-T-005 | token 冲突测试通过，主旋律和 bass anchor 不被挤掉 | 通过 | 2/2 测试 PASS |
| @PLAN:task-T-006 | 新增配置可通过 CLI 传入，测试确认未接入视频融合模块 | 通过 | 2/2 测试 PASS |
| @PLAN:section-1.3-check-1 | MIDI 输入归一通过 | 通过 | T-001 4 测试 PASS |
| @PLAN:section-1.3-check-2 | piano_svsep 审计结构通过 | 通过 | T-002 4 测试 PASS |
| @PLAN:section-1.3-check-3 | MPDR 核心评分通过 | 通过 | T-003+T-004 4 测试 PASS |
| @PLAN:section-1.3-check-4 | 纯音频主链路确认 | 通过 | T-006 smoke test PASS |

### 已知问题 (id: section-4.2)

**无已知问题**。全部测试通过，语法检查零错误。piano_svsep 模型真实推理验证需要本机模型权重路径——该路径为环境依赖（见 @DESIGN:section-5.2-q-1），不属于代码实现范畴。

---

## 产物清单 (id: section-5)

### 文件变更统计 (id: section-5.1)

| 类型 | 数量 |
|------|------|
| 修改文件 | 3 |
| 新增文件 | 2 |

### 变更明细 (id: section-5.2)

| 文件 | 操作 | 关联变更 ID |
|------|------|------------|
| `src/audio_to_yaml_converter.py:149-311` (AudioPipelineConfig) | 修改 | C-001 |
| `src/audio_to_yaml_converter.py:693-694` (stats 初始化) | 修改 | C-002 |
| `src/audio_to_yaml_converter.py:761-762` (_read_midi_notes) | 修改 | C-002 |
| `src/audio_to_yaml_converter.py:839-956` (_cluster_midi_note_onsets) | 新增方法 | C-002 |
| `src/audio_to_yaml_converter.py:1055-1073` (_separate_hands_with_svsep) | 修改 | C-003 |
| `src/audio_to_yaml_converter.py:1366-1432` (_build_svsep_mpdr_candidate stats) | 修改 | C-005 |
| `src/audio_to_yaml_converter.py:1469-1470` (token 冲突丢弃累计) | 修改 | C-005 |
| `src/audio_to_yaml_converter.py:1562-1571` (_extract_primary_melody_path) | 修改 | C-004 |
| `src/audio_to_yaml_converter.py:1626-1629` (_score_primary_melody_note) | 修改 | C-004 |
| `src/audio_to_yaml_converter.py:1881-1893` (_select_mpdr_left_notes bass) | 修改 | C-006 |
| `src/audio_to_yaml_converter.py:2251-2270` (_mpdr_candidate_priority) | 新增方法 | C-006 |
| `src/audio_to_yaml_converter.py:2298-2302` (_candidates_to_modifier_safe_tokens) | 修改 | C-006 |
| `src/audio_to_yaml_converter.py:2422-2423` (_score_svsep_mpdr_stats) | 修改 | C-005 |
| `src/audio_to_yaml_converter.py:4024-4035` (CLI) | 修改 | C-007 |
| `src/audio_to_yaml_converter.py:4112-4121` (main) | 修改 | C-007 |
| `src/audio_to_yaml_converter.py:356,368` (MpdrNoteCandidate) | 修改 | C-006 |
| `src/svsep_hand_separator.py:97-386` (separate) | 修改 | C-003 |
| `src/svsep_hand_separator.py:390-454` (审计 dataclass) | 新增 | C-003 |
| `tests/test_audio_to_yaml_svsep_mpdr.py` | 新增 | C-002, C-004, C-005, C-006, C-007 |
| `tests/test_svsep_hand_separator_audit.py` | 新增 | C-003 |
| `docs/audio_to_yaml_converter使用说明.md` | 修改 | C-007 |

### 依赖变更 (id: section-5.3)

**无新增外部依赖**。所有变更基于项目现有依赖（pretty_midi、pytest、dataclasses）完成，未引入新的第三方库。

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 条目 | 供后续引用 |
|----------|------|-----------|
| change-C-001 | 扩展 AudioPipelineConfig 配置字段 | @PRACTICE:change-C-001 |
| change-C-002 | 实现 MIDI onset 聚类方法 | @PRACTICE:change-C-002 |
| change-C-003 | 新增 piano_svsep 分离审计结构 | @PRACTICE:change-C-003 |
| change-C-004 | 增强主旋律 DP 评分 | @PRACTICE:change-C-004 |
| change-C-005 | 增强 MPDR 候选统计与审计指标 | @PRACTICE:change-C-005 |
| change-C-006 | 增强 token 冲突优先级 | @PRACTICE:change-C-006 |
| change-C-007 | CLI 配置接线与 smoke 验证 | @PRACTICE:change-C-007 |
| section-4.1 | 验收标准结果 | @PRACTICE:section-4.1 |
| section-4.2 | 已知问题 | @PRACTICE:section-4.2 |

---

## 执行覆盖矩阵 (id: appendix-b)

| 规划任务 | 对应变更 | 完成状态 | 备注 |
|----------|----------|----------|------|
| @PLAN:task-T-001 | C-001, C-002 | 完成 | onset 聚类配置 + 方法实现 |
| @PLAN:task-T-002 | C-003 | 完成 | 审计 dataclass + separate 改造 |
| @PLAN:task-T-003 | C-001, C-004 | 完成 | 权重字段 + DP 评分增强 |
| @PLAN:task-T-004 | C-005 | 完成 | stats 审计字段 + bass_anchor_integrity |
| @PLAN:task-T-005 | C-006 | 完成 | is_bass_anchor + 优先级方法 |
| @PLAN:task-T-006 | C-007 | 完成 | CLI 接线 + smoke tests |
| @PLAN:section-1.3-check-1 | C-002 | 通过 | 4 测试 PASS |
| @PLAN:section-1.3-check-2 | C-003 | 通过 | 4 测试 PASS |
| @PLAN:section-1.3-check-3 | C-004, C-005 | 通过 | 4 测试 PASS |
| @PLAN:section-1.3-check-4 | C-007 | 通过 | smoke test PASS |

### 设计覆盖确认

| 设计条目 | 对应变更 | 覆盖状态 |
|----------|----------|----------|
| @DESIGN:FR-001 (SVSEP-MPDR v3 主链路) | C-002, C-003, C-004, C-005, C-006, C-007 | 已覆盖 |
| @DESIGN:FR-002 (MIDI onset 聚类) | C-001, C-002 | 已覆盖 |
| @DESIGN:FR-003 (AI 分离审计) | C-003 | 已覆盖 |
| @DESIGN:FR-004 (主旋律 DP) | C-001, C-004 | 已覆盖 |
| @DESIGN:FR-005 (MPDR 候选精排) | C-005 | 已覆盖 |
| @DESIGN:FR-006 (token 冲突) | C-006 | 已覆盖 |
| @DESIGN:FR-007 (转换报告) | C-005, C-007 | 已覆盖 |
| @DESIGN:NFR-001 (真实执行与失败透明) | C-002, C-003, C-007 | 已覆盖 |
| @DESIGN:NFR-002 (可审计性) | C-003, C-005, C-007 | 已覆盖 |
| @DESIGN:NFR-004 (参数可配置) | C-001, C-004, C-007 | 已覆盖 |
| @DESIGN:section-3.4-decision-1 (svsep_mpdr 主链路) | C-002 ~ C-007 | 已覆盖 |
| @DESIGN:section-3.4-decision-2 (不降级) | C-003, C-007 | 已覆盖 |
| @DESIGN:section-3.4-decision-3 (主旋律优先) | C-004, C-006 | 已覆盖 |
| @DESIGN:section-3.4-decision-4 (纯音频链路) | C-007 | 已覆盖 |
| @DESIGN:change-1 ~ change-7 | C-001 ~ C-007 | 一一对应，全部覆盖 |
