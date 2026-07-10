# hands_decoupled v2 信号增强型 -- 最终算法说明

> 作者：JucieOvo  
> 日期：2026-04-27  
> 版本：信号增强版 (v2)

---

## 一、核心哲学

在平移范式（统一八度偏移 k，和弦音程精确保留）内部做左右手角色解耦。左右手区分**仅用于决定"该简化谁"**——左手简化后与右手统一折叠到 C3-B5，不强制音区分区。

v2 相比 v1 的核心升级：**捡回 MIDI 中两个被丢弃的信号维度**（velocity 和 duration），零成本提升分割精度和简化品质。

---

## 二、算法全流程（五阶段）

```
88键 MIDI (pitch, start, end, velocity)
         │
阶段 0：量化分组（不变）
  grouped_pitches: {time_index → [pitch, ...]}
         │
阶段 1：全局趋势线提取
  1a. 全曲粗窗采样（4拍窗口，取中位数）
  1b. 非因果双向指数平滑（α=0.05）
  1c. 插值回每个时间片 → global_ref[t]
  用途：保证跨乐段听感一致
         │
阶段 2：休止符分割 + 三重交叉验证确定左右手分割线
  2a. 连续空时间片 ≥ 0.5拍 → 段边界
  2b. 段内双峰检测 → 候选分割线
  2c. 运动模式验证（级进集中于上方、跳进集中于下方）
  2d. velocity验证（高力度集中于上方、低力度集中于下方）
  2e. 任意一重验证失败 → 退化为段内中位数 → 兜底 MIDI 60
         │
阶段 3：段内左手简化 + 统一折叠
  对段内每个时间片：
    3a. 按分割线分离 RH/LH（基于原始 MIDI 音高）
    3b. 右手：完整保留，不做任何裁剪
    3c. 左手：和声功能优先级裁剪
        - 优先级：根音(bass) > 三音 > 七音 > 延伸音 > 五度 > 八度重复
        - 裁剪上限：min(left_max_chord_notes, lh_density_cap)
        - lh_density_cap 由右手 duration加权密度动态决定
    3d. 合并简化后左手 + 右手
    3e. 统一折叠到 C3-B5 (使用段内因果 ref_pitch)
         │
阶段 4：总密度控制
  折叠后若 token 数 > max_chord_notes
  → 按音高极值优先裁剪（最高音旋律/最低音低音/中间填充）
         │
阶段 5：Token 输出（不变）
  pitch → token + 去重 + 休止符插入
```

---

## 三、关键算法详解

### 3.1 velocity 辅助分割验证

```
验证逻辑:
  统计段内所有音符的 velocity 中位数
  velocity ≥ 中位数 → 高力度组（更可能为旋律）
  velocity < 中位数  → 低力度组（更可能为伴奏）

  高力度组中 ≥ split_pitch 的比例 > 50% ？
  低力度组中 < split_pitch 的比例 > 50% ？

  两者都满足 → velocity 验证通过
```

与双峰检测（统计）和运动模式（序列）构成三重交叉验证，显著降低交叉声部误判率。

### 3.2 和声功能优先级简化

```
给定左手音高集合（原始MIDI），以最低音为功能参考点：

排序逻辑:
  1. 最低音 (bass) -- 和声根基，铁定保留
  2. 与bass差3-4半音 (三度音) -- 决定大三/小三品质
  3. 与bass差10-11半音 (七度音) -- 决定和弦类型
  4. 其他音程的美学排序:
     - 5-6半音 (增四/纯四) > 8-9半音 (小六/大六)
     - > 1-2半音 (小二/大二) > 纯八度(12n)

  5. 纯五度(7半音) -- 信息量最低，首先丢弃
  6. 八度重复(12n, n≥1) -- 必定丢弃
```

不依赖和弦识别，纯音程计算 O(n²)，n≤10 时可忽略。

### 3.3 duration加权右手密度 → 左手上限

```
对于时间片 ti，取前后各半拍窗口内所有右手音符:

  rh_weighted = Σ duration_weight(p)
  其中 duration_weight(p) = min(p.duration_beats / quantize_beat, 1.0)

  rh_weighted ≤ 2.5  → lh_cap = 1
  rh_weighted 2.5-5  → lh_cap = 2
  rh_weighted 5-7.5  → lh_cap = 3
  rh_weighted > 7.5  → lh_cap = 4

  lh_max = min(config.left_max_chord_notes, lh_cap)
```

duration 加权使全音符（旋律骨干）权重 1.0，十六分装饰音权重 1/8，比纯事件计数更准确反映旋律"忙碌程度"。

---

## 四、配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pitch_compression_mode` | `adaptive_octave_fold` | 可选 `hands_decoupled` |
| `max_chord_notes` | 4 | 总密度上限 |
| `left_max_chord_notes` | 4 | 左手和弦绝对上限 |
| `ref_smoothing` | 0.2 | ref_pitch 平滑系数 |
| `phrase_gap_beats` | 0.5 | 休止符分割阈值 |
| `global_trend_alpha` | 0.05 | 全局趋势平滑系数 |
| `global_trend_window_beats` | 4.0 | 趋势采样窗口 |

---

## 五、与 v2 (adaptive_octave_fold) 的对比

| 维度 | adaptive_octave_fold | hands_decoupled v2 |
|------|---------------------|---------------------|
| ref_pitch | 全局因果单一 | 段内因果 + 全局趋势重置 |
| 分段 | 无 | 休止符自动分段 |
| 左右手判定 | 无 | 三重重交叉验证 (双峰+运动+velocity) |
| 和弦裁剪 | 一刀切 max_chord_notes | 右手不裁 / 左手和声功能优先级 |
| 左手密度 | -- | duration加权右手密度动态约束 |
| 延音 | 忽略 | 忽略（硬约束） |
| velocity | 未读取 | 用于分割验证 |
| duration | 未使用 | 用于密度加权 + 和弦优先级 |
| 折叠 | 统一 C3-B5 | 统一 C3-B5（不强制左右手分区） |
