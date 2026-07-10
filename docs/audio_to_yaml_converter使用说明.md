# 音频转 YAML 工具使用说明

## 一、用途

`src/audio_to_yaml_converter.py` 用于执行音频到游戏可执行 YAML 曲谱的转换流水线，支持两条路径：

**路径 A：音频全管线**
1. 使用 Demucs 对输入音频进行乐器分轨。
2. 使用 Transkun 将钢琴 stem 转录为 MIDI。
3. 将 MIDI 转换为项目可执行的 YAML 曲谱并写出转换报告。

**路径 B：MIDI 直转**
- 提供已有 MIDI 文件时，跳过 Demucs 分轨与钢琴转录，直接执行 MIDI 到 YAML 转换。

## 二、前置条件

1. 已安装 `requirements.txt` 中声明的全部依赖。
2. 已安装可用的 PyTorch 版本（CPU 或 CUDA）。
3. Transkun 权重：不指定 `--transcription-checkpoint` 时使用内置默认 .pt 文件。
4. Demucs 可在当前 Python 环境中通过 `python -m demucs` 执行。
5. 输入音频必须是真实存在的本地文件，支持 `.mp3`、`.flac`、`.wav`、`.m4a`、`.ogg`、`.aac`。
6. SVSEP-MPDR 模式（`svsep_mpdr`）：需额外提供 piano_svsep 模型权重 `.ckpt` 文件。

## 三、示例命令

### 音频全管线（自动 BPM + 转录 + 压缩）

```powershell
python src/audio_to_yaml_converter.py `
  --audio "song.mp3" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 0 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6 --left-max-chord-notes 3 `
  --phrase-gap-beats 0.5 --global-trend-alpha 0.05 `
  --global-trend-window-beats 4.0
```

> `--bpm 0` 时通过 librosa onset 检测自动获取真实 BPM。

### MIDI 直转（跳过 Demucs 与转录）

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "work/song/midi/song.mid" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 126 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6
```

### 使用 SVSEP-MPDR 模式

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "work/song/midi/song.mid" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 126 `
  --pitch-compression-mode svsep_mpdr `
  --svsep-model-path "path/to/piano_svsep.ckpt" `
  --svsep-device cpu `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6
```

## 四、可执行范围限制

默认音域限制为 C3 到 B5：

| 原始音区 | 输出符号 |
|----------|----------|
| C3 到 B3 | `-1` 到 `-7` |
| C4 到 B4 | `1` 到 `7` |
| C5 到 B5 | `+1` 到 `+7` |

`--out-of-range-policy` 控制超出范围时的行为：

| 值 | 行为 |
|----|------|
| `error`（默认） | 检测到超范围音符直接报错退出，不做降级处理 |
| `octave_fold` | 按八度将超范围音符迁移到 C3-B5 可执行范围内 |

## 五、音高压缩模式

`--pitch-compression-mode` 决定 MIDI 音符如何压缩映射到游戏 36 键（C3-B5）音域。**默认值为 `adaptive_octave_fold`。**

### 模式一览

| 模式 | 原理 | 推荐度 |
|------|------|:---:|
| `adaptive_octave_fold` | 和弦统一八度偏移 k + ref_pitch 指数平滑 | **默认** |
| `attention_weighted` | 小节滑动窗口 + 交叉注意力 + velocity 加权 + 精排 | 推荐 |
| `hands_decoupled` | 左右手解耦 + 三重验证 + 和声功能简化 | 推荐 |
| `svsep_mpdr` | GNN 左右手分离 + 主旋律 DP + MPDR 密度预算 + 五维精排 | 推荐 |
| `octave_fold` | 逐音独立八度折叠（无上下文） | 不推荐 |
| `none` | 不压缩，超范围直接报错 | 仅窄音域 |

> `score_aware_theory` 模式已在 v3 基准评测中淘汰，不再推荐使用。

### 模式详解

**`adaptive_octave_fold`（默认）**
- 以和弦为单位，为每个和弦统一选择一个八度偏移量 k。
- 使用 ref_pitch 指数平滑（`--ref-smoothing`）维持旋律线的连续性。
- 在保持和声内部音程关系的前提下整体上移或下移。

**`attention_weighted`**
- 以小节为滑动窗口，通过交叉注意力计算音轨间的依赖关系。
- 结合 velocity（力度）加权对音符进行精细排序。
- 支持 `--left-max-chord-notes` 控制左手最大音符数。

**`hands_decoupled`**
- 将 MIDI 事件按估算的左右手分离为两条独立轨。
- 分别处理后通过三重验证合并。
- 利用全局趋势线（`--global-trend-alpha`、`--global-trend-window-beats`）指导音区分配。
- 以休止符分割乐句（`--phrase-gap-beats`），减少越界跳变。

**`svsep_mpdr`**
- 使用 GNN（piano_svsep 模型）进行高质量的左右手分离。
- 主旋律通过动态规划（DP）从右手轨中提取。
- 左手轨按 MPDR（Maximum Perceived Density Budget）密度预算进行音符筛选。
- 候选结果经贝斯锚点、和声色彩、声部连接、velocity、时值、音区碰撞、低音浑浊等**五维精排**确定最优解。
- 支持 `--sustain-split` 长低音续打击键。

**`octave_fold`**
- 逐音独立按八度折叠，不考虑任何上下文关系。
- 会破坏和弦内部的音程结构，仅在对音乐性要求极低时使用。

**`none`**
- 不做任何压缩。超出 C3-B5 范围的音符直接报错。
- 仅适用于音符完全落在 36 键范围内的简单曲目。

### 各模式独有参数

| 参数 | 适用模式 | 默认值 | 说明 |
|------|----------|--------|------|
| `--ref-smoothing` | adaptive_octave_fold, hands_decoupled | 0.2 | ref_pitch 指数平滑系数 |
| `--left-max-chord-notes` | hands_decoupled, attention_weighted | 3 | 左手轨最大音符数 |
| `--phrase-gap-beats` | hands_decoupled | 0.5 | 休止符分割阈值（拍） |
| `--global-trend-alpha` | hands_decoupled | 0.05 | 全局趋势线平滑系数 |
| `--global-trend-window-beats` | hands_decoupled | 4.0 | 全局趋势采样窗口（拍） |
| `--svsep-model-path` | svsep_mpdr | — | piano_svsep .ckpt 路径（必填） |
| `--svsep-device` | svsep_mpdr | cpu | GNN 推理设备（cpu/cuda） |

## 六、输出文件

| 路径 | 内容 |
|------|------|
| `--output-yaml` 指定路径 | 最终可执行 YAML 曲谱 |
| `work-dir/demucs/` | Demucs 分轨音频文件 |
| `work-dir/midi/` | Transkun 转录 MIDI 文件 |
| `work-dir/conversion_report.json` | 完整转换报告（含统计信息） |
| `work-dir/score_aware_theory/` | score_aware_theory 模式中间产物（已淘汰） |

## 七、注意事项

1. Demucs 默认模型为 `htdemucs_6s`，分轨目标固定为 `piano`。
2. Transkun 更适合纯钢琴音频，混合 stem 的转录质量不可保证。
3. 如不传 `--allow-accidentals`，遇到黑键音高会直接报错。
4. 如果 `--key-press-seconds` 大于等于最短事件时长，工具会直接报错。
5. `--octave_fold` 模式会改变原始音高关系，仅建议在目标键位范围有限时使用。
6. 各压缩模式不对超出 36 键的音符做降级：必须配合 `--out-of-range-policy octave_fold`。
7. Windows 下音频文件 stem 末尾不得含句点或空格，工具会自动创建安全副本。
8. BPM 自动检测（`--bpm 0`）在音频全管线中自动执行并写入转录 MIDI 的 tempo 轨道。之后的 MIDI 复用无需再传音频。
9. 同音重叠碎片自动合并：`--merge-sustained-notes`（默认开启），可用 `--no-merge-sustained-notes` 关闭；间隔阈值由 `--merge-sustained-gap-beats` 控制。

## 八、完整参数参考

### 输入输出

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--audio` | path | — | 真实输入音频路径 |
| `--input-midi` | path | — | 已有 MIDI 路径，提供时跳过 Demucs 与钢琴转录 |
| `--output-yaml` | path | **必填** | 输出 YAML 曲谱路径 |
| `--work-dir` | path | **必填** | 中间文件与报告目录 |
| `--song-name` | str | **必填** | 输出 YAML 曲谱名称 |

### BPM 与节拍

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--bpm` | float | 0 | BPM，0 表示自动检测 |
| `--beat-unit` | int | 4 | 节拍单位 |

### 时序控制

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--start-delay-seconds` | float | 3.0 | 自动弹奏启动前等待秒数 |
| `--key-press-seconds` | float | 0.0 | 单次按键保持时间（播放层保底 1ms） |

### 音频分轨

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--demucs-model` | str | htdemucs_6s | Demucs 模型名称 |
| `--demucs-stem` | str | piano | 分轨目标，固定为 piano |

### 钢琴转录（Transkun）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--transcription-checkpoint` | path | — | Transkun 模型权重 .pt 路径 |
| `--transcription-device` | str | cpu | 推理设备（cpu / cuda） |
| `--transcription-segment-hop-size` | float | None | segment 步长（秒），None 使用模型默认 |
| `--transcription-segment-size` | float | None | segment 尺寸（秒），None 使用模型默认 |

### MIDI 处理

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--quantize-beat` | float | 0.25 | MIDI 量化节拍单位 |
| `--max-chord-notes` | int | 6 | 单个和弦最大音符数 |
| `--max-score-events` | int | 5000 | 最大 score 事件数量 |
| `--allow-accidentals` | flag | False | 允许输出半音 token（#1, b3, #4, #5, b7） |
| `--out-of-range-policy` | str | error | 超范围音符策略：error / octave_fold |

### 音高压缩

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--pitch-compression-mode` | str | adaptive_octave_fold | 音高压缩模式，可选值见第五节 |

### 延音合并

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--merge-sustained-notes` | flag | True（默认） | 合并同音重叠 MIDI 碎片 |
| `--no-merge-sustained-notes` | flag | — | 关闭同音合并 |
| `--merge-sustained-gap-beats` | float | 0.25 | 同音合并允许的最大间隔拍数 |

### 起音聚类

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--disable-onset-cluster` | flag | False | 禁用 MIDI 起音聚类 |
| `--onset-cluster-window-beats` | float | 0.08 | 起音聚类相邻窗口拍数 |
| `--onset-cluster-max-span-beats` | float | 0.12 | 起音聚类最大跨度拍数 |

### SVSEP-MPDR 基础配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--svsep-model-path` | path | — | piano_svsep 模型权重 .ckpt 路径 |
| `--svsep-device` | str | cpu | piano_svsep 推理设备（cpu / cuda） |

### SVSEP-MPDR 候选扫描

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-candidate-count` | int | 12 | 参数扫描候选数量上限 |
| `--mpdr-scan-delta-ratio` | float | 0.15 | 参数扫描相对扰动比例 |
| `--mpdr-right-ref-pitch` | float | 67.0 | 右手折叠参考中心（MIDI 音高） |
| `--mpdr-left-ref-pitch` | float | 55.0 | 左手折叠参考中心（MIDI 音高） |
| `--mpdr-left-window-high` | int | 67 | 左手折叠窗口最高 MIDI 音高 |
| `--mpdr-right-window-low` | int | 60 | 右手折叠窗口最低 MIDI 音高 |
| `--mpdr-melody-protection-strength` | float | 1.0 | 主旋律保护强度 |
| `--mpdr-melody-onset-guard-beats` | float | 0.5 | 主旋律起音保护窗口拍数 |

### SVSEP-MPDR 密度预算

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-left-opacity-base` | float | 3.6 | 左手基础感知密度预算 |
| `--mpdr-left-opacity-min` | float | 1.2 | 左手最小感知密度预算 |
| `--mpdr-left-opacity-max` | float | 9.0 | 左手最大感知密度预算 |
| `--mpdr-melody-rest-bonus` | float | 2.5 | 右手休止时左手预算增量 |

### SVSEP-MPDR 五维精排权重

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-bass-anchor-weight` | float | 1.4 | 低音锚点权重 |
| `--mpdr-harmony-color-weight` | float | 1.1 | 和声色彩权重 |
| `--mpdr-voice-leading-weight` | float | 0.7 | 声部连接权重 |
| `--mpdr-velocity-weight` | float | 0.45 | velocity 分析权重 |
| `--mpdr-duration-weight` | float | 0.35 | 时值分析权重 |

### SVSEP-MPDR 惩罚项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-duplicate-penalty` | float | 0.9 | 八度重复惩罚 |
| `--mpdr-onset-collision-penalty` | float | 1.2 | 同起音遮蔽惩罚 |
| `--mpdr-register-collision-penalty` | float | 1.1 | 音区碰撞惩罚 |
| `--mpdr-register-collision-semitones` | float | 12.0 | 音区碰撞半音阈值 |
| `--mpdr-low-mud-penalty` | float | 0.8 | 低音浑浊惩罚 |
| `--mpdr-low-mud-pitch` | int | 48 | 低音浑浊判定音高阈值 |
| `--mpdr-density-penalty` | float | 0.3 | 左手局部密度惩罚 |
| `--mpdr-duration-ducking-strength` | float | 0.7 | 长音覆盖主旋律惩罚 |
| `--mpdr-repeat-suppression-beats` | float | 0.5 | 非主旋律同音重复抑制窗口 |
| `--mpdr-repeat-overlap-tolerance-beats` | float | 0.05 | 延音重叠判定容差 |

### SVSEP-MPDR 精排评分权重

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-score-melody-weight` | float | 0.30 | 主旋律完整度权重 |
| `--mpdr-score-masking-weight` | float | 0.25 | 遮蔽规避权重 |
| `--mpdr-score-harmony-weight` | float | 0.20 | 和声完整度权重 |
| `--mpdr-score-bass-weight` | float | 0.15 | 低音连续性权重 |
| `--mpdr-score-register-weight` | float | 0.10 | 音区清晰度权重 |

### SVSEP-MPDR 主旋律 DP 权重

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-melody-pitch-weight` | float | 1.0 | 音高评分权重 |
| `--mpdr-melody-duration-weight` | float | 0.35 | 时值评分权重 |
| `--mpdr-melody-velocity-weight` | float | 0.45 | 力度评分权重 |
| `--mpdr-melody-beat-weight` | float | 0.20 | 强拍评分权重 |
| `--mpdr-melody-continuity-weight` | float | 0.70 | 连续性转移权重 |
| `--mpdr-melody-large-jump-penalty` | float | 0.35 | 大跳惩罚 |
| `--mpdr-melody-repetition-penalty` | float | 0.20 | 同音重复惩罚 |

### 长低音续打（SVSEP-MPDR）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--sustain-split` | flag | False | 启用长低音续打击键 |
| `--sustain-split-threshold` | float | 1.0 | 触发续打的 duration_beats 下限 |
| `--sustain-split-max-density` | int | 3 | 中点位置最大同拍音符数 |

## 九、预设模板

### 残酷天使（MIDI 直转，adaptive_octave_fold）

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "work/cruel_angel_thesis/midi/【Animenz】残酷天使的行动纲领.mid" `
  --output-yaml config/cruel_angel_final.yaml `
  --work-dir work/cruel_angel_final `
  --song-name "残酷天使的行动纲领" --bpm 126 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals --out-of-range-policy octave_fold --max-chord-notes 6 `
  --left-max-chord-notes 3 --phrase-gap-beats 0.5 `
  --global-trend-alpha 0.05 --global-trend-window-beats 4.0
```

### 音频全管线（自动 BPM + 转录 + 压缩）

```powershell
python src/audio_to_yaml_converter.py `
  --audio "song.mp3" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 0 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals --out-of-range-policy octave_fold --max-chord-notes 6 `
  --left-max-chord-notes 3 --phrase-gap-beats 0.5 `
  --global-trend-alpha 0.05 --global-trend-window-beats 4.0
```

---

> 作者：JucieOvo