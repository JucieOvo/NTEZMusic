# NTEZMusic

将完整钢琴曲（88 键）自适应缩编到《异环》游戏 3 八度 36 键键盘的工具链。

## 流水线

```
音频(MP3/FLAC) → Demucs 分轨 → 钢琴转录 → MIDI → BPM 检测 → 自适应压缩 → YAML → SendInput 弹奏
```

## 快速开始

```powershell
# 安装
pip install -r requirements.txt

# MIDI → YAML（已有 MIDI，跳过转录）
python src/audio_to_yaml_converter.py `
  --input-midi "song.mid" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 126 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6

# 音频 → YAML（全管线，自动 BPM）
python src/audio_to_yaml_converter.py `
  --audio "song.mp3" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 0 `
  --pitch-compression-mode attention_weighted `
  --allow-accidentals --out-of-range-policy octave_fold

# 游戏内弹奏（管理员运行）
python src/piano_auto_player.py --config config/song.yaml
```

## 压缩模式

| 模式 | 特点 |
|------|------|
| `attention_weighted` | **推荐** — 小节滑动窗口 + 交叉注意力 + velocity 加权 + 精排 |
| `hands_decoupled` | 左右手解耦 + 三重验证 + 和声功能简化 |
| `adaptive_octave_fold` | 和弦统一 k + ref_pitch 平滑 |
| `octave_fold` | 逐音独立八度折叠 |
| `none` | 不压缩，超范围报错 |

## 核心算法

**attention_weighted** 模式在 88→36 键压缩中实现：

- **小节对齐滑动窗口**：每 4 拍一小节，4 小节窗口，逐小节自适应左右手分割线
- **三重交叉验证**：双峰检测 + 运动模式 + velocity 统计确定分割线
- **交叉注意力评分**：正弦位置编码 + 全局/段内双层交叉注意力替代固定优先级查表
- **Velocity 加权**：高力度音符优先保留，应对交叉手场景
- **精排管线**：多参数扫描生成候选，四维全局质量分选择最优

实测（残酷天使 3450 音符）：输出 2745 音符（79.6%），MFCC 相似度 0.993，色度相似度 0.991。

## 项目结构

```
src/
├── audio_to_yaml_converter.py    # 全管线入口
├── piano_auto_player.py          # SendInput 弹奏
├── positional_encoding.py        # 正弦位置编码
├── cross_attention.py            # 交叉注意力
├── note_scorer.py                # 音符评分
└── reranker.py                   # 候选精排
config/                           # YAML 曲谱
work/                             # 中间产物
docs/                             # 技术文档
```

## 文档

- [算法说明](docs/attention_weighted_algorithm.md)
- [使用说明](docs/usage_guide.md)
- [CLI 快速参考](AGENTS.md)

## 风险警告

自动弹奏使用 Windows SendInput API（R3 级操作注入），可能触发游戏反作弊检测导致账号封禁。以**管理员身份**运行。开发者不对账号封禁负责。

## 依赖

- Python 3.10+
- PyYAML, pretty_midi, librosa, scipy, soundfile
- demucs (Hybrid Transformer Demucs)
- piano_transcription_inference (CNN + CRF)

## 许可

MIT
