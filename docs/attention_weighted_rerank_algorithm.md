# 算法加权交叉注意力精排方案

> 作者：JucieOvo
> 日期：2026-04-28
> 状态：待审批

---

## 一、方案定位

**纯算法实现**，不引入 PyTorch/TensorFlow/任何神经网络。用**手设权重函数**替代学习参数，用**正弦位置编码**编码时间关系，用**加权投票**替代 softmax 注意力，实现与 Transformer 交叉注意力等价的计算图——区别仅在于权重是设计的而非学习的。

**一句话**：将 attention 的数学骨架保留（Q·K^T → softmax → 加权求和），但 Q、K、V 的投影矩阵替换为手工设计的相似度函数。

---

## 二、Token 化方案

### 2.1 词汇表

| Token ID | 含义 |
|----------|------|
| 0 | 休止符（rest），当前时间片无音符 |
| 1-88 | MIDI 音高 21-108（A0-C8），token_id = pitch - 20 |

### 2.2 序列结构

将 MIDI 按时间片量化为 token 序列：

```
S = [(t₁, T₁), (t₂, T₂), ..., (tₙ, Tₙ)]
```

- `tᵢ`：量化时间片索引（`round(start_beat / quantize_beat)`）
- `Tᵢ`：该时间片的 token 集合（如 `{34, 46, 53}` 表示 MIDI 54, 66, 73 三个音）
- 空时间片：`Tᵢ = {0}`

---

## 三、位置编码（纯算法版）

### 3.1 目的

使时间上相近的时间片拥有相近的编码向量，时间上远离的则编码差异大。后续交叉注意力通过位置编码向量的内积来实现"时间邻近加权"。

### 3.2 公式

```
PE(beat, 2i)   = sin(beat / τ^(2i/D))
PE(beat, 2i+1) = cos(beat / τ^(2i/D))
```

其中：
- `beat`：该时间片的绝对拍点值 = `t × quantize_beat`
- `D`：编码维度（建议 32）
- `τ`：波长基数（建议 10000，与原始 Transformer 一致）
- `i = 0, 1, ..., D/2 - 1`

### 3.3 时间邻近权重

两个时间片 `t_a` 和 `t_b` 的位置邻近度直接用编码向量内积：

```
temporal_proximity(t_a, t_b) = PE(beat_a) · PE(beat_b) / D
```

**性质**：
- `beat_a = beat_b` 时内积约等于 1（同位置的编码完全相同）
- `beat_a - beat_b` 越大，内积趋近于 0
- 不需要调参，波形频率自动决定"注意力窗口"宽度

### 3.4 与高斯窗的对比

| 方式 | 公式 | 窗口宽度 | 参数 |
|------|------|:---:|:---:|
| 高斯窗 | `exp(-Δt² / 2σ²)` | 由 σ 控制 | 需手动设 σ |
| 正弦内积 | `PE(t_a) · PE(t_b) / D` | 多尺度自调节（低频头看远，高频头看近） | 无 |

正弦内积的优势是自动获得**多尺度时间视野**——低频维度对长时间依赖敏感（类似看"乐段结构"），高频维度对短时间变化敏感（类似看"邻音关系"）。这正是 Transformer 多头注意力的核心能力。

---

## 四、全局交叉注意力（Global Cross-Attention）

### 4.1 定义

对候选压缩结果中的每个音符 `p_candidate`（位于时间片 `t`），计算它在原始 MIDI 中的"受支持度"：

```
GlobalAttention(p, t) = Σ_{t'} Σ_{q ∈ orig_notes[t']}
    temporal_proximity(t, t')           # 位置编码内积
    × pitch_similarity(p, q)            # 音高相似度
    × density_weight(t')                # 原曲密度权重
    × duration_weight(q)                # 音符时长权重
```

### 4.2 逐项说明

**音高相似度 `pitch_similarity(p, q)`**：

```
pitch_similarity(p, q) = max(0, 1 - |p - q| / octave_span)
```

其中 `octave_span = 12`（一个八度）。同一八度内的音符得高分，超过一个八度则为 0。

**变体**：区分 pitch class 匹配和八度匹配

```
pitch_similarity(p, q) = 0.7 × same_pitch_class(p, q) + 0.3 × octave_proximity(p, q)

其中：
  same_pitch_class(p, q) = 1 if (p mod 12) == (q mod 12) else 0
  octave_proximity(p, q) = max(0, 1 - |octave(p) - octave(q)| / 4.0)
```

**直觉**：原始 MIDI 中如果存在一个音高相近且时间相邻的音符，那么压缩后保留 p 是有依据的。

**原曲密度权重 `density_weight(t')`**：

```
density_weight(t') = min(|orig_notes[t']| / max_density, 1.0)
```

其中 `max_density = 8`。原曲密集段落中的音符携带更多"和声信息"，被保留的优先级应更高。

**音符时长权重 `duration_weight(q)`**：

```
duration_weight(q) = min(duration_beats(q) / 2.0, 1.0)
```

长音符（≥2 拍）权重为 1.0，短装饰音权重递减。长音符承载和声骨架，丢弃影响更大。

### 4.3 复杂度优化

全域双重求和是 O(T² × N²)，对 2000 时间片 × 平均 6 音符不可行。

**优化**：利用 `temporal_proximity` 的自然衰减，只对 `|t - t'| ≤ window` 的时间片求和。`window` 取位置编码内积降至 0.05 的索引差（约 50-80 个时间片，即 3-5 拍）。

---

## 五、段内交叉注意力（Segment Cross-Attention）

### 5.1 段分割

复用现有 `_segment_by_gaps`：连续空时间片 ≥ `phrase_gap_beats / quantize_beat` 触发段边界。

### 5.2 定义

与全局交叉注意力相同的公式结构，但：

1. **求和范围限制**：`t'` 仅在当前段内
2. **额外注入和声上下文**：

```
SegmentAttention(p, t, seg) = Σ_{t' ∈ seg} Σ_{q ∈ orig_notes[t']}
    temporal_proximity(t, t')
    × pitch_similarity(p, q)
    × harmonic_context_weight(p, orig_notes[t'])   # 新增：和声功能上下文
    × duration_weight(q)
```

**和声上下文权重 `harmonic_context_weight(p, chord_notes)`**：

```
若 p 在 chord_notes 中与最低音的 pitch class 距离为:
  - 0 半音（同音/八度）     → 0.2
  - 3-4 半音（三度，和弦品质） → 0.9
  - 7 半音（五度，和声支撑）   → 0.5
  - 10-11 半音（七度，色彩）   → 0.6
  - 其他                     → 0.3
```

这与现有固定优先级查表不同——它是在**原始 MIDI 的和声上下文中**衡量 p 的功能重要性，而非孤立判断。

**全局 vs 段内的分工**：
- 全局层：回答"这个音符在全曲中有多重要？"（长程结构感知）
- 段内层：回答"这个音符在本乐句的和声中有多重要？"（局部功能感知）

---

## 六、综合评分与音符选择

### 6.1 单音符综合得分

对候选时间片 `t` 的每个音符 `p`：

```
Score(p) = α · ChordFunction(p, t)           # 现有和声功能优先级（归一化）
         + β · GlobalAttention(p, t)           # 全局交叉注意力（归一化）
         + γ · SegmentAttention(p, t, seg)     # 段内交叉注意力（归一化）
         + δ · VoiceLeading(p, prev, next)     # 声部进行平滑度
```

### 6.2 逐项说明

**ChordFunction（现有逻辑，归一化）**：

```
现有优先级 → 映射到 [0, 1]：
  bass(0.0) → 1.0, 三度(10.0) → 0.9, 七度(15.0) → 0.85,
  纯四/大六(40.0) → 0.5, 小二/大二(60.0) → 0.3,
  纯五度(80.0) → 0.2, 八度重复(100.0) → 0.0
```

**VoiceLeading**：

```
VoiceLeading(p, prev, next) = 0.5 × smoothness(p, prev_notes)
                            + 0.5 × smoothness(p, next_notes)

其中 smoothness(p, neighbor_notes) = min_{q ∈ neighbor_notes} (1 - |p - q| / 12.0)
```

鼓励选择和前后时间片已有音符形成级进（≤2 半音）关系的音符，提升声部进行的平滑感。

### 6.3 归一化

各项在 [0, 1] 区间。权重默认值：

| 权重 | 默认值 | 说明 |
|------|:---:|------|
| α | 0.30 | 和声功能（乐理先验） |
| β | 0.25 | 全局注意力（长程一致性） |
| γ | 0.25 | 段内注意力（局部和声功能） |
| δ | 0.20 | 声部进行（听觉流畅度） |

### 6.4 音符选择

对每个时间片，计算所有候选音符的 Score，从高到低排序，取前 `max_chord_notes` 个。若出现同 token（映射后同一键位），仅保留得分最高的。

---

## 七、精排管线（Re-ranking Pipeline）

### 7.1 候选生成

用现有 `hands_decoupled` 算法，扫描以下参数变体生成 K 个候选：

| 参数 | 扫描范围 | 步长 | 候选数 |
|------|---------|:---:|:---:|
| `left_max_chord_notes` | [2, 3, 4] | 1 | 3 |
| `ref_smoothing` | [0.1, 0.2, 0.3] | 0.1 | 3 |
| `global_trend_alpha` | [0.03, 0.05, 0.08] | - | 3 |
| `phrase_gap_beats` | [0.25, 0.5, 1.0] | - | 3 |

全部组合 = 3 × 3 × 3 × 3 = 81 个候选。可裁剪到最合理的 12-16 个。

### 7.2 候选评分

对每个候选的整首曲谱计算全局质量分：

```
GlobalScore(candidate) = w₁ · MelodicPreservation   # 旋律保持度
                       + w₂ · HarmonicCompleteness  # 和声完整度
                       + w₃ · DensityBalance        # 密度平衡度
                       + w₄ · VoiceLeadingQuality   # 声部进行质量
```

**MelodicPreservation**：候选与原始 MIDI 最高音轨迹的 DTW 归一化距离（越小越好，取倒数映射到 [0,1]）

**HarmonicCompleteness**：每个时间片中，候选保留的音符数占原始音符数的比例（加权，低音音符权重更高）

**DensityBalance**：全曲各段时间片音符密度的方差倒数（越均匀越好）

**VoiceLeadingQuality**：相邻时间片最近声部跳变的平均半音数（越小越好，取倒数映射）

### 7.3 精排输出

对每个候选：
1. 计算 `GlobalScore`
2. 对候选内每个时间片用综合评分做局部调优（增删个别音符）
3. 重新计算 `GlobalScore`
4. 选择得分最高的候选

---

## 八、与现有算法的对比

| 维度 | `hands_decoupled` (当前) | 本方案 |
|------|------------------------|--------|
| 和弦简化策略 | 和声功能固定优先级 + 一刀切密度裁剪 | 综合评分（功能 + 全局注意力 + 段内注意力 + 声部进行） |
| 时间上下文 | 段内独立（段间信息隔离） | 位置编码多尺度时间视野，全局 + 段内双层参考 |
| 音符丢弃依据 | 只考虑音程优先级 | 同时考虑原始 MIDI 中的邻近支持度 + 和声功能 + 声部平滑度 |
| 关键调参 | 7 个配置参数 | 4 个权重系数（α,β,γ,δ） + 候选扫描 |
| 确定性 | 完全确定 | 完全确定（无随机性） |
| 依赖增量 | 无 | 无（纯 Python 数学运算） |
| 代码行数 | ~600 行 | 预计新增 ~250 行 |

---

## 九、实施计划

### Phase 1：位置编码模块
- `src/positional_encoding.py`：`compute_positional_encoding(beat, dim=32)` + `temporal_proximity(t1, t2)`
- ~40 行

### Phase 2：交叉注意力模块
- `src/cross_attention.py`：`compute_global_attention()` + `compute_segment_attention()`
- ~80 行

### Phase 3：综合评分器
- `src/note_scorer.py`：`score_note(p, t, context)` + `select_chord_notes()`
- ~60 行

### Phase 4：精排管线
- `src/reranker.py`：`generate_candidates()` + `score_candidate()` + `select_best()`
- ~70 行

### Phase 5：集成
- 在 `audio_to_yaml_converter.py` 中新增 `pitch_compression_mode="attention_weighted"`
- ~30 行改动

---

## 十、可行性总结

| 评估维度 | 结论 |
|----------|------|
| 技术可行性 | **高** — 纯 Python 数学运算，零外部依赖 |
| 计算开销 | O(T × W × N²)，W=窗口大小≈60，T=2000，N≈6 → 约 4M 次浮点运算，< 1 秒 |
| 参数可解释性 | **高** — 4 个权重系数 + 候选扫描参数，均有乐理含义 |
| 与现有代码兼容 | **高** — 复用 `_segment_by_gaps`、`_pitch_to_token`、量化分组 |
| 预期改善方向 | 和弦简化更平滑（不再一刀切）、声部进行更流畅（VoiceLeading 项）、跨段一致性更好（全局注意力） |

> 作者：JucieOvo
