# NTEZMusic CLI 命令参考

> **风险警告**：自动弹奏使用 Windows SendInput API（R3 级操作注入），可能触发游戏反作弊检测导致账号封禁。以**管理员身份**运行。开发者不对账号封禁负责。

## 音频 -> YAML（全管线：Demucs 分轨 + Transkun 转录 + 压缩）

```powershell
python src/audio_to_yaml_converter.py `
  --audio "path/to/song.flac" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" `
  --bpm 0 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals `
  --out-of-range-policy octave_fold
```

## MIDI -> YAML（跳过 Demucs 和转录，直接压缩）

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "path/to/song.mid" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" `
  --bpm 126 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals `
  --out-of-range-policy octave_fold
```

## 自动弹奏

```powershell
python src/piano_auto_player.py --config config/song.yaml
```

## 完整参数

```
--audio PATH              输入音频路径
--input-midi PATH          输入 MIDI 路径（跳过 Demucs 和转录）
--output-yaml PATH         输出 YAML 路径（必填）
--work-dir PATH            中间文件目录（必填）
--song-name NAME           曲名（必填）
--bpm FLOAT                BPM，0 为自动检测（默认 0）
--beat-unit INT            节拍单位（默认 4）
--start-delay-seconds FLOAT 弹奏前等待秒数（默认 3.0）
--key-press-seconds FLOAT  按键保持秒数（默认 0.0，播放层保底 1ms）
--quantize-beat FLOAT      量化步长（默认 0.25）
--max-chord-notes INT      单和弦最大音数（默认 6）
--max-score-events INT     最大事件数（默认 5000）
--allow-accidentals        允许半音 token
--out-of-range-policy {error,octave_fold}  超范围策略
--pitch-compression-mode {adaptive_octave_fold,attention_weighted,hands_decoupled,svsep_mpdr,octave_fold,none}
--ref-smoothing FLOAT      ref_pitch 平滑系数（默认 0.2）
--left-max-chord-notes INT  左手最大和弦音数（默认 3，仅 hands_decoupled / attention_weighted）
--phrase-gap-beats FLOAT   休止符分割阈值拍数（默认 0.5，仅 hands_decoupled / attention_weighted）
--global-trend-alpha FLOAT 全局趋势平滑系数（默认 0.05，仅 hands_decoupled / attention_weighted）
--global-trend-window-beats FLOAT 趋势采样窗口拍数（默认 4.0，仅 hands_decoupled / attention_weighted）
--demucs-model MODEL       Demucs 模型（默认 htdemucs_6s）
--demucs-stem STEM         分轨目标（固定 piano，不可更改）
--transcription-checkpoint PATH  Transkun 模型权重 .pt 路径（不指定使用内置默认）
--transcription-device {cpu,cuda}  Transkun 推理设备（默认 cpu）
--transcription-segment-hop-size FLOAT  Transkun segment 步长秒数（默认使用模型值）
--transcription-segment-size FLOAT  Transkun segment 尺寸秒数（默认使用模型值）
--svsep-model-path PATH     piano_svsep 模型权重 .ckpt 路径（仅 svsep_mpdr）
--svsep-device {cpu,cuda}   piano_svsep 推理设备（默认 cpu，仅 svsep_mpdr）
--mpdr-*-*                  SVSEP-MPDR 候选、密度预算、旋律保护与精排参数
```

## 音高压缩模式

| 模式 | 原理 | 推荐度 |
|------|------|:---:|
| `adaptive_octave_fold` | 和弦统一八度偏移 k + ref_pitch 指数平滑 | **默认** |
| `attention_weighted` | 小节滑动窗口 + 交叉注意力 + velocity 加权 + 精排 | 推荐 |
| `hands_decoupled` | 左右手解耦 + 三重验证 + 和声功能简化 | 推荐 |
| `svsep_mpdr` | GNN 左右手分离 + 主旋律 DP + MPDR 密度预算 + 五维精排 | 推荐 |
| `octave_fold` | 逐音独立八度折叠（无上下文） | 不推荐 |
| `none` | 不压缩，超范围直接报错 | 仅窄音域 |

> `score_aware_theory` 模式已在 v3 基准评测中淘汰，不再推荐使用。

## 预设模板

### 残酷天使（MIDI）

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

> BPM 自动检测：音频文件使用 librosa onset 检测真实 BPM，并写入转录 MIDI 的 tempo 轨道。后续用 `--input-midi` 复用同一 MIDI 时不再需要 BPM 参数。
