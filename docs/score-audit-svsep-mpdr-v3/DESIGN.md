---
stage: design
title: 36 键缩编谱五线谱对照审查设计
author: JucieOvo
date: 2026-07-01
status: draft
sp_spec: docs/superpowers/specs/2026-07-01-36-key-staff-layout-audit-design.md
---

# 36 键缩编谱五线谱对照审查设计

## 关联 Superpowers 工作文档 (id: section-sp-ref)

- **Superpowers 设计文档**: @SP:spec/2026-07-01-36-key-staff-layout-audit-design
- **brainstorming 会话日期**: 2026-07-01
- **提炼者**: JucieOvo
- **文档路径说明**: 用户要求本次 i2aspec 文档写入子文件夹，因此本文档使用 `docs/score-audit-svsep-mpdr-v3/DESIGN.md`，不覆盖既有 `docs/DESIGN.md`。

---

## 背景与动机 (id: section-1.1)

当前项目用于将现实世界钢琴曲转换为可在游戏 36 键琴盘上演奏的 YAML 曲谱。已有自动弹奏器负责读取 YAML 并发送真实键盘输入，相关播放器配置加载逻辑位于 `src/piano_auto_player.py:139`；音频到 YAML 的离线转换主链路位于 `src/audio_to_yaml_converter.py:1`，其中 36 键目标音域常量和 token 映射相关常量从 `src/audio_to_yaml_converter.py:130` 开始定义。

用户反馈当前 `svsep_mpdr_v3` 缩编精排存在综合问题，包括但不限于声部关系、节奏织体、音区布局和 YAML 符号化输出。由于用户自述为乐理盲，不能依靠主观描述逐项归因，因此需要建立一个可审阅的五线谱对照审查流程：把当前 36 键 YAML 转成可读谱面视图，并用原曲音频转录得到的参考谱进行交叉对照，按现实钢琴谱排版规则和 36 键缩编原则输出问题清单。

不进行本设计的后果是：后续算法修改会缺少可追溯的问题来源，容易把音频转录误差、合理缩编取舍和真实精排缺陷混为一谈。

---

## 目标与非目标 (id: section-1.2)

### 目标

- 目标 1：审查以下三个目标 YAML 的五线谱排版与缩编合理性：`config/one_last_kiss_svsep_mpdr_v3.yaml:1`、`config/tada_koe_hitotsu_svsep_mpdr_v3.yaml:1`、`config/cruel_angel_svsep_mpdr_v3.yaml:1`。
- 目标 2：将当前 YAML 反解为 36 键范围内的 MIDI、MusicXML 和结构化事件数据。
- 目标 3：同时使用项目现有 MIDI 与重新从原曲音频转录得到的新 MIDI，构成双源参考。
- 目标 4：按小节和拍点窗口对齐三路谱面，比较旋律、低音、声部分层、和弦密度、节奏织体与输入稳定性。
- 目标 5：将审查结果分为 Critical、Major、Minor、Info，并区分高置信精排问题、疑似问题、参考转录不稳定和可接受缩编取舍。
- 目标 6：为后续 MPDR 评分、声部分离、音区规划、节奏压缩或 YAML 输出规则的优化提供结构化证据。

### 非目标

- 不直接修改 `src/audio_to_yaml_converter.py:1` 或任何转换算法。
- 不覆盖现有 YAML、MIDI、音频或既有设计文档。
- 不把重新转录得到的参考谱声明为官方谱或绝对正确谱。
- 不使用 Mock、Stub、伪造 MIDI、伪造 MusicXML 或伪造审查统计。
- 不通过联网寻找或复制官方/商业五线谱。
- 不做规避游戏检测、后台注入或绕过行为。
- 不要求 36 键缩编谱完全等同 88 键钢琴原谱。

---

## 范围边界 (id: section-1.3)

### 包含

- 三个 `svsep_mpdr_v3` YAML 的读取、校验与反解。
- 项目现有 MIDI 的引用或标准化副本生成。
- 原曲音频的重新真实转录。
- MIDI 与 YAML 反解结果到 MusicXML 的谱面视图导出。
- 三路谱面的小节级、拍点级和局部窗口级对齐。
- 乐理与钢琴缩编排版规则审查。
- 单曲报告与总报告产出。

### 不包含

- 不包含算法修复、代码重构或参数调优实践。
- 不包含人工补谱、手工改谱或官方谱复刻。
- 不包含播放器 SendInput 行为改造。
- 不包含引入外部云服务或远程 API。

### 与现有系统的关系

本设计以现有音频转 YAML 项目为输入来源，但在产物层面独立于现有生成链路。审查产物默认写入 `work/score_audit/` 与 `docs/score-audit-svsep-mpdr-v3/`，不覆盖当前 `config/`、`work/*/midi/` 或既有 `docs/DESIGN.md`。

---

## 关键术语 (id: section-1.4)

| 术语 | 定义 |
|------|------|
| 36 键缩编谱 | 将完整钢琴曲压缩到游戏 C3-B5 范围内并以 YAML token 表示的可演奏谱。 |
| 目标 YAML | 本次被审查的三个 `svsep_mpdr_v3` YAML 文件。 |
| existing-reference | 项目现有 MIDI，代表当前项目已有音频转录参考。 |
| fresh-reference | 重新从原曲音频真实转录得到的新 MIDI，作为独立参考。 |
| reduced-target | 目标 YAML 反解得到的 36 键 MIDI / MusicXML。 |
| 旋律可见性 | 主旋律在缩编谱中是否清楚、连续且未被伴奏遮蔽。 |
| 低音锚点 | 小节或和声窗口中承担和声根基的低声部音符。 |
| 声部分层 | 高声部、低声部和中间和声填充是否形成稳定、可读的钢琴谱结构。 |
| 可接受缩编取舍 | 因 36 键限制导致的合理省略、八度压缩或和声简化。 |

---

## 功能需求 (id: section-2.1)

### FR-001: 解析审查清单与输入材料 (id: FR-001)

- **描述**: 系统必须明确每首曲子的目标 YAML、项目现有 MIDI、原曲音频和审查输出目录。
- **用户场景**: 用户指定三首曲目后，需要一次性生成三首曲子的对照审查结果。
- **优先级**: 必须

### FR-002: YAML 反解为谱面事件 (id: FR-002)

- **描述**: 系统必须读取目标 YAML，将 token、和弦、休止符和 beat 累积反解为 MIDI 音符事件，并保留原 YAML 事件编号。
- **用户场景**: 用户需要知道某个问题对应到 YAML 的哪一个事件或小节。
- **优先级**: 必须

### FR-003: 导出 MIDI 与 MusicXML 谱面视图 (id: FR-003)

- **描述**: 系统必须为 existing-reference、fresh-reference 和 reduced-target 导出可查看的 MIDI 与 MusicXML。
- **用户场景**: 用户需要用五线谱视图理解算法审查结论。
- **优先级**: 必须

### FR-004: 重新从原曲音频真实转录 (id: FR-004)

- **描述**: 系统必须对三首原曲音频重新执行真实转录，生成 fresh-reference MIDI，不允许用现有 MIDI 复制冒充。
- **用户场景**: 用户希望避免只用当前生成链路上游 MIDI 导致审查自证。
- **优先级**: 必须

### FR-005: 三路谱面对齐 (id: FR-005)

- **描述**: 系统必须按小节、拍点和局部窗口对齐 existing-reference、fresh-reference 与 reduced-target。
- **用户场景**: 用户需要判断 YAML 在某个小节相对参考谱损失或扭曲了什么。
- **优先级**: 必须

### FR-006: 按钢琴谱排版规则审查 (id: FR-006)

- **描述**: 系统必须审查主旋律可见性、低音锚点、声部分层、和弦密度、节奏织体和输入稳定性。
- **用户场景**: 用户需要把“听起来不对”拆成可修复的问题类型。
- **优先级**: 必须

### FR-007: 输出结构化问题清单和报告 (id: FR-007)

- **描述**: 系统必须输出 `issues.json`、单曲 `summary.md` 和总报告，问题需包含等级、定位、类型、证据、置信度和后续建议。
- **用户场景**: 用户需要基于审查结果决定下一轮算法优化方向。
- **优先级**: 必须

---

## 非功能需求 (id: section-2.2)

### NFR-001: 真实执行与失败透明 (id: NFR-001)

- **类型**: 可用性
- **描述**: 所有转录、解析、导出和审查必须基于真实文件与真实依赖执行。
- **量化指标**: 任何必要依赖不可用时直接报错并停止对应曲目，不生成伪报告。

### NFR-002: 可追溯性 (id: NFR-002)

- **类型**: 可维护性
- **描述**: 每个问题必须能追溯到曲目、小节/拍点、参考源现象、目标 YAML 现象和规则依据。
- **量化指标**: `issues.json` 中每个 Major 及以上问题必须包含定位字段、证据字段和置信度字段。

### NFR-003: 不覆盖既有产物 (id: NFR-003)

- **类型**: 安全
- **描述**: 审查过程不得覆盖现有 YAML、MIDI、音频、转换报告或根级 `docs/DESIGN.md`。
- **量化指标**: 所有新增文件写入 `work/score_audit/` 或 `docs/score-audit-svsep-mpdr-v3/`。

### NFR-004: Windows 本机兼容 (id: NFR-004)

- **类型**: 兼容性
- **描述**: 路径、命令和依赖调用必须适配 Windows 11 本机环境。
- **量化指标**: 文档与计划使用 Windows 路径，真实运行时不依赖 Linux-only 路径语义。

### NFR-005: 审查结论分层 (id: NFR-005)

- **类型**: 可用性
- **描述**: 报告必须区分高置信精排问题、疑似问题、参考转录不稳定和可接受缩编取舍。
- **量化指标**: 每个问题必须有 `severity` 与 `confidence`；不可把所有差异直接判错。

---

## 约束条件 (id: section-2.3)

### 技术约束

- 必须使用真实音频、真实 MIDI、真实 YAML 和真实谱面导出工具。
- 不允许 Mock、Stub、伪造转录结果、伪造 MusicXML 或伪造统计。
- 不允许静默回退到现有 MIDI 作为 fresh-reference；重新转录失败必须明确报告阻塞。
- 不允许硬编码审查结论；阈值必须在计划阶段以配置或命名常量形式定义。
- 新增 Python 文件如进入实施阶段，必须遵守中文模块注释、中文详细注释和作者 JucieOvo 规则。

### 资源约束

- 重新转录依赖本机真实音频转谱环境，可能需要 GPU 和已安装模型。
- MusicXML 导出依赖本机可用的 MIDI / 乐谱处理库。
- 三首曲目重新转录和导出可能耗时较长。

### 合规约束

- 不使用未授权官方谱或商业谱作为输入。
- 不上传音频、MIDI 或谱面到外部服务。
- 不引入任何与游戏反作弊规避相关的行为。

---

## 架构概览 (id: section-3.1)

本次设计涉及 7 个逻辑模块。数据流向为：审查清单 → 输入解析 → YAML 反解 / 参考转录 → 谱面导出 → 三路对齐 → 规则审查 → 报告汇总。

```text
+-------------------+
| AuditManifest     |
+---------+---------+
          |
          v
+-------------------+       +-----------------------+
| InputResolver     | ----> | FreshTranscription    |
+---------+---------+       +-----------+-----------+
          |                             |
          v                             v
+-------------------+       +-----------------------+
| YamlScoreDecoder  | ----> | NotationExporter      |
+---------+---------+       +-----------+-----------+
          |                             |
          +-------------+---------------+
                        v
              +-------------------+
              | ScoreAligner      |
              +---------+---------+
                        v
              +-------------------+
              | LayoutRuleAuditor |
              +---------+---------+
                        v
              +-------------------+
              | AuditReportWriter |
              +-------------------+
```

---

## 模块职责 (id: section-3.2)

### 模块 A: AuditManifest (id: section-3.2-mod-A)

- **职责**: 描述三首曲目的目标 YAML、原曲音频、现有 MIDI 和输出目录。
- **对外接口**: 提供曲目清单与路径配置。
- **依赖**: 用户指定的目标文件和项目现有目录结构。

### 模块 B: InputResolver (id: section-3.2-mod-B)

- **职责**: 校验清单中的所有输入文件是否存在，并建立每首曲目的材料索引。
- **对外接口**: 接收 manifest，返回标准化曲目输入对象。
- **依赖**: 文件系统与路径校验逻辑。

### 模块 C: YamlScoreDecoder (id: section-3.2-mod-C)

- **职责**: 将目标 YAML 的 score 反解为 36 键范围内的 MIDI note 事件。
- **对外接口**: 接收 YAML 路径，返回带 YAML 事件编号的谱面事件序列。
- **依赖**: 现有 YAML 格式、键盘 token 映射、BPM 与 beat 累积规则。

### 模块 D: FreshTranscriptionRunner (id: section-3.2-mod-D)

- **职责**: 从原曲音频重新执行真实音频转 MIDI。
- **对外接口**: 接收音频路径和输出目录，返回 fresh-reference MIDI 路径。
- **依赖**: 项目现有音频转谱工具链、GPU 或 CPU 推理环境。

### 模块 E: NotationExporter (id: section-3.2-mod-E)

- **职责**: 将 existing-reference、fresh-reference 和 reduced-target 导出为 MIDI / MusicXML 谱面视图。
- **对外接口**: 接收谱面事件或 MIDI 路径，输出标准化 MIDI、MusicXML 与辅助 JSON。
- **依赖**: `pretty_midi`、`music21` 或同类本地谱面处理库。

### 模块 F: ScoreAligner (id: section-3.2-mod-F)

- **职责**: 按小节、拍点和局部窗口对齐三路谱面。
- **对外接口**: 接收三路谱面事件，输出 `alignment.json`。
- **依赖**: BPM、tempo、量化单位、小节窗口配置。

### 模块 G: LayoutRuleAuditor (id: section-3.2-mod-G)

- **职责**: 按乐理和钢琴谱排版规则识别精排问题。
- **对外接口**: 接收对齐结果，输出结构化 issue 列表。
- **依赖**: 审查规则配置、问题分级规则、置信度规则。

### 模块 H: AuditReportWriter (id: section-3.2-mod-H)

- **职责**: 生成单曲报告、总报告和机器可读问题清单。
- **对外接口**: 接收统计和 issue 列表，输出 Markdown 与 JSON。
- **依赖**: 报告模板与输出目录。

---

## 关键流程 (id: section-3.3)

### 流程 1: 输入准备与校验 (id: section-3.3-flow-1)

1. 触发条件：用户批准设计并进入实施规划后执行。
2. `AuditManifest` 描述三首曲目的目标 YAML、音频、现有 MIDI 和输出目录。
3. `InputResolver` 校验所有文件路径。
4. 异常路径：任一必要输入缺失时直接报错，报告缺失路径，不创建伪产物。

### 流程 2: YAML 反解为 reduced-target (id: section-3.3-flow-2)

1. 触发条件：目标 YAML 路径校验通过。
2. `YamlScoreDecoder` 读取 `song`、`keyboard` 和 `score`。
3. 将 token 映射回 MIDI pitch，将 beat 累积为绝对拍点。
4. 输出 `target_reduced.mid`、`target_reduced.musicxml` 和事件 JSON。
5. 异常路径：非法 token、非法 beat 或不可解析和弦直接报错。

### 流程 3: 原曲音频重新转录 (id: section-3.3-flow-3)

1. 触发条件：原曲音频路径校验通过。
2. `FreshTranscriptionRunner` 调用真实音频转谱链路。
3. 生成 `fresh_reference.mid`。
4. 导出 `fresh_reference.musicxml`。
5. 异常路径：转录依赖、模型或音频读取失败时直接记录阻塞，不使用现有 MIDI 代替。

### 流程 4: 现有 MIDI 标准化 (id: section-3.3-flow-4)

1. 触发条件：项目现有 MIDI 路径校验通过。
2. 将现有 MIDI 作为 `existing_reference.mid` 的来源记录或复制到审查目录。
3. 导出 `existing_reference.musicxml`。
4. 异常路径：现有 MIDI 不存在或不可解析时报告阻塞。

### 流程 5: 三路谱面对齐 (id: section-3.3-flow-5)

1. 触发条件：三路谱面数据准备完成。
2. `ScoreAligner` 依据 BPM / tempo 建立拍点轴。
3. 按 1 小节主窗口、0.5 拍或 1 拍细窗口统计三路谱面特征。
4. 输出 `alignment.json`。
5. 异常路径：tempo 不可解析或总时长严重不一致时输出高等级对齐问题。

### 流程 6: 乐理与排版规则审查 (id: section-3.3-flow-6)

1. 触发条件：对齐结果生成完成。
2. `LayoutRuleAuditor` 检查硬性问题、旋律可见性、低音锚点、声部分层、和弦密度、节奏织体和输入稳定性。
3. 为每个问题分配 severity、confidence、定位和证据。
4. 输出 `issues.json`。
5. 异常路径：参考源互相矛盾时降低置信度并标为 Info 或需听感复核。

### 流程 7: 报告汇总 (id: section-3.3-flow-7)

1. 触发条件：单曲审查完成。
2. `AuditReportWriter` 生成每首曲目的 `summary.md`。
3. 汇总三首曲目的共性问题，生成 `docs/score-audit-svsep-mpdr-v3/score_audit_svsep_mpdr_v3.md`。
4. 异常路径：如果某首曲目因真实依赖失败而未完成，报告必须如实标记阻塞原因。

---

## 关键决策 (id: section-3.4)

### 决策 1: 使用双源交叉验证而非单一 MIDI 参考 (id: section-3.4-decision-1)

- **背景**: 单一参考源可能把转录误差误判为精排问题。
- **选项**:
  - A: 只用项目现有 MIDI -- 快，但可能与当前 YAML 同源。
  - B: 只用重新转录 MIDI -- 独立，但可能引入新的转录误差。
  - C: 同时使用现有 MIDI 和重新转录 MIDI -- 工作量最大，但能区分参考不稳定和真实精排问题。
- **选择**: C
- **理由**: 用户希望按五线谱和乐理规范审查，审查比快速试听更需要降低误判。
- **后果**: 需要真实重新转录三首曲目，执行时间和依赖要求更高。

### 决策 2: 报告分层而不是所有差异都判错 (id: section-3.4-decision-2)

- **背景**: 36 键缩编必然与 88 键参考谱不同。
- **选项**:
  - A: 逐音对比，所有不同都判错 -- 简单但不符合缩编事实。
  - B: 按结构功能和钢琴谱排版规则分层判断 -- 更复杂，但更符合音乐缩编目标。
- **选择**: B
- **理由**: 本项目目标是可演奏缩编，不是完整复刻原谱。
- **后果**: 审查规则需要记录置信度和可接受取舍。

### 决策 3: 只做审查设计，不在本阶段修算法 (id: section-3.4-decision-3)

- **背景**: 当前阶段尚未进入实施计划，且用户需要先理解问题来源。
- **选项**:
  - A: 审查和修复同时做 -- 速度快但容易缺少追溯链。
  - B: 先完成审查与报告，再进入算法修复设计 -- 流程更长但更稳。
- **选择**: B
- **理由**: 符合 F 盘工程流程和设计先行要求。
- **后果**: 本轮产物是审查工具/报告设计，算法修正会作为后续阶段处理。

### 决策 4: 审查产物写入独立子目录 (id: section-3.4-decision-4)

- **背景**: 项目根级 `docs/DESIGN.md` 已存在另一轮设计文档，用户要求写入子文件夹。
- **选项**:
  - A: 覆盖根级 `docs/DESIGN.md` -- 标准路径清晰但会破坏既有文档。
  - B: 写入独立子目录 -- 保留既有文档，当前设计有独立追溯入口。
- **选择**: B
- **理由**: 用户明确要求写入子文件夹，且避免覆盖已有设计。
- **后果**: 后续 PLAN / PRACTICE / AUDIT 也应优先写入 `docs/score-audit-svsep-mpdr-v3/` 以保持追溯闭环。

---

## 模块间接口 (id: section-4.1)

### 接口: AuditManifest → InputResolver (id: section-4.1-iface-1)

- **调用方向**: 审查入口将曲目清单交给输入解析器。
- **方法**: 本地配置读取或命令行参数传递。
- **输入**: 曲目 ID、目标 YAML 路径、现有 MIDI 路径、原曲音频路径、输出目录。
- **输出**: 校验后的曲目输入对象；失败时返回明确路径错误。

### 接口: InputResolver → YamlScoreDecoder (id: section-4.1-iface-2)

- **调用方向**: 输入解析器为每首曲目提供目标 YAML 路径。
- **方法**: 本地文件读取。
- **输入**: YAML 路径与输出目录。
- **输出**: reduced-target 谱面事件、MIDI、MusicXML 和反解统计。

### 接口: InputResolver → FreshTranscriptionRunner (id: section-4.1-iface-3)

- **调用方向**: 输入解析器为每首曲目提供原曲音频路径。
- **方法**: 本地真实音频转谱调用。
- **输入**: 音频路径、工作目录、转录配置。
- **输出**: fresh-reference MIDI；失败时抛出真实错误。

### 接口: NotationExporter → ScoreAligner (id: section-4.1-iface-4)

- **调用方向**: 谱面导出器将三路标准事件交给对齐器。
- **方法**: 内存数据传递与 JSON 中间文件。
- **输入**: existing-reference、fresh-reference、reduced-target 三路事件。
- **输出**: `alignment.json`。

### 接口: ScoreAligner → LayoutRuleAuditor (id: section-4.1-iface-5)

- **调用方向**: 对齐器输出统计窗口，审查器读取并判定问题。
- **方法**: 结构化 JSON 数据传递。
- **输入**: 小节窗口、拍点窗口和局部异常窗口特征。
- **输出**: `issues.json`。

### 接口: LayoutRuleAuditor → AuditReportWriter (id: section-4.1-iface-6)

- **调用方向**: 审查器将问题清单与统计交给报告生成器。
- **方法**: 本地文件写入。
- **输入**: issue 列表、曲目统计、参考源一致性统计。
- **输出**: 单曲 `summary.md` 与总报告 Markdown。

---

## 外部接口 (id: section-4.2)

### 外部工具: 项目现有音频转谱链路 (id: section-4.2-ext-1)

- **提供方**: 本项目本机音频转换环境。
- **用途**: 从原曲音频重新生成 fresh-reference MIDI。
- **协议**: 本地 Python / 命令行调用。
- **失败处理**: 不降级；失败时直接报告真实依赖、模型或音频错误。

### 外部库: MIDI 与谱面处理库 (id: section-4.2-ext-2)

- **提供方**: 本机 Python 环境中的 `pretty_midi`、`music21` 或同类库。
- **用途**: 解析 MIDI、生成 MIDI、导出 MusicXML。
- **协议**: 本地库调用。
- **失败处理**: 不降级；失败时停止对应导出任务并报告原因。

### 外部查看器: MusicXML 五线谱查看工具 (id: section-4.2-ext-3)

- **提供方**: 用户本机可用的五线谱查看器，例如 MuseScore 或其他 MusicXML 查看工具。
- **用途**: 供用户人工查看导出的五线谱视图。
- **协议**: 本地文件打开。
- **失败处理**: 不影响结构化审查结果，但报告中说明未验证人工查看环境。

---

## 已知风险 (id: section-5.1)

### 风险 1: 重新转录结果本身存在误差 (id: section-5.1-risk-1)

- **描述**: 音频转 MIDI 可能识别错音、高低八度或节奏。
- **影响**: 可能将参考错误误判为 YAML 精排错误。
- **概率**: 高
- **缓解措施**: 同时对比项目现有 MIDI，并将参考源不一致区域标为低置信或 Info。

### 风险 2: MIDI 到 MusicXML 的谱面排版不等于人工钢琴谱 (id: section-5.1-risk-2)

- **描述**: 自动导出的 MusicXML 可能缺少人工谱面的声部、连线和手部分配。
- **影响**: 视觉谱面可读性有限。
- **概率**: 中
- **缓解措施**: 审查以结构化事件和规则统计为主，MusicXML 作为辅助视图。

### 风险 3: 三路谱面 tempo 或总时长不一致 (id: section-5.1-risk-3)

- **描述**: YAML BPM、现有 MIDI tempo 和新转录 MIDI tempo 可能不完全一致。
- **影响**: 小节对齐可能偏移，导致错误定位。
- **概率**: 中
- **缓解措施**: 对齐阶段使用拍点窗口和容差规则，不按事件序号硬比。

### 风险 4: 36 键约束导致真实不可避免的信息损失 (id: section-5.1-risk-4)

- **描述**: 原曲复杂织体无法完全塞入 36 键和有限同按能力。
- **影响**: 部分差异虽然显著，但属于合理缩编。
- **概率**: 高
- **缓解措施**: 报告中明确区分可接受缩编取舍与真实精排问题。

### 风险 5: 依赖环境不可用 (id: section-5.1-risk-5)

- **描述**: 转录、MIDI 解析或 MusicXML 导出依赖可能缺失。
- **影响**: 无法完成 fresh-reference 或谱面导出。
- **概率**: 中
- **缓解措施**: 规划阶段先做真实环境检查；失败时报告阻塞，不生成伪结果。

---

## 未决问题 (id: section-5.2)

### 问题 1: 三首曲目的精确音频与现有 MIDI 映射 (id: section-5.2-q-1)

- **描述**: 实施前需要由输入解析器或人工确认三份目标 YAML 对应的原曲音频和现有 MIDI 路径。
- **阻塞规划**: 否
- **负责人**: JucieOvo

### 问题 2: MusicXML 导出工具选择 (id: section-5.2-q-2)

- **描述**: 需要在计划阶段确认使用 `music21`、MuseScore 命令行或其他本机工具导出 MusicXML。
- **阻塞规划**: 否
- **负责人**: JucieOvo

### 问题 3: 审查阈值初始值 (id: section-5.2-q-3)

- **描述**: 和弦密度、旋律断裂、低音缺失、音区碰撞等阈值需在计划阶段定义为可配置参数。
- **阻塞规划**: 否
- **负责人**: JucieOvo

---

## 假设列表 (id: section-5.3)

### 假设 1: 项目存在三首曲目的原曲音频 (id: section-5.3-a-1)

- **假设内容**: 项目根目录或工作目录中存在可用于重新转录的原曲音频。
- **如果假设不成立**: fresh-reference 无法生成，对应曲目审查阻塞。
- **验证方式**: 计划阶段用只读文件搜索确认音频路径。

### 假设 2: 项目现有 MIDI 可以作为第一参考源 (id: section-5.3-a-2)

- **假设内容**: `work/*/midi/*.mid` 中存在三首曲目的现有 MIDI。
- **如果假设不成立**: 对应曲目只能使用 fresh-reference，报告需降低部分结论置信度。
- **验证方式**: 计划阶段逐曲校验 MIDI 路径。

### 假设 3: YAML token 到 MIDI pitch 的反解规则与现有播放器一致 (id: section-5.3-a-3)

- **假设内容**: 反解逻辑可以复用现有键盘映射和 C3-B5 目标音域约束。
- **如果假设不成立**: 需先补充 token 规范审查，否则 reduced-target 谱面不可信。
- **验证方式**: 使用 `PianoConfigLoader` 真实读取 YAML，并对 token 映射做往返校验。

### 假设 4: 用户接受审查结论中包含需听感复核项 (id: section-5.3-a-4)

- **假设内容**: 因参考转录可能不稳定，部分问题不会直接判为算法错误。
- **如果假设不成立**: 需要用户提供人工确认谱或官方授权谱作为更强参考。
- **验证方式**: 报告中明确列出置信度和复核原因。

---

## 计划变更项 (id: section-6.1)

### 变更: 新增审查设计与追溯文档目录 (id: change-1)

@DESIGN:section-3.4-decision-4

- **操作类型**: 新增
- **目标**: `docs/score-audit-svsep-mpdr-v3/DESIGN.md:1`
- **内容**: 保存本次五线谱对照审查的 i2aspec 设计文档。
- **原因**: 用户要求写入子文件夹，且根级 `docs/DESIGN.md` 已有其他设计内容。
- **影响**: 后续 PLAN、PRACTICE、AUDIT 应在同一子目录内形成追溯闭环。

### 变更: 新增审查工作产物目录 (id: change-2)

@DESIGN:FR-007

- **操作类型**: 新增
- **目标**: `work/score_audit/` 新目录
- **内容**: 保存每首曲目的参考 MIDI、MusicXML、对齐 JSON、问题 JSON 和单曲报告。
- **原因**: 审查产物较多，需要与原始 `config/` 和现有 `work/` 产物隔离。
- **影响**: 不覆盖已有曲目产物；新增目录可独立清理和复查。

### 变更: 新增 YAML 反解审查能力 (id: change-3)

@DESIGN:FR-002

- **操作类型**: 新增
- **目标**: 新文件，计划阶段确定具体路径
- **内容**: 将目标 YAML score 反解为带事件编号的 MIDI note 事件、MIDI 文件和 MusicXML。
- **原因**: 当前 YAML 是游戏输入格式，无法直接按五线谱规则审查。
- **影响**: 依赖现有 YAML token 规范和键盘映射规则。

### 变更: 新增双源参考转录与标准化能力 (id: change-4)

@DESIGN:FR-004

- **操作类型**: 新增
- **目标**: 新文件，计划阶段确定具体路径
- **内容**: 对原曲音频重新转录，并将项目现有 MIDI 标准化为可对齐参考。
- **原因**: 需要降低单一参考源带来的误判风险。
- **影响**: 依赖真实音频转谱环境；失败时阻塞对应曲目。

### 变更: 新增谱面对齐与规则审查能力 (id: change-5)

@DESIGN:FR-005
@DESIGN:FR-006

- **操作类型**: 新增
- **目标**: 新文件，计划阶段确定具体路径
- **内容**: 按小节和拍点窗口对齐三路谱面，并执行旋律、低音、声部、密度、节奏和输入稳定性审查。
- **原因**: 用户需要将“精排不好”拆解为可修复的问题类型。
- **影响**: 输出 `alignment.json` 和 `issues.json`，供报告和后续算法优化引用。

### 变更: 新增审查报告生成能力 (id: change-6)

@DESIGN:FR-007

- **操作类型**: 新增
- **目标**: `docs/score-audit-svsep-mpdr-v3/score_audit_svsep_mpdr_v3.md` 新文件
- **内容**: 生成三首曲目的总审查报告，汇总共性问题和后续算法优化建议。
- **原因**: 用户需要面向乐理盲也可理解的结构化审查结论。
- **影响**: 为后续 PLAN / PRACTICE / AUDIT 提供问题来源和验收依据。

---

## 受影响范围 (id: section-6.2)

| 文件 | 范围 | 变更类型 | 关联变更 ID |
|------|------|----------|------------|
| `docs/score-audit-svsep-mpdr-v3/DESIGN.md` | 新文件 | 新增 | change-1 |
| `docs/score-audit-svsep-mpdr-v3/score_audit_svsep_mpdr_v3.md` | 新文件 | 新增 | change-6 |
| `work/score_audit/` | 新目录 | 新增 | change-2 |
| 审查工具源码路径待计划阶段确定 | 新文件 | 新增 | change-3, change-4, change-5 |
| `config/one_last_kiss_svsep_mpdr_v3.yaml:1` | 只读输入 | 不修改 | change-3, change-5 |
| `config/tada_koe_hitotsu_svsep_mpdr_v3.yaml:1` | 只读输入 | 不修改 | change-3, change-5 |
| `config/cruel_angel_svsep_mpdr_v3.yaml:1` | 只读输入 | 不修改 | change-3, change-5 |

---

## 追溯索引 (id: appendix-a)

| 锚点 ID | 章节 | 供 PLAN 引用 |
|----------|------|-------------|
| section-1.1 | 背景与动机 | @DESIGN:section-1.1 |
| section-1.2 | 目标与非目标 | @DESIGN:section-1.2 |
| section-1.3 | 范围边界 | @DESIGN:section-1.3 |
| FR-001 | 解析审查清单与输入材料 | @DESIGN:FR-001 |
| FR-002 | YAML 反解为谱面事件 | @DESIGN:FR-002 |
| FR-003 | 导出 MIDI 与 MusicXML 谱面视图 | @DESIGN:FR-003 |
| FR-004 | 重新从原曲音频真实转录 | @DESIGN:FR-004 |
| FR-005 | 三路谱面对齐 | @DESIGN:FR-005 |
| FR-006 | 按钢琴谱排版规则审查 | @DESIGN:FR-006 |
| FR-007 | 输出结构化问题清单和报告 | @DESIGN:FR-007 |
| NFR-001 | 真实执行与失败透明 | @DESIGN:NFR-001 |
| NFR-002 | 可追溯性 | @DESIGN:NFR-002 |
| NFR-003 | 不覆盖既有产物 | @DESIGN:NFR-003 |
| NFR-004 | Windows 本机兼容 | @DESIGN:NFR-004 |
| NFR-005 | 审查结论分层 | @DESIGN:NFR-005 |
| section-3.2-mod-A | AuditManifest | @DESIGN:section-3.2-mod-A |
| section-3.2-mod-B | InputResolver | @DESIGN:section-3.2-mod-B |
| section-3.2-mod-C | YamlScoreDecoder | @DESIGN:section-3.2-mod-C |
| section-3.2-mod-D | FreshTranscriptionRunner | @DESIGN:section-3.2-mod-D |
| section-3.2-mod-E | NotationExporter | @DESIGN:section-3.2-mod-E |
| section-3.2-mod-F | ScoreAligner | @DESIGN:section-3.2-mod-F |
| section-3.2-mod-G | LayoutRuleAuditor | @DESIGN:section-3.2-mod-G |
| section-3.2-mod-H | AuditReportWriter | @DESIGN:section-3.2-mod-H |
| section-3.3-flow-1 | 输入准备与校验 | @DESIGN:section-3.3-flow-1 |
| section-3.3-flow-2 | YAML 反解为 reduced-target | @DESIGN:section-3.3-flow-2 |
| section-3.3-flow-3 | 原曲音频重新转录 | @DESIGN:section-3.3-flow-3 |
| section-3.3-flow-4 | 现有 MIDI 标准化 | @DESIGN:section-3.3-flow-4 |
| section-3.3-flow-5 | 三路谱面对齐 | @DESIGN:section-3.3-flow-5 |
| section-3.3-flow-6 | 乐理与排版规则审查 | @DESIGN:section-3.3-flow-6 |
| section-3.3-flow-7 | 报告汇总 | @DESIGN:section-3.3-flow-7 |
| section-3.4-decision-1 | 使用双源交叉验证 | @DESIGN:section-3.4-decision-1 |
| section-3.4-decision-2 | 报告分层 | @DESIGN:section-3.4-decision-2 |
| section-3.4-decision-3 | 先审查不修算法 | @DESIGN:section-3.4-decision-3 |
| section-3.4-decision-4 | 独立子目录 | @DESIGN:section-3.4-decision-4 |
| change-1 | 新增审查设计与追溯文档目录 | @DESIGN:change-1 |
| change-2 | 新增审查工作产物目录 | @DESIGN:change-2 |
| change-3 | 新增 YAML 反解审查能力 | @DESIGN:change-3 |
| change-4 | 新增双源参考转录与标准化能力 | @DESIGN:change-4 |
| change-5 | 新增谱面对齐与规则审查能力 | @DESIGN:change-5 |
| change-6 | 新增审查报告生成能力 | @DESIGN:change-6 |
