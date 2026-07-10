# piano_svsep 集成方案

## 1. 目标

用开源深度学习模型 [piano_svsep](https://github.com/CPJKU/piano_svsep)（GNN 声部/谱表预测）替换当前自研的基于音高直方图 + 三重交叉验证的左右手分割算法，实现：

1. **真实声部分离** → MIDI 音符按 piano_svsep 预测的 staff 标签（1=低音/左手, 2=高音/右手）拆分为两个独立轨道
2. **分别简化** → 左右手各自独立做和声简化（交叉注意力评分），互不干扰
3. **合并压缩** → 简化后的两声部音符合并，做统一八度折叠（C3-B5）
4. **整体精排** → 复用现有关键参数扫描 + 全局质量分精排（reranker）

---

## 2. 新管线 vs 旧管线 对比

### 2.1 旧管线（attention_weighted）

```
MIDI → 量化分组 → 音高直方图双峰检测 + 运动模式验证 + velocity验证
   → 分割线 → 按音高划分左右手 → 左手交叉注意力简化 → 合并
   → 八度折叠 → 最终和声密度裁剪 → Token输出 → 精排(12候选)
```

**问题**：分割线基于启发式算法，依赖音高分布假设，复杂曲目（跨谱表声部、双手交叠）易出错。

### 2.2 新管线（svsep_attention_weighted）

```
MIDI → music21转MusicXML → piano_svsep (GNN预测staff标签)
   → 按staff拆分: LH(staff=1), RH(staff=2)
   → LH独立交叉注意力简化 (left_max_chord_notes)
   → RH独立交叉注意力简化 (right_max_chord_notes，默认=全部保留)
   → 合并 L+R → 八度折叠(C3-B5) → Token输出 → 精排(12候选)
```

**优势**：
- GNN 模型在 DCML 数据集训练，能捕捉音符间的图关系（连接边、小节内共现、跨小节依赖）
- 同时预测 voice 和 staff，处理跨谱表声部
- 不再需要启发式分割线、运动模式验证、velocity 验证
- 左右手独立简化，避免左手简化策略污染右手旋律

---

## 3. 技术方案

### 3.1 新增依赖

| 包 | 版本 | 用途 |
|----|------|------|
| `music21` | latest | MIDI → MusicXML 转换 |
| `partitura` | >=1.8.0 | MusicXML 乐谱加载（piano_svsep 依赖） |
| `torch` | >=2.0 | piano_svsep 推理（与现有 transkun 共用环境） |
| `torch_geometric` | latest | GNN 图计算 |
| `pytorch_lightning` | latest | 模型加载 |
| `scipy` | latest | 连通分量分析 |

**注意**：`piano_svsep` 本身通过 `pip install .` 安装，其中已声明 `partitura==1.8.0`, `torch`, `torch_geometric`, `pytorch_lightning`, `scipy`。music21 需额外安装。

### 3.2 新增模块: `src/svsep_hand_separator.py`

```
┌─────────────────────────────────────────────┐
│           SvsepHandSeparator                  │
├─────────────────────────────────────────────┤
│ __init__(model_path: Path)                   │
│   - 加载 piano_svsep 预训练模型 .ckpt       │
│   - 初始化 PLPianoSVSep                     │
│                                              │
│ separate(midi_path: Path, temp_dir: Path)    │
│   → tuple[list[MidiNoteEvent],              │
│           list[MidiNoteEvent]]               │
│   (left_hand_notes, right_hand_notes)       │
│                                              │
│ 内部步骤:                                    │
│   1. music21 MIDI → MusicXML (temp)          │
│   2. partitura 加载 MusicXML → pg_graph      │
│   3. 模型推理: predict_step()                │
│      → predicted_voices, predicted_staff     │
│   4. assign_voices() 标注 staff 字段         │
│   5. 按 staff 拆分为 L/R 两组 note 对象      │
│   6. 将 partitura Note 转回 MidiNoteEvent    │
│       (保留 pitch, velocity, start/end beat) │
│   7. 返回 (lh_notes, rh_notes)               │
└─────────────────────────────────────────────┘
```

### 3.3 修改文件: `src/audio_to_yaml_converter.py`

#### 3.3.1 AudioPipelineConfig 新增字段

```python
# ---- piano_svsep 声部分离参数 ----
svsep_enabled: bool = False              # 启用 piano_svsep 真实声部分离
svsep_model_path: Path | None = None     # piano_svsep 模型权重路径（.ckpt）
right_max_chord_notes: int = 6           # 右手轨最大音符数（简化裁剪保留上限）
```

#### 3.3.2 MidiToYamlConverter 修改点

**修改 `convert()` 方法（第 464 行）**：
```python
# 如果启用 svsep，在读取 MIDI notes 后、构建 score events 前注入声部分离
if config.svsep_enabled:
    lh_notes, rh_notes = self._separate_hands_svsep(midi_notes, config)
    # 将左右手标签附加到 MidiNoteEvent（扩展字段或并行字典）
    # 方案A: 扩展 MidiNoteEvent 添加 hand 字段
    # 方案B: 构建 hand_label: dict[int, str] 映射（pitch → hand）
```

**方案选择**：推荐**方案 B**——在分离后分别生成 `grouped_pitches_lh` 和 `grouped_pitches_rh`，传入新的压缩函数，不修改 MidiNoteEvent 数据结构。这样对现有管线侵入最小。

**新增 `_build_svsep_attention_weighted_grouped_notes()` 方法**：

```
伪代码:

def _build_svsep_attention_weighted_grouped_notes(
    self,
    grouped_pitches_lh: dict[int, list[int]],   # 左手音高
    grouped_pitches_rh: dict[int, list[int]],   # 右手音高
    midi_notes: tuple[MidiNoteEvent, ...],      # 用于 velocity/duration
    config: AudioPipelineConfig,
) -> dict[int, list[str]]:
    """
    SVSEP + 交叉注意力管线:
    阶段0: 左右手已由 piano_svsep 分离
    阶段1: 提取全局趋势线 (复用 _extract_global_trend)
    阶段2: 小节切分 (复用 _compute_bar_windows)
    阶段3: 每小节内:
        a. LH 交叉注意力简化 (select_chord_notes, max=left_max_chord_notes)
        b. RH 交叉注意力简化 (select_chord_notes, max=right_max_chord_notes)
        c. 合并 L+R → _adaptive_octave_fold_slice()
        d. 最终和弦密度裁剪 (select_chord_notes, max=max_chord_notes)
    阶段4: Token 输出 (去重, 排序)
    阶段5: 精排 (复用 reranker.select_best_candidate)
    """
```

**关键差异点**（与 `attention_weighted` 对比）：

| 维度 | attention_weighted | svsep_attention_weighted |
|------|-------------------|-------------------------|
| 左右手分割方式 | `_compute_bar_split_pitch()` 音高分割线 + 三重验证 | piano_svsep GNN 预测 staff 标签 |
| 右手是否简化 | 不简化（完整保留） | 可选简化（`right_max_chord_notes` 控制） |
| 左手简化 | `select_chord_notes`（交叉注意力） | 同左（复用） |
| 全局趋势线 | `_extract_global_trend` | 同左（复用，但基于合并后的全音符集） |
| 八度折叠 | `_adaptive_octave_fold_slice` | 同左（复用） |
| 精排 | `reranker` 12候选选择 | 同左（复用） |

**修改 `_build_score_events()` 调度**（第 568 行之后新增）：

```python
if config.pitch_compression_mode == "svsep_attention_weighted":
    # 分离左右手音高
    lh_pitches, rh_pitches = self._build_svsep_split_groups(
        grouped_pitches=grouped_pitches,
        midi_notes=midi_notes,
        config=config,
    )
    grouped_notes = self._build_svsep_attention_weighted_grouped_notes(
        grouped_pitches_lh=lh_pitches,
        grouped_pitches_rh=rh_pitches,
        midi_notes=midi_notes,
        config=config,
    )
```

### 3.4 命令行参数新增

```powershell
--pitch-compression-mode svsep_attention_weighted  # 新增模式
--svsep-model-path "path/to/piano_svsep/model.ckpt"  # 模型权重路径
--right-max-chord-notes 6                             # 右手最大和弦音数
```

### 3.5 数据流图

```
MIDI 文件 (.mid)
    │
    ├─► pretty_midi → MidiNoteEvent 列表
    │
    ├─► music21 → MusicXML (.musicxml, 临时文件)
    │         │
    │         ▼
    │   piano_svsep model.ckpt
    │         │
    │         ▼
    │   staff 预测: 每个音符 → staff=1(LH) 或 staff=2(RH)
    │         │
    │         ├─► LH notes: list[MidiNoteEvent]  → grouped_pitches_lh
    │         └─► RH notes: list[MidiNoteEvent]  → grouped_pitches_rh
    │
    ├─── _extract_global_trend(all_pitches)  → global_ref[t]
    ├─── _compute_bar_windows(all_timesteps) → bar_ranges
    │
    ▼
Per bar per timestep:
    ├─── select_chord_notes(LH pitches)  → simplified_lh
    ├─── select_chord_notes(RH pitches)  → simplified_rh
    ├─── merge → _adaptive_octave_fold_slice() → mapped_pitches
    ├─── select_chord_notes(mapped_pitches, max_chord_notes) → final
    └─── _pitch_to_token() → tokens

    ▼
reranker.select_best_candidate(12 candidates)
    │
    ▼
Token 去重 + 和弦文本化 → YAML score events
```

---

## 4. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| piano_svsep 依赖 torch_geometric，可能与现有 torch 版本冲突 | 安装失败 | 使用独立 conda/pip 环境或 `--no-deps` 手动安装 |
| music21 MIDI→MusicXML 丢失 velocity/duration 精度 | 左右手拆分后音符属性失真 | 拆分后通过原始 MidiNoteEvent 的 (pitch, start_beat) 匹配回填 velocity/duration |
| 模型仅训练于 DCML 数据集（古典/爵士），泛化到 AniSong/MIDI 转录可能有偏差 | 分离准确率下降 | 加 `--svsep-confidence-threshold` 参数，低于阈值回退到音高分割线方案 |
| partitura 加载大型 MusicXML 性能问题 | 转换耗时增加 | 缓存 MusicXML 到 work_dir/svsep/，避免重复转换 |
| 左右手独立简化 + 全合并后八度折叠可能导致碰撞 | token 去重时丢失音符 | 已有 `collisions_avoided` 统计和去重保护，且八度折叠本身处理音区重叠 |

---

## 5. 实施步骤

### 第一阶段：环境准备
1. 安装 piano_svsep 及其依赖
2. 下载预训练模型权重 `model.ckpt`
3. 安装 music21

### 第二阶段：核心模块开发
4. 创建 `src/svsep_hand_separator.py`
5. 实现 MIDI→MusicXML 转换 + piano_svsep 推理 + staff 拆分
6. 单元测试：验证分离结果与预期左手/右手分布

### 第三阶段：管线集成
7. 在 `AudioPipelineConfig` 添加 `svsep_enabled`、`svsep_model_path`、`right_max_chord_notes`
8. 在 `_build_score_events()` 添加 `svsep_attention_weighted` 调度分支
9. 实现 `_build_svsep_attention_weighted_grouped_notes()` 方法
10. 实现 `_build_svsep_split_groups()` 辅助方法

### 第四阶段：验证
11. 用已知曲目（残酷天使、或其他参考曲目）对比新旧管线输出 YAML
12. 人工审查左右手分离质量
13. 弹奏测试

---

## 6. 待决策问题

1. **右手是否简化？** 当前 `right_max_chord_notes` 默认 6（等于 max_chord_notes），即右手实际不裁剪。是否需要降低（如 4）以压缩密度？
2. **svsep 模式命名**：`svsep_attention_weighted` 较长，是否接受？
3. **是否需要 voice 信息？** piano_svsep 同时预测 voice 和 staff。当前只需 staff（区分左右手）。voice（同谱表内声部，如四部和声的 SATB）暂不利用。
4. **MIDI→MusicXML 转换准确性**：music21 的 MIDI 导入依赖 tempo 信息。当前 Transkun 输出的 MIDI tempo=120 时会被 BPM 自动检测覆盖。需验证 music21 转换后拍点一致性。

---

作者：JucieOvo
创建日期：2026-04-30
