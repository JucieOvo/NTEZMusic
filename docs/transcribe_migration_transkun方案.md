# 钢琴转录模型迁移方案：piano_transcription_inference → Transkun

## 一、目标

将钢琴音频转 MIDI 的转录引擎从 qiuqiangkong 的 `piano_transcription_inference`（CNN + CRF）切换为 Yujia-Yan 的 `transkun`（Neural Semi-CRF Transformer V2），享受更高的转录精度和更活跃的社区维护。

Transkun 仓库：https://github.com/Yujia-Yan/Transkun

## 二、转录精度对比

| 指标 | piano_transcription_inference | Transkun V2 (Maestro V3) |
|------|------------------------------|--------------------------|
| 架构 | CNN + CRF | Transformer + Neural Semi-CRF |
| Note Onset F1 | ~0.92 | 0.983 |
| Note Onset+Offset F1 | ~0.85 | 0.935 |
| pip 发布 | 否，仅 GitHub 安装 | 是 (`pip install transkun`) |
| CUDA 支持 | 是 | 是 |

Transkun V2 在 Maestro V3 上的 Note Onset F1 达到 0.983，显著优于旧的 CNN 方案。

## 三、API 差异分析

### 3.1 当前 API（piano_transcription_inference）

```python
from piano_transcription_inference.inference import PianoTranscription
transcriptor = PianoTranscription(device="cuda", checkpoint_path=checkpoint_path)
transcriptor.transcribe(audio_samples, midi_path)
```

输入：16 kHz 单声道 numpy 数组 → 输出：直接写入 MIDI 文件路径。

### 3.2 目标 API（Transkun）

```python
from moduleconf import parseFromFile
import torch

# 加载配置和模型
confManager = parseFromFile(confPath)
TransKun = confManager["Model"].module.TransKun
conf = confManager["Model"].config
checkpoint = torch.load(weightPath, map_location=device)
model = TransKun(conf=conf).to(device)
model.load_state_dict(checkpoint["best_state_dict"] or checkpoint["state_dict"], strict=False)
model.eval()

# 转录
x = torch.from_numpy(audio).to(device)  # 需要匹配 model.fs 采样率
notesEst = model.transcribe(x, stepInSecond=segmentHopSize, segmentSizeInSecond=segmentSize)
outputMidi = writeMidi(notesEst)
outputMidi.write(outPath)
```

输入：numpy 数组（需匹配模型采样率，默认 44100 Hz）→ 输出：`writeMidi()` 写 MIDI 文件。

### 3.3 关键差异点

| 项目 | 旧引擎 | Transkun |
|------|--------|----------|
| 安装方式 | GitHub 直装 | pip install transkun |
| 导入方式 | `importlib.import_module` 动态导入 | `import transkun` 静态导入 |
| 设备参数 | 字符串 `"cuda"` / `"cpu"` | torch device 对象 |
| 采样率 | 固定 16000 Hz | 模型自定 `model.fs`（默认 44100 Hz） |
| 模型加载 | 构造时传 checkpoint_path | 需先 `torch.load` 再 `load_state_dict` |
| 配置加载 | 无额外配置文件 | 需加载 `.conf` 配置文件 |
| segment 参数 | 无 | 支持 `segmentHopSize` / `segmentSizeInSecond` |
| MIDI 写入 | 直接传路径 | 需调用 `writeMidi()` |

## 四、变更范围

### 4.1 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `requirements.txt` | **修改** | 替换依赖声明 |
| `src/audio_to_yaml_converter.py` | **重写** | 重写 `PianoTranscriber` 类核心逻辑 |
| `README.md` | **修改** | 更新依赖说明中的模型名称 |
| `AGENTS.md` | **修改** | 更新 CLI 参考中的转录说明 |
| `docs/audio_to_yaml_pipeline方案.md` | **修改** | 更新方案文档中的模型引用 |

### 4.2 不修改的文件

- `src/piano_auto_player.py` — 自动弹奏无关转录引擎
- `src/cross_attention.py` — 音高压缩无关转录引擎
- `src/positional_encoding.py` — 同上
- `src/note_scorer.py` — 同上
- `src/reranker.py` — 同上
- `config/*.yaml` — 曲谱格式不变
- `work/` 目录下所有中间产物 — 仅在重新运行时重新生成

## 五、PianoTranscriber 重写方案

### 5.1 新增参数

在 `AudioPipelineConfig` 和 CLI 参数中新增以下可选项：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--transcription-device` | str | `"cpu"` | 转录推理设备，可选 `cuda` |
| `--transcription-segment-hop-size` | float | None | segment 步长（秒），None 使用模型默认 |
| `--transcription-segment-size` | float | None | segment 尺寸（秒），None 使用模型默认 |

### 5.2 `--transcription-checkpoint` 参数语义调整

| 旧语义 | 新语义 |
|--------|--------|
| `piano_transcription_inference` 的 checkpoint 路径 | Transkun 的模型权重 `.pt` 路径（对应 `--weight`） |

当 `--transcription-checkpoint` 未提供时：
- 旧行为：使用 `piano_transcription_inference` 内置默认权重
- 新行为：使用 Transkun 内置默认权重（`pretrained/2.0.pt`）和配置（`pretrained/2.0.conf`）

### 5.3 `_load_audio_samples` 方法适配

Transkun 需要匹配模型内在采样率（`model.fs`，默认 44100 Hz），旧引擎固定 16000 Hz 重塑。需要在模型实例化后动态适配采样率。

```python
# 新逻辑：
# 1. 先用 librosa 加载原始采样率
# 2. 模型实例化后，若音频采样率不匹配 model.fs，使用 soxr 重采样
# 3. 转换后转为 torch tensor 送模型推理
```

### 5.4 `_fix_midi_tempo` 方法保留

该方法不依赖转录引擎 API，仅对生成的 MIDI 做 BPM 修正，逻辑完全独立，无需修改。

## 六、数据流变更

调用链不变，仅 `PianoTranscriber.transcribe()` 内部实现替换：

```
音频文件 → DemucsSeparator → piano stem WAV
    → PianoTranscriber.transcribe()
        ├─ _load_audio_samples() → numpy 数组
        ├─ 加载 Transkun 模型（conf + weight）
        ├─ 采样率匹配检测 + 可选 soxr 重采样
        ├─ model.transcribe() → notesEst
        ├─ writeMidi() → MIDI 文件
        └─ _fix_midi_tempo() → 修正 BPM 写入
    → MIDI 文件 → MidiToYamlConverter → YAML 曲谱
```

## 七、依赖变更

### 7.1 requirements.txt

```diff
- piano_transcription_inference @ git+https://github.com/qiuqiangkong/piano_transcription_inference.git
+ transkun
```

Transkun 已发布到 PyPI，可直接 `pip install transkun`。其传递依赖包括 `torch`、`pydub`、`soxr`、`moduleconf`、`numpy` 等，由 pip 自动解析。

### 7.2 额外运行时依赖

`soxr` 在 Windows 下通过 pip 安装通常附带预编译 wheel。若 pip 安装失败，需用户手动安装 C++ 编译工具链，这也是 Transkun 本身的依赖。

## 八、风险点

1. **采样率不匹配风险**：旧管线固定 16000 Hz 输入，Transkun 默认 44100 Hz（`model.fs`）。需要在模型实例化后使用 `soxr` 做重采样适配，不能在代码中硬编码。
2. **soxr 在 Windows 下的可用性**：`soxr` 是 Transkun 的传递依赖，如果 pip 安装成功则可用；如果因缺少编译器导致 wheel 构建失败，需要降级到 `scipy.signal.resample` 作为后备。
3. **默认 checkpoint 的产品策略**：Transkun 随 pip 包发布的默认 checkpoint 是 **无踏板延音 (No Pedal Ext)** 版本，这是作者认为更接近真实演奏的选择。但论文报告的其他 checkpoint 指标更高且支持踏板延音，用户若需要更高质量可手动下载对应 `.pt` 并通过 `--transcription-checkpoint` 指定。
4. **CUDA 版本兼容**：Transkun 依赖 PyTorch，CUDA 版本需与本机 GPU 驱动匹配。不会在 requirements.txt 中写死 PyTorch 版本。
5. **API 稳定性**：Transkun 目前的 API 面向 2.0 预训练模型，若未来 API 变更需同步维护。
6. **输出 MIDI 兼容性**：`writeMidi()` 输出的 MIDI 格式需要与下游 `MidiToYamlConverter` 的 `pretty_midi` 解析兼容。理论上 `pretty_midi` 读取标准 MIDI 文件不应该有问题，但需在真实音频上验证。

## 九、不涉及的内容

- 不改变 Demucs 分轨逻辑
- 不改变 MIDI → YAML 转换逻辑
- 不改变自动弹奏逻辑
- 不改变音高压缩算法
- 不删除 `--input-midi` 模式（该模式跳过转录，完全不受影响）

## 十、实施顺序

1. 更新 `requirements.txt`，将 `piano_transcription_inference` 替换为 `transkun`
2. 安装新依赖：`pip install transkun`
3. 重写 `PianoTranscriber` 类：
   a. 新增 `transcription_device`、`transcription_segment_hop_size`、`transcription_segment_size` 参数
   b. 重写 `transcribe()` 方法使用 Transkun API
   c. 适配 `_load_audio_samples()` 为动态采样率
   d. 保留 `_fix_midi_tempo()` 不变
4. 更新 `AudioPipelineConfig` 数据类和 CLI 参数解析
5. 更新 `README.md`、`AGENTS.md`、`docs/audio_to_yaml_pipeline方案.md`
6. 使用真实音频执行一次端到端验证（Demucs 分轨 → Transkun 转录 → MIDI → YAML → 回读校验）

## 十一、需要审批的问题

1. 是否同意将转录引擎从 `piano_transcription_inference` 切换到 `transkun`
2. 是否同意在 `requirements.txt` 中替换依赖
3. 是否同意新增 `--transcription-device`、`--transcription-segment-hop-size`、`--transcription-segment-size` 三个可选参数
4. 是否同意 `--transcription-checkpoint` 语义从旧引擎 checkpoint 变更为 Transkun 权重路径

> 作者：JucieOvo
> 创建日期：2026-04-30
