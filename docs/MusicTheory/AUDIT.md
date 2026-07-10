---
stage: audit
title: svsep_mpdr_v3 缩编谱乐理审查结果
author: JucieOvo
date: 2026-07-01
status: draft
scope: MusicTheory
---

# svsep_mpdr_v3 缩编谱乐理审查结果

## 关联 Agent 审查记录 (id: section-sp-ref)

- **Superpowers 设计文档**: @SP:spec/2026-07-01-36-key-staff-layout-audit-design
- **实践产物范围**: @SP:commit/--（本轮未提交 git，项目当前不是 git 仓库）
- **Agent 审查记录**:
  - @SP:review/local-score-audit-script -- spec: 通过, code: 通过核心自测
- **脚本化审查工具**: `work/score_audit/score_audit_tool.py`
- **审查测试**: `tests/test_score_audit_tool.py`
- **测试结果**: `pytest tests/test_score_audit_tool.py -q` 通过，3 passed
- **说明**: 本文档整合脚本化审查发现，并补充独立的乐理与谱面排版核查。当前审查只使用目标 YAML 与项目现有 MIDI；尚未执行 fresh-reference 重新转录，因此涉及“与原曲绝对一致性”的判断需在下一轮复核。

---

## 审计范围 (id: section-1.1)

### 被审计条目

- 目标 YAML: `config/one_last_kiss_svsep_mpdr_v3.yaml:1`
- 目标 YAML: `config/tada_koe_hitotsu_svsep_mpdr_v3.yaml:1`
- 目标 YAML: `config/cruel_angel_svsep_mpdr_v3.yaml:1`
- 现有参考 MIDI: `work/one_last_kiss_svsep_mpdr_v3/midi/【Animenz】One Last Kiss - 新·福音战士剧场版：终 钢琴.mid`
- 现有参考 MIDI: `work/tada_koe_hitotsu_svsep_mpdr_v3/midi/ただ声一つ (只想说一声) - ロクデナシ.mid`
- 现有参考 MIDI: `work/cruel_angel_svsep_mpdr_v3/midi/【Animenz】残酷天使的行动纲领 – 新世纪福音战士 OP1 钢琴版.mid`
- 审查产物: `work/score_audit/README.md:1`
- 单曲报告: `work/score_audit/one_last_kiss/preliminary_findings.md:1`
- 单曲报告: `work/score_audit/tada_koe_hitotsu/preliminary_findings.md:1`
- 单曲报告: `work/score_audit/cruel_angel/preliminary_findings.md:1`

### 审计排除项

- 未执行 fresh-reference 重新音频转录，因此不审计“重新转录参考谱与目标 YAML 的差异”。
- 未导出 MusicXML，本轮只生成 MIDI 与结构化 JSON/Markdown 初步审查结果。
- 未修改 `src/audio_to_yaml_converter.py:1`，因此不进行算法实现符合性审计。
- 未人工试听游戏内实际播放结果，因此输入稳定性结论基于播放器行为和 MIDI/YAML 结构推断。

---

## 审计方法 (id: section-1.2)

1. **脚本化反解**: 使用 `work/score_audit/score_audit_tool.py` 将目标 YAML 反解为 `target_reduced.mid`，保留 YAML event 索引、拍点、token 和 pitch。
2. **参考 MIDI 对照**: 将项目现有 MIDI 复制为 `existing_reference.mid`，并读取 tempo、音符数、音区分布、起音密度和总拍数。
3. **2 拍窗口统计**: 以 2 拍窗口近似观察 2/4 网格下的声部密度、起音数量、音区跨度、低音锚点和高密度位置。
4. **候选拍号诊断**: 同时统计 2/4 与 4/4 候选网格下的起音位置、高密度和弦位置、混合升降半音位置。
5. **问题分类**: 将差异归类为输入稳定性风险、异常重音位置、和弦密度过高、节奏织体块状化、低音锚点疑似缺失、音区压缩过窄、节奏碎片化。
6. **置信度分层**: 结构性问题与输入层问题标为高置信；涉及原曲绝对正确性的结论保留为 medium 置信度，等待 fresh-reference 转录复核。

---

## 设计符合性审计 (id: section-2)

### 审计项 2.1: @DESIGN:FR-002 -- YAML 反解为谱面事件 (id: audit-2.1)

- **设计条目**: @DESIGN:FR-002
- **对应实践变更**: @PRACTICE:change-C-001（脚本化审查工具新增）
- **Agent 审查结果**: @SP:review/local-score-audit-script -- core tests 通过
- **符合度**: 完全符合

- **逐项检查**:
  - [x] YAML token 能映射回 MIDI pitch。
  - [x] beat 能累积为绝对拍点。
  - [x] 和弦 token 能展开为同起点多个音符。
  - [x] 能保留 YAML event 索引用于回溯。
  - [x] 能检测重复 pitch、混合升降半音、高密度和弦等结构性风险。

- **偏差说明**: 无偏差。

### 审计项 2.2: @DESIGN:FR-003 -- 导出 MIDI 与 MusicXML 谱面视图 (id: audit-2.2)

- **设计条目**: @DESIGN:FR-003
- **对应实践变更**: @PRACTICE:change-C-002（MIDI 产物生成）
- **Agent 审查结果**: @SP:review/local-score-audit-script -- MIDI 生成完成
- **符合度**: 有偏差

- **逐项检查**:
  - [x] 已生成三首曲目的 `target_reduced.mid`。
  - [x] 已生成三首曲目的 `existing_reference.mid` 副本。
  - [ ] 未生成 MusicXML。

- **偏差说明** (形式 B):

```diff
## 设计: 导出 MIDI 与 MusicXML 谱面视图
- @DESIGN:FR-003: 为 existing-reference、fresh-reference 和 reduced-target 导出 MIDI 与 MusicXML。
+ 实际: 本轮只生成 existing_reference.mid 与 target_reduced.mid，未生成 MusicXML，也未生成 fresh-reference。
原因: 用户要求先直接对比当前文件并定位可优化位置，本轮优先完成 MIDI 结构审查。
影响: 当前结论基于 MIDI/JSON/Markdown，不包含五线谱视觉排版截图或 MusicXML 可视化核查。
```

### 审计项 2.3: @DESIGN:FR-004 -- 重新从原曲音频真实转录 (id: audit-2.3)

- **设计条目**: @DESIGN:FR-004
- **对应实践变更**: --
- **Agent 审查结果**: --
- **符合度**: 未实现

- **逐项检查**:
  - [ ] 未执行原曲音频重新转录。
  - [ ] 未生成 fresh-reference MIDI。
  - [ ] 未进行现有 MIDI 与 fresh-reference 的一致性复核。

- **偏差说明** (形式 B):

```diff
## 设计: 双源参考审查
- @DESIGN:FR-004: 对三首原曲音频重新执行真实转录，生成 fresh-reference MIDI。
+ 实际: 本轮仅使用项目现有 MIDI 作为参考源。
原因: 用户要求先查看当前哪些地方能优化，具体优化策略后续再规划。
影响: 当前审查能定位结构性与输入层问题，但不能完全排除现有 MIDI 转录误差。
```

### 审计项 2.4: @DESIGN:FR-005 -- 三路谱面对齐 (id: audit-2.4)

- **设计条目**: @DESIGN:FR-005
- **对应实践变更**: @PRACTICE:change-C-003（2 拍窗口对齐 JSON）
- **Agent 审查结果**: @SP:review/local-score-audit-script -- alignment 产物生成
- **符合度**: 有偏差

- **逐项检查**:
  - [x] 已完成 existing-reference 与 reduced-target 的 2 拍窗口对齐。
  - [x] 已输出 `alignment_2beat.json`。
  - [ ] 未纳入 fresh-reference。
  - [ ] 未执行完整三路谱面对齐。

- **偏差说明** (形式 B):

```diff
## 设计: 三路谱面对齐
- @DESIGN:FR-005: 对齐 existing-reference、fresh-reference 与 reduced-target。
+ 实际: 本轮只对齐 existing-reference 与 reduced-target。
影响: 可识别当前 YAML 相对项目现有 MIDI 的结构差异；尚不能做双源交叉验证。
```

### 审计项 2.5: @DESIGN:FR-006 -- 按钢琴谱排版规则审查 (id: audit-2.5)

- **设计条目**: @DESIGN:FR-006
- **对应实践变更**: @PRACTICE:change-C-004（issue 分类与 Markdown 报告）
- **Agent 审查结果**: @SP:review/local-score-audit-script -- issue 分类生成
- **符合度**: 部分符合

- **逐项检查**:
  - [x] 已审查输入稳定性风险。
  - [x] 已审查异常重音位置。
  - [x] 已审查和弦密度。
  - [x] 已审查节奏织体块状化。
  - [x] 已审查低音锚点疑似缺失。
  - [x] 已审查音区压缩过窄。
  - [ ] 未审查完整五线谱视觉排版，例如谱表分配、符干方向、连线、声部编号。

- **偏差说明** (形式 B):

```diff
## 设计: 钢琴谱排版规则审查
- @DESIGN:FR-006: 审查主旋律可见性、低音锚点、声部分层、和弦密度、节奏织体和输入稳定性。
+ 实际: 已完成低音、密度、织体、输入稳定性与音区跨度审查；主旋律可见性与视觉谱表排版仍需 MusicXML 或 fresh-reference 复核。
影响: 当前结论可指导 MPDR 结构优化，但不能替代完整乐谱视觉审稿。
```

---

## 规划符合性审计 (id: section-3)

### 审计项 3.1: @PLAN:-- -- 用户要求跳过正式规划 (id: audit-3.1)

- **任务**: --
- **对应实践变更**: @PRACTICE:change-C-001 ~ C-004
- **Agent 审查结果**: @SP:review/local-score-audit-script
- **执行完整度**: 被跳过

- **验收结果**:
  - “先看当前哪些地方能优化”: 通过，已生成三首曲目的初步审查报告。
  - “具体优化策略后续再详细规划”: 通过，本文档仅给审查结果，改进方案见 `docs/MusicTheory/PLAN.md`。

- **偏差说明** (形式 B):

```diff
## 流程: 规划阶段
- 标准流程: 先生成完整 PLAN，再执行审查工具实现。
+ 实际: 用户要求直接开始审查；本轮先生成脚本与审查产物，再补写 MusicTheory 审计与计划文档。
影响: 文档顺序与标准流程有偏差，但产物均可追溯到脚本、测试和输出文件。
```

---

## 代码质量审计 (id: section-4)

### 4.1 Agent 审查问题汇总

| 来源 | 级别 | 描述 | 文件:行号 | 状态 |
|------|------|------|----------|------|
| @SP:review/local-score-audit-script | Minor | `pretty_midi` 和 `yaml` 在 Pyright 中存在环境解析告警，但 Python310 实际运行和 pytest 均通过 | `work/score_audit/score_audit_tool.py:40` | 已知环境告警 |
| @SP:review/local-score-audit-script | Minor | `python` 命令指向 Python 2.7，需使用 Python310 绝对路径运行测试与脚本 | -- | 已规避 |
| @SP:review/local-score-audit-script | Important | 本轮未生成 MusicXML 与 fresh-reference，审查结论需标注限制 | -- | 已在本文档标注 |

### 4.2 结构审查（人类抽查）

| 设计模块 | 实际文件 | 职责一致 | 备注 |
|----------|----------|----------|------|
| @DESIGN:section-3.2-mod-C YamlScoreDecoder | `work/score_audit/score_audit_tool.py` | 是 | 以 `decode_yaml_score` 实现 YAML 反解 |
| @DESIGN:section-3.2-mod-E NotationExporter | `work/score_audit/score_audit_tool.py` | 部分 | 只导出 MIDI，未导出 MusicXML |
| @DESIGN:section-3.2-mod-F ScoreAligner | `work/score_audit/score_audit_tool.py` | 部分 | 完成 2 拍窗口对齐，未做三路完整对齐 |
| @DESIGN:section-3.2-mod-G LayoutRuleAuditor | `work/score_audit/score_audit_tool.py` | 部分 | 完成结构规则审查，未做视觉五线谱审查 |
| @DESIGN:section-3.2-mod-H AuditReportWriter | `work/score_audit/score_audit_tool.py` | 是 | 生成 `preliminary_findings.md` 与 `README.md` |

### 4.3 接口审查（人类抽查）

| 设计接口 | 设计签名 | 实际签名 | 一致 |
|----------|----------|----------|------|
| @DESIGN:section-4.1-iface-2 | YAML 路径 → reduced-target 谱面事件、MIDI、统计 | `decode_yaml_score(yaml_path: Path) -> DecodedYamlScore` + `write_notes_to_midi(...)` | 部分 |
| @DESIGN:section-4.1-iface-4 | 三路事件 → `alignment.json` | existing-reference + reduced-target → `alignment_2beat.json` | 部分 |
| @DESIGN:section-4.1-iface-5 | 对齐窗口 → `issues.json` | `detect_layout_issues(...) -> list[dict]` | 是 |
| @DESIGN:section-4.1-iface-6 | issues + stats → Markdown 报告 | `write_song_markdown(...)` + `write_index_markdown(...)` | 是 |

---

## 设计-实践差异 (id: section-5.1)

| 差异 ID | 设计条目 | 实践表现 | 类别 | Agent 已标记 |
|----------|----------|----------|------|------------|
| diff-1 | @DESIGN:FR-003 | 未生成 MusicXML | 未实现 | 是 |
| diff-2 | @DESIGN:FR-004 | 未重新转录 fresh-reference | 未实现 | 是 |
| diff-3 | @DESIGN:FR-005 | 只做两路对齐，未做三路对齐 | 有意偏差 | 是 |
| diff-4 | @DESIGN:FR-006 | 未做视觉五线谱符干/谱表排版审查 | 未实现 | 是 |
| diff-5 | @DESIGN:NFR-002 | Major 问题包含定位、证据、置信度 | 无偏差 | 是 |
| diff-6 | @DESIGN:NFR-003 | 所有新增文件写入 `work/score_audit/` 与 `docs/MusicTheory/` | 无偏差 | 是 |

---

## 差异分类 (id: section-5.2)

| 类别 | 数量 | 说明 |
|------|------|------|
| 有意偏差 | 1 | 用户要求先直接审查当前文件，故先做两路对齐 |
| 无意偏差 | 0 | 当前未发现无意偏差 |
| 未实现 | 3 | MusicXML、fresh-reference、视觉五线谱排版审查未完成 |
| Agent 已标记 | 6 | 已在本文档与报告中明确标记 |

---

## 未覆盖项 (id: section-5.3)

| 设计条目 | 原因 |
|----------|------|
| @DESIGN:FR-004 | 本轮未重新从原曲音频转录，需后续执行 |
| @DESIGN:FR-003 MusicXML 部分 | 本轮先生成 MIDI 和结构化报告，MusicXML 后续补充 |
| @DESIGN:section-3.2-mod-D FreshTranscriptionRunner | 未触发 fresh-reference 任务 |
| @DESIGN:section-4.2-ext-3 MusicXML 查看工具 | 未进行人工五线谱查看 |

---

## 整体评估 (id: section-6.1)

- 设计符合率: 约 55%（YAML 反解、MIDI 产物、两路对齐、结构问题报告已完成；fresh-reference、MusicXML、完整视觉谱面审查未完成）
- 规划执行率: 不适用（用户明确要求跳过正式规划，直接完成当前审查）
- 追溯链完整性: 约 75%（脚本、测试、MIDI、JSON、Markdown 可追溯；缺少 fresh-reference 与 MusicXML 链路）

总体评价：本轮已经足以确认 `svsep_mpdr_v3` 当前存在系统性乐理/精排问题，主要集中在混合升降半音输入风险、弱拍高密度和弦、节奏织体块状化、低音锚点缺失与音区压缩过窄。当前结论可作为后续 MPDR 改进方案的依据，但不能替代完整双源转录与 MusicXML 视觉审查。

---

## 严重问题 (id: section-6.2)

### 严重 1: 同事件混合升降半音导致输入稳定性风险 (id: audit-6.2-1)

- **来源**: `work/score_audit/*/issues.json`
- **Superpowers 级别**: Critical
- **描述**: 三首曲目均存在大量同时包含 `#` 与 `b` 的 YAML 和弦事件。播放器层需要将 Ctrl 与 Shift 修饰键分组发送，这会破坏和弦同时性。
- **证据**:
  - One Last Kiss: 249 个混合升降事件。
  - ただ声一つ: 90 个混合升降事件。
  - 残酷天使的行动纲领: 76 个混合升降事件。
- **修复建议**: 在 token 选择或和弦输出层约束同一事件的 accidental style，必要时拆分事件或重选等音表达。

### 严重 2: 残酷天使的行动纲领最大同按达到 7 (id: audit-6.2-2)

- **来源**: `work/score_audit/cruel_angel/compare_summary.json`
- **Superpowers 级别**: Critical
- **描述**: 目标最大同按为 7，超过当前游戏输入稳定性和 36 键缩编谱可读性安全范围。
- **证据**: `cruel_angel` 目标最大同按 7，和弦密度过高问题 33 条。
- **修复建议**: 为弱拍与高风险修饰键事件设置更严格的密度上限，并将强拍厚度与弱拍厚度分开控制。

---

## 建议改进 (id: section-6.3)

### 建议 1: 引入拍号与强弱拍感知 (id: audit-6.3-1)

- **来源**: 2/4 与 4/4 网格诊断
- **Superpowers 级别**: Important
- **描述**: 三首曲目的高密度和弦大量落在 2/4 非 0/1 拍位置，尤其 `cruel_angel` 在 1.75、0.75、1.5、1.25 拍位置最突出。

### 建议 2: 区分流动织体与纵向和弦 (id: audit-6.3-2)

- **来源**: 节奏织体块状化问题
- **Superpowers 级别**: Important
- **描述**: 若参考 MIDI 2 拍窗口存在 6~11 个起音，而目标只有 1~2 个起音，则说明快速织体被压成块状和弦。

### 建议 3: 增强低音锚点保护 (id: audit-6.3-3)

- **来源**: 低音锚点疑似缺失问题
- **Superpowers 级别**: Important
- **描述**: 多处参考窗口最低音在低音区或更低，但目标最低音被抬到 MIDI 60 附近，低音支撑消失。

### 建议 4: 限制声部挤压和音区过窄 (id: audit-6.3-4)

- **来源**: 音区压缩过窄问题
- **Superpowers 级别**: Important
- **描述**: `cruel_angel` 多处参考跨度 36~62 半音，目标只有 12~18 半音且包含 8~14 个音，声部层次被压扁。

---

## 后续行动项 (id: section-6.4)

| 行动 ID | 描述 | 优先级 | 关联问题 |
|----------|------|--------|----------|
| ACT-001 | 设计 accidental style 统一与混合升降事件消解规则 | 严重 | audit-6.2-1 |
| ACT-002 | 为 MPDR 增加拍号/强弱拍密度权重 | 严重 | audit-6.2-2, audit-6.3-1 |
| ACT-003 | 识别流动织体并避免块状化压缩 | 高 | audit-6.3-2 |
| ACT-004 | 增强低音锚点窗口级保护 | 高 | audit-6.3-3 |
| ACT-005 | 增加音区跨度与声部分层约束 | 高 | audit-6.3-4 |
| ACT-006 | 补充 fresh-reference 重新转录与 MusicXML 视觉审查 | 中 | audit-2.2, audit-2.3, audit-2.4 |

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 条目 | 类型 |
|----------|------|------|
| audit-2.1 | YAML 反解符合性 | 审计项 |
| audit-2.2 | MIDI/MusicXML 产物符合性 | 审计项 |
| audit-2.3 | fresh-reference 实现情况 | 审计项 |
| audit-2.4 | 三路谱面对齐符合性 | 审计项 |
| audit-2.5 | 乐理规则审查符合性 | 审计项 |
| audit-6.2-1 | 混合升降半音严重问题 | 严重问题 |
| audit-6.2-2 | 最大同按过高严重问题 | 严重问题 |
| audit-6.3-1 | 拍号与强弱拍建议 | 建议改进 |
| audit-6.3-2 | 织体识别建议 | 建议改进 |
| audit-6.3-3 | 低音锚点建议 | 建议改进 |
| audit-6.3-4 | 音区分层建议 | 建议改进 |

---

## 三维审计矩阵 (id: appendix-b)

| 设计 | 规划 | 实践 | 审计 | Agent 审查 | 状态 |
|------|------|------|------|----------|------|
| @DESIGN:FR-002 | --（用户跳过正式 PLAN） | @PRACTICE:change-C-001 | audit-2.1 | @SP:review/local-score-audit-script | 完全符合 |
| @DESIGN:FR-003 | --（用户跳过正式 PLAN） | @PRACTICE:change-C-002 | audit-2.2 | @SP:review/local-score-audit-script | 有偏差 |
| @DESIGN:FR-004 | --（用户跳过正式 PLAN） | -- | audit-2.3 | -- | 未实现 |
| @DESIGN:FR-005 | --（用户跳过正式 PLAN） | @PRACTICE:change-C-003 | audit-2.4 | @SP:review/local-score-audit-script | 有偏差 |
| @DESIGN:FR-006 | --（用户跳过正式 PLAN） | @PRACTICE:change-C-004 | audit-2.5 | @SP:review/local-score-audit-script | 部分符合 |
| @DESIGN:FR-007 | --（用户跳过正式 PLAN） | @PRACTICE:change-C-005 | section-6.1 | @SP:review/local-score-audit-script | 部分符合 |

不完整链路说明：
- PLAN 列为 `--`：用户明确要求先直接完成当前审查，后续优化策略再详细规划。
- PRACTICE 列为 `--`：对应设计条目本轮未执行，例如 fresh-reference 重新转录。
- Agent 审查列为 `--`：对应条目未执行，因此无审查记录。
