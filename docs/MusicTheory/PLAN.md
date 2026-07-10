---
stage: plan
title: svsep_mpdr_v3 乐理精排改进方案
author: JucieOvo
date: 2026-07-01
status: draft
scope: MusicTheory
---

# svsep_mpdr_v3 乐理精排改进方案

## 关联 Superpowers 工作文档 (id: section-sp-ref)

- **Superpowers 计划文档**: @SP:plan/--（用户要求先直接输出 MusicTheory 改进方案，未生成 Superpowers 计划）
- **writing-plans 会话日期**: 2026-07-01
- **提炼者**: JucieOvo
- **关联审计文档**: `docs/MusicTheory/AUDIT.md`
- **关联设计文档**: `docs/score-audit-svsep-mpdr-v3/DESIGN.md`
- **说明**: 本文档是基于 `docs/MusicTheory/AUDIT.md` 的 i2a plan 格式改进方案，聚焦后续如何优化 MPDR 精排策略；不包含完整代码实现。

---

## 规划目标 (id: section-1.1)

将 @DESIGN:FR-006 与 `docs/MusicTheory/AUDIT.md` 中识别出的 6 类问题拆解为 8 个可执行任务，目标是在不改变游戏 36 键硬约束的前提下，使 `svsep_mpdr_v3` 输出更符合钢琴缩编谱的排版逻辑与游戏输入稳定性。

### 阶段划分

- 阶段 1: 输入稳定性与符号规范 -- 2 个任务，目标：消除同事件混合升降半音和过高同按风险。
- 阶段 2: 拍号与节奏织体建模 -- 2 个任务，目标：让 MPDR 感知强弱拍与流动织体，减少异常重音和块状化。
- 阶段 3: 声部功能与音区层次 -- 2 个任务，目标：加强低音锚点、旋律可见性和音区跨度控制。
- 阶段 4: 审查闭环与验证 -- 2 个任务，目标：复跑审查脚本、补充 fresh-reference/MusicXML 验证并输出对比报告。

---

## 任务依赖总览 (id: section-1.2)

```text
T-001 → T-002 → T-003 → T-004
                  ↘ T-005 → T-006
T-007 ----------------------↗
T-008 依赖 T-001~T-007 完成
```

说明：

- T-001 是输入稳定性基础，必须先完成，否则后续密度和节奏判断会被 Ctrl/Shift 拆分影响。
- T-002 依赖 T-001，用于把同按上限从固定值改为与修饰键风险、拍点位置相关。
- T-003 和 T-004 共同解决异常重音与块状化问题。
- T-005 和 T-006 共同解决低音锚点、旋律可见性与音区挤压问题。
- T-007 是验证链路补强，可与 T-003~T-006 并行准备，但最终验收依赖前面策略完成。
- T-008 是总体验收任务，必须最后执行。

---

## 检查点 (id: section-1.3)

### 检查点 1: 输入稳定性基线通过 (id: section-1.3-check-1)

- **位置**: T-002 完成后
- **验证**: 三首目标 YAML 中同事件混合升降半音事件数显著下降；`cruel_angel` 最大同按不再超过配置上限。
- **阻塞**: 未通过 → 不得进入节奏与声部优化阶段。

### 检查点 2: 节奏排版基线通过 (id: section-1.3-check-2)

- **位置**: T-004 完成后
- **验证**: 弱拍高密度和弦数量下降，参考起音密集但目标起音过少的块状化窗口减少。
- **阻塞**: 未通过 → 不得进入最终验收。

### 检查点 3: 声部层次基线通过 (id: section-1.3-check-3)

- **位置**: T-006 完成后
- **验证**: 低音锚点缺失和音区压缩过窄问题数量下降，且主旋律候选不被低音策略遮蔽。
- **阻塞**: 未通过 → 不得进入最终验收。

### 检查点 4: 审查闭环通过 (id: section-1.3-check-4)

- **位置**: T-008 完成后
- **验证**: `work/score_audit/` 中新旧结果可对比，`docs/MusicTheory/AUDIT.md` 的严重问题均有改善证据。
- **阻塞**: 未通过 → 不得声明本轮 MusicTheory 优化完成。

---

## 任务摘要 (id: section-2)

### T-001: 统一 accidental style 并消解同事件混合升降半音 (id: task-T-001)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-006, @DESIGN:NFR-002 |
| **关联审计** | @AUDIT:audit-6.2-1 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 在 token 输出前增加同事件 accidental style 约束，避免一个和弦同时包含 `#` 与 `b`。 |
| **依赖** | 无 |
| **产物** | `src/audio_to_yaml_converter.py`（修改，具体行号实施阶段确认）、测试文件（新增或修改） |
| **验收标准** | 三首曲目混合升降半音事件数明显下降，且不得通过伪造或静默删除旋律音实现。 |

### T-002: 建立修饰键风险感知的同按上限 (id: task-T-002)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-006, @DESIGN:section-3.3-flow-6 |
| **关联审计** | @AUDIT:audit-6.2-2 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 将同按上限从单一 `max_chord_notes` 扩展为按修饰键风险、拍点位置和声部角色动态收缩的上限。 |
| **依赖** | T-001 |
| **产物** | MPDR 候选裁剪逻辑、输入风险统计字段、真实回归测试 |
| **验收标准** | `cruel_angel` 最大同按不超过目标安全阈值，高风险修饰键事件不会同时满密度输出。 |

### T-003: 引入拍号与强弱拍权重 (id: task-T-003)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-005, @DESIGN:FR-006 |
| **关联审计** | @AUDIT:audit-6.3-1 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 为 MPDR 增加 beat position 分析，区分 2/4、4/4 或配置拍号下的强拍、次强拍和弱拍。 |
| **依赖** | T-002 |
| **产物** | 拍号配置、beat position 权重函数、弱拍密度惩罚统计 |
| **验收标准** | 2/4 非 0/1 拍位置的高密度和弦数量下降，强拍上的低音/旋律骨干保留率不下降。 |

### T-004: 识别流动织体并抑制块状化压缩 (id: task-T-004)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-006, @DESIGN:section-3.3-flow-6 |
| **关联审计** | @AUDIT:audit-6.3-2 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 在局部窗口中识别分解和弦、快速旋律与装饰音，避免将多起音流动织体压成单个块状和弦。 |
| **依赖** | T-003 |
| **产物** | 织体分类统计、流动窗口保护规则、块状化问题回归测试 |
| **验收标准** | 参考起音密集但目标起音过少的窗口数量下降，目标 YAML 不通过无意义增音伪造流动感。 |

### T-005: 增强低音锚点窗口级保护 (id: task-T-005)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-006, @DESIGN:section-3.2-mod-G |
| **关联审计** | @AUDIT:audit-6.3-3 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 在每个和声窗口内识别低音功能音，并为首拍/强拍低音锚点设置保护优先级。 |
| **依赖** | T-003 |
| **产物** | 低音锚点评分权重、窗口级低音保留统计、低音缺失回归测试 |
| **验收标准** | `cruel_angel` 与 `tada_koe_hitotsu` 中参考最低音明显低于目标最低音的窗口数量下降。 |

### T-006: 增加音区跨度与声部分层约束 (id: task-T-006)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-006, @DESIGN:section-3.2-mod-G |
| **关联审计** | @AUDIT:audit-6.3-4 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 对多声部窗口增加最小音区跨度、旋律/低音距离和中声部拥挤惩罚，避免所有音挤在 C4 附近。 |
| **依赖** | T-005 |
| **产物** | 音区清晰度评分、声部挤压惩罚、音区压缩过窄回归测试 |
| **验收标准** | 参考跨度大但目标跨度极窄的窗口数量下降，且目标不会生成超出 C3-B5 的 token。 |

### T-007: 补充 fresh-reference 与 MusicXML 审查链路 (id: task-T-007)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-003, @DESIGN:FR-004, @DESIGN:FR-005 |
| **关联审计** | @AUDIT:audit-2.2, @AUDIT:audit-2.3, @AUDIT:audit-2.4 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 在现有审查脚本基础上补充 fresh-reference 重新转录、MusicXML 导出和三路谱面对齐。 |
| **依赖** | 无，可与 T-003~T-006 并行准备 |
| **产物** | `fresh_reference.mid`、`*.musicxml`、三路 `alignment.json`、扩展审查报告 |
| **验收标准** | 三首曲目均有 fresh-reference 与 MusicXML；若真实依赖不可用，必须明确阻塞原因，不生成伪结果。 |

### T-008: 复跑三首曲目并更新审查闭环 (id: task-T-008)

| 字段 | 内容 |
|------|------|
| **关联设计** | @DESIGN:FR-007, @DESIGN:NFR-002, @DESIGN:NFR-005 |
| **关联审计** | @AUDIT:section-6.4 |
| **Superpowers 任务** | @SP:plan/-- |
| **描述** | 使用优化后的算法重新生成三首目标 YAML，复跑 `work/score_audit/score_audit_tool.py` 并更新 MusicTheory 审查文档。 |
| **依赖** | T-001~T-007 |
| **产物** | 新一轮 YAML、审查 JSON、MIDI、MusicXML、`docs/MusicTheory/AUDIT.md` 更新版 |
| **验收标准** | 审计中的 Critical 问题均有改善证据，Major 问题数量较当前基线下降；所有测试真实通过。 |

---

## 环境与工具 (id: section-3)

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.10 | 运行审查脚本与项目转换管线 |
| PyYAML | 现有环境版本 | 读取与写出 YAML 曲谱 |
| pretty_midi | 现有环境版本 | 读取/写出 MIDI 与统计音符事件 |
| music21 | 需真实安装 | MIDI/MusicXML 转换与五线谱视图导出 |
| Demucs | 现有项目链路 | 原曲音频分轨 |
| Transkun | 现有项目链路 | 原曲音频重新转录 fresh-reference MIDI |
| pytest | 现有环境版本 | 测试审查脚本与 MPDR 回归用例 |

| 工具 | 版本 | 用途 |
|------|------|------|
| `C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe` | Python 3.10 | 当前实测可运行 pytest 与审查脚本 |
| `python` | Python 2.7（Scoop） | 不应用于本项目测试和脚本运行 |
| `work/score_audit/score_audit_tool.py` | 2026-07-01 版 | 当前审查基线工具 |

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 条目 | 供后续引用 |
|----------|------|-----------|
| task-T-001 | 统一 accidental style | @PLAN:task-T-001 |
| task-T-002 | 修饰键风险同按上限 | @PLAN:task-T-002 |
| task-T-003 | 拍号与强弱拍权重 | @PLAN:task-T-003 |
| task-T-004 | 流动织体识别 | @PLAN:task-T-004 |
| task-T-005 | 低音锚点保护 | @PLAN:task-T-005 |
| task-T-006 | 音区跨度与声部分层 | @PLAN:task-T-006 |
| task-T-007 | fresh-reference 与 MusicXML | @PLAN:task-T-007 |
| task-T-008 | 复跑审查闭环 | @PLAN:task-T-008 |
| section-1.3-check-1 | 输入稳定性基线 | @PLAN:section-1.3-check-1 |
| section-1.3-check-2 | 节奏排版基线 | @PLAN:section-1.3-check-2 |
| section-1.3-check-3 | 声部层次基线 | @PLAN:section-1.3-check-3 |
| section-1.3-check-4 | 审查闭环 | @PLAN:section-1.3-check-4 |

---

## 设计覆盖矩阵 (id: appendix-b)

| 设计/审计条目 | 锚点 | 对应任务 | 覆盖状态 |
|----------|------|----------|----------|
| YAML 反解为谱面事件 | @DESIGN:FR-002 | T-008 | 已覆盖 |
| 导出 MIDI 与 MusicXML | @DESIGN:FR-003 | T-007, T-008 | 已覆盖 |
| 重新从原曲音频真实转录 | @DESIGN:FR-004 | T-007 | 已覆盖 |
| 三路谱面对齐 | @DESIGN:FR-005 | T-007, T-008 | 已覆盖 |
| 按钢琴谱排版规则审查 | @DESIGN:FR-006 | T-001~T-008 | 已覆盖 |
| 输出结构化问题清单和报告 | @DESIGN:FR-007 | T-008 | 已覆盖 |
| 真实执行与失败透明 | @DESIGN:NFR-001 | T-007, T-008 | 已覆盖 |
| 可追溯性 | @DESIGN:NFR-002 | T-008 | 已覆盖 |
| 不覆盖既有产物 | @DESIGN:NFR-003 | T-007, T-008 | 已覆盖 |
| 审查结论分层 | @DESIGN:NFR-005 | T-008 | 已覆盖 |
| 混合升降半音严重问题 | @AUDIT:audit-6.2-1 | T-001 | 已覆盖 |
| 最大同按过高严重问题 | @AUDIT:audit-6.2-2 | T-002 | 已覆盖 |
| 拍号与强弱拍建议 | @AUDIT:audit-6.3-1 | T-003 | 已覆盖 |
| 织体识别建议 | @AUDIT:audit-6.3-2 | T-004 | 已覆盖 |
| 低音锚点建议 | @AUDIT:audit-6.3-3 | T-005 | 已覆盖 |
| 音区分层建议 | @AUDIT:audit-6.3-4 | T-006 | 已覆盖 |
