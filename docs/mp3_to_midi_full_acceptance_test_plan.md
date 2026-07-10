# MP3 → MIDI → 36 键 YAML 全链路真实验收方案

> 编写日期：2026-07-10  
> 作者：JucieOvo  
> 状态：待技术总监审批  
> 验收范围：两首 MAESTRO 真实钢琴录音的 MP3 输入、Demucs 分轨、Transkun 转录、`attention_weighted` 缩编与最终 YAML 输出

## 一、目标与结论边界

本方案验证当前项目按真实用户使用方式运行时，是否能够完成：

```text
MP3
→ Demucs 钢琴分轨
→ Transkun 原始转录 MIDI
→ BPM 写入与 MIDI 清洗/起音聚类
→ attention_weighted 36 键缩编
→ YAML
```

验收分为两个互不替代的层次：

1. **转录正确性**：将 `work/midi/input.mid` 与同一次 MAESTRO Disklavier 演奏的 `reference_performance.midi` 做逐音比对。
2. **缩编可用性**：验证最终 YAML 可被项目严格加载、所有 token 可映射、所有和弦满足 36 键及并发键数约束。

本次通过后，只能得出“这两首真实样本在当前版本、当前模型与当前环境下通过验收”。不得据此推导 Demucs 对混合音乐、所有钢琴曲、或任意缩编模式均正确。

## 二、基准输入与权威参考

| 曲目 | MP3 输入 | 同演奏 MIDI 金标准 | 用途 |
|---|---|---|---|
| 巴赫《C 大调前奏曲与赋格》BWV 846 | `work/transcription_benchmark/bach_bwv846/input.mp3` | `work/transcription_benchmark/bach_bwv846/reference_performance.midi` | 规则织体与基础正确性 |
| 肖邦《革命练习曲》Op.10 No.12 | `work/transcription_benchmark/chopin_op10_no12/input.mp3` | `work/transcription_benchmark/chopin_op10_no12/reference_performance.midi` | 高密度、宽音域与复杂节奏压力 |

两组 MP3 均由同一条 MAESTRO WAV 转码而来，参考 MIDI 为同一次 Disklavier 演奏的 performance MIDI。文件身份、哈希、时长与许可记录见 `work/transcription_benchmark/README.md` 及各曲目 `metadata.json`。

## 三、固定执行配置

### 3.1 主验收模式

本轮采用 `attention_weighted`：它是当前成熟的默认缩编路线，且不依赖 `piano_svsep` 的额外模型注册。为避免比较口径变化，两首曲目使用完全相同的参数：

```text
--bpm 0
--pitch-compression-mode attention_weighted
--allow-accidentals
--out-of-range-policy octave_fold
--max-chord-notes 6
--left-max-chord-notes 3
--phrase-gap-beats 0.5
--global-trend-alpha 0.05
--global-trend-window-beats 4.0
```

`--bpm 0` 将触发 librosa BPM 检测，并由 `_fix_midi_tempo()` 写入转录 MIDI 的 tempo 元数据。该步骤不应改写 MIDI 音符的起音、结束、音高或力度。

### 3.2 不采用的模式

- 不采用 `svsep_mpdr`：其额外依赖 `piano_svsep` 包注册，当前环境检查显示该包尚未安装为可导入包。
- 不以 `score_aware_theory` 作为本轮主验收模式：该模式用于下一阶段的乐谱语义验证。

因此，**本轮不要求 MusicXML 产物**。`attention_weighted` 正常完成时应得到 Demucs stem、Transkun MIDI、`conversion_report.json` 与最终 YAML；若强制把 MusicXML 当作条件，会造成错误验收。

## 四、执行前硬性预检

以下检查必须全部真实通过；任一失败即停止，不使用伪数据、替代模型或 CPU 降级掩盖问题。

| 检查项 | 预期 |
|---|---|
| 基准文件 | 两首 `input.mp3` 与两个 `reference_performance.midi` 存在且哈希匹配 metadata |
| Python 环境 | 当前解释器可导入 torch、demucs、transkun、pretty_midi、librosa、music21、mir_eval、soxr、moduleconf、PyYAML |
| GPU | `torch.cuda.is_available()` 为 `True`；本次明确使用 CUDA |
| Transkun 权重 | 已存在包内 `transkun/pretrained/2.0.pt` 与同名 `.conf` |
| Demucs | `python -m demucs.separate --help` 可用；若 `htdemucs_6s` 未缓存，首次真实运行允许联网下载官方权重，下载失败则直接停止 |
| 音频工具 | `ffmpeg` 与 `ffprobe` 可执行 |
| 评测库 | `mir_eval.transcription` 与 `mir_eval.transcription_velocity` 可导入 |

## 五、每首曲目的真实执行命令

### 5.1 巴赫 BWV 846

```powershell
python src/audio_to_yaml_converter.py `
  --audio "work/transcription_benchmark/bach_bwv846/input.mp3" `
  --output-yaml "work/transcription_benchmark/bach_bwv846/final_attention_weighted.yaml" `
  --work-dir "work/transcription_benchmark/bach_bwv846/pipeline" `
  --song-name "巴赫 C大调前奏曲与赋格 BWV 846" `
  --bpm 0 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals `
  --out-of-range-policy octave_fold `
  --max-chord-notes 6 `
  --left-max-chord-notes 3 `
  --phrase-gap-beats 0.5 `
  --global-trend-alpha 0.05 `
  --global-trend-window-beats 4.0 `
  --transcription-device cuda
```

### 5.2 肖邦 Op.10 No.12

```powershell
python src/audio_to_yaml_converter.py `
  --audio "work/transcription_benchmark/chopin_op10_no12/input.mp3" `
  --output-yaml "work/transcription_benchmark/chopin_op10_no12/final_attention_weighted.yaml" `
  --work-dir "work/transcription_benchmark/chopin_op10_no12/pipeline" `
  --song-name "肖邦 革命练习曲 Op.10 No.12" `
  --bpm 0 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals `
  --out-of-range-policy octave_fold `
  --max-chord-notes 6 `
  --left-max-chord-notes 3 `
  --phrase-gap-beats 0.5 `
  --global-trend-alpha 0.05 `
  --global-trend-window-beats 4.0 `
  --transcription-device cuda
```

## 六、必须保留的真实中间产物

每首曲目输出隔离在各自的 `pipeline/` 目录，以免两份名为 `input` 的中间文件相互覆盖。

| 阶段 | 预期产物 | 验收目的 |
|---|---|---|
| 分轨 | `pipeline/demucs/htdemucs_6s/input/piano.wav` | 检查 Demucs 是否真实完成并可解码 |
| 转录 | `pipeline/midi/input.mid` | 原始 Transkun MIDI；唯一用于与 MAESTRO performance MIDI 逐音比对的估计结果 |
| 转换报告 | `pipeline/conversion_report.json` | 记录 BPM、压缩统计和运行信息 |
| 最终缩编 | `final_attention_weighted.yaml` | 游戏键盘谱输出 |
| 评测 | `transcription_evaluation.json` 与 `transcription_evaluation.md` | 逐音指标、误差分布与失败定位 |
| 验收报告 | `acceptance_report.md` | 两层验收结论与全部命令退出状态 |

## 七、最小新增评测工具

新增一个只读评测脚本，例如 `work/transcription_benchmark/evaluate_transcription.py`。它不得修改估计 MIDI、参考 MIDI、WAV、MP3 或 YAML，只读取两个 MIDI 并生成 JSON/Markdown 报告。

### 7.1 输入

- `--reference-midi`：MAESTRO `reference_performance.midi`
- `--estimated-midi`：当前运行生成的 `pipeline/midi/input.mid`
- `--output-json`、`--output-markdown`

### 7.2 核心指标

使用 `mir_eval.transcription`，将 MIDI pitch 转为 Hz，onset/offset 转为秒：

| 指标 | API / 口径 |
|---|---|
| 音高+onset+offset P/R/F1 | `mir_eval.transcription.precision_recall_f1_overlap()` |
| 音高+onset P/R/F1 | 同接口，`offset_ratio=None` |
| onset-only P/R/F1 | `mir_eval.transcription.evaluate()` 的诊断项；不作为主结论 |
| 匹配对 | `mir_eval.transcription.match_notes()` |
| onset/offset 误差 | 基于一对一匹配对统计 MAE、RMSE、P50、P95 |
| velocity | 使用 `mir_eval.transcription_velocity` 的 F1；另输出匹配对 velocity MAE、RMSE 与 Pearson 相关系数 |
| 错误清单 | 漏检、额外、错音候选及对应时间位置 |

默认 MIREX 容差：onset 50 ms、pitch 50 cents、offset 容差 `max(参考时值×20%, 50 ms)`。此容差是评测协议，不是项目“通过阈值”。

### 7.3 对齐原则

MAESTRO 参考 MIDI 与 MP3 的来源为同一次演奏，默认不做时间伸缩、DTW 或静默裁切。先直接评测；仅当 onset 误差分布显示稳定全局偏移时，才将偏移值与原始结果一并记录并重新计算校准指标。禁止静默修改 MIDI 时间轴。

## 八、验收门与报告口径

### 8.1 门 A：管线可运行性

每首曲目必须满足：

1. 主命令退出码为 0。
2. 预期 stem、raw MIDI、conversion report、YAML 均存在且非空。
3. stem 和 YAML 可被真实解析；raw MIDI 可由 `pretty_midi` 解析。
4. YAML 能被 `PianoConfigLoader` 严格加载。

### 8.2 门 B：转录正确性

必须报告全部 note 指标、速度指标和错误清单。**本轮不预设伪造的行业通过阈值**；先观察巴赫与肖邦的真实基线，再由技术总监批准长期门槛。

报告需明确区分：

- Demucs 引起的缺失或新增；
- Transkun 的漏检、插入、错音、onset/offset 误差；
- 后续清洗、聚类和缩编造成的变化。

### 8.3 门 C：缩编输出约束

最终 YAML 必须通过：

1. 必填字段与 BPM 严格校验；
2. token 映射校验；
3. 所有音符在 C3–B5 / MIDI 48–83 映射能力范围内；
4. 每个和弦音数不超过 `max_chord_notes=6`；
5. 不存在同一时间片相同 token 冲突；
6. 总时长、事件数和异常统计写入报告。

门 C 只验证输出合法与约束合规，不替代人工乐理听感审核。

## 九、执行顺序

1. 运行所有预检并保存版本、CUDA、模型缓存和磁盘空间证据。
2. 实现并以真实 MAESTRO MIDI 做小范围自检的评测器；不使用 Mock 或伪造 MIDI。
3. 运行巴赫完整链路，保存所有中间产物。
4. 运行肖邦完整链路，保存所有中间产物。
5. 对两首 raw Transkun MIDI 做原始、未校准逐音评测。
6. 校验两个 YAML，运行既有 score audit 中可复用的真实检查。
7. 输出总报告，按曲目与阶段列出成功、失败、错误分布和限制。
8. 对报告、数据路径、哈希、命令退出状态和指标计算进行独立复审。

## 十、失败处理

| 情况 | 处理 |
|---|---|
| Demucs 权重下载失败 | 直接记录网络/模型阻塞并停止该曲目；不跳过 Demucs |
| CUDA 或 Transkun 权重不可用 | 直接报错停止；不切换 CPU 伪装为同一验收 |
| 主命令失败 | 保留日志和已生成中间产物，定位失败阶段；不得使用手工替代 MIDI |
| raw MIDI 缺失或不可解析 | 判定门 A 失败，不能进行 F1 评测 |
| 评测器错误 | 修复评测器后重跑；不得手填指标 |
| YAML 约束错误 | 判定门 C 失败，转录门 B 仍按 raw MIDI 单独报告 |

## 十一、审批点

获得明确“开始”后，按本方案执行以下有副作用操作：

1. 创建评测器代码与真实验收输出目录。
2. 首次下载缺失的官方 Demucs 权重（若需要）。
3. 调用 Demucs、Transkun、librosa、缩编和 YAML 校验。
4. 写入真实评测 JSON、Markdown 和验收报告。

未获批准前，仅保留本方案，不运行模型、不创建评测代码、不生成测试结果。
