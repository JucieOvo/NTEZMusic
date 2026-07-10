# Transkun 原始 88 键 MIDI 真实评测方案

> 编写日期：2026-07-10  
> 作者：JucieOvo  
> 状态：已执行完成，结果见 `docs/transkun_raw_midi_evaluation_report.md`

## 一、目标与范围

本次评测只回答一个问题：在不经过 `_fix_midi_tempo()` 修改的条件下，Transkun 2.0.1 从真实音频生成的 88 键 MIDI 质量如何，以及剩余错误主要来自音高内容还是起音时序。

评测范围止于 88 键 MIDI，不进入 MIDI 清洗、MusicXML、乐理分析、36 键缩编、YAML 生成或自动弹奏。

## 二、评测对象定义

为避免继续混淆“原始输出”和“后处理输出”，统一定义三类不可变产物：

| 标识 | 产物 | 定义 |
|------|------|------|
| A | MAESTRO 参考 MIDI | 与真实录音配对的 Disklavier `reference_performance.midi` |
| B | Transkun 原始 MIDI | `model.transcribe()` 得到 `notes_est` 后，由 `writeMidi()` 直接写出的 120 BPM MIDI，不调用 `_fix_midi_tempo()` |
| C | tempo 覆盖后 MIDI | 当前主管线生成的 `pipeline/midi/input.mid`，其 tempo 已被 librosa 检测值原地覆盖 |

当前 A、C 已存在，B 没有被项目保留。因此，现有 C 不能直接改名或当作 B 使用。

## 三、基准曲目与真实环境

首轮使用已有两组 MAESTRO v3.0.0 真实配对数据：

1. 巴赫 BWV 846：`work/transcription_benchmark/bach_bwv846/`
2. 肖邦 Op.10 No.12：`work/transcription_benchmark/chopin_op10_no12/`

执行环境固定为：

- Windows 11；
- RTX 3090 24GB；
- PyTorch 2.7.1 + CUDA 11.8；
- Transkun 2.0.1，既有 checkpoint；
- mir_eval 0.8.2；
- 使用真实音频、真实模型和真实参考 MIDI；
- CUDA、checkpoint 或真实依赖不可用时直接报错停止，不切换 CPU，不使用 Mock、Stub、缓存伪结果或降级路径。

## 四、执行前产物审计

在重新推理前，先只读确认每首曲目的以下文件：

- `input.mp3`；
- `source.wav`；
- `reference_performance.midi`；
- Demucs 生成的 `piano.wav`；
- 当前 tempo 覆盖后的 `pipeline/midi/input.mid`；
- checkpoint、模型配置和现有评测报告。

记录文件大小、SHA-256、音频采样率、声道数、时长、MIDI TPQN、tempo map、音符数量及音高范围。任何基准文件缺失或哈希与现有 metadata 不一致时停止执行。

## 五、原始 MIDI 生成策略

### 5.1 权威产物

使用与当前项目相同的 Transkun 加载、重采样、segment size、segment hop size 和 checkpoint，对现有 Demucs `piano.wav` 重新执行一次真实推理。

推理结果必须分别保存：

```text
evaluation/raw_transkun/notes_est.json
evaluation/raw_transkun/transkun_raw_120bpm.mid
evaluation/raw_transkun/run_manifest.json
```

其中：

- `notes_est.json` 保存模型输出的 pitch、start、end、velocity、hasOnset 和 hasOffset；
- `transkun_raw_120bpm.mid` 是 B，不允许后续原地修改；
- `run_manifest.json` 保存输入哈希、checkpoint 哈希、依赖版本、GPU、采样率、分段参数、运行时间和输出哈希。

### 5.2 tempo 派生产物

如果需要复现当前 C，只能从 B 复制生成独立派生文件，例如：

```text
evaluation/tempo_rewritten/transkun_detected_bpm.mid
```

禁止修改 B。随后验证 B 与派生 C 除 tempo 元事件外，音符 pitch、velocity、tick、事件顺序和音符数量完全一致。

### 5.3 现有 C 的用途

现有 `pipeline/midi/input.mid` 只用于复现实验和语义对照。不能通过简单改回 120 BPM 后将其宣称为原始文件；这种做法最多作为辅助一致性验证，权威 B 必须来自重新推理并在 tempo 修改前独立落盘。

## 六、首轮评测矩阵

首轮只控制 tempo 变量，避免同时改变多个因素：

| 组别 | 估计 MIDI | 参考 MIDI | 目的 |
|------|-----------|-----------|------|
| E1 | 新生成的 B | A | 测量 Transkun 原始端到端质量 |
| E2 | 由 B 独立生成的 tempo 覆盖版 | A | 测量 `_fix_midi_tempo()` 的独立影响 |
| E3 | 现有 C | A | 复核历史结果是否可复现 |
| E4 | B 经诊断性全局 offset 校准 | A | 判断是否存在固定录音链路偏移 |
| E5 | B 经诊断性线性 scale 校准 | A | 判断 B 是否仍有时钟缩放问题 |
| E6 | B 经局部 DTW/beat 对齐 | A | 仅诊断局部 tempo 漂移，不作为端到端成绩 |

只有 E1 可作为“原始 Transkun 质量”。E4、E5、E6 只能作为根因诊断，不能替代 E1。

## 七、指标与错误拆分

每首曲目、每个实验组至少输出：

1. onset-only Precision、Recall、F1，忽略 pitch；
2. onset+pitch Precision、Recall、F1，使用 50ms/50cents；
3. onset+pitch+offset Precision、Recall、F1；
4. velocity-aware F1；
5. frame-level piano-roll Precision、Recall、F1；
6. 匹配音符 onset/offset 残差的中位数、MAE、RMSE、P95；
7. 参考音符数、估计音符数、匹配数、漏检数和插入数；
8. 按音区、力度、局部复音密度、音符时值和 Transkun 分段边界分桶的错误统计。

根据指标差异判断问题类型：

- onset-only 高而 onset+pitch 低：以错音、漏音或假音为主；
- onset-only 同样低：存在显著起音时序问题；
- onset+pitch 高而 offset 指标显著下降：以时值或踏板相关问题为主；
- E1 明显高于 E2：确认 tempo 覆盖是主要污染源；
- E5 明显高于 E1：B 仍存在全局时钟缩放；
- E6 后仍低：剩余问题主要不是局部 tempo 漂移，而是音高内容或真实漏检/插入。

## 八、第二阶段消融触发条件

只有当 E1 的 onset+pitch F1 仍明显不足时，才进入输入前处理消融：

| 输入 | Demucs | 目的 |
|------|--------|------|
| MAESTRO `source.wav` | 否 | Transkun 在无损独奏钢琴上的基础上限 |
| `input.mp3` | 否 | 单独测量 MP3 编码影响 |
| `source.wav` | 是 | 单独测量 Demucs 对无损独奏钢琴的影响 |
| `input.mp3` | 是 | 复现当前完整前端输入 |

四组必须使用同一 checkpoint、同一重采样方式和同一分段参数。不得在同一次对比中同时更换模型、阈值或评测协议。

## 九、验收与停止条件

评测完成必须同时满足：

1. A、B、C 定义清晰且文件互不覆盖；
2. B 的 tempo 为 120 BPM，并能从 `notes_est` 重建相同音符内容；
3. B 与 tempo 派生版除 tempo 元事件及其秒级解释外，音符事件语义一致；
4. 两首曲目均生成完整 JSON 指标和逐音错误明细；
5. Raw 与诊断性校准指标分别报告；
6. 不根据单一 F1 提前判断旋律或节奏问题；
7. 所有结果都可由 manifest 中的真实输入和环境复现。

如果真实依赖缺失、CUDA 不可用、checkpoint 不一致、基准哈希不一致或 B 无法可靠生成，则停止并报告阻塞，不用替代数据继续。

## 十、预计涉及的实现范围

审批后再确定最小实现方式，预计仅涉及：

1. 为评测保留 `notes_est` 和 tempo 修改前 MIDI 的独立产物；
2. 扩充或新增只服务于 MAESTRO 真实评测的诊断脚本；
3. 生成独立评测目录、manifest、JSON 指标和 Markdown 报告。

不得在本轮同时修改 Transkun 模型、Demucs 参数、缩编逻辑或播放层。评测结论确认前不实施修复。
