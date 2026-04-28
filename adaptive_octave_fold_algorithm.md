# NTEZMusic 自适应八度折叠算法 (Adaptive Octave Fold)

> 作者：JucieOvo  
> 版本：v2

---

## 概述

将完整钢琴 MIDI（88 键）自适应缩编到游戏 3 八度 36 键键盘（C3-B5），通过精确八度移位保留所有半音音程关系。平滑参考中心 `ref_pitch` 跟踪音乐重心，使八度切换隐匿于乐句自然呼吸处。

**一行命令**：

```powershell
python src/audio_to_yaml_converter.py `
  --input-midi "piano.mid" `
  --output-yaml "config/output.yaml" `
  --work-dir "work/output" `
  --song-name "曲名" --bpm 126 `
  --allow-accidentals `
  --out-of-range-policy octave_fold `
  --max-chord-notes 6
```

---

## 算法

### 输入输出

| | 说明 |
|---|---|
| 输入 | MIDI 音符序列 (pitch, start_beat, end_beat) |
| 输出 | YAML 简谱，所有音符落于 C3-B5 (MIDI 48-83) |

### 第一步：量化分组

按 `quantize_beat`（默认 0.25 拍，十六分音符）将 MIDI 音符分配到时间片网格。同一时间片内的音符构成一个和弦。

### 第二步：自适应八度折叠

维护平滑参考中心 `ref_pitch`（初始值 65.5 = F4，窗口中心）。

对每个时间片的音符合集 `{p₁, p₂, ..., pₙ}`：

```
1. 若音符自身跨度 ≤ 36 半音（窗口宽度）：
   寻找能使全部音符落入 [48, 83] 的统一八度偏移 k：
     for each k ∈ [-6, 6]:
         if ∀p: 48 ≤ p + 12k ≤ 83:
             k 合法
   从所有合法 k 中选择使映射后平均音高最接近 ref_pitch 的那个。
   → 所有音符统一偏移：pᵢ' = pᵢ + 12k

2. 若跨度 > 36 半音（极少见，钢琴曲中几乎不会出现）：
   各自独立映射到最接近 ref_pitch 的合法位置。

3. 更新 ref_pitch（指数平滑）：
   ref_pitch ← 0.8 × ref_pitch + 0.2 × avg(p₁', ..., pₙ')
```

**关键性质**：
- 和弦内**统一 k**：所有音符偏移相同八度数，和弦内部音程**绝对不变**。
- `ref_pitch` 平滑：不会突变，八度切换发生在音乐自然过渡处。
- 全 36 键动态使用：无功能分区，旋律可以出现在任何键位。

### 第三步：碰撞消解

同一时间片内多个音符映射到相同键位时：
- 去重保留一个（键位唯一性）
- 超 `max_chord_notes`（默认 4）时优先保留跨度最大的音符集合

### 第四步：Token 输出

将映射后的 MIDI 音高按 `NATURAL_PITCH_CLASSES` / `SHARP_PITCH_CLASSES` 转换为 YAML 简谱 token：

| pitch class | 0(C) | 1(C#) | 2(D) | 3(Eb) | 4(E) | 5(F) | 6(F#) | 7(G) | 8(G#) | 9(A) | 10(Bb) | 11(B) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| token | 1 | #1 | 2 | b3 | 3 | 4 | #4 | 5 | #5 | 6 | b7 | 7 |

前缀规则：音高在 C3-B3 → `-`，C4-B4 → 无，C5-B5 → `+`。

---

## 修饰键冲突处理

游戏键盘的修饰键是全局状态：Ctrl（升半音）和 Shift（降半音）**不能同时生效**，后按入的会顶掉先前的。

当同一和弦同时包含 `#` 和 `b` token 时，钢琴播放器自动拆分：

```
t=0:     按下 Ctrl → 按下升半音组键 + 安全自然键
t=1ms:   释放 Ctrl 组 → 释放 Ctrl → 按下 Shift → 按下降半音组键 + 安全自然键
t=hold:  释放全部
```

1ms 切换间隔低于人耳听觉融合阈值（~50ms），听感等同于同时发声。

---

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--pitch-compression-mode` | adaptive_octave_fold | 压缩模式 |
| `--quantize-beat` | 0.25 | 量化步长（拍） |
| `--max-chord-notes` | 4 | 单和弦最大音符数 |
| `--ref-smoothing` | 0.2 | ref_pitch 平滑系数 |
| `--allow-accidentals` | off | 开放半音 token |
| `--out-of-range-policy` | error | octave_fold 允许折叠 |
| `--key-press-seconds` | 0 | YAML 层无延迟，播放层保底 1ms |

---

## 与其它模式的对比

| 模式 | 核心机制 | 音程保真 | 推荐度 |
|------|---------|---------|--------|
| `none` | 不压缩，超范围直接报错 | - | 仅限窄音域曲目 |
| `octave_fold` | 逐音按八度折叠，无上下文 | 保留 | 一般 |
| `global_linear` | 全曲统一线性比例压缩 | 破坏（round 误杀） | **不推荐** |
| `range_rearrange` | 逐拍分三层映射 | 保留 | 一般 |
| **`adaptive_octave_fold`** | 统一 k + ref_pitch 平滑 | **精确保留，和弦统一** | **默认推荐** |

---

## 实测数据（Animenz《残酷天使的行动纲领》）

| 指标 | 数值 |
|------|------|
| 原始音符 | 3423 |
| 输出音符 | 2983 |
| 八度迁移率 | 49.7% |
| 和弦密度 | 11 → 4 |
| ref_pitch 轨迹 | F4 → B3 |
| 乐谱事件 | 2210 |

> 作者：JucieOvo
