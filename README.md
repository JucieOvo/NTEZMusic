# NTEZMusic

将完整钢琴曲（88 键）自适应缩编到《异环》游戏 3 八度 36 键键盘的工具链。

## 流水线

```
音频(MP3/FLAC) -> Demucs 分轨 -> 钢琴转录 -> MIDI -> BPM 检测 -> 自适应压缩 -> YAML -> SendInput 弹奏
```

## 快速开始

```powershell
# 安装依赖
pip install -r requirements.txt

# MIDI -> YAML（已有 MIDI，跳过转录）
python src/audio_to_yaml_converter.py `
  --input-midi "song.mid" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 126 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6

# 音频 -> YAML（全管线：Demucs + Transkun + 压缩）
python src/audio_to_yaml_converter.py `
  --audio "song.mp3" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 0 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals --out-of-range-policy octave_fold `
  --max-chord-notes 6

# 游戏内弹奏（管理员运行）
python src/piano_auto_player.py --config config/song.yaml
```

## 音高压缩模式

| 模式 | 原理 | 基准得分 | 推荐 |
|------|------|:---:|:---:|
| `adaptive_octave_fold` | 和弦统一八度偏移 k + ref_pitch 指数平滑 | **0.5310** | **默认推荐** |
| `attention_weighted` | 小节滑动窗口 + 交叉注意力 + velocity 加权 + 精排 | 0.5305 | 推荐 |
| `hands_decoupled` | 左右手解耦 + 三重验证 + 和声功能简化 | 0.5279 | 推荐 |
| `svsep_mpdr` | GNN 左右手分离 + 主旋律 DP + MPDR 密度预算 | 0.5273 | 推荐 |
| `octave_fold` | 逐音独立八度折叠（无上下文） | — | 不推荐 |
| `none` | 不压缩，超范围直接报错 | — | 仅窄音域 |

*基准得分来自 2026-07 五首不同曲风钢琴曲综合评测，详见 `research` 分支 `work/arrangement_all_modes_20260711/reports/`。*

## 项目结构

```
src/
  audio_to_yaml_converter.py      # 全管线入口（MIDI -> YAML）
  piano_auto_player.py            # SendInput 游戏内弹奏
  arrangement_benchmark/          # 缩编算法客观评测框架
    runner.py                       # 基准实验编排器
    constraints.py                  # 异环 36 键硬约束校验
    metrics.py                      # 六维客观指标计算
    rendering.py                    # FluidSynth 真实钢琴渲染
  svsep_hand_separator.py         # piano_svsep GNN 左右手分离
  cross_attention.py              # 交叉注意力评分
  note_scorer.py                  # 音符优先级评分
  reranker.py                     # 候选精排

config/                           # YAML 曲谱配置示例

tests/                            # 测试套件
```

## 环境要求

- **Python**: 3.10+
- **CUDA**: 推荐 RTX 3090+（Demucs 分轨与 Transkun 转录需要 GPU）
- **外部工具**: FluidSynth 2.5+、ffmpeg 8.0+
- **SoundFont**: Salamander Grand Piano V3+（真实钢琴渲染）
- **模型权重**: Demucs/Transkun/piano_svsep 首次运行时自动下载或通过 modelscope 获取

## 依赖

```
PyYAML, pretty_midi, music21, librosa, scipy, numpy
torch, torchaudio, soundfile, partitura
demucs (Hybrid Transformer Demucs)
transkun (Neural Semi-CRF Transformer V2)
```

## 风险警告

自动弹奏使用 Windows SendInput API（R3 级操作注入），可能触发游戏反作弊检测导致账号封禁。以**管理员身份**运行。开发者不对账号封禁负责。

## 分支说明

| 分支 | 用途 |
|------|------|
| `main` | 工作分支 — 开箱即用工具链 |
| `research` | 研究分支 — 完整基准数据、实验结果、算法演进记录 |

## 许可

MIT
