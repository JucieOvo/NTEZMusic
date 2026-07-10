# 自适应八度折叠缩编算法 -- 修正方案 (v2)

## 一、核心认知修正

**之前的错误理解**：游戏键盘的 QWERTY / ASDF / ZXCV 三行是三个"功能区"——旋律必须放在高行、和声必须放在中行、低音必须放在低行。

**正确理解**：三行键盘只是游戏的**视觉操作分区**，共 36 键形成**一个完整的连续 3 八度音域**（C3-B5，MIDI 48-83）。`+` / 无 / `-` 前缀仅是键位记号，不对应任何功能角色。

```
完整的 3 八度游戏键盘 (36 键):

高行 (QWERTY)  q w e r t y u  → 音符 +1 #1 +2 b3 +3 +4 #4 +5 #5 +6 b7 +7
中行 (ASDFGH)  a s d f g h j  → 音符  1 #1  2 b3  3  4 #4  5 #5  6 b7  7
低行 (ZXCVBN)  z x c v b n m  → 音符 -1 #1 -2 b3 -3 -4 #4 -5 #5 -6 b7 -7
```

每行 12 半音 × 3 行 = 36 半音 = 3 八度。 

**核心原则：所有 36 键平等可用。旋律可以出现在任何行，低音也可以。唯一约束是总量不能超出 36 半音窗口。**

## 二、新算法设计

### 2.1 名称

`adaptive_octave_fold` —— 自适应八度折叠

### 2.2 总流程

```
Phase 0: 量化分组 → 按 quantize_beat 将音符分配到时间片网格

Phase 1: 自适应八度折叠（逐时间片）
  └── 维护平滑参考中心 ref_pitch
  └── 对每个时间片的音符集：
      ├── 寻找能使全部音符落入 [48, 83] 的统一八度偏移 k
      ├── 若存在共同 k，选最接近 ref_pitch 的那个
      ├── 若不存在（和声自身跨度 > 36 半音，极少见），各自独立映射
      └── 指数平滑更新 ref_pitch

Phase 2: 碰撞消解
  └── 同一时间片内同键位去重
  └── 超 max_chord_notes 时按角色优先级裁剪（旋律 > 低音 > 和声）
  └── 和声音程优先级辅助排序

Phase 3: Token 输出
  └── 转换为 YAML 简谱 token
```

### 2.3 ref_pitch 跟踪

```
ref_pitch 初始值 = 65.5（F4，36 键窗口中心）

每次处理一个时间片后：
  mapped_avg = sum(映射后音符音高) / 音符数
  ref_pitch = (1 - smoothing) * ref_pitch + smoothing * mapped_avg
  smoothing 默认 0.2
```

### 2.4 角色信息仅用于碰撞消解

不再在每个时间片强制分离声部。改为：
- 碰撞时**旋律音优先保留**（最高音 = 旋律候选）
- 低音次之（最低音 = 低音候选）
- 和声最后裁剪

这意味着：旋律可能出现在低行键位，低音可能出现在高行键位——只要整体音程关系正确。

## 三、与旧方案的对比

| 维度 | 旧方案 (v1) | 新方案 (v2) |
|------|------------|------------|
| 音区划分 | 旋律锁 [72,83], 和声锁 [60,71], 低音锁 [48,59] | 全 36 键动态使用 |
| 旋律自由度 | 限定于高行 12 键内 | 可在 36 键任何位置 |
| C 音旋律 | 永远 +1 (C5, 高音区底部) | 可随 ref_pitch 出现在更舒适的位置 |
| 算法结构 | 乐句分割 + 三层分离 + 独立 DP | 统一八度折叠 + 碰撞消解 |
| 代码复杂度 | ~600 行 | ~200 行 |
| 角色用途 | 决定映射目标区 | 仅用于碰撞消解优先级 |

## 四、参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ref_smoothing` | 0.2 | ref_pitch 指数平滑系数 |
| `min_layer_gap_semitones` | 3 | 碰撞消解时旋律与低音最小间距 |

其他参数（phrase_gap_threshold_beats, time_step_beats, median_filter_window, DP 权重等）在新方案中**不再需要**。

## 五、代码变更范围

1. 将 `pitch_compression_mode` 枚举值从 `adaptive_octave_smart` 改为 `adaptive_octave_fold`
2. 重写 `_build_adaptive_octave_smart_grouped_notes` → `_build_adaptive_octave_fold_grouped_notes`
3. 删除: `_segment_phrases_by_gaps`, `_separate_layers_with_filtering`, `_median_filter_line`, `_map_line_dp`, `_process_harmony_layer`, `_find_unified_k_for_harmony`, `_enforce_dense_voicing`
4. 新增: `_adaptive_octave_fold_slice`（单时间片的八度折叠）
5. 简化 `_resolve_collisions_full`
6. 清理 `AudioPipelineConfig` 中不再需要的字段
7. 更新命令行参数

> 作者：JucieOvo
