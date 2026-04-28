# NTEZMusic 使用说明

> 作者：JucieOvo
> 日期：2026-04-28

---

## 一、环境要求

- Windows 10/11（SendInput API 依赖）
- Python 3.10+
- CUDA（Demucs 分轨与钢琴转录需要 GPU，CPU 亦可但极慢）
- 管理员权限（自动弹奏需要 R3 级操作注入）

---

## 二、安装

```powershell
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 安装依赖（使用国内镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 额外依赖
pip install librosa soundfile scipy -i https://pypi.tuna.tsinghua.edu.cn/simple
```

`requirements.txt`：
```
PyYAML>=6.0.1
pretty_midi>=0.2.10
demucs>=4.0.1
piano_transcription_inference @ git+https://github.com/qiuqiangkong/piano_transcription_inference.git
```

---

## 三、快速开始

### 3.1 MIDI → YAML（已有 MIDI 文件，最快）

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "path/to/song.mid" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" `
  --bpm 126 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals `
  --out-of-range-policy octave_fold
```

### 3.2 音频 → YAML（完整管线）

```powershell
python src/audio_to_yaml_converter.py `
  --audio "path/to/song.mp3" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" `
  --bpm 0 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals `
  --out-of-range-policy octave_fold
```

`--bpm 0` 表示自动检测：
- 音频文件：用 librosa onset 包络检测实际 BPM
- MIDI 文件：从 tempo 轨道读取。若为转录默认值 120 且有同名音频文件，则用音频重新检测
- 检测到的 BPM 会写入 MIDI tempo 轨道，后续复用不用再传

### 3.3 在游戏内弹奏

```powershell
# 以管理员身份运行
python src/piano_auto_player.py --config config/song.yaml
```

默认 3 秒后自动开始。进入游戏后切换到钢琴界面等待。

---

## 四、推荐参数

### 4.1 标准模式（attention_weighted，推荐）

```powershell
--pitch-compression-mode attention_weighted `
--allow-accidentals `
--out-of-range-policy octave_fold `
--max-chord-notes 6 `
--left-max-chord-notes 3 `
--phrase-gap-beats 0.5 `
--global-trend-alpha 0.05 `
--global-trend-window-beats 4.0
```

### 4.2 简化模式（hands_decoupled，较大和弦）

```powershell
--pitch-compression-mode hands_decoupled `
--allow-accidentals `
--out-of-range-policy octave_fold `
--max-chord-notes 6 `
--left-max-chord-notes 3
```

### 4.3 极简模式（adaptive_octave_fold，单线）

```powershell
--pitch-compression-mode adaptive_octave_fold `
--allow-accidentals `
--out-of-range-policy octave_fold `
--max-chord-notes 6
```

---

## 五、压缩模式对比

| 模式 | 左右手 | 和弦简化 | 上下文 | 推荐度 |
|------|:---:|:---:|:---:|:---:|
| `none` | 无 | 无 | 无 | 仅窄音域 |
| `octave_fold` | 无 | 一刀切 | 无 | 一般 |
| `adaptive_octave_fold` | 无 | 极值优先 | ref_pitch | 简单曲目 |
| `hands_decoupled` | 双峰+运动+velocity | 和声优先级 | 段内趋势 | 适中曲目 |
| `attention_weighted` | 滑动窗口三重验证 | **交叉注意力+velocity** | **全局+段内双层** | **推荐** |

---

## 六、弹奏参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--start-delay-seconds` | 3.0 | 弹奏开始前等待秒数 |
| `--key-press-seconds` | 0.0 | 按键保持时间（0 表示播放层 1ms 保底） |
| `--quantize-beat` | 0.25 | 量化精度（拍） |

---

## 七、完整参数列表

```
--audio PATH              输入音频路径（与 --input-midi 二选一）
--input-midi PATH          输入 MIDI 路径（提供时跳过 Demucs 与转录）
--output-yaml PATH         输出 YAML 路径（必填）
--work-dir PATH            中间文件目录（必填）
--song-name NAME           曲名（必填）
--bpm FLOAT                BPM，0 = 自动检测（默认 0）
--beat-unit INT            节拍单位（默认 4）
--start-delay-seconds FLOAT 弹奏前等待秒数（默认 3.0）
--key-press-seconds FLOAT  按键保持秒数（默认 0.0）
--quantize-beat FLOAT      量化步长（默认 0.25）
--max-chord-notes INT      单和弦最大音数（默认 6）
--max-score-events INT     最大事件数（默认 5000）
--allow-accidentals        允许半音 token
--out-of-range-policy {error,octave_fold}  超范围策略
--pitch-compression-mode {none,octave_fold,adaptive_octave_fold,hands_decoupled,attention_weighted}
--ref-smoothing FLOAT      ref_pitch 平滑系数（默认 0.2）
--left-max-chord-notes INT  左手最大和弦音数（默认 2）
--phrase-gap-beats FLOAT   休止符分割阈值（默认 0.5）
--global-trend-alpha FLOAT 全局趋势平滑系数（默认 0.05）
--global-trend-window-beats FLOAT 趋势采样窗口（默认 4.0）
--demucs-model MODEL       Demucs 模型（默认 htdemucs_6s）
--demucs-stem STEM         分轨目标（固定 piano）
--transcription-checkpoint PATH  钢琴转录权重路径
```

---

## 八、项目结构

```
NTEZMusic/
├── src/
│   ├── audio_to_yaml_converter.py    # 全管线入口（Demucs+转录+压缩）
│   ├── piano_auto_player.py          # 自动弹奏器（SendInput 注入）
│   ├── positional_encoding.py        # 正弦位置编码 + 时间邻近度
│   ├── cross_attention.py            # 全局/段内交叉注意力
│   ├── note_scorer.py                # 综合评分 + 和弦选择
│   └── reranker.py                   # 候选生成 + 精排
├── config/                           # YAML 曲谱输出
├── work/                             # 中间产物（Demucs 分轨、MIDI 转录、报告）
├── docs/                             # 技术文档
├── requirements.txt
├── AGENTS.md                         # CLI 快速参考
├── README.md
└── .gitignore
```

---

## 九、常见问题

**Q: 弹奏时没有声音或只弹了几个音？**
A: 必须以管理员身份运行终端。修饰键冲突时 1ms 交替可能被某些安全软件拦截。

**Q: 转录出的 MIDI 调不对？**
A: 运行 `--bpm 0` 让系统根据音频自动检测 BPM。检测值会写入 MIDI。

**Q: 和弦听起来很混浊？**
A: 降低 `--max-chord-notes` 到 3 或 4，或使用 `attention_weighted` 模式。

**Q: 旋律不明显/被伴奏淹没？**
A: 使用 `attention_weighted` 模式，其 velocity 加权会优先保留高力度旋律音。
