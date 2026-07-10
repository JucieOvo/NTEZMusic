# NTEZMusic 项目代码情况说明书

> 面向后续开发者的完整项目导览
> 
> 编写日期：2026-07-11
> 项目根目录：F:\NTEZMusic
> 作者：JucieOvo

---

## 目录

1. [项目定位与核心目标](#一项目定位与核心目标)
2. [项目全景架构](#二项目全景架构)
3. [目录结构总览](#三目录结构总览)
4. [技术栈全景](#四技术栈全景)
5. [核心算法详解](#五核心算法详解)
6. [子系统说明](#六子系统说明)
7. [数据流汇总](#七数据流汇总)
8. [文档体系索引](#八文档体系索引)
9. [配置与测试体系](#九配置与测试体系)
10. [已知问题与改进方向](#十已知问题与改进方向)

---

## 一、项目定位与核心目标

### 1.1 一句话描述

将完整钢琴曲（88 键）自适应缩编到《异环》游戏 3 八度 36 键键盘（C3-B5，MIDI 48-83）的 YAML 曲谱生成 + 自动弹奏工具链。

### 1.2 硬约束

| 约束项 | 限制 |
|--------|------|
| 键盘范围 | C3-B5（MIDI 48-83），36 键 |
| 同时按键数 | 最多 6 键（取决于配置） |
| 延音踏板 | 游戏中无延音踏板支持 |
| 力度输出 | 游戏中无力度感应 |
| 输入方式 | SendInput API（需要管理员权限） |

### 1.3 目标游戏

| 游戏 | 键盘映射 | 通道数 | 适配模式 |
|------|---------|--------|---------|
| **异环 (NTEZ)** | 三行键盘 21 白键 + 升降半音 | 多音同时（最多 6 键） | `attention_weighted` / `svsep_mpdr` |
| **UpAgain** | 单行键盘 15 白键 | 单音轨 | C 大调白键移调 + 单音化 |

### 1.4 核心指标

#### Transkun 转录质量（88 键 MIDI）

评测基于 MAESTRO v3.0.0 真实录音的 Transkun 2.0.1 原始 120 BPM MIDI（产物 B）：

| 指标 | 巴赫 BWV 846 | 肖邦 Op.10 No.12 |
|------|-------------:|-----------------:|
| onset+pitch F1 | **0.9950** | **0.9728** |
| onset+pitch+offset F1 | 0.9372 | 0.8400 |
| velocity-aware F1 | 0.9361 | 0.8270 |
| frame piano-roll accuracy | 0.9070 | 0.6811 |
| onset MAE | 3.41 ms | 4.71 ms |
| onset P95 | 8.07 ms | 11.72 ms |
| offset P95（完整匹配对） | 42.24 ms | 46.09 ms |

> **重要发现（2026-07-10）**：此前项目评测使用 `_fix_midi_tempo()` 修改后的 MIDI（C）直接相比 MAESTRO 参考 MIDI，onset+pitch F1 仅约 0.05。经诊断确认，问题根因是 tempo 覆盖导致时间轴被拉伸，而非 Transkun 生成错误旋律。原始 B MIDI 实际转录质量极高。

#### 36 键缩编质量（attention_weighted 模式）

实测数据（残酷天使的行动纲领，3450 音符）：

| 指标 | 值 |
|------|-----|
| 输出音符数 | 2745（79.6%） |
| MFCC 相似度 | 0.993 |
| 色度相似度 | 0.991 |
| 压缩模式 | `attention_weighted` |

---

## 二、项目全景架构

### 2.1 顶层架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            NTEZMusic 项目架构                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  音频文件 (.mp3/.flac/.wav)                                                  │
│      │                                                                      │
│      ▼                                                                      │
│  ╔══════════════════════════════════════════════════════════════════╗        │
│  ║             音频主管线 (audio_to_yaml_converter.py)              ║        │
│  ║   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ║        │
│  ║   │ Demucs  │   │ Transkun │   │piano_svsep│   │  MPDR    │   ║        │
│  ║   │ 分轨    │──▶│ 转录    │──▶│ GNN分离  │──▶│ 缩编+精排│   ║        │
│  ║   └──────────┘   └──────────┘   └──────────┘   └───────────┘   ║        │
│  ╚══════════════════════════════════════════════════════════════════╝        │
│      │                                                                      │
│      ▼                                                                      │
│  ╔══════════════════════════════════════════════════════════════════╗        │
│  ║            多模态视频管线 (pipeline_runner.py)                   ║        │
│  ║   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ║        │
│  ║   │ ffmpeg  │   │ Transkun │   │ MediaPipe│   │ Fusion   │   ║        │
│  ║   │ 分离    │──▶│ 音频特征 │   │ 手部追踪 │──▶│ 融合对齐 │   ║        │
│  ║   └──────────┘   └──────────┘   └──────────┘   └───────────┘   ║        │
│  ╚══════════════════════════════════════════════════════════════════╝        │
│      │                                                                      │
│      ▼                                                                      │
│  ╔══════════════════════════════════════════════════════════════════╗        │
│  ║              游戏弹奏器 (piano_auto_player.py)                   ║        │
│  ║   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ║        │
│  ║   │ YAML    │   │ Notation │   │ VK Mapper │   │ SendInput│   ║        │
│  ║   │ 加载器  │──▶│ 解析器  │──▶│ 键码映射 │──▶│ API输入  │   ║        │
│  ║   └──────────┘   └──────────┘   └──────────┘   └───────────┘   ║        │
│  ╚══════════════════════════════════════════════════════════════════╝        │
│      │                                                                      │
│      ▼                                                                      │
│  ╔══════════════════════════════════════════════════════════════════╗        │
│  ║             UpAgain 游戏适配管线                                ║        │
│  ║   ┌──────────┐   ┌──────────┐   ┌──────────┐                    ║        │
│  ║   │主旋律提取│   │C大调移调│   │ ScanCode │                    ║        │
│  ║   │ +单音化  │──▶│ +网格量化│──▶│ 注入弹奏 │                    ║        │
│  ║   └──────────┘   └──────────┘   └──────────┘                    ║        │
│  ╚══════════════════════════════════════════════════════════════════╝        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 两条管线的关系

项目包含两条并行的处理管线：

| 特性 | 纯音频主管线 | 多模态视频管线 |
|------|------------|---------------|
| 输入 | 音频文件 或 已有 MIDI | 视频文件 |
| 声部分离 | piano_svsep (GNN) | MediaPipe 手部追踪 + 时空融合 |
| 缩编策略 | MPDR 参数扫描 + 精排 | 简单 velocity 窗口 |
| 工程成熟度 | 高（~4129 行，完整配置系统） | MVP 原型（~300 行，硬编码） |
| 推荐程度 | 主力路线 | 实验性路线 |

---

## 三、目录结构总览

```
F:\NTEZMusic/
│
├── src/                               # 核心源代码
│   ├── audio_to_yaml_converter.py     # [核心] 全管线入口，~4129 行
│   ├── piano_auto_player.py           # [核心] 自动弹奏器，~861 行
│   ├── svsep_hand_separator.py        # GNN 声部分离封装
│   ├── cross_attention.py             # 交叉注意力评分（手设权重）
│   ├── positional_encoding.py         # 正弦位置编码
│   ├── note_scorer.py                 # 音符综合评分
│   ├── reranker.py                    # 候选精排选择
│   ├── score_model.py                  # 乐谱语义 MVP 数据模型
│   ├── midi_event_canonicalizer.py     # 机翻 MIDI 清洗与起音聚类
│   ├── beat_grid_estimator.py          # 节拍与小节网格估计
│   ├── score_quantizer.py              # MIDI 音符到乐谱网格量化
│   ├── staff_notation_builder.py       # music21 五线谱构建与 MusicXML 导出
│   ├── score_notation_validator.py     # MusicXML 回读与合规校验
│   ├── score_semantic_analyzer.py      # 主旋律、低音与和弦角色基础分析
│   ├── theory_aware_36key_arranger.py  # 乐理感知 36 键缩编与 token 输出
│   ├── score_aware_theory_pipeline.py  # 乐谱语义感知 MVP 独立入口
│   ├── fusion_engine.py               # 多模态融合引擎
│   ├── dual_stream_extractor.py       # 双轨特征提取
│   ├── media_separator.py             # 音视频分离
│   ├── pipeline_runner.py             # 多模态总装流水线
│   ├── midi_to_upagain_yaml.py        # MIDI -> UpAgain YAML
│   ├── run_upagain_adaptation.py      # UpAgain 适配总控
│   ├── run_partial_fusion.py          # 局部融合执行
│   ├── analyze_score.py               # YAML 曲谱分析
│   └── inspect_midi_gap.py            # MIDI 间隙排查
│
├── config/                            # 19 个 YAML 曲谱文件
├── docs/                              # 技术文档
│
├── piano_svsep/                       # GNN 声部分离子模块（ISMIR 2024）
│   ├── piano_svsep/
│   │   ├── models/                    #   GNN 模型定义 (SAGEConv)
│   │   ├── data/                      #   数据集/预处理
│   │   └── utils/                     #   图构建/声部分离工具
│   └── launch_scripts/                #   训练/推理入口
│
├── UpAgain/                           # 《UpAgain》游戏适配
│   ├── upagain_auto_player.py         #   自动弹奏器（ScanCode 注入）
│   └── upagain_yaml_converter.py      #   C大调移调/网格量化
│
├── tests/                             # 测试（7 文件，98+ 用例）
│   ├── test_audio_to_yaml_svsep_mpdr.py
│   ├── test_svsep_hand_separator_audit.py
│   ├── test_score_audit_tool.py
│   ├── test_score_aware_theory_mvp.py
│   ├── test_score_aware_theory_arrangement.py
│   ├── test_raw_midi_generator.py       # [新增] Transkun 原始 MIDI 生成器验收测试
│   └── test_diagnostic_evaluator.py      # [新增] 诊断评测器 v2 回归测试
│
├── work/                              # 中间产物目录
│   └── transcription_benchmark/       # [新增] 转录质量基准测试
│       ├── raw_midi_generator.py      #    Transkun 原始 B MIDI 生成器
│       ├── diagnostic_evaluator.py    #    诊断性逐音评测器 v2
│       ├── bach_bwv846/               #    巴赫 BWV 846 基准数据
│       └── chopin_op10_no12/          #    肖邦 Op.10 No.12 基准数据
├── rel/                               # 旧版本归档
│
├── AGENTS.md                          # CLI 命令参考
├── README.md                          # 项目 README
└── requirements.txt                   # 核心依赖
```

---

## 四、技术栈全景

### 4.1 核心运行时依赖

| 组件 | 用途 | 调用方式 |
|------|------|---------|
| `Python 3.10+` | 运行时 | 推荐路径：`C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe` |
| `PyYAML >=6.0.1` | YAML 读写 | `import yaml` |
| `pretty_midi >=0.2.10` | MIDI 解析 | `import pretty_midi` |
| `music21 >=9.1.0` | 五线谱对象构建、MusicXML 导出与回读 | `stream.Score`, `converter.parse()` |
| `librosa` | BPM 检测、音频特征 | `librosa.onset.onset_strength`, `librosa.beat.beat_track` |
| `scipy` | 科学计算 | `import scipy` |
| `soundfile` | 音频读写 | `import soundfile` |
| `mir_eval >=0.8.2` | 转录评测标准库（onset/pitch/offset/velocity/frame） | `mir_eval.transcription` |
| `numpy >=1.20` | 数组运算（评测管线） | `import numpy as np` |

### 4.2 转录评测工具链（新增 2026-07-10）

| 组件 | 用途 | 路径 |
|------|------|------|
| `raw_midi_generator.py` | 生成 Transkun 原始 120 BPM MIDI（产物 B），产出 `notes_est.json` + MIDI + `run_manifest.json` | `work/transcription_benchmark/` |
| `diagnostic_evaluator.py` v2 | 诊断性逐音评测器，分离 raw_metrics / diagnostic_metrics / error_buckets，支持全局 scale/offset 校准与局部对齐诊断 | `work/transcription_benchmark/` |

### 4.3 音频处理

| 组件 | 用途 | 调用方式 |
|------|------|---------|
| **Demucs** (htdemucs_6s) | 音频分轨 | `python -m demucs --name htdemucs_6s <audio>` |
| **Transkun** (Neural Semi-CRF Transformer V2) | 钢琴音频 -> MIDI 转录 | `python -m transkun.transcribe` 或 `model.transcribe()` |
| **mido** | MIDI tempo 重写 | `mido.MidiFile`, `mido.bpm2tempo()` |
| **soxr** | 音频重采样 | `soxr.resample()` (Transkun 要求) |

### 4.4 GNN 声部分离

| 组件 | 用途 | 调用方式 |
|------|------|---------|
| **piano_svsep** | GNN 声部/谱表预测（ISMIR 2024） | 本地方案：`pip install -e piano_svsep/ --no-deps` |
| **music21** | MIDI -> MusicXML，五线谱语义 MVP 构建 | `converter.parse()`, `stream.Score.write()` |
| **partitura >=1.8.0** | MusicXML 加载 | `pt.load_score()` |
| **torch >=2.0** | 深度学习推理 | `torch.load()`, `torch.no_grad()` |
| **torch_geometric** | GNN 图计算 | `pyg.data.Batch.from_data_list()` |
| `pytorch_lightning` | 模型加载 | `PLPianoSVSep.load_from_checkpoint()` |

### 4.5 视觉处理

| 组件 | 用途 | 调用方式 |
|------|------|---------|
| **MediaPipe Tasks** (0.10.x+) | 手部关键点检测 | `vision.HandLandmarker` |
| **OpenCV (cv2)** | 视频解码 | `cv2.VideoCapture` |
| **hand_landmarker.task** | MediaPipe 预训练模型 | 需放置于 `src/` 目录下 |

### 4.6 平台特定

| 组件 | 用途 | 说明 |
|------|------|------|
| **Windows SendInput API** | 键盘事件注入 | `ctypes.windll.user32.SendInput`，管理员权限 |
| **ffmpeg** | 音视频分离 | 通过 `subprocess.run()` 调用 |
| **MapVirtualKeyW** | 虚拟键码 -> 扫描码 | 仅 UpAgain 模式使用 |

### 4.7 关键依赖缺失说明

`requirements.txt` 仅列出 4 个核心依赖，实际运行还需要额外安装：

```bash
# 核心
pip install librosa scipy soundfile mido
pip install music21 partitura
pip install torch torch_geometric pytorch_lightning

# piano_svsep（源码安装）
cd piano_svsep && pip install -e . --no-deps

# MediaPipe（仅多模态管线）
pip install mediapipe opencv-python

# ffmpeg（系统安装，用于多模态管线）
# 需要将 ffmpeg 加入系统 PATH

# 转录评测工具（仅基准测试管线）
pip install mir_eval numpy scipy mido pretty_midi
```

---

## 五、核心算法详解

### 5.1 算法演进路线

```
v1 (三分区强制映射)                    【已废弃】
  └── 错误: 把三行键盘理解成功能分区

v2 (自适应八度折叠)                     【基础模式】
  └── 核心: 统一八度偏移 k + ref_pitch 指数平滑
  └── 局限: 不区分左右手, 角色串扰

hands_decoupled v2                     【次推荐模式】
  └── 核心: 全局趋势线 + 三重交叉验证分割线 + 左手简化
  └── 局限: 基于启发式的分割线

attention_weighted                     【推荐模式】
  └── 核心: 正弦位置编码 + 交叉注意力 + velocity 加权 + 精排
  └── 实测: 79.6% 保留率, 0.993 MFCC 相似度

svsep_mpdr (v3)                        【最高质量模式】
  └── 核心: GNN 声部分离 + MPDR 感知密度预算 + 五维精排
  └── 设计: 纯音频主链路, 不依赖视频
```

### 5.2 attention_weighted 算法（推荐模式）

**文档**：`docs/attention_weighted_algorithm.md`

**流程图**：

```
MIDI 量化分组 -> {时间片: [原始音高]}
    |
    v
全局趋势线提取 -> global_ref[t]（非因果双向指数平滑）
    |
    v
小节切分（4 拍 = 1 小节 = 16 时间片）
    |
    v
逐小节滑动窗口分割线（5 小节窗口）
    |-- 双峰检测（音高直方图平滑）
    |-- 运动模式验证（级进 vs 跳进）
    |-- velocity 验证（高力度 vs 低力度）
    |-- 终裁：三重交叉验证
    |
    v
逐时间片处理：
    |-- 左右手分离（按分割线）
    |-- 八度折叠到 C3-B5（统一 k，音程精确保留）
    |-- 左手简化（交叉注意力评分选择）
    |-- 最终密度控制
    |
    v
精排管线（27 候选 -> 12 候选 -> 四维全局质量分 -> 最优）
```

**核心评分公式**（四维线性加权）：

```
Score = 0.30 x ChordFunction + 0.25 x GlobalAttention + 0.25 x SegmentAttention + 0.20 x VoiceLeading
```

- **和声功能优先级**：基于音程距离的乐理先验
- **全局交叉注意力**：正弦位置编码 + 音高相似度 + 密度权重 + 时长权重
- **段内交叉注意力**：同全局公式 + 和声上下文权重
- **声部进行平滑度**：与前后时间片的最小级进距离

### 5.3 svsep_mpdr 算法（当前最新）

**设计文档**：`docs/DESIGN.md`（SVSEP-MPDR v3）

**流程图**：

```
MIDI 文件
    |
    v
MidiEventNormalizer
    |-- Onset 聚类（velocity 加权平均起音合并）
    |-- 同音延音合并（merge_sustained_gap_beats 间隔合并）
    |-- 时间片量化
    |
    v
SvsepHandSeparator（GNN 声部分离）
    |-- MIDI -> music21 -> MusicXML
    |-- partitura 加载 -> note_array
    |-- piano_svsep(GNN) 推理 -> pred_staff
    |-- 双指针回填到原始 MIDI notes
    |-- 审计验证 (match_ratio >= 90%)
    |
    v
左右手分别延音合并 + 左手长低音续打击键
    |
    v
MelodyPathTracker
    |-- DP 动态规划追踪右手主旋律路径
    |-- 评分维度: pitch + velocity + duration
    |-- 惩罚项: 大跳 + 同音重复
    |
    v
MpdrCandidateBuilder（参数扫描生成候?）
    |-- 扫描参数: mpdr_melody_protection_strength
    |              mpdr_left_opacity_base
    |              mpdr_register_collision_penalty
    |-- 每个候选执行完整缩编
    |
    v
MpdrCandidateReranker（五维精排）
    |-- melody_integrity (0.30)
    |-- masking_avoidance (0.25)
    |-- harmony_integrity (0.20)
    |-- bass_continuity (0.15)
    |-- register_clarity (0.10)
    |
    v
GameKeyboardTokenEmitter
    |-- C3-B5 折叠 + token 冲突消解
    |-- 优先级: 主旋律 > bass anchor > 右手非旋律 > 左手和声
    |
    v
YAML score events + conversion_stats
```

**与旧管线的关键差异**：

| 维度 | attention_weighted（旧） | svsep_mpdr（新） |
|------|------------------------|-----------------|
| 声部分离 | 启发式分割线（音高直方图 + 三重验证） | piano_svsep GNN 预测 staff 标签 |
| 右手处理 | 全部保留 | DP 主旋律路径追踪 + 有选择保留 |
| 左手简化 | 交叉注意力 + 密度上限裁剪 | MPDR 感知密度预算（bass 锚点 + 色彩音 + 声部连接） |
| 八度折叠 | 统一 k + ref_pitch 平滑 | 左右手独立参考中心折叠 |
| 评分维度 | 4 维（旋律/和声/密度/声部） | 5 维（旋律/遮蔽/和声/低音/音区） |

### 5.4 hands_decoupled 算法

- 核心：全局趋势线 + 休止符分割 + 三重交叉验证 + 左手和声功能优先级裁剪
- 详细文档：`docs/hands_decoupled_v2_final.md`

### 5.5 自适应八度折叠（基础模式）

- 核心：和弦内统一 k，遍历 [-6, +6] 八度偏移，选最接近 ref_pitch 的 k
- 详细文档：`docs/adaptive_octave_fold_algorithm.md`

---

## 六、子系统说明

### 6.1 音频 -> MIDI 前端 (`audio_to_yaml_converter.py`)

| 组件 | 职责 | 关键方法 |
|------|------|---------|
| `DemucsSeparator` | 调用 Demucs 音频分轨 | `separate(config) -> stem_path` |
| `PianoTranscriber` | Transkun 钢琴转录 | `transcribe(audio_path, config) -> midi_path` |
| `AudioToYamlPipeline` | 流水线调度（音频分轨 -> 转录 -> 转换） | `run(config) -> yaml_path` |
| `MidiToYamlConverter` | MIDI -> YAML 转换（核心） | `convert(midi_path, config) -> dict` |

**关键数据预处理**：

- `_merge_sustained_midi_notes()`：贪心流式合并同音碎片（延音踏板造成的重叠碎音）
- `_cluster_midi_note_onsets()`：velocity 加权平均起音聚类（毫秒级偏差的和弦合并）
- `_estimate_audio_bpm()`：librosa onset + dynamic programming beat tracker

### 6.2 GNN 声部分离 (`svsep_hand_separator.py`)

- 将 MIDI 转为 MusicXML，通过 piano_svsep 预测 staff 标签
- 返回 `SvsepSeparationResult`（含 left_notes, right_notes, audit）
- 审计包含 8 个维度：match_ratio, predicted_staff_count 等

### 6.3 交叉注意力评分 (`cross_attention.py` + `note_scorer.py`)

- 非神经网络的纯算法注意力：手设相似度函数替代可学习投影矩阵
- 正弦位置编码：`PE(beat, 2i) = sin(beat / 10000^(2i/D))`
- 时间邻近度：位置编码向量内积 / D 归一化

### 6.4 精排管线 (`reranker.py`)

- 扫描 3 参数（left_max_chord_notes, ref_smoothing, global_trend_alpha）
- 27 个组合裁剪到 12 个候选
- 四维全局质量分选择最优

### 6.5 自动弹奏器 (`piano_auto_player.py`)

| 组件 | 职责 | 说明 |
|------|------|------|
| `PianoConfigLoader` | YAML 加载与严格校验 | 禁止使用默认值掩盖错误 |
| `NotationParser` | 简谱解析（休止符/和弦/变音） | `CHORD_PATTERN = re.compile(r"\[[^\]]+\]|\S+")` |
| `VirtualKeyMapper` | 简谱 token -> Windows 虚拟键码 | 三行键盘映射 |
| `WinApiInputBackend` | SendInput API 封装 | ctypes 直接调用 |
| `PianoAutoPlayer` | 节拍时间线调度 | BPM 驱动，修饰键冲突 1ms 交替 |

**键盘映射**：

```
+1 #1 +2 b3 +3 +4 #4 +5 #5 +6 b7 +7    (QWERTY 行, C5-B5)
 1 #1  2 b3  3  4 #4  5 #5  6 b7  7    (ASDFGHJ 行, C4-B4)
-1 #1 -2 b3 -3 -4 #4 -5 #5 -6 b7 -7    (ZXCVBN 行, C3-B3)
```

- 前缀规则：`+` = 高音区，无前缀 = 中音区，`-` = 低音区
- `#` = 升半音（Ctrl），`b` = 降半音（Shift）

### 6.6 多模态视频管线

| 组件 | 职责 | 关键方法 |
|------|------|---------|
| `media_separator` | ffmpeg 音视频分离 | `extract_media_streams()` |
| `AudioFeatureExtractor` | Transkun 音频转写 | `run_transcription()` |
| `VisionFeatureExtractor` | MediaPipe 手部追踪 | `run_visual_tracking()` |
| `MultiModalFusionEngine` | 音视频融合对齐 | `align_and_assign()`, `determine_melody_by_velocity()` |

**对齐算法**：
1. 0.05s 起音聚合（和弦合并）
2. 0.15s 容差窗口（音频 vs 视频时间偏移）
3. 空间排序映射（低音 -> 左手，高音 -> 右手）
4. 上下文推理（三层递进：音域强制 -> 历史惯性 -> Unknown）

### 6.7 UpAgain 适配管线

| 组件 | 职责 | 关键方法 |
|------|------|---------|
| `MidiMelodyExtractor` | 右手轨主旋律提取 | Viterbi DP 最优旋律路径 |
| `PitchToNotationTokenConverter` | MIDI 音高 -> 简谱 Token | `convert_pitch(pitch)` |
| `UpAgainTimeHopMusicalConverter` | C大调移调 + 网格量化 | `convert(input_path, output_path)` |
| `UpAgainNotationParser` | 单音过滤 + 映射 | 和弦取最高音 |
| `EmulatorDirectInputBackend` | ScanCode 注入 | `MapVirtualKeyW` + `KEYEVENTF_SCANCODE` |

**单音化三阶段**：
1. Viterbi DP：发声组内力度 + 连续性最优单音路径
2. 网格取最高音：0.125 拍网?内保留最高音
3. 解析器端和弦过滤：和弦中取最高权重音符（防御性设计）

### 6.8 转录评测管线（新增 2026-07-10）

| 组件 | 职责 | 路径 |
|------|------|------|
| `raw_midi_generator.py` | Transkun 原始 120 BPM MIDI（产物 B）生成器，产出不可变三类产物：`notes_est.json` + `transkun_raw_120bpm.mid` + `run_manifest.json` | `work/transcription_benchmark/` |
| `diagnostic_evaluator.py` v2 | 诊断性逐音评测器，将音高内容错误与起音/时值/速度/时序错误分离诊断 | `work/transcription_benchmark/` |

**diagnostic_evaluator.py 评测维度：**

| 维度 | 说明 |
|------|------|
| raw_metrics | 端到端原始比对（不校准）：onset_only F1、onset_pitch F1、onset_pitch_offset F1、velocity_aware F1、frame_piano_roll accuracy |
| diagnostic_metrics | 诊断性校准指标：global_offset（最优恒定偏移）、global_scale（最优全局缩放因子）、scale_then_offset（缩放+残余偏移）、local_alignment（分段滑动窗口局部 tempo 漂移诊断） |
| error_buckets | 未匹配音符分类：nearly_matched、正确起音/错误音高、正确音高/错误起音、纯漏检/纯插入 |

**A/B/C 产物体系（评测约定）：**

| 标识 | 定义 | 性质 |
|------|------|------|
| **A** | MAESTRO Disklavier `reference_performance.midi` | 黄金标准参考 |
| **B** | Transkun `notes_est` 经 `writeMidi()` 直接生成的 120 BPM 原始 MIDI | 不可变，供评测与演奏 |
| **C** | B 的 tempo 元事件被 `_fix_midi_tempo()` 覆盖后的播放 MIDI | 已被禁止用于评测 |

**关键发现**：
- B 与 C 的 note_on/note_off tick、音高、力度、通道、顺序完全逐事件一致
- 唯一差异是 tempo 元事件（B: 500000 μs/beat → C: ~510000-534000 μs/beat）
- B 的真实 onset+pitch F1 为 0.9950（巴赫）和 0.9728（肖邦）
- C 未校准时 F1 仅为 0.0475/0.0534 — 不代表转录质量
- `_fix_midi_tempo()` 通过拉伸时间轴制造了"旋律错音"假象

---

## 七、数据流汇总

### 7.1 主线数据流（推荐流程）

**注意（2026-07-10 诊断结论）**：`_fix_midi_tempo()` 通过修改 tempo 元事件而不重算 tick，破坏了 MIDI 的绝对时间尺度。已确认 Transkun 原始 120 BPM MIDI（B）的音符内容正确（F1=0.97-0.99），评测应直接使用 B，不得使用被 tempo 覆盖的 C。

```
音频文件 (.mp3/.flac)
    |
    |-- Demucs 分轨 -> piano.wav
    |-- Transkun 转录 -> transkun_raw_120bpm.mid (B，不可变产物)
    |-- [可选] BPM 检测（librosa）-> detected_bpm.json (独立 sidecar)
    |
    |-- [评测] 使用 B vs MAESTRO 参考 A，经 diagnostic_evaluator.py 评测
    |
    v
MidiToYamlConverter.convert(midi, config)
    |
    |-- _read_midi_notes() -> 合并延音碎片 + onset 聚类 -> MidiNoteEvent[]
    |
    |-- [svsep_mpdr 模式]:
    |     |-- _separate_hands_with_svsep() -> left_notes, right_notes
    |     |-- 左右手分别延音合并
    |     |-- _build_svsep_mpdr_score_events():
    |           |-- _split_svsep_sustained_notes() (左手长低音续打)
    |           |-- _generate_svsep_mpdr_candidates() (参数扫描)
    |           |-- _build_svsep_mpdr_candidate() (逐候选缩编):
    |           |     |-- 右手: DP 主旋律追踪 + 选择性保留
    |           |     |-- 左手: MPDR 预算感知选择
    |           |     |-- 八度折叠 + token 冲突消解
    |           |     |-- 五维全局评分
    |           |-- 选最优候选 -> YAML score events
    |
    |-- [attention_weighted 模式]:
    |     |-- _build_attention_weighted_grouped_notes():
    |           |-- 全局趋势线
    |           |-- 滑动窗口分割线 + 三重验证
    |           |-- 自适应八度折叠
    |           |-- 交叉注意力评分选择
    |           |-- 精排管线 (12候选 -> 最优)
    |
    v
YAML 曲谱 -> piano_auto_player.py 加载 -> SendInput API -> 游戏内弹奏
```

### 7.2 视频输入替代路线

```
视频文件 (.mp4)
    |
    |-- ffmpeg 分离 -> 视频轨 (.mp4) + 音频轨 (.wav)
    |-- Transkun 音频转写 -> temp_transkun_base.mid
    |-- MediaPipe 手部追踪 -> temp_hand_tracking.json
    |
    v
MultiModalFusionEngine
    |-- load_data()
    |-- align_and_assign(0.15s 容差) -> 左右手分配
    |-- determine_melody_by_velocity(2s 窗口) -> is_melody 标记
    |-- export_separated_midi() -> final_hand_split_score.mid
    |
    v
（接入 UpAgain 适配管线）
```

### 7.3 UpAgain 适配数据流

```
final_hand_split_score.mid
    |
    |-- MidiMelodyExtractor.extract_melody_events()
    |     |-- 定位 "Right Hand" 轨
    |     |-- 0.03s 起音分组 -> Viterbi DP -> 单音路径
    |     |-- serialize_to_yaml() -> temp_raw_score.yaml
    |
    v
UpAgainTimeHopMusicalConverter.convert()
    |     |-- 简谱 token -> 绝对音高
    |     |-- 0.125 拍网格量化
    |     |-- C大调最佳移调（扫描 -5 ~ +7 半音）
    |     |-- 八度居中（对齐 C5=72）
    |     |-- 黑键残余纠正
    |     |-- 合并相邻休止符
    |
    v
upagain_playable.yaml
    |
    |-- UpAgainNotationParser.parse_score()
    |-- EmulatorDirectInputBackend._send_key_event()
    |     |-- MapVirtualKeyW (VK -> ScanCode)
    |     |-- SendInput (wVk=0, wScan=scan_code, flags=SCANCODE)
    |
    v
MuMu 模拟器内弹奏
```

### 7.4 转录评测数据流（新增 2026-07-10）

```
MAESTRO 真实配对数据
    |
    |-- input.mp3 (Disklavier 录音)
    |-- source.wav (WAV 格式)
    |-- reference_performance.midi (A, 黄金标准)
    |
    v
raw_midi_generator.py
    |-- _load_audio_samples() → 读取 Demucs piano stem
    |-- _load_transkun_model() → 加载 Transkun 2.0.1
    |-- model.transcribe() → notes_est (pitch/start/end/velocity)
    |-- writeMidi() → transkun_raw_120bpm.mid (B, 不可变)
    |-- _build_run_manifest() → run_manifest.json (输入/模型/输出哈希)
    |
    v
diagnostic_evaluator.py
    |-- extract_notes_from_midi(A) → ref_intervals, ref_pitches
    |-- extract_notes_from_midi(B/C) → est_intervals, est_pitches
    |
    |-- raw_metrics: mir_eval 标准比对 (onset/pitch/offset/velocity/frame)
    |-- diagnostic_metrics:
    |     |-- global_offset (网格搜索最优恒定偏移)
    |     |-- global_scale (网格搜索最优缩放因子 α, 0.8-1.2)
    |     |-- scale_then_offset (α 缩放后搜索残余 offset)
    |     |-- local_alignment (分段滑动窗口局部 tempo 漂移)
    |
    |-- error_buckets: 匹配失败逐音分类
    |     |-- nearly_matched (offset 不满足但 onset+pitch 正确)
    |     |-- co_wp (正确起音/错误音高)
    |     |-- cp_wo (正确音高/错误起音)
    |     |-- pure_missed (纯漏检)
    |     |-- pure_false_alarm (纯插入)
    |
    v
diagnostic_raw_b.json + diagnostic_tempo_c.json
```

**关键校准预期**：
C 的绝对时间被拉伸因子 `120/detected_BPM`，因此 `global_scale` 应恢复 `detected_BPM/120`：
- 巴赫 ≈ 117.5/120 = 0.979
- 肖邦 ≈ 112.3/120 = 0.936

---

## 八、文档体系索引

`docs/` 目录共 37 篇文档，分为 10 类：

### 第 1 类：SVSEP-MPDR v3 四件套（核心迭代）

| 文档 | 摘要 |
|------|------|
| `DESIGN.md` | v3 设计蓝图：7 功能需求、6 核心模块、4 关键决策 |
| `PLAN.md` | 6 个可执行任务、4 个检查点、依赖图 |
| `PRACTICE.md` | 7 项变更的执行记录（含代码 diff） |
| `AUDIT.md` | 三维审计：设计符合率 100%、规划执行率 100% |

### 第 2 类：算法演进

| 文档 | 摘要 |
|------|------|
| `implementation_chronicle.md` | **必读**。项目从 v1 到 v2 的完整演进纪实 |
| `attention_weighted_algorithm.md` | 推荐模式的完整算法说明 |
| `hands_decoupled_v2_final.md` | hands_decoupled v2 信号增强版算法 |
| `adaptive_octave_fold_algorithm.md` | 自适应八度折叠 v2 算法说明 |
| `attention_weighted_rerank_algorithm.md` | 精排管线方案 |

### 第 3 类：工具使用

| 文档 | 摘要 |
|------|------|
| `audio_to_yaml_converter使用说明.md` | 音频转换工具完整使用文档 |
| `usage_guide.md` | 面向用户的完整使用指南 |
| `audio_to_yaml_pipeline方案.md` | Demucs + Transkun 方案设计 |

### 第 4 类：YAML 曲谱与弹奏

| 文档 | 摘要 |
|------|------|
| `yaml_score_guide.md` | YAML 曲谱手写规范 |
| `piano_auto_play方案.md` | 自动弹奏器设计方案 |

### 第 5 类：声部分离

| 文档 | 摘要 |
|------|------|
| `svsep_integration_proposal.md` | piano_svsep GNN 集成方案，新旧管线对比 |

### 第 6 类：主旋律提取

| 文档 | 摘要 |
|------|------|
| 5 篇优化方案 + 4 篇实践审计报告 | 涵盖 Viterbi DP、时域裁切、阈值优化等 |

### 第 7 类：多模态融合

| 文档 | 摘要 |
|------|------|
| 3 篇方案 | 手部错排修复、实时追踪改进、视觉标注优化 |

### 第 8 类：其他

| 文档 | 摘要 |
|------|------|
| `transcribe_migration_transkun方案.md` | 转录引擎迁移方案 |
| `sustain_split_design.md` | 左手延音选择性续打设计 |
| `superpowers/specs/` 2 篇 | 详细设计规格 |
| `superpowers/plans/` 1 篇 | 执行计划 |
| UpAgain 系列 6 篇 | UpAgain 适配方案 |

### 第 9 类：转录质量评测（新增 2026-07-10）

| 文档 | 摘要 |
|------|------|
| `transkun_raw_midi_evaluation_plan.md` | Transkun 原始 MIDI 真实评测方案：产物定义、评测矩阵、执行策略 |
| `transkun_raw_midi_evaluation_report.md` | **必读**。评测结论：B 原始 MIDI F1=0.97-0.99，`_fix_midi_tempo()` 是评测失败的根因 |
| `mp3_to_midi_diagnosis_brief_for_expert_model.md` | 诊断 Brief v1（历史版，部分结论已被 v2 推翻） |
| `mp3_to_midi_diagnosis_brief_for_expert_model_v2.md` | **必读**。诊断 Brief v2：修正版，确认 tempo 覆盖为根因，重定优化方向 |

### 第 10 类：项目导览

| 文档 | 摘要 |
|------|------|
| `project_code_overview.md` | 本文件，项目代码总览说明书 |
| `近期代码变更与研究方向说明.md` | **必读**。阶段性成果汇总与分析，面向技术总监 |
| `score_aware_theory_arrangement_plan.md` | 乐谱语义感知缩编完整技术方案 |

---

## 九、配置与测试体系

### 9.1 YAML 曲谱格式

```yaml
song:
  name: "曲名"        # 必填，非空字符串
  bpm: 126            # 必填，> 0
  beat_unit: 4        # 必填，> 0

playback:
  start_delay_seconds: 3.0    # 开始前等待
  key_press_seconds: 0.0      # 按键保持（播放层保底 1ms）

keyboard:
  high: { "1": "q", "2": "w", ... }    # C5-B5
  middle: { "1": "a", "2": "s", ... }   # C4-B4
  low: { "1": "z", "2": "x", ... }      # C3-B3

score:
  - { notes: "1 3 5",  beat: 0.25 }
  - { notes: "[+1 +3 +5]", beat: 0.50 }
  - { notes: "0",      beat: 1.00 }
```

### 9.2 CLI 参数体系（audio_to_yaml_converter.py）

完整参数见 `AGENTS.md`，核心参数分类：

| 类别 | 参数示例 |
|------|---------|
| 输入源 | `--audio`, `--input-midi` |
| 输出 | `--output-yaml`, `--work-dir`, `--song-name` |
| 压缩模式 | `--pitch-compression-mode`（5 种模式） |
| 效果控制 | `--max-chord-notes`, `--allow-accidentals` |
| 左右手 | `--left-max-chord-notes`, `--phrase-gap-beats` |
| 转录 | `--transcription-checkpoint`, `--transcription-device` |
| svsep_mpdr | `--svsep-model-path`, `--mpdr-*-*`（共 30+ 参数） |
| 延音处理 | `--sustain-split-*` |

### 9.3 测试体系

| 测试文件 | 用例数 | 覆盖内容 |
|----------|--------|---------|
| `test_audio_to_yaml_svsep_mpdr.py` | 12 | svsep_mpdr 管线端到端测试 |
| `test_svsep_hand_separator_audit.py` | 4 | GNN 分离审计验证 |
| `test_score_audit_tool.py` | 1 | 曲谱审计工具测试 |
| `test_score_aware_theory_mvp.py` | ~10 | score_aware_theory MVP 阶段测试 |
| `test_score_aware_theory_arrangement.py` | ~15 | score_aware_theory 编排 + 集成测试 |
| `test_raw_midi_generator.py` | 20 | Transkun 原始 MIDI 生成器真实验收测试 |
| `test_diagnostic_evaluator.py` | ~50 | 诊断评测器 v2 回归测试（含 scale 恢复验证） |
| **合计** | **~112** | |

**测试覆盖遗漏**（建议补充）：
- `piano_auto_player.py`（自动弹奏器）零测试覆盖
- `cross_attention.py`、`note_scorer.py`、`reranker.py` 单元测试缺失
- 多模态视频管线零测试覆盖
- UpAgain 适配管线零测试覆盖

### 9.4 审计工具

`work/score_audit/score_audit_tool.py` 对 YAML 曲谱进行 7 步审查：

1. 读取原始 MIDI 与转换后 YAML
2. 同步复制（时间片上采样对齐）
3. 音域超界检查
4. 缺失低音检查
5. 缺失旋律峰检查
6. 覆盖率统计
7. 单和弦重叠键检查（同一时间片同 token 冲突）

审计结果示例（三首曲目均包含 200+ 个 issue）：

| 指标 | One Last Kiss | 残酷天使 | ただ声一つ |
|------|:---:|:---:|:---:|
| 总 issue 数 | 235 | 625 | 311 |
| 覆盖率 | 65.1% | 70.4% | 80.5% |
| 混合升降 | 249 | 74 | 758 |
| 升半音 % | 10.3% | 3.1% | 34.7% |

---

## 十、已知问题与改进方向

### 10.1 各子系统的关键问题

#### A. 音频主管线（~4129 行）

| 问题 | 严重性 | 说明 |
|------|--------|------|
| `_fix_midi_tempo()` 破坏时间轴 | **极高** | 已确认将 onset+pitch F1 从 0.99 级别降至 0.05 级别。通过修改 tempo 元事件而不重算 tick，导致绝对时间被全局拉伸。主管线应改为保留原始 120 BPM MIDI（B）作为不可变产物，BPM 作为独立 sidecar 传递。 |
| 基于被污染 C MIDI 的历史评测 | **高** | 此前所有基于 `pipeline/midi/input.mid`（C）的评测结论需要复核。B 的转录质量远高于此前认知。 |
| `_build_svsep_mpdr_candidate` 左手选择逻辑仅实现 bass+fifth | **高** | 理论上应支持 bass anchor / harmony color / voice leading / velocity / duration 等多维度，但当前仅 bass anchor + color fifth 被实现 |
| MIDI -> MusicXML 往返误差 | **中** | pretty_midi -> music21 -> partitura 的多步格式转换可能丢失精度 |
| 参数扫描多样性不足 | **低** | 27 候选裁剪到 12，但实际仅 3 参数 x 3 取值，同一曲目内的候选差异有限 |
| 统计字段混合赋值 bug (v3) | **中** | 第 1206-1217 行存在 `remapped_notes`, `octave_moved_notes` 等统计变量在 svsep_mpdr 模式下被混合覆盖的风险 |
| 对 piano_svsep 强依赖 | **高** | svsep_mpdr 模式完全依赖 GNN 分离结果，分离失败整个管线不可用 |

#### B. 自动弹奏器

| 问题 | 严重性 | 说明 |
|------|--------|------|
| 零测试覆盖 | **高** | 861 行关键代码没有任何测试 |
| SendInput 反作弊风险 | **高** | R3 级操作注入可能触发游戏反作弊检测 |
| 无干运行模式 | **中** | 无法在不实际按键的情况下验证 YAML |
| UIPI 权限限制 | **中** | 管理员权限下 SendInput 仍可能被 UIPI 阻塞 |

#### C. 多模态管线

| 问题 | 严重性 | 说明 |
|------|--------|------|
| 工程成熟度低 | **高** | MVP 原型，硬编码路径/参数，无缓存机制 |
| 视觉遮挡处理弱 | **中** | MediaPipe 丢帧时仅依赖简单音域规则回退 |
| 对齐算法假定"低音->左手" | **中** | 交叉手场景完全失效 |
| 主旋律判定忽略预存标记 | **中** | MIDI 中的 `is_melody` 标记被 Viterbi 忽略 |

#### D. UpAgain 适配

| 问题 | 严重性 | 说明 |
|------|--------|------|
| Viterbi 权重失衡 | **中** | `w_vel=0.1` 几乎不影响路径选择 |
| 黑键残余纠正过于简单 | **低** | 优先向上取整而非最近距离 |
| 缺乏防御性校验 | **低** | 极端音域可能导致 KeyError |

#### E. 文档

| 问题 | 严重性 | 说明 |
|------|--------|------|
| 环境搭建指南缺失 | **高** | `requirements.txt` 仅 4 个依赖，缺少完整的一键搭建说明 |
| API 参考缺失 | **高** | 4000+ 行的核心类无面向开发者的 API 参考 |
| 模型权重获取说明缺失 | **高** | piano_svsep 的 `.ckpt` 文件来源未文档化 |
| 跨文档引用断裂 | **低** | `@DESIGN:xxx` 锚点无法在普通 Markdown 中跳转 |

### 10.2 建议的迭代优先级

| 优先级 | 改进项 | 影响范围 |
|--------|--------|---------|
| P0 | 完善环境搭建文档（一键 install 脚本） | 新开发者 onboarding |
| P0 | 为自动弹奏器添加单元测试 + 干运行模式 | 弹奏安全性 |
| P0 | 补全 piano_svsep 模型权重文档 | GNN 分离管线可用性 |
| P1 | 修复 svsep_mpdr 左手选择逻辑（实现 harmony / voice_leading 维度） | 压缩质量 |
| P1 | 添加 API 参考文档（MidiToYamlConverter 类） | 后续开发效率 |
| P1 | config/ 下 19 个 YAML 的用途对照表 | 曲谱管理 |
| P2 | 修复统计字段混合赋值 bug | 数据准确性 |
| P2 | 多模态管线硬化（路径参数化 + 缓存 + logging） | 视频管线可用性 |
| P2 | `_predict_hand_from_context` 增强（引入 GNN 结果作为补充） | 交叉手场景 |
| P3 | Viterbi 权重调优 | UpAgain 主旋律精度 |
| P3 | 参数扫描扩展（增加更多扫描维度） | 精排效果 |

---

### 附录 A：文件行数分布

| 文件 | 行数 | 类型 |
|------|------|------|
| `src/audio_to_yaml_converter.py` | 4128 | 核心逻辑 |
| `src/piano_auto_player.py` | 861 | 核心逻辑 |
| `src/svsep_hand_separator.py` | 478 | GNN 分离 |
| `src/reranker.py` | 363 | 精排 |
| `src/note_scorer.py` | 295 | 评分 |
| `src/midi_to_upagain_yaml.py` | 295 | UpAgain |
| `src/fusion_engine.py` | 273 | 多模态 |
| `src/cross_attention.py` | 271 | 注意力 |
| `UpAgain/upagain_auto_player.py` | 225 | UpAgain |
| `src/positional_encoding.py` | 84 | 编码 |
| **合计** | **~7644** | 核心源码 |

### 附录 B：术语表

| 术语 | 说明 |
|------|------|
| MPDR | Melody-Protecting Dynamic Reduction，主旋律保护型动态缩编 |
| svsep | 声部分离（Voice Separation） |
| GNN | 图神经网络（piano_svsep 的底层模型） |
| Transkun | Neural Semi-CRF Transformer V2 钢琴转录模型 |
| Demucs | Facebook Research 的音频分离模型 |
| MFCC | Mel 频率倒谱系数，音频特征相似度度量 |
| SendInput | Windows API，用于注入键盘/鼠标事件 |
| ScanCode | 键盘硬件级扫描码，绕过虚拟键码过滤 |
| SendInput | Windows API，用于注入键盘/鼠标事件 |
| Viterbi | 动态规划算法，用于全局最优序列路径搜索 |

---

> 本说明书由 5 个平行子代理在 2026-07-09 对全项目源码、33 篇文档和测试体系进行完整探索后合成。
