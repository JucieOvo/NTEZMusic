# 编曲算法基准实验实施计划

> 作者：JucieOvo
> 日期：2026-07-11
> 关联设计：`docs/superpowers/specs/2026-07-11-arrangement-algorithm-benchmark-design.md`
> 执行环境：Windows 11 / Python 3.10
> 状态：已获“开始”授权，待计划审查后执行

## 一、实施目标

在不修改现有缩编算法参数和业务逻辑的前提下，建立独立的真实基准实验子系统，并用两首指定 MP3 完成以下闭环：

1. Demucs 钢琴分轨与 Transkun 原始 B MIDI 生成；
2. 五种正式算法的固定参数横向运行；
3. `score_aware_theory` 真实端到端就绪检查；
4. YAML 严格解析、36 键 MIDI 反解与硬约束校验；
5. 同一真实采样大钢琴音源下的 FluidSynth WAV 渲染与 ffmpeg MP3 编码；
6. 六维原曲保真指标、双歌曲等权排名与匿名盲听材料生成；
7. manifest、许可证说明、失败日志和最终报告输出。

本计划不开发新缩编算法，不针对歌曲调参，不使用 Mock、Stub、正弦波、伪造输入或伪造结果。

## 二、文件结构

### 2.1 新增生产文件

| 文件 | 单一职责 |
|------|----------|
| `src/arrangement_benchmark/__init__.py` | 基准实验包声明 |
| `src/arrangement_benchmark/models.py` | 不可变配置、产物路径、约束结果和指标结果数据模型 |
| `src/arrangement_benchmark/config_loader.py` | 严格加载并校验集中实验配置 |
| `src/arrangement_benchmark/score_io.py` | 使用真实项目 YAML 格式严格解析 token，并反解统一力度、无踏板的36键 MIDI |
| `src/arrangement_benchmark/constraints.py` | 校验 YAML、反解 MIDI、WAV、MP3 和异环硬约束 |
| `src/arrangement_benchmark/metrics.py` | 计算 D1-D6 六维保真指标与双歌曲等权总分 |
| `src/arrangement_benchmark/rendering.py` | FluidSynth 真实采样渲染、ffmpeg 编码和产物回读验证 |
| `src/arrangement_benchmark/runner.py` | 编排素材准备、算法执行、渲染、指标、排名、盲听和 manifest |
| `src/arrangement_benchmark/cli.py` | Windows 命令行入口，错误直接退出 |
| `config/arrangement_benchmark_20260711.yaml` | 两首歌曲、候选算法、统一参数、音源、工具和输出路径的唯一配置源 |

### 2.2 新增真实测试文件

| 文件 | 使用的真实数据 |
|------|----------------|
| `tests/test_arrangement_benchmark_config.py` | 正式实验配置和两首真实 MP3 |
| `tests/test_arrangement_benchmark_score_io.py` | `config/cruel_angel_svsep_mpdr_v3.yaml` 与真实反解 MIDI |
| `tests/test_arrangement_benchmark_constraints.py` | 现有真实 YAML、`work/score_audit/cruel_angel/target_reduced.mid` |
| `tests/test_arrangement_benchmark_metrics.py` | `work/score_audit/cruel_angel/existing_reference.mid` 与 `target_reduced.mid` |
| `tests/test_arrangement_benchmark_rendering.py` | 合法 SoundFont、FluidSynth、ffmpeg 和真实 MIDI |
| `tests/test_arrangement_benchmark_runner.py` | 正式配置、真实现有 MIDI/YAML 产物与真实工具预检 |

### 2.3 允许修改的现有文件

| 文件 | 修改边界 |
|------|----------|
| `docs/superpowers/specs/2026-07-11-arrangement-algorithm-benchmark-design.md` | 仅修正已发现的真实 YAML 接口与时值语义，不改变已批准目标 |
| `AGENTS.md` | 实验完成后补充基准命令和产物路径，不修改现有命令行为 |

禁止将基准逻辑继续堆入 `src/audio_to_yaml_converter.py`。新子系统只能调用其现有公开类和配置；如必须使用私有 MPDR 旋律接口，先记录技术债，不在本轮重构缩编算法。

## 三、固定接口与真实数据

### 3.1 输入

- `F:\NTEZMusic\【Animenz】残酷天使的行动纲领 – 新世纪福音战士 OP1 钢琴版.mp3`
- `F:\NTEZMusic\ただ声一つ (只想说一声) - ロクデナシ.mp3`

### 3.2 不可变基准 MIDI

不得调用 `AudioToYamlPipeline.run(--audio ...)` 生成基准 MIDI，因为现有 `PianoTranscriber.transcribe()` 会调用 `_fix_midi_tempo()`。

真实流程固定为：

```text
MP3
  -> Demucs htdemucs_6s piano stem
  -> work/transcription_benchmark/raw_midi_generator.py
  -> transkun_raw_120bpm.mid
  -> 发布为 reference/base_midi/song.mid
  -> 全阶段重复校验 SHA-256
```

BPM 检测值写入 sidecar 和算法配置，不改写 B MIDI。

### 3.3 候选算法

- `octave_fold`
- `adaptive_octave_fold`
- `hands_decoupled`
- `attention_weighted`
- `svsep_mpdr`

`score_aware_theory` 仅在两首歌均产生非空、可重读且通过全部硬约束的 YAML 后进入排名。原始 B MIDI 是唯一 R0 参考，`none` 不运行。

## 四、任务分解与 TDD 顺序

## 任务 1：集中配置和数据模型

**新增文件**：

- `config/arrangement_benchmark_20260711.yaml`
- `src/arrangement_benchmark/__init__.py`
- `src/arrangement_benchmark/models.py`
- `src/arrangement_benchmark/config_loader.py`
- `tests/test_arrangement_benchmark_config.py`

### RED

1. 编写测试，从正式配置加载两首真实 MP3。
2. 断言两首输入存在且 SHA-256 可计算。
3. 断言候选集合、权重之和、输出根目录、统一 `reduced_velocity`、工具路径和 SoundFont 字段完整，并确认 `left_max_chord_notes=3` 与当前 CLI 默认值一致。
4. 断言任何缺字段、相对越界路径或权重和不为 1 时直接抛错。

真实测试命令：

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_config.py -q
```

预期 RED：`arrangement_benchmark` 包不存在，测试导入失败。

### GREEN

实现冻结 dataclass 与严格配置加载器。所有数值来自 YAML 配置，不在业务代码硬编码。模块、类、函数和关键变量使用详细中文注释，作者统一为 JucieOvo。

### 验证

重复运行该测试；随后执行：

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_config.py -q
```

## 任务 2：严格 YAML 解析与36键 MIDI 反解

**新增文件**：

- `src/arrangement_benchmark/score_io.py`
- `tests/test_arrangement_benchmark_score_io.py`

### RED

1. 使用 `PianoConfigLoader` 加载真实 `config/cruel_angel_svsep_mpdr_v3.yaml`。
2. 将其反解为真实 `pretty_midi.PrettyMIDI`。
3. 断言 pitch 全部位于 48-83、velocity 全部等于配置值、无 control change、事件持续时间由 YAML `beat` 和 BPM 换算。
4. 写出临时 MIDI 后重新读取，断言音符数量、pitch、起止时间和 velocity 一致。

预期 RED：`score_io` 不存在。

### GREEN

实现：

- 严格 token 解析，非法 token 直接抛 `ValueError`；
- 休止符只推进绝对拍点；
- 和弦同起音；
- 每个事件 `beat` 必须为有限正数；
- 输出统一 velocity、无 CC64、无任何 pedal；
- MIDI 写出后强制回读验证。

禁止导入或调用 `work/yaml_render_compare.py`，避免正弦波和静默 token 忽略逻辑进入正式管线。

### 验证

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_score_io.py -q
```

## 任务 3：硬约束校验

**新增文件**：

- `src/arrangement_benchmark/constraints.py`
- `tests/test_arrangement_benchmark_constraints.py`

### RED

基于真实 YAML 和真实反解 MIDI 断言：

- 顶层为 `song/playback/keyboard/score`；
- `song` 内含 `name/bpm/beat_unit`；
- pitch 48-83；
- 单事件最多 6 音；
- 无重复 pitch/token；
- beat 为有限正数，累计绝对拍点严格递增；
- 缩编 MIDI velocity 统一且无 pedal；
- YAML/MIDI 可重新读取。

预期 RED：约束校验器不存在。

### GREEN

实现返回结构化 `ConstraintReport`；任一失败即 `passed=False` 并列出真实证据，不提供默认值或降级通道。runner 收到失败报告后排除候选。

### 验证

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_constraints.py -q
```

## 任务 4：六维保真指标

**新增文件**：

- `src/arrangement_benchmark/metrics.py`
- `tests/test_arrangement_benchmark_metrics.py`

### RED

使用真实 `existing_reference.mid` 与 `target_reduced.mid`：

1. D1 返回旋律音高保留与轮廓分量；
2. D2 对真实 WAV 色度计算返回有限值；
3. D3 对 onset 网格计算 F1；
4. D4 返回低音锚点保留与轮廓距离；
5. D5 返回声部相对顺序与音区层次；
6. D6 对同源 WAV 的音频特征返回有限值；
7. 总分严格使用 25/20/20/15/10/10 权重；
8. 任一输入缺失、序列为空、音频无法读取或结果非有限数时直接抛错。

音频指标测试必须使用真实渲染 WAV；在任务 6 音源就绪前先让该测试保持 RED，不添加跳过或替代数据。

### GREEN

实现确定性规则：

- D1：统一量化窗口、八度等价匹配和轮廓相关；
- D2：WAV 色度逐帧余弦相似；
- D3：量化 onset presence F1；
- D4：低音 pitch class/轮廓保留；
- D5：同时事件最高/最低声部和相对顺序；
- D6：同采样率 WAV 的 MFCC 或设计文档声明的等效特征；
- 两首歌曲先各自计算，再等权平均，禁止按音符数加权。

边界情况必须报错，不用 0、1 或其他默认值填充。

### 验证

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_metrics.py -q
```

## 任务 5：真实采样钢琴渲染

**新增文件**：

- `src/arrangement_benchmark/rendering.py`
- `tests/test_arrangement_benchmark_rendering.py`

### RED

1. 用真实 MIDI、真实 SoundFont 和真实 FluidSynth 渲染 WAV。
2. 用真实 ffmpeg 将 WAV 编码为 MP3。
3. 用 `soundfile`/`librosa` 回读 WAV，用 ffprobe 或 ffmpeg 回读 MP3。
4. 断言音频非空、时长为正、采样率和通道数符合集中配置。

在 FluidSynth 或 SoundFont 尚未就绪时，测试必须真实失败并报告缺少项，不允许 skip、正弦波或其他音源替代。

### GREEN

实现严格预检和 `subprocess.run(..., check=True)` 调用：

- 工具必须存在并记录版本；
- SoundFont 必须存在、SHA-256 与合规记录一致；
- FluidSynth 先输出无损 WAV；
- ffmpeg 只负责 WAV 到 MP3；
- 原始 MIDI 不改写；
- 缩编 MIDI 来自任务 2；
- 任何非零退出码、空文件或回读失败立即抛错。

### 验证

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_rendering.py -q
```

## 任务 6：音源和真实依赖预检

**产物**：

- `work/arrangement_benchmark_20260711/soundfont_compliance_report.md`
- `work/arrangement_benchmark_20260711/requirements_frozen.txt`
- `work/arrangement_benchmark_20260711/tool_versions.json`
- 配置指定的合法 SoundFont 文件及许可证文本

### 执行顺序

1. 检查 Python、CUDA、Demucs、Transkun、piano_svsep、FluidSynth、ffmpeg、librosa、pretty_midi、music21、partitura 和 PyTorch 真实版本。
2. Python 依赖缺失时使用中国镜像安装：

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <缺失依赖>
```

3. 从许可证清晰的上游来源获取一份真实采样大钢琴 SoundFont；保存来源 URL、许可证、文件哈希和下载时间。
4. 不接受来源不明、许可证缺失、纯正弦波或物理建模替代品。
5. 重新运行任务 5 渲染测试直到 GREEN。

## 任务 7：实验 runner 与 manifest

**新增文件**：

- `src/arrangement_benchmark/runner.py`
- `src/arrangement_benchmark/cli.py`
- `tests/test_arrangement_benchmark_runner.py`

### RED

基于正式配置和真实现有产物测试：

- 输出目录完全由配置推导；
- 每个阶段写入命令、参数、版本、SHA-256 和状态；
- 基准 MIDI 在每阶段前后哈希一致；
- 五个正式候选使用同一 B MIDI 和固定参数；
- `score_aware_theory` 先运行两首就绪门槛；
- 候选失败被记录并排除，不伪造成成功；
- 排名前两名以匿名代号复制 WAV/MP3，映射单独封存；
- 不自动生成用户盲听结论。

### GREEN

实现阶段状态机：

```text
preflight
  -> prepare_source
  -> transcribe_raw_b
  -> score_aware_readiness
  -> arrange_candidates
  -> validate_constraints
  -> build_reduced_midi
  -> render_audio
  -> calculate_metrics
  -> rank
  -> prepare_blind_assets
  -> write_reports
```

任何依赖阶段失败立即停止当前候选或整个实验，严格遵守设计文档失败策略。

### 验证

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_runner.py -q
```

## 任务 8：完整真实执行

### 8.1 环境预检

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m arrangement_benchmark.cli --config config/arrangement_benchmark_20260711.yaml preflight
```

预检不通过时直接修复真实依赖或报告阻塞，不运行后续阶段。

### 8.2 两首曲目完整运行

```powershell
$env:PYTHONPATH = "F:\NTEZMusic\src"
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m arrangement_benchmark.cli --config config/arrangement_benchmark_20260711.yaml run
```

不得复用旧 YAML 作为本轮结果。允许复用已验证且哈希、来源与本轮配置完全一致的不可变 B MIDI；否则重新生成。

### 8.3 产物验收

检查：

- 两首原始 B MIDI 与原始大钢琴 WAV/MP3；
- 每个合格候选的 YAML、缩编 MIDI、WAV、MP3；
- 每曲每算法约束报告和 D1-D6 指标；
- 双歌曲等权排名；
- 匿名 A/B 盲听文件及封存映射；
- manifest、依赖冻结、SoundFont 合规报告和运行日志。

## 任务 9：全量验证与审阅

### 9.1 诊断

对所有新增/修改 Python 文件运行 LSP diagnostics，错误和警告必须处理。

### 9.2 真实测试

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_arrangement_benchmark_config.py tests/test_arrangement_benchmark_score_io.py tests/test_arrangement_benchmark_constraints.py tests/test_arrangement_benchmark_metrics.py tests/test_arrangement_benchmark_rendering.py tests/test_arrangement_benchmark_runner.py -v
```

随后运行项目现有相关测试：

```powershell
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py tests/test_score_aware_theory_mvp.py tests/test_score_aware_theory_arrangement.py tests/test_score_audit_tool.py tests/test_raw_midi_generator.py -v
```

### 9.3 产物完整性

对 manifest 中每个文件重新计算 SHA-256；逐个回读 YAML、MIDI、WAV、MP3；确认排名数据只来自真实产物。

### 9.4 后实现审阅

执行安全、目标符合性、代码质量、真实 QA 和上下文一致性审阅。所有审阅通过后，才能报告客观排名和盲听候选。

## 五、实施边界

1. 不提交 git；当前项目不是 git 仓库。
2. 不调用 Windows SendInput，不进行游戏内自动弹奏。
3. 不修改现有算法默认参数，不针对两首歌调参。
4. 不在依赖或音源失败时降级到 CPU、其他音源、正弦波或伪数据。
5. 不在盲听完成前公开匿名代号映射。
6. 客观冠军与盲听偏好不一致时，同时报告并等待用户裁定。
