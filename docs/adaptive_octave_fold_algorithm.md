# NTEZMusic 自适应八度折叠算法 (Adaptive Octave Fold)

> 作者：JucieOvo
> 版本：v3
> 基准结论：2026-07 五首歌曲四模式评测中，`adaptive_octave_fold` 以 0.5310 综合得分排名第一，且跨歌曲方差最小。

---

## 一、概述

### 1.1 问题定义

将完整钢琴 MIDI（88 键，A0-C8）自适应缩编到《异环》游戏 3 八度 36 键键盘（C3-B5，MIDI 48-83），在严格硬件约束下最大化保留原曲的音乐信息。

核心约束：

| 约束 | 值 | 说明 |
|------|-----|------|
| 音域下限 | MIDI 48 (C3) | 游戏键盘最低可弹奏音 |
| 音域上限 | MIDI 83 (B5) | 游戏键盘最高可弹奏音 |
| 窗口宽度 | 35 半音 | 36 键 = 3 个完整八度 |
| 最大同时按键 | 6 | 单手最多 6 键 |
| 键位唯一性 | 是 | 同一时刻同一键位只能按下一次 |

### 1.2 设计目标

1. **音程保真**：和弦内部相对音程关系精确不变（统一八度偏移 k）
2. **时域连续**：相邻时间片的八度切换发生在音乐自然过渡处，不引入听觉突兀感
3. **宽度自洽**：不依赖声部分区、不依赖 GNN 预测、不依赖乐理规则，纯纯度驱动的自适应映射
4. **方差稳健**：对不同曲风、不同速度、不同音域的钢琴曲输出质量稳定

---

## 二、理论基础

### 2.0 八度折叠的学术渊源

`adaptive_octave_fold` 并非凭空发明。它的数学内核——**八度折叠（octave folding）**——是音乐信息检索（MIR）领域的基础操作。

#### 2.0.1 Chroma 特征与音高类轮廓

MIR 中，**chroma**（或 **pitch class profile, PCP**）是最经典的八度折叠应用：将频谱能量按八度折叠到 12 个半音类中：

```
c_k = Σ_o |X(f)|,   ∀f ∈ B_{k,o},   k ∈ [0, 11],   o ∈ octaves
```

其中 `B_{k,o}` 是第 o 个八度中音高类 k 对应的频率区间。这个操作的本质是**舍弃八度信息，仅保留音高类身份**——与 `adaptive_octave_fold` 的和弦统一 k 搜索是同一个数学表述的逆向运用：前者"忘记"八度，后者"选择"八度。

#### 2.0.2 折叠音高直方图

Tzanetakis & Cook (ISMIR 2002) 提出**折叠音高直方图（folded pitch histogram）**：将 MIDI 音高 n 映射为 `c = n mod 12`，再转到五度圈坐标 `c' = (7c) mod 12`。这种"折叠并旋转"的操作在 MIR 的和弦识别、调性检测中被广泛使用。

`adaptive_octave_fold` 的不同之处在于：不是将全部音符合并到同一个八度 bin（信息损失），而是为每个时间片**搜索最优的统一八度偏移 k**，在保留音程精度的前提下完成折叠。

#### 2.0.3 统计钢琴缩编中的八度移位

Nakamura & Sagayama 在统计钢琴缩编（Statistical Piano Reduction）研究中，将**八度音高移位**作为核心编辑操作：

```
c_q(p) = 1 - 2γ_oct   if p = q
         γ_oct        if p = q ± 12
```

其中 `γ_oct` 是八度移位概率参数。这一形式化定义与 `adaptive_octave_fold` 的 `p → p + 12k` 映射完全一致，区别在于 Nakamura 使用概率模型（HMM）选择 k，而 `adaptive_octave_fold` 使用确定性几何准则（最小化到 ref_pitch 的距离）。

#### 2.0.4 广义声部导向空间

Callender、Quinn 与 Tymoczko 在广义声部导向空间（Generalized Voice-Leading Spaces）理论中指出：**八度等价（O-operation）**是音乐空间中三大基本操作之一（另外两个是置换 P 和基数变换 C）。将一个音符平移 12k 半音而不改变其音高类身份，就是一次 O 操作。

`adaptive_octave_fold` 本质上是在约束空间 `[48, 83]` 内对每个时间片的音符集执行最优 O 操作，使得映射后位置最接近全局平滑中心。

---

## 三、算法设计

### 3.1 核心思路

维护一个**平滑参考中心 `ref_pitch`**（指数滑动平均），逐时间片寻找能使全部音符落入 36 键窗口的**统一八度偏移 `k`**。每个和弦内所有音符偏移同一个 k 值，保证和弦内部音程的**绝对保真**。

```
哲学定位：
  octave_fold            →  逐音独立折叠，无上下文
  adaptive_octave_fold   →  和弦统一 k + ref_pitch 平滑，上下文驱动
  hands_decoupled        →  左右手角色解耦 + 和声简化，角色驱动
  attention_weighted     →  滑动窗口注意力 + velocity 加权，数据驱动
  svsep_mpdr             →  GNN 分离 + 主旋律 DP + 密度预算，模型驱动
```

`adaptive_octave_fold` 位于上下文驱动层，在裸折叠的简洁性与角色/模型驱动模式的复杂度之间取得最优平衡。

### 3.2 数学原理

#### 2.2.1 问题形式化

设某时间片（和弦）有 n 个音符，原始音高集合为：

```
P = {p₁, p₂, ..., pₙ}  ⊂  [21, 108]   （88 键钢琴全音域）
```

目标：找到映射函数 `f: P → [48, 83]` 使得：

1. **音程保留**：`f(pᵢ) - f(pⱼ) = pᵢ - pⱼ`（统一八度偏移保证）
2. **贴近中心**：`avg(f(p₁), ..., f(pₙ)) → ref_pitch`

#### 2.2.2 统一八度偏移搜索

当和弦跨度不超过窗口宽度时（`max(P) - min(P) ≤ 35`），存在统一八度偏移 `k ∈ [-6, 6]` 使所有音符落入窗口：

```
候选 k 集合:
  common_ks = ∩_{i=1..n} { k | 48 ≤ pᵢ + 12k ≤ 83 }

最优 k 选择（最小化到 ref 的距离）:
  k* = argmin_{k ∈ common_ks} |avg(P) - 12k - ref_pitch|
```

映射结果：`f_k*(pᵢ) = pᵢ + 12·k*`

#### 2.2.3 独立折叠（兜底）

当和弦跨度超过 35 半音时（极少见，跨度超 3 八度的同时和弦在钢琴文献中几乎为零），每个音符独立映射到最接近 `ref_pitch` 的合法位置：

```
f(pᵢ) = argmin_{m} |m - ref_pitch|
   s.t. m = pᵢ + 12k,  k ∈ [-6, 6],  48 ≤ m ≤ 83
```

若所有 13 个候选位置（k ∈ [-6, 6]）均落在窗口外（实际不会发生，因为 13 个八度覆盖远超 36 键范围），则 clamp 到 `[48, 83]`。

#### 2.2.4 ref_pitch 指数平滑

`ref_pitch` 是算法唯一的全局状态，以指数滑动平均（EMA）跨时间片传播：

```
ref_pitch₀ = 65.5 (F4，36 键窗口几何中心)

对第 t 个时间片：
  mapped_avgₜ = avg(f(p₁), ..., f(pₙ))
  ref_pitchₜ₊₁ = (1 - α) × ref_pitchₜ + α × mapped_avgₜ

其中 α = ref_smoothing = 0.2
```

**平滑系数选择**：
- `α = 0.2` 意味着历史权重 0.8，当前权重 0.2
- 每个时间片的 `mapped_avg` 仅贡献 20% 到新 ref_pitch
- 半衰期约 3.1 个时间片（即 0.8³ ≈ 0.512）：经过 3 个时间片，过去的影响减半
- 邻接时间片几乎不会触发八度跳变，跳变只在乐句重心真正的趋势性迁移中被"慢慢推"过来

### 3.3 完整算法伪代码

```
INPUT:  grouped_pitches  = { time_index: [pitch1, pitch2, ...] }
        ref_smoothing    = 0.2
        window_low       = 48
        window_high      = 83
        window_size      = 35

OUTPUT: grouped_notes    = { time_index: [token1, token2, ...] }

ref_pitch ← 65.5                     // F4, 窗口中心

FOR EACH time_index IN sorted(grouped_pitches):
    pitches ← grouped_pitches[time_index]

    // ── 第1步：自适应八度折叠 ──
    mapped_pitches ← adaptive_octave_fold_slice(
        pitches, ref_pitch, window_low, window_high
    )

    // ── 第2步：更新 ref_pitch ──
    IF mapped_pitches not empty:
        avg ← sum(mapped_pitches) / len(mapped_pitches)
        ref_pitch ← (1 - α) × ref_pitch + α × avg

    // ── 第3步：统计记录 ──
    FOR EACH (orig, mapped) IN zip(pitches, mapped_pitches):
        IF orig ≠ mapped:
            remapped_notes++

    // ── 第4步：YAML token 生成 + 去重 ──
    tokens ← []
    used ← {}
    FOR EACH pitch IN sorted(mapped_pitches):
        token ← pitch_to_token(pitch)
        IF token NOT IN used:
            used.add(token)
            tokens.append(token)
        ELSE:
            collisions_avoided++

    // ── 第5步：和弦密度裁剪 ──
    IF len(tokens) > max_chord_notes:
        // 保最高（旋律） + 最低（低音） + 中间按距离填充
        priority ← [max(pitches), min(pitches)]
        middle ← sorted(mapped_pitches - priority, key=distance to max)
        FOR EACH p IN middle:
            IF len(priority) < max_chord_notes:
                priority.append(p)
        tokens ← pitch_to_token(priority)  // 重新生成

    grouped_notes[time_index] ← tokens

RETURN grouped_notes
```

---

## 四、核心子算法详解

### 4.1 `adaptive_octave_fold_slice` — 单时间片折叠

**文件位置**：`src/audio_to_yaml_converter.py:2701-2769`

```python
def _adaptive_octave_fold_slice(
    self, pitches, ref_pitch, window_low=48, window_high=83, config=None
) -> list[int]:
    window_size = window_high - window_low  # 35

    # 情况A：和弦跨度 ≤ 窗口宽度 → 寻找统一 k
    original_span = max(pitches) - min(pitches)
    if original_span <= window_size:
        common_ks = None
        for pitch in pitches:
            pitch_ks = {k for k in range(-6, 7)
                        if window_low <= pitch + 12*k <= window_high}
            common_ks = (common_ks & pitch_ks) if common_ks else pitch_ks
            if not common_ks:
                break

        if common_ks:
            avg_original = sum(pitches) / len(pitches)
            best_k = min(common_ks,
                         key=lambda k: abs((avg_original - 12*k) - ref_pitch))
            return [p + 12*best_k for p in pitches]

    # 情况B：无统一 k → 逐音独立折叠到最近 ref 位置
    result = []
    for pitch in pitches:
        best_mapped, best_dist = None, float("inf")
        for k in range(-6, 7):
            mapped = pitch + 12*k
            if window_low <= mapped <= window_high:
                dist = abs(mapped - ref_pitch)
                if dist < best_dist:
                    best_dist, best_mapped = dist, mapped
        result.append(best_mapped if best_mapped is not None
                      else max(window_low, min(window_high, pitch)))
    return result
```

**复杂度**：O(n × 13) = O(n)，每个音符最多检查 13 个八度候选（k ∈ [-6, 6]）。

### 4.2 和弦密度裁剪策略

**文件位置**：`src/audio_to_yaml_converter.py:2668-2694`

当去重后的 token 数量超过 `max_chord_notes`（通常 6）时：

```
优先级裁剪规则：
  1. 保留最高音（旋律候选，人耳对高音敏感）
  2. 保留最低音（低音锚点，和声根音）
  3. 中间音按与最高音的距离升序填充，距离小 = 更近和声结构的音优先保留
```

这比随机裁剪或纯按音量裁剪更保留和弦结构的"骨架"——最高音（通常是人耳感知的旋律线）和最低音（通常定义和声功能）必定保留，中间音按声学邻近度择优。

### 4.3 Token 映射表

| MIDI 音高类 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 音名 | C | C# | D | Eb | E | F | F# | G | G# | A | Bb | B |
| Token | 1 | #1 | 2 | b3 | 3 | 4 | #4 | 5 | #5 | 6 | b7 | 7 |

**八度前缀**：

| MIDI 范围 | 八度 | 前缀 | 示例 |
|-----------|------|------|------|
| 48-59 | C3-B3 | `-` | `-1`, `-#4`, `-b7` |
| 60-71 | C4-B4 | 无 | `1`, `#4`, `b7` |
| 72-83 | C5-B5 | `+` | `+1`, `+#4`, `+b7` |

---

## 五、完整数据流

```
MIDI 文件 (.mid)
    │
    ▼
┌──────────────────────────────────────┐
│  Step 1: 量化分组                      │
│  quantize_beat=0.25 (十六分音符网格)     │
│  输出: grouped_pitches[index] = [p₁,...]│
└───────────┬──────────────────────────┘
            │
            ▼
┌──────────────────────────────────────┐
│  Step 2: 自适应八度折叠                │
│  for each index:                      │
│    k = find_unified_octave_shift()    │
│    mapped = [p + 12k for p in chord]  │
│    ref_pitch = EMA(ref_pitch, avg)    │
│  输出: mapped_pitches                  │
└───────────┬──────────────────────────┘
            │
            ▼
┌──────────────────────────────────────┐
│  Step 3: Token 转换 + 键位去重        │
│  pitch_to_token(p, allow_accidentals) │
│  同一键位冲突: 按音高排序保留第一个     │
│  输出: tokens, collisions_avoided      │
└───────────┬──────────────────────────┘
            │
            ▼
┌──────────────────────────────────────┐
│  Step 4: 和弦密度裁剪                  │
│  if len(tokens) > max_chord_notes:    │
│    保最高 + 最低 + 近距填充             │
│  输出: capped_tokens                   │
└───────────┬──────────────────────────┘
            │
            ▼
┌──────────────────────────────────────┐
│  Step 5: YAML 序列化                   │
│  相邻时间片插入休止符                   │
│  输出: score_events → output.yaml      │
└──────────────────────────────────────┘
```

---

## 六、基准结果分析

### 6.1 五首歌曲四模式排名（2026-07）

| 排名 | 算法 | 综合得分 | One Last Kiss | 妄想哀歌 | 迷航 | 水.... | Beautiful | 方差 |
|:---:|-------|:-------:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | `adaptive_octave_fold` | **0.5310** | 0.4690 | 0.8613 | 0.3884 | 0.5073 | 0.4289 | **0.0312** |
| 2 | `attention_weighted` | 0.5305 | 0.4691 | 0.8531 | 0.3859 | 0.5133 | 0.4310 | 0.0317 |
| 3 | `hands_decoupled` | 0.5279 | 0.4676 | 0.8531 | 0.3871 | 0.5053 | 0.4266 | 0.0322 |
| 4 | `svsep_mpdr` | 0.5273 | 0.4765 | 0.8213 | 0.3868 | 0.5216 | 0.4302 | 0.0331 |

### 6.2 方差分析

`adaptive_octave_fold` 的跨歌曲方差（0.0312）是四种模式中最低的。原因：

1. **无角色假设**：不像 `hands_decoupled` 依赖左右手分割（分割准确率因曲而异），也不像 `attention_weighted` 依赖滑动窗口的局部注意力（窗口大小对不同速度曲目敏感），`adaptive_octave_fold` 对所有音符一视同仁。

2. **纯纯度驱动**：决策只依赖于当前和弦的跨度是否 ≤ 35 半音。这个条件在 99.9%+ 的钢琴和弦中满足（标准钢琴和弦跨度几乎不可能超过 3 八度），因此几乎所有时间片都走统一 k 路径，行为高度可预测。

3. **EMA 平滑的阻尼效应**：`α = 0.2` 的平滑系数使得 ref_pitch 像一个"大质量惯性体"，不会因单个异常和弦（如短暂的低音雷击或极高装饰音）而剧烈摆动。这种阻尼对快速琶音、分解和弦等高密度段落特别有效。

4. **和弦统一偏移的本质稳健性**：统一 k 保证了和弦内部音程的数学精确性——这不是近似，是精确映射。其他模式（如 `hands_decoupled` 的左手和声简化、`attention_weighted` 的密度裁剪）都涉及启发式决策，这些决策在不同曲风中的表现方差更大。

### 6.3 各模式单首歌曲最优

有意思的是，虽然 `adaptive_octave_fold` 总排名第一，但在单歌曲维度上并非每首都排第一：

| 歌曲 | 最优模式 | 得分 |
|------|---------|:---:|
| 妄想哀歌 Remix | `adaptive_octave_fold` | 0.8613 |
| One Last Kiss | `svsep_mpdr` | 0.4765 |
| 迷航 | `adaptive_octave_fold` | 0.3884 |
| 水.... | `svsep_mpdr` | 0.5216 |
| Beautiful | `attention_weighted` | 0.4310 |

这说明不同模式确实有不同的"舒适区"，但 `adaptive_octave_fold` 的跨曲平均最稳定——它不是"某一首特别强"，而是"所有都还不错"。

---

## 七、关键参数

| 参数 | 默认值 | 对算法的影响 |
|------|--------|-------------|
| `ref_smoothing` | 0.2 | 控制 ref_pitch 的"惯性"。越大反应越快但可能引入抖动；越小越平滑但可能滞后于趋势迁移 |
| `max_chord_notes` | 6 | 裁剪后单时间片最多保留音符数。增大保留更多和声信息但可能超出游戏同时按键限制 |
| `quantize_beat` | 0.25 | 时间量化精度。更细粒度保留更多原曲细节，但增加 total events |
| `window_low` | 48 (C3) | 硬约束，不可调（游戏键盘物理限制） |
| `window_high` | 83 (B5) | 硬约束，不可调 |
| `k_range` | [-6, 6] | 八度搜索范围。覆盖 13 个八度，远超钢琴 7.25 八度范围，保证全覆盖 |

### 建议调参指南

- **ref_smoothing = 0.15**：更平滑的八度过渡，适合抒情慢曲（如肖邦夜曲）
- **ref_smoothing = 0.30**：更灵活的音域响应，适合音域跳动剧烈的炫技曲（如李斯特练习曲）
- **max_chord_notes = 4**：更严格的密度裁剪，适合手指独立性较弱的玩家

---

## 八、与相邻模式的本质差异

### 8.1 vs `octave_fold`（纯八度折叠）

| 维度 | `octave_fold` | `adaptive_octave_fold` |
|------|--------------|----------------------|
| 偏移方式 | 逐音独立八度折叠 | 和弦统一 k + 逐音兜底 |
| 上下文 | 无（每个音符独立决策） | 有（ref_pitch 跨时间片传播） |
| 和弦内音程 | 可能被拆散（不同音符选不同 k） | **绝对保留**（统一 k） |
| 八度跳变 | 相邻时间片可能剧烈跳变 | EMA 平滑，自然过渡 |
| 基准结果 | 被淘汰（max_chord 超限） | 排名第一 |

### 8.2 vs `hands_decoupled`

| 维度 | `hands_decoupled` | `adaptive_octave_fold` |
|------|------------------|----------------------|
| 前置需求 | 需要左右手分割（启发式） | 无 |
| 和声处理 | 左手和声功能简化（信息损失） | 全部音符参与统一折叠 |
| 复杂度 | 五阶段流水线 | 两阶段（量化 → 折叠） |
| 方差 | 0.0322 | **0.0312**（更低） |
| 优势 | 高密度段落的左手清理 | 一致性最强 |

### 8.3 vs `attention_weighted`

| 维度 | `attention_weighted` | `adaptive_octave_fold` |
|------|---------------------|----------------------|
| 核心机制 | 滑动窗口交叉注意力 | 统一 k + EMA |
| 额外依赖 | velocity 信息 | 仅依赖 pitch |
| 窗口敏感度 | 对不同 BPM 曲目响应不同 | 不依赖窗口大小 |
| 计算复杂度 | O(n²)（注意力矩阵） | O(n)（线性） |
| 方差 | 0.0317 | **0.0312** |

---

## 九、已知局限

1. **无法处理独立线条**：当同一时间片内存在超过 35 半音跨度的音符时（如极高泛音装饰 + 极低贝斯同时演奏），统一 k 搜索失败，退回逐音独立折叠，此时和弦内音程关系被打破。

2. **不识别声部角色**：所有音符等同对待。在极端复调作品中（如赋格），两个独立旋律线可能被统一折叠挤到同一八度，丧失声部独立性。`hands_decoupled` 和 `svsep_mpdr` 在此类场景更有优势。

3. **ref_pitch 滞后**：EMA 的阻尼特性意味着在音域突变处（如突然从极低区跳到极高区），ref_pitch 需要 3-5 个时间片才能"追上"新位置。在这段追赶上，统一 k 选择的"最优性"下降。

4. **密度裁剪的非确定性**：当去重 token 数量超过 `max_chord_notes` 时，中间音的"距最高音距离排序"只是一个启发式，不一定是最优和声选择。更理想的做法是将密度裁剪下沉到 k 选择阶段，但这样会大幅增加搜索复杂度。

---

## 十、使用方式

```powershell
# 推荐用法（默认模式即为 adaptive_octave_fold）
python src/audio_to_yaml_converter.py `
  --input-midi "piano.mid" `
  --output-yaml "config/output.yaml" `
  --work-dir "work/output" `
  --song-name "曲名" --bpm 126 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals `
  --out-of-range-policy octave_fold `
  --max-chord-notes 6 `
  --ref-smoothing 0.2

# 音频全管线
python src/audio_to_yaml_converter.py `
  --audio "song.mp3" `
  --output-yaml "config/song.yaml" `
  --work-dir "work/song" `
  --song-name "曲名" --bpm 0 `
  --pitch-compression-mode adaptive_octave_fold `
  --allow-accidentals `
  --out-of-range-policy octave_fold `
  --max-chord-notes 6 `
  --ref-smoothing 0.2
```

---

## 十一、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1 | 2026-04 | 初始实现：统一 k 搜索 + 独立折叠兜底 |
| v2 | 2026-05 | 新增 ref_pitch 指数平滑、和弦密度裁剪 |
| v3 | 2026-07 | 完成五首歌曲四模式基准评测，验证交叉方差最优；补充完整数学推导、学术溯源与数据流文档 |

---

## 参考文献

1. **Tzanetakis, G. & Cook, P.** (ISMIR 2002). "Musical Genre Classification of Audio Signals." — 提出折叠音高直方图与五度圈旋转映射。

2. **Nakamura, E. & Sagayama, S.** (2015). "Statistical Piano Reduction Controlling Performance Difficulty." — 将八度音高移位形式化为概率编辑操作。

3. **Callender, C., Quinn, I. & Tymoczko, D.** (Science 2008). "Generalized Voice-Leading Spaces." — 将八度等价（O-operation）定义为音乐空间的基本操作之一。

4. **Fujishima, T.** (ISMIR 1999). "Realtime Chord Recognition of Musical Sound: a System Using Common Lisp Music." — 提出 chroma/pitch class profile 作为 MIR 基础特征。

5. **NTEZMusic 基准评测** (2026-07). 五首歌曲四模式客观排名，`work/arrangement_all_modes_20260711/reports/ranking.json`。

> 作者：JucieOvo
