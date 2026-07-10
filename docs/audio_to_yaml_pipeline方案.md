# Demucs 分轨转 MIDI 再转 YAML 方案

## 一、目标

在现有 YAML 自动弹奏项目基础上，新增一条独立的音频转换流水线：

1. 输入真实音频文件。
2. 使用 Demucs 执行真实分轨，提取钢琴相关音轨。
3. 使用 `transkun` 将钢琴音轨转写为 MIDI。
4. 将 MIDI 转换为当前项目可执行的 YAML 曲谱格式。
5. 在转换阶段强制约束音域、节拍粒度、和弦规模与键位映射，确保输出不超过现有自动弹奏器可执行编译范围。

本方案不改变 `src/piano_auto_player.py` 的实时键盘输入逻辑，只新增离线转换工具与必要文档。

## 二、现有项目约束

当前项目的可执行曲谱格式由 `config/*.yaml` 与 `docs/yaml_score_guide.md` 定义，核心约束如下：

1. YAML 顶层必须包含 `song`、`playback`、`keyboard`、`score` 四个区域。
2. `score` 中每个事件包含 `notes` 与 `beat`。
3. 支持三类音区标记：低音区 `-1` 到 `-7`，中音区 `1` 到 `7`，高音区 `+1` 到 `+7`。
4. 支持休止符 `0`。
5. 支持和弦 `[1 3 5]` 形式。
6. 支持升半音 `#` 与降半音 `b`，但实际可执行性取决于游戏钢琴界面对组合键的支持。

因此，MIDI 转 YAML 阶段必须把 MIDI 音高限制到当前键盘映射能表达的范围内。超范围音符不得伪造为近似音符，必须按配置选择直接报错或显式移除并记录报告；默认建议直接报错。

## 三、模块划分

建议新增 `src/audio_to_yaml_converter.py`，作为独立命令行工具，不嵌入自动弹奏主程序。

### 3.1 AudioPipelineConfig

负责保存转换参数：

1. 输入音频路径。
2. 输出目录。
3. Demucs 模型名称。
4. 钢琴转录 checkpoint 路径或模型下载目录。
5. 输出 YAML 的歌曲名、BPM、节拍单位、启动延迟、按键时长。
6. MIDI 到 YAML 的量化单位、最大和弦音数、允许音域。
7. 超范围音符处理策略，默认 `error`。

### 3.2 DemucsSeparator

负责调用 Demucs CLI 执行真实分轨。

执行策略：

1. 使用 `python -m demucs` 或 `demucs` 命令执行分轨。
2. 不在代码内模拟分轨结果。
3. 命令失败、输出文件缺失或目标 stem 不存在时直接抛错。
4. 默认优先读取 `other.wav` 或指定 stem 作为转录输入；如果用户需要更准确的钢琴分离，需要明确指定适合钢琴的 Demucs 模型或外部分轨模型。

风险说明：标准 Demucs 常见模型通常输出 `vocals`、`drums`、`bass`、`other`，并不一定有独立 `piano` stem。若使用普通音乐分轨，钢琴通常落在 `other` 中，转录质量受混入乐器影响。若必须获得独立钢琴音轨，需要选择支持 piano stem 的模型或额外引入专用分离模型。

### 3.3 PianoTranscriber

负责调用 `transkun` 执行真实音频到 MIDI 转录。

执行策略：

1. 依赖 PyPI 包 `transkun` (Neural Semi-CRF Transformer V2)。
2. 优先通过其 Python API 生成 MIDI。
3. 不生成伪 MIDI，不写占位事件。
4. 若模型权重不存在、CUDA/CPU 环境不满足、依赖导入失败或推理失败，直接报错。

注意事项：Transkun 依赖 PyTorch 与音频处理库，Windows 下可能需要单独安装兼容版本。依赖安装必须使用真实依赖，不使用 Mock。

### 3.4 MidiToYamlConverter

负责读取真实 MIDI 文件并转换成现有 YAML 曲谱。

核心逻辑：

1. 读取 MIDI note on 与 note off 事件，构建音符开始时间、结束时间、持续时间与音高。
2. 使用 MIDI tempo 或用户传入 BPM 计算拍点。
3. 按固定量化单位把开始时间和持续时间对齐到可执行节拍网格。
4. 同一量化起点的音符合并为和弦。
5. 事件之间存在空隙时插入休止符 `0`。
6. 将 MIDI 音高映射为 YAML 简谱 token。
7. 输出严格符合当前 `PianoConfigLoader` 校验规则的 YAML。

## 四、MIDI 音高到 YAML token 的映射规则

当前键盘映射本质是 21 个基础音位：低音区 7 个、中音区 7 个、高音区 7 个。为了不超出可执行范围，建议建立明确的可配置音域窗口。

默认建议：

1. 以 C 大调为基础映射。
2. `middle 1` 对应 C4。
3. `middle 2` 对应 D4。
4. `middle 3` 对应 E4。
5. `middle 4` 对应 F4。
6. `middle 5` 对应 G4。
7. `middle 6` 对应 A4。
8. `middle 7` 对应 B4。
9. `low 1` 到 `low 7` 对应 C3 到 B3。
10. `high 1` 到 `high 7` 对应 C5 到 B5。

在该规则下，默认可执行音域为 C3 到 B5。MIDI 中低于 C3 或高于 B5 的音符默认直接报错。

半音处理：

1. C、D、E、F、G、A、B 映射为 `1` 到 `7`。
2. C#、D#、F#、G#、A# 优先映射为 `#1`、`#2`、`#4`、`#5`、`#6`。
3. Db、Eb、Gb、Ab、Bb 可以等价映射为 `b2`、`b3`、`b5`、`b6`、`b7`，但为了输出稳定，建议统一使用升号。
4. 若用户确认游戏不支持组合键变音，应禁止半音输出，检测到黑键音高时直接报错。

## 五、确保不超过可执行范围的规则

转换器必须在输出前执行以下校验：

1. 音域校验：所有 token 必须能在 `keyboard` 映射中找到对应基础键。
2. 半音校验：如禁用半音，则任何 `#` 或 `b` token 都必须报错。
3. 和弦规模校验：单个和弦内音符数量不得超过配置的 `max_chord_notes`。
4. 时间粒度校验：所有 `beat` 必须大于 0，且不得小于配置的最小节拍单位。
5. 按键持续校验：`playback.key_press_seconds` 必须小于最短事件实际秒数。
6. score 长度校验：若输出事件数量超过配置上限，直接报错，避免生成过大且不可稳定执行的曲谱。
7. YAML 回读校验：生成后调用现有 `PianoConfigLoader` 读取一次，确保结构与类型真实可执行。

默认不做自动降八度、自动删音、自动合并复杂和弦等降级处理。此类处理会改变音乐内容，必须由用户通过显式参数开启后才允许执行，并在转换报告中记录。

## 六、依赖建议

`requirements.txt` 可新增以下依赖，但需在实施时根据 Windows 与 PyTorch 版本确认：

1. `demucs`
2. `transkun`，建议从 pip 安装。
3. `pretty_midi` 或 `mido`，用于 MIDI 解析。
4. `soundfile`、`librosa` 等依赖由上游库决定。

安装 PyTorch 时需要根据本机 CUDA 环境选择真实可用版本，不建议在项目中写死通用安装命令。

## 七、命令行设计

建议新增命令：

```powershell
python src/audio_to_yaml_converter.py `
  --audio "input/song.wav" `
  --output-yaml "config/generated_song.yaml" `
  --work-dir "work/audio_to_yaml" `
  --song-name "generated_song" `
  --bpm 120 `
  --demucs-model "htdemucs" `
  --demucs-stem "other" `
  --transcription-checkpoint "models/piano_transcription" `
  --quantize-beat 0.25 `
  --max-chord-notes 6 `
  --out-of-range-policy "error"
```

参数说明：

1. `--audio`：真实输入音频文件。
2. `--output-yaml`：生成的 YAML 曲谱路径。
3. `--work-dir`：分轨、MIDI、报告等中间文件目录。
4. `--bpm`：输出 YAML 使用的 BPM。若 MIDI 内含可靠 tempo，可允许自动读取。
5. `--demucs-model`：Demucs 模型名称。
6. `--demucs-stem`：用于钢琴转录的 stem，默认 `other`。
7. `--transcription-checkpoint`：Transkun 模型权重位置。
8. `--quantize-beat`：量化粒度，默认不小于 `0.25`。
9. `--max-chord-notes`：单事件最多同时按下的音符数。
10. `--out-of-range-policy`：默认 `error`。

## 八、数据流

1. 用户提供音频文件。
2. `DemucsSeparator` 检查音频存在。
3. 调用 Demucs 生成 stem wav。
4. 检查目标 stem wav 是否真实存在。
5. `PianoTranscriber` 调用 `transkun` 生成 MIDI。
6. 检查 MIDI 文件是否真实存在且包含 note 事件。
7. `MidiToYamlConverter` 解析 MIDI 事件。
8. 执行量化、合并和弦、插入休止符。
9. 执行音域、半音、和弦规模、节拍粒度校验。
10. 写出 YAML 与转换报告。
11. 使用现有 YAML 加载器回读校验。

## 九、风险点

1. Demucs 不一定输出独立钢琴 stem，普通歌曲转录准确率不可保证。
2. `transkun` 更适合纯钢琴音频，混合 `other` stem 可能产生误检。
3. MIDI 的 88 键音域远大于当前游戏键盘 21 个基础音位，超范围音符会频繁触发错误。
4. 半音依赖当前自动弹奏器的组合键策略，游戏端若不支持则必须禁用半音。
5. 复杂钢琴曲的和弦密度可能超过游戏输入可稳定执行范围。
6. Windows 下音频依赖、PyTorch、ffmpeg 与模型权重路径需要逐项验证。

## 十、实施顺序

1. 确认是否允许新增代码、依赖与命令行工具。
2. 确认 Demucs 使用的模型与目标 stem，若无钢琴专用 stem，默认使用 `other`。
3. 确认 `transkun` 的安装方式与模型权重路径。
4. 新增独立转换脚本 `src/audio_to_yaml_converter.py`。
5. 新增或更新依赖说明。
6. 新增使用文档与输出报告说明。
7. 使用真实短音频执行一次端到端验证。
8. 使用现有 `PianoConfigLoader` 对生成 YAML 回读校验。

## 十一、需要审批的问题

实施前需要确认以下决策：

1. 是否允许新增 `src/audio_to_yaml_converter.py`。
2. 是否允许更新 `requirements.txt`。
3. 是否接受默认可执行音域 C3 到 B5。
4. 是否允许输出半音 token。
5. Demucs 是否使用默认 `htdemucs` 与 `other` stem，还是指定支持 piano stem 的模型。
6. 是否已有 `transkun` 模型权重路径。

> 作者：JucieOvo
