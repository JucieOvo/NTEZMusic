# 音频转 YAML 工具使用说明

## 一、用途

`src/audio_to_yaml_converter.py` 用于执行真实的音频转换流水线：

1. 使用 Demucs 对输入音频分轨。
2. 使用 `transkun` 将目标 stem 转为 MIDI。
3. 将 MIDI 转换为当前项目可执行的 YAML 曲谱。
4. 写出转换报告，并使用现有 YAML 加载器回读校验。

## 二、前置条件

1. 已安装 `requirements.txt` 中声明的依赖。
2. 已安装可用的 PyTorch 版本。
3. 如需指定权重，已准备 Transkun 所需 checkpoint .pt 文件；不指定时使用内置默认权重。
4. Demucs 可在当前 Python 环境中通过 `python -m demucs` 执行。
5. 输入音频必须是真实存在的本地文件。

## 三、示例命令

```powershell
python src/audio_to_yaml_converter.py `
  --audio "input/song.wav" `
  --output-yaml "config/generated_song.yaml" `
  --work-dir "work/audio_to_yaml" `
  --song-name "generated_song" `
  --bpm 120 `
  --demucs-model "htdemucs" `
  --demucs-stem "other" `
  --quantize-beat 0.25 `
  --max-chord-notes 6 `
  --max-score-events 5000 `
  --out-of-range-policy "octave_fold" `
  --allow-accidentals
```

如果已经存在转录好的 MIDI，可以跳过 Demucs 与钢琴转录，直接执行 MIDI 到 YAML：

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "work/audio_to_yaml/midi/song.mid" `
  --output-yaml "config/generated_song.yaml" `
  --work-dir "work/audio_to_yaml" `
  --song-name "generated_song" `
  --bpm 120 `
  --quantize-beat 0.25 `
  --max-chord-notes 6 `
  --allow-accidentals `
  --pitch-compression-mode "global_linear"
```

## 四、可执行范围限制

默认音域限制为 C3 到 B5：

1. C3 到 B3 输出为低音区 `-1` 到 `-7`。
2. C4 到 B4 输出为中音区 `1` 到 `7`。
3. C5 到 B5 输出为高音区 `+1` 到 `+7`。

默认检测到超出范围的 MIDI 音符时，工具会直接报错，不会自动降八度或伪造近似音符。
如果显式传入 `--out-of-range-policy "octave_fold"`，工具会把超出 C3 到 B5 的音符按八度迁移到可执行范围内。

## 五、音域压缩重排

`--pitch-compression-mode "global_linear"` 会先统计全曲最低音与最高音，再把全曲所有 MIDI 音符按同一个线性函数整体压缩到 C3 到 B5。
该模式不会只处理超范围音符，因此更适合把完整钢琴曲压缩进游戏有限音域，避免旋律跨越边界时突然换调。

`--pitch-compression-mode "range_rearrange"` 会按时间片重排 MIDI 音符：

1. 最高音优先作为旋律，默认放入高音区。
2. 最低音优先作为低音功能音，默认放入低音区。
3. 中间音作为和声填充，默认放入中音区。
4. 重复目标键位会去重。
5. 超过 `--max-chord-notes` 的和声音会裁剪，并写入转换报告。

半音会按游戏界面实际黑键表达输出：`#1`、`b3`、`#4`、`#5`、`b7`。

可调整目标音区：

```powershell
--melody-target-octave 5 --harmony-target-octave 4 --bass-target-octave 3
```

## 六、输出文件

1. `--output-yaml` 指定的 YAML 曲谱。
2. `work-dir/demucs/` 下的 Demucs 分轨文件。
3. `work-dir/midi/` 下的转录 MIDI 文件。
4. `work-dir/conversion_report.json` 转换报告。

## 七、注意事项

1. 标准 Demucs 模型通常不会输出独立钢琴 stem，默认使用 `other.wav`。
2. `transkun` 更适合纯钢琴音频，混合 stem 的识别质量不可保证。
3. 如不传 `--allow-accidentals`，遇到黑键音高会直接报错。
4. 如果 `key_press_seconds` 大于等于最短事件时长，工具会直接报错。
5. `octave_fold` 会改变原始音高，只建议在目标游戏键位范围有限时使用。
6. `global_linear` 更适合完整曲目整体压缩；`range_rearrange` 更像按声部缩编，可能在大跨度旋律中产生局部跳变。

> 作者：JucieOvo

## 八、SVSEP-MPDR v3 新增参数 (v3.0)

### 起音聚类参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--disable-onset-cluster` | flag | False | 禁用 MIDI 起音聚类 |
| `--onset-cluster-window-beats` | float | 0.08 | 起音聚类相邻窗口拍数 |
| `--onset-cluster-max-span-beats` | float | 0.12 | 起音聚类最大跨度拍数 |

### 主旋律 DP 评分参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mpdr-melody-pitch-weight` | float | 1.0 | 主旋律音高评分权重 |
| `--mpdr-melody-duration-weight` | float | 0.35 | 主旋律时值评分权重 |
| `--mpdr-melody-velocity-weight` | float | 0.45 | 主旋律力度评分权重 |
| `--mpdr-melody-beat-weight` | float | 0.20 | 主旋律强拍评分权重 |
| `--mpdr-melody-continuity-weight` | float | 0.70 | 主旋律连续性转移权重 |
| `--mpdr-melody-large-jump-penalty` | float | 0.35 | 主旋律大跳惩罚 |
| `--mpdr-melody-repetition-penalty` | float | 0.20 | 主旋律同音重复惩罚 |
