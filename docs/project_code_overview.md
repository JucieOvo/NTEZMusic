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
| **异环 (NTEZ)** | 三行键盘 21 白键 + 升降半音 | 多音同时（最多 6 键） | `adaptive_octave_fold` / `attention_weighted` / `hands_decoupled` / `svsep_mpdr` |
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

#### 36 键缩编质量（基准评测，5 曲目平均，2026-07-11）

| 模式 | 综合保真分 | 排名 |
|------|----------:|:---:|
| `adaptive_octave_fold` | **0.5310** | #1 |
| `attention_weighted` | 0.5305 | #2 |
| `hands_decoupled` | 0.5279 | #3 |
| `svsep_mpdr` | 0.5273 | #4 |

> `score_aware_theory` 模式已在 v3 基准评测中淘汰，不再推荐使用。

---

## 二、项目全景架构

### 2.1 顶层架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            NTEZMusic 项目架构                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  音频文件 (.mp3/.flac/.wav)  或  现有 MIDI                                   │
│      │                                                                      │
│      ▼                                                                      │
│  ╔══════════════════════════════════════════════════════════════════╗        │
│  ║             音频主管线 (audio_to_yaml_converter.py)              ║        │
│  ║   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ║        │
│  ║   │ Demucs  │   │ Transkun │   │piano_svsep│   │  缩编+精排│   ║        │
│  ║   │ 分轨    │──▶│ 转录    │──▶│ GNN分离  │──▶│  算法选择 │   ║        │
│  ║   └──────────┘   └──────────┘   └──────────┘   └───────────┘   ║        │
│  ╚══════════════════════════════════════════════════════════════════╝        │
│      │                                                                      │
│      ▼                                                                      │
│  ╔══════════════════════════════════════════════════════════════════╗        │
│  ║       多算法基准评测 (arrangement_benchmark/)                     ║        │
│  ║   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ║        │
│  ║   │YAML配置 │   │ 多候选  │   │ MIDI    │   │ 六维保真 │   ║        │
│  ║   │ 加载    │──▶│ 执行    │──▶│ 反解    │──▶│ 指标评分 │   ║        │
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

### 2.2 项目演进说明

项目已精简为单一音频主管线 + 基准评测框架。此前存在的**多模态视频管线**（`pipeline_runner.py`、`media_separator.py`、`dual_stream_extractor.py`、`fusion_engine.py`、`run_partial_fusion.py` 及 MediaPipe 手部追踪）已从代码库移除，不再维护。

新增 `arrangement_benchmark/` 子模块，提供多歌曲、多算法的真实基准评测框架，支持对四种活跃压缩模式进行六维保真指标量化对比。


---

## 三、目录结构总览

```
F:\NTEZMusic/
│
├── src/                                    # 核心源代码（21 文件 + 1 子模块）
│   ├── audio_to_yaml_converter.py          # [核心] 全管线入口，~4129 行
│   ├── piano_auto_player.py                # [核心] 自动弹奏器，~861 行
│   ├── svsep_hand_separator.py             # GNN 声部分离封装
│   ├── cross_attention.py                  # 交叉注意力评分（手设权重）
│   ├── positional_encoding.py              # 正弦位置编码
│   ├── note_scorer.py                      # 音符综合评分
│   ├── reranker.py                         # 候选精排选择
│   ├── score_model.py                      # 乐谱语义 MVP 数据模型
│   ├── midi_event_canonicalizer.py         # 机翻 MIDI 清洗与起音聚类
│   ├── beat_grid_estimator.py              # 节拍与小节网格估计
│   ├── score_quantizer.py                  # MIDI 音符到乐谱网格量化
│   ├── staff_notation_builder.py           # music21 五线谱构建与 MusicXML 导出
│   ├── score_notation_validator.py         # MusicXML 回读与合规校验
│   ├── score_semantic_analyzer.py          # 主旋律、低音与和弦角色基础分析
│   ├── theory_aware_36key_arranger.py      # 乐理感知 36 键缩编与 token 输出
│   ├── score_aware_theory_pipeline.py      # 乐谱语义感知 MVP 独立入口（已淘汰模式）
│   ├── midi_to_upagain_yaml.py             # MIDI -> UpAgain YAML
│   ├── run_upagain_adaptation.py           # UpAgain 适配总控
│   ├── analyze_score.py                    # YAML 曲谱分析
│   └── inspect_midi_gap.py                 # MIDI 间隙排查
│   │
│   └── arrangement_benchmark/              # [新增] 多算法基准评测框架（9 文件）
│       ├── cli.py                          #   Windows 命令行入口
│       ├── config_loader.py                #   集中 YAML 配置加载
│       ├── models.py                       #   不可变实验数据模型
│       ├── runner.py                       #   多候选真实执行编排
│       ├── constraints.py                  #   异环硬约束校验（36 键/6 键上限）
│       ├── metrics.py                      #   六维原曲保真指标计算
│       ├── rendering.py                    #   缩编 MIDI -> WAV 真实渲染
│       ├── score_io.py                     #   36 键 YAML 反解与输入音序写入
│       └── __init__.py
│
├── config/                                 # YAML 曲谱文件
├── docs/                                   # 技术文档（54 篇，重组中）
│
├── piano_svsep/                            # GNN 声部分离子模块（ISMIR 2024）
│   ├── piano_svsep/
│   │   ├── models/                         #   GNN 模型定义 (SAGEConv)
│   │   ├── data/                           #   数据集/预处理
│   │   └── utils/                          #   图构建/声部分离工具
│   └── launch_scripts/                     #   训练/推理入口
│
├── UpAgain/                                # 《UpAgain》游戏适配
│   ├── upagain_auto_player.py              #   自动弹奏器（ScanCode 注入）
│   └── upagain_yaml_converter.py           #   C大调移调/网格量化
│
├── tests/                                  # 测试（22 文件）
│   ├── test_audio_to_yaml_svsep_mpdr.py
│   ├── test_svsep_hand_separator_audit.py
│   ├── test_svsep_hand_separator_inference.py
│   ├── test_score_audit_tool.py
│   ├── test_score_aware_theory_mvp.py
│   ├── test_score_aware_theory_arrangement.py
│   ├── test_raw_midi_generator.py
│   ├── test_diagnostic_evaluator.py
│   ├── test_demucs_windows_input_staging.py
│   ├── test_evaluate_transcription.py
│   ├── test_arrangement_benchmark_config.py
│   ├── test_arrangement_benchmark_runner.py
│   ├── test_arrangement_benchmark_constraints.py
│   ├── test_arrangement_benchmark_metrics.py
│   ├── test_arrangement_benchmark_rendering.py
│   ├── test_arrangement_benchmark_score_io.py
│   ├── test_arrangement_benchmark_artifacts.py
│   ├── test_arrangement_all_modes_config.py
│   ├── test_arrangement_all_modes_runner.py
│   ├── test_arrangement_all_modes_artifacts.py
│   └── conftest.py
│
├── work/                                   # 中间产物目录
│   └── transcription_benchmark/            # [新增] 转录质量基准测试
│       ├── raw_midi_generator.py           #    Transkun 原始 B MIDI 生成器
│       ├── diagnostic_evaluator.py         #    诊断性逐音评测器 v2
│       ├── bach_bwv846/                    #    巴赫 BWV 846 基准数据
│       └── chopin_op10_no12/               #    肖邦 Op.10 No.12 基准数据
│
├── rel/                                    # 旧版本归档
│
├── AGENTS.md                               # CLI 命令参考
├── README.md                               # 项目 README
└── requirements.txt                        # 核心依赖
```

### 3.1 已移除文件

以下文件因多模态视频管线废弃而移除：

| 文件 | 原用途 |
|------|------|
| `src/media_separator.py` | ffmpeg 音视频分离 |
| `src/dual_stream_extractor.py` | 双轨特征提取 |
| `src/fusion_engine.py` | 多模态融合引擎 |
| `src/pipeline_runner.py` | 多模态总装流水线 |
| `src/run_partial_fusion.py` | 局部融合执行 |
| `src/hand_landmarker.task` | MediaPipe 预训练模型 |


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

### 4.5 平台特定

| 组件 | 用途 | 说明 |
|------|------|------|
| **Windows SendInput API** | 键盘事件注入 | `ctypes.windll.user32.SendInput`，管理员权限 |
| **MapVirtualKeyW** | 虚拟键码 -> 扫描码 | 仅 UpAgain 模式使用 |

### 4.6 关键依赖缺失说明

`requirements.txt` 仅列出 4 个核心依赖，实际运行还需要额外安装：

```bash
# 核心
pip install librosa scipy soundfile mido
pip install music21 partitura
pip install torch torch_geometric pytorch_lightning

# piano_svsep（源码安装）
cd piano_svsep && pip install -e . --no-deps

# 转录评测工具（仅基准测试管线）
pip install mir_eval numpy scipy mido pretty_midi
```

### 4.7 已移除的依赖

多模态视频管线移除后，以下依赖不再需要：

- `mediapipe` -- MediaPipe 手部关键点检测
- `opencv-python` -- 视频解码
- `ffmpeg`（系统安装）-- 音视频分离


---

## 五、核心算法详解

### 5.1 算法演进路线与基准排名

基于 `arrangement_benchmark` 对 5 首曲目（残酷天使的行动纲领、One Last Kiss、ただ声一つ、巴赫 BWV 846、肖邦 Op.10 No.12）的综合六维保真评测（2026-07-11）：

| 排名 | 模式 | 综合保真分 | 状态 |
|:---:|------|----------:|------|
| #1 | `adaptive_octave_fold` | **0.5310** | **默认模式** |
| #2 | `attention_weighted` | 0.5305 | 推荐模式 |
| #3 | `hands_decoupled` | 0.5279 | 推荐模式 |
| #4 | `svsep_mpdr` | 0.5273 | 最高工程复杂度模式 |
| -- | `score_aware_theory` | -- | **已淘汰** |

```
v1 (三分区强制映射)                    【已废弃】
  └── 错误: 把三行键盘理解成功能分区

v2 (自适应八度折叠)                     【默认模式，#1 排名】
  └── 核心: 统一八度偏移 k + ref_pitch 指数平滑
  └── 基准: 综合分 0.5310，5 曲目平均最佳

attention_weighted                     【推荐模式，接近默认】
  └── 核心: 正弦位置编码 + 交叉注意力 + velocity 加权 + 精排
  └── 基准: 综合分 0.5305，次优但工程复杂度适中

hands_decoupled                        【推荐模式】
  └── 核心: 全局趋势线 + 休止符分割 + 三重交叉验证 + 左手和声功能优先级裁剪
  └── 基准: 综合分 0.5279

svsep_mpdr (v3)                        【最高工程复杂度模式】
  └── 核心: GNN 声部分离 + MPDR 感知密度预算 + 五维精排
  └── 基准: 综合分 0.5273，GNN 强依赖
```

> `score_aware_theory` 模式已在 v3 基准评测中淘汰。该模式试图基于乐谱语义进行缩编，但在实际评测中表现不如纯信号处理方法，且工程复杂度高，不再推荐使用。

### 5.2 adaptive_octave_fold 算法（默认模式，#1 排名）

**文档**：`docs/adaptive_octave_fold_algorithm.md`

**核心原理**：
- 和弦内统一八度偏移 k：对每个时间片内的和弦，搜索 [-6, +6] 范围内的八度偏移，统一将和弦整体平移
- ref_pitch 指数平滑：维护全局参考音高，使用平滑系数 alpha 指数加权移动平均，避免参考音高突变
- 选择标准：八度折叠后的和弦平均音高最接近当前 ref_pitch 的 k 值

**流程图**：

```
MIDI notes 按时间片分组
    │
    v
逐时间片处理：
    │-- ref_pitch 指数平滑更新（alpha = 0.2）
    │-- 遍历 k in [-6, +6]：
    │     │-- 计算和弦折叠后均值与 ref_pitch 的距离
    │     │-- 选择距离最小的 k
    │-- 全部和弦音符应用最优 k 偏移
    │
    v
超范围音高处理（octave_fold 策略）：
    │-- 折叠到 C3-B5 范围内
    │
    v
GameKeyboardTokenEmitter
    │-- token 生成与冲突消解
```

**优势**：
- 算法最简单，无外部模型依赖，运行速度最快
- 和弦整体移动，保持和弦内部音程关系不变
- ref_pitch 平滑使音域切换自然，无突兀跳变

**局限**：
- 不区分左右手角色，和弦内所有音符统一偏移
- 无主旋律保护机制

### 5.3 attention_weighted 算法（#2 排名）

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


### 5.4 hands_decoupled 算法（#3 排名）

**详细文档**：`docs/hands_decoupled_v2_final.md`

- **核心**：全局趋势线 + 休止符分割 + 三重交叉验证 + 左手和声功能优先级裁剪
- **与 attention_weighted 的关键差异**：使用全局趋势线作为左右手分割参考，而非滑动窗口分割线；增加了休止符触发的短语分割逻辑

### 5.5 svsep_mpdr 算法（#4 排名）

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
MpdrCandidateBuilder（参数扫描生成候选）
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

**与其他模式的差异**：

| 维度 | adaptive_octave_fold | attention_weighted | svsep_mpdr |
|------|---------------------|-------------------|------------|
| 声部分离 | 无（统一处理） | 启发式分割线 | GNN 预测 staff 标签 |
| 复杂度 | 最低 | 中 | 最高（piano_svsep 依赖） |
| 外部依赖 | 无 | 无 | torch + torch_geometric + piano_svsep |
| 排名 | #1 | #2 | #4 |


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


### 6.6 多算法基准评测框架 (`arrangement_benchmark/`)

新增于 2026-07-11 的独立评测子系统，用于对多种缩编算法进行标准化横向比较。

| 组件 | 职责 | 关键方法 |
|------|------|---------|
| `BenchmarkConfig` / `SongSpec` / `CandidateSpec` | 不可变实验配置数据模型 | `load_benchmark_config() -> BenchmarkConfig` |
| `BenchmarkRunner` | 多候选真实执行编排（构造固定参数、执行缩编、收集产物） | `run_all_candidates()` |
| `ConstraintChecker` | 异环硬约束校验（36 键音域、6 键同时上限） | `check_constraints() -> ConstraintReport` |
| `FidelityMetrics` | 六维原曲保真指标计算（旋律/节奏/低音/和声/声部/音频相似度） | `calculate_midi_fidelity() -> MidiFidelityResult` |
| `ScoreIO` | 36 键 YAML 反解与输入音序 MIDI 写入 | `parse_yaml_score()`, `write_input_midi()` |
| `Renderer` | 缩编 MIDI -> WAV 真实渲染（FluidSynth + SoundFont） | `render_midi_to_wav()` |

**评测流程**：

```
YAML 配置（歌曲 + 候选算法 + 固定参数）
    │
    v
  BenchmarkRunner
    │-- 遍历每个歌曲 + 候选组合
    │-- 构造独立 work-dir
    │-- 调用 audio_to_yaml_converter 执行缩编
    │-- 收集产物（YAML + 缩编 MIDI + 统计）
    │
    v
  ConstraintChecker（硬约束校验）
    │-- 音域检查
    │-- 6 键上限检查
    │
    v
  FidelityMetrics（六维保真评分）
    │-- 旋律保真（DP 主旋律路径相关系数）
    │-- 节奏保真（起音时间序列相关系数）
    │-- 低音保真（最低音序列相关系数）
    │-- 和声保真（色度向量相似度）
    │-- 声部分离保真（左右手分割准确性）
    │-- 音频频谱相似度（Mel 频谱余弦相似度）
    │
    v
  综合排名与 HTML 报告
```

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
2. 网格取最高音：0.125 拍网格内保留最高音
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
- 唯一差异是 tempo 元事件（B: 500000 mus/beat -> C: ~510000-534000 mus/beat）
- B 的真实 onset+pitch F1 为 0.9950（巴赫）和 0.9728（肖邦）
- C 未校准时 F1 仅为 0.0475/0.0534 -- 不代表转录质量
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
    |-- [adaptive_octave_fold 模式](默认):
    |     |-- ref_pitch 指数平滑
    |     |-- 逐时间片和弦统一八度折叠（k in [-6, +6] 搜索）
    |     |-- token 冲突消解
    |
    |-- [attention_weighted 模式]:
    |     |-- _build_attention_weighted_grouped_notes():
    |           |-- 全局趋势线
    |           |-- 滑动窗口分割线 + 三重验证
    |           |-- 自适应八度折叠
    |           |-- 交叉注意力评分选择
    |           |-- 精排管线 (12候选 -> 最优)
    |
    |-- [hands_decoupled 模式]:
    |     |-- 全局趋势线 + 休止符分割
    |     |-- 三重交叉验证 + 左手和声功能优先级裁剪
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
    v
YAML 曲谱 -> piano_auto_player.py 加载 -> SendInput API -> 游戏内弹奏
```

### 7.2 基准评测数据流

```
arrangement_benchmark 配置 YAML（歌曲 + 候选算法 + 固定参数）
    |
    v
BenchmarkRunner
    |-- 逐歌曲 + 候选构造 work-dir
    |-- 调用 audio_to_yaml_converter.py 执行缩编
    |-- 收集产物：YAML + 缩编 MIDI + conversion_stats
    |
    v
ConstraintChecker
    |-- 36 键音域校验
    |-- 6 键同时上限校验
    |
    v
FidelityMetrics
    |-- 输入 MIDI -> 旋律/节奏/低音/和声/声部分离特征
    |-- 缩编 MIDI -> 同维度特征
    |-- 六维相关系数 -> 综合保真分
    |
    v
HTML 报告（排名表 + 单曲细项 + 算法对比）
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
    |-- _load_audio_samples() -> 读取 Demucs piano stem
    |-- _load_transkun_model() -> 加载 Transkun 2.0.1
    |-- model.transcribe() -> notes_est (pitch/start/end/velocity)
    |-- writeMidi() -> transkun_raw_120bpm.mid (B, 不可变)
    |-- _build_run_manifest() -> run_manifest.json (输入/模型/输出哈希)
    |
    v
diagnostic_evaluator.py
    |-- extract_notes_from_midi(A) -> ref_intervals, ref_pitches
    |-- extract_notes_from_midi(B/C) -> est_intervals, est_pitches
    |
    |-- raw_metrics: mir_eval 标准比对 (onset/pitch/offset/velocity/frame)
    |-- diagnostic_metrics:
    |     |-- global_offset (网格搜索最优恒定偏移)
    |     |-- global_scale (网格搜索最优缩放因子 alpha, 0.8-1.2)
    |     |-- scale_then_offset (alpha 缩放后搜索残余 offset)
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
- 巴赫 ~ 117.5/120 = 0.979
- 肖邦 ~ 112.3/120 = 0.936

---

## 八、文档体系索引

> 注意：`docs/` 目录当前包含 54 篇文档，正在进行重组。以下为当前分类概览。

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
| `attention_weighted_algorithm.md` | attention_weighted 模式完整算法说明 |
| `hands_decoupled_v2_final.md` | hands_decoupled v2 信号增强版算法 |
| `hands_decoupled_algorithm_report.md` | hands_decoupled 算法综合报告 |
| `adaptive_octave_fold_algorithm.md` | 自适应八度折叠 v2 算法说明（现为默认模式） |
| `attention_weighted_rerank_algorithm.md` | 精排管线方案 |

### 第 3 类：工具使用

| 文档 | 摘要 |
|------|------|
| `audio_to_yaml_converter使用说明.md` | 音频转换工具完整使用文档 |
| `usage_guide.md` | 面向用户的完整使用指南 |
| `audio_to_yaml_pipeline方案.md` | Demucs + Transkun 方案设计 |
| `audio_pipeline_svsep_mpdr_developer_guide.md` | SVSEP-MPDR 开发者指南 |

### 第 4 类：YAML 曲谱与弹奏

| 文档 | 摘要 |
|------|------|
| `yaml_score_guide.md` | YAML 曲谱手写规范 |
| `piano_auto_play方案.md` | 自动弹奏器设计方案 |
| `piano_auto_player_and_test_system.md` | 弹奏器与测试系统说明 |

### 第 5 类：声部分离

| 文档 | 摘要 |
|------|------|
| `svsep_integration_proposal.md` | piano_svsep GNN 集成方案，新旧管线对比 |

### 第 6 类：主旋律提取

| 文档 | 摘要 |
|------|------|
| 5 篇优化方案 + 4 篇实践审计报告 | 涵盖 Viterbi DP、时域裁切、阈值优化等 |

### 第 7 类：算法设计与优化

| 文档 | 摘要 |
|------|------|
| `phrase_adaptive_reduction方案.md` | 短语自适应缩编方案 |
| `pitch_compression_rearrange方案.md` | 音高压缩重组方案 |
| `proposal_role_decoupled_dlaf.md` | 角色解耦 DLAF 提案 |
| `score_aware_theory_arrangement_plan.md` | 乐谱语义感知缩编技术方案（对应已淘汰模式） |
| `score_optimization_discussion.md` | 曲谱优化讨论 |
| `sustain_split_design.md` | 左手延音选择性续打设计 |
| `transformer_cross_attention_feasibility.md` | Transformer 交叉注意力可行性研究 |
| `transcribe_migration_transkun方案.md` | 转录引擎迁移方案 |
| `yaml_score_compression方案.md` | YAML 曲谱压缩方案 |

### 第 8 类：UpAgain 适配

| 文档 | 摘要 |
|------|------|
| UpAgain 系列 8 篇 | UpAgain 适配方案、弹奏器、换调、频率量化等 |

### 第 9 类：转录质量评测（新增 2026-07-10）

| 文档 | 摘要 |
|------|------|
| `transkun_raw_midi_evaluation_plan.md` | Transkun 原始 MIDI 真实评测方案：产物定义、评测矩阵、执行策略 |
| `transkun_raw_midi_evaluation_report.md` | **必读**。评测结论：B 原始 MIDI F1=0.97-0.99，`_fix_midi_tempo()` 是评测失败的根因 |
| `mp3_to_midi_diagnosis_brief_for_expert_model.md` | 诊断 Brief v1（历史版，部分结论已被 v2 推翻） |
| `mp3_to_midi_diagnosis_brief_for_expert_model_v2.md` | **必读**。诊断 Brief v2：修正版，确认 tempo 覆盖为根因，重定优化方向 |
| `mp3_to_midi_benchmark_download_plan.md` | MAESTRO 基准数据下载计划 |
| `mp3_to_midi_full_acceptance_test_plan.md` | 全验收测试计划 |

### 第 10 类：项目导览与管理

| 文档 | 摘要 |
|------|------|
| `project_code_overview.md` | 本文件，项目代码总览说明书 |
| `近期代码变更与研究方向说明.md` | **必读**。阶段性成果汇总与分析，面向技术总监 |
| `rel_workspace_sync_plan.md` | 工作区同步计划 |
| `MusicTheory/` | 乐理模块（AUDIT.md + PLAN.md） |
| `superpowers/` | 详细设计规格与执行计划 |
| `score-audit-svsep-mpdr-v3/` | SVSEP-MPDR v3 审计报告 |

### 第 11 类：多模态融合（已归档）

原 3 篇方案文档（手部错排修复、实时追踪改进、视觉标注优化）对应的多模态视频管线代码已移除，文档保留供参考。

### 第 12 类：视觉标注（已归档）

原 1 篇左右手标注优化方案对应的代码已移除。


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
| 压缩模式 | `--pitch-compression-mode`（4 种活跃模式，默认 `adaptive_octave_fold`） |
| 效果控制 | `--max-chord-notes`, `--allow-accidentals` |
| 左右手 | `--left-max-chord-notes`, `--phrase-gap-beats` |
| 转录 | `--transcription-checkpoint`, `--transcription-device` |
| svsep_mpdr | `--svsep-model-path`, `--mpdr-*-*`（共 30+ 参数） |
| 延音处理 | `--sustain-split-*` |

### 9.3 测试体系

| 测试文件 | 覆盖内容 |
|----------|---------|
| `test_audio_to_yaml_svsep_mpdr.py` | svsep_mpdr 管线端到端测试 |
| `test_svsep_hand_separator_audit.py` | GNN 分离审计验证 |
| `test_svsep_hand_separator_inference.py` | GNN 分离推理验证 |
| `test_score_audit_tool.py` | 曲谱审计工具测试 |
| `test_score_aware_theory_mvp.py` | score_aware_theory MVP 阶段测试 |
| `test_score_aware_theory_arrangement.py` | score_aware_theory 编排 + 集成测试 |
| `test_raw_midi_generator.py` | Transkun 原始 MIDI 生成器真实验收测试 |
| `test_diagnostic_evaluator.py` | 诊断评测器 v2 回归测试（含 scale 恢复验证） |
| `test_demucs_windows_input_staging.py` | Demucs Windows 输入暂存测试 |
| `test_evaluate_transcription.py` | 转录评测集成测试 |
| `test_arrangement_benchmark_config.py` | 基准框架配置加载测试 |
| `test_arrangement_benchmark_runner.py` | 基准框架执行测试 |
| `test_arrangement_benchmark_constraints.py` | 硬约束校验测试 |
| `test_arrangement_benchmark_metrics.py` | 保真指标计算测试 |
| `test_arrangement_benchmark_rendering.py` | MIDI 渲染测试 |
| `test_arrangement_benchmark_score_io.py` | YAML/MIDI 反解测试 |
| `test_arrangement_benchmark_artifacts.py` | 基准产物完整性测试 |
| `test_arrangement_all_modes_config.py` | 全模式集中配置测试 |
| `test_arrangement_all_modes_runner.py` | 全模式执行测试 |
| `test_arrangement_all_modes_artifacts.py` | 全模式产物审计测试 |
| `conftest.py` | pytest 共享配置 |

**测试覆盖遗漏**（建议补充）：
- `piano_auto_player.py`（自动弹奏器）零测试覆盖
- `cross_attention.py`、`note_scorer.py`、`reranker.py` 单元测试缺失
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

#### C. arrangement_benchmark

| 问题 | 严重性 | 说明 |
|------|--------|------|
| 评测曲目数量有限 | **中** | 当前仅 5 首曲目，统计显著性有限 |
| SoundFont 渲染依赖外部工具 | **中** | FluidSynth 渲染需要系统安装 |
| 六维指标权重固定 | **低** | 综合评分按等权计算，缺少可调权重 |

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
| 文档重组进行中 | **中** | 54 篇文档需要重新分类归档，多模态相关文档需标记为已归档 |

### 10.2 建议的迭代优先级

| 优先级 | 改进项 | 影响范围 |
|--------|--------|---------|
| P0 | 完善环境搭建文档（一键 install 脚本） | 新开发者 onboarding |
| P0 | 为自动弹奏器添加单元测试 + 干运行模式 | 弹奏安全性 |
| P0 | 补全 piano_svsep 模型权重文档 | GNN 分离管线可用性 |
| P1 | 修复 svsep_mpdr 左手选择逻辑（实现 harmony / voice_leading 维度） | 压缩质量 |
| P1 | 添加 API 参考文档（MidiToYamlConverter 类） | 后续开发效率 |
| P1 | 完成 docs/ 目录重组（分类归档、标记废弃文档） | 文档可维护性 |
| P2 | 修复统计字段混合赋值 bug | 数据准确性 |
| P2 | 扩展 benchmark 曲目库（增加更多曲风） | 评测代表性 |
| P3 | Viterbi 权重调优 | UpAgain 主旋律精度 |
| P3 | 参数扫描扩展（增加更多扫描维度） | 精排效果 |


---

### 附录 A：文件行数分布

| 文件 | 行数 | 类型 |
|------|------|------|
| `src/audio_to_yaml_converter.py` | 4128 | 核心管线 |
| `src/arrangement_benchmark/runner.py` | 887 | 基准执行器 |
| `src/piano_auto_player.py` | 861 | 自动弹奏 |
| `src/svsep_hand_separator.py` | 478 | GNN 分离 |
| `src/arrangement_benchmark/metrics.py` | 360 | 保真指标 |
| `src/reranker.py` | 363 | 精排 |
| `src/note_scorer.py` | 295 | 评分 |
| `src/midi_to_upagain_yaml.py` | 295 | UpAgain |
| `src/cross_attention.py` | 271 | 注意力 |
| `UpAgain/upagain_auto_player.py` | 225 | UpAgain |
| `src/arrangement_benchmark/models.py` | 189 | 数据模型 |
| `src/positional_encoding.py` | 84 | 位置编码 |

> 已移除文件（`fusion_engine.py` 273 行、`media_separator.py`、`dual_stream_extractor.py`、`pipeline_runner.py`、`run_partial_fusion.py`）不再列入统计。

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
| Viterbi | 动态规划算法，用于全局最优序列路径搜索 |
| benchmark | 多歌曲、多算法的标准化横向评测框架 |

---

> 本说明书于 2026-07-11 更新，针对当前代码库（src/ 30 文件、docs/ 54 文档、tests/ 22 文件）进行了全面时效性核查，移除已废弃的多模态视频管线引用，更新算法排名与基准评测结果，新增 arrangement_benchmark 子系统说明。
