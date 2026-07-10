# hands_decoupled 算法 -- 最终方案报告

> 作者：JucieOvo  
> 日期：2026-04-27  
> 状态：审批通过，进入实现

---

## 一、算法名称

`hands_decoupled` —— 基于角色解耦的双层注意力自适应折叠

**核心哲学**：在平移范式（统一八度偏移 k 保证和弦音程精确保留）内部做左右手角色解耦，融合方案 D/E/F 各自的最优贡献。

---

## 二、当前算法的根本问题

`adaptive_octave_fold` (v2) 维护单根全局因果 ref_pitch，相当于单层全局注意力。问题：

1. **角色串扰**：旋律的高音区推高 ref_pitch，拖滞后段低音区——无关 token 强制耦合
2. **一刀切裁剪**：max_chord_notes 全局统一，不区分旋律/伴奏
3. **无左右手概念**：没有按 88 键物理落点区分左右手

---

## 三、最终方案（融合 D/E/F 精华）

### 整体流程（五阶段）

```
输入：MIDI 音符事件流
输出：YAML 简谱 score 事件

阶段 0：量化分组（不变）
  grouped_pitches: {time_index → [pitch, ...]}

阶段 1：全局趋势线提取（方案D）
  1a. 粗粒度窗口采样：按 4 拍窗口滑动，每窗口取音高中位数
  1b. 非因果双向指数平滑（alpha=0.05）
  1c. 插值到每个时间片粒度 → global_ref[t]
  用途：保证跨乐句听感一致性

阶段 2：休止符分割 + 段内自适应左右手分割（D + E轻量验证）
  2a. 按连续空时间片（≥0.5拍）分割为乐段
  2b. 每段内：
      - 双峰检测：构建音高直方图，平滑后找双峰 → 候选分割线
      - 运动模式交叉验证：
        * 收集段内所有相邻时间片的音符对
        * 级进（≤2半音）集中在候选线上方 → 旋律性验证通过
        * 跳进（5-7半音）集中在候选线下方 → 低音性验证通过
      - 验证通过则采纳双峰分割线；失败则退化为段内中位数分割
      - 兜底：MIDI 60 (C4)

阶段 3：左右手独立折叠（方案E 角色解耦改造）
  3a. 右手轨（≥ split_pitch 的音符）：
      - 目标窗口：C4-B5 (MIDI 60-83)
      - 独立因果 ref_pitch_rh[t]，alpha=0.15
      - 段内独立平滑，段间重置为对应 global_ref
      - 不对右手轨做任何裁剪
  3b. 左手轨（< split_pitch 的音符）：
      - 目标窗口：C3-G4 (MIDI 48-67)
      - 独立因果 ref_pitch_lh[t]，alpha=0.25
      - 段间重置规则同上
  3c. 折叠逻辑复用 _adaptive_octave_fold_slice，增加窗口参数

阶段 4：IIP 音程完整性裁剪（方案F）
  4a. 仅对左手轨执行（右手轨不裁剪）
  4b. 若左手轨某时间片折叠后音符数 > left_max_chord_notes：
      - 计算该组音符的音程签名矩阵
      - 为每个音符计算音程贡献度：若移除 p 会导致某特征音程完全消失 → 高贡献
      - 贪心选取贡献度最高的 N 个音符
  4c. left_max_chord_notes = 2（默认，保留根音+色彩音）

阶段 5：轨间碰撞消解 + Token 输出
  5a. 合并左右手轨的折叠结果
  5b. 碰撞消解优先级：右手 > 左手
  5c. 碰撞时左手微调 ±1 半音（若仍在窗口内且不产生新碰撞），否则丢弃
  5d. 去重 + 调用 _pitch_to_token 生成 YAML token
  5e. 休止符插入逻辑不变
```

---

## 四、与 v2 (adaptive_octave_fold) 的对比

| 维度 | v2 | hands_decoupled |
|------|-----|-----------------|
| ref_pitch 数量 | 1 根全局因果 | 2 根（左右手独立）+ 1 根全局趋势 |
| 分段 | 无 | 休止符自动分段 |
| 左右手判定 | 无 | 双峰检测 + 运动模式交叉验证 |
| 和弦裁剪 | 全局一刀切 max_chord_notes | 右手不限 / 左手 IIP 裁剪 |
| 目标窗口 | 全 36 键 C3-B5 | 右手 C4-B5 / 左手 C3-G4 |
| 因果性 | 因果 | 全局趋势非因果 + 轨内因果 |
| 音程保真 | 统一 k（精确保留） | 统一 k（精确保留） |

---

## 五、新增配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pitch_compression_mode` | `adaptive_octave_fold` | 可选新增 `hands_decoupled` |
| `left_max_chord_notes` | 2 | 左手轨 IIP 裁剪后最大音符数 |
| `phrase_gap_beats` | 0.5 | 休止符分割阈值（拍数） |
| `global_trend_alpha` | 0.05 | 全局趋势线平滑系数 |
| `global_trend_window_beats` | 4.0 | 全局趋势采样窗口（拍数） |

---

## 六、废弃代码清单

删除以下内容：
- `pitch_compression_mode` 取值 `range_rearrange` 和 `global_linear`
- `_build_rearranged_grouped_notes` 方法
- `_build_global_linear_grouped_notes` 方法
- `_rearrange_time_slice` 方法
- `_move_pitch_to_octave` 方法（仅 range_rearrange 使用）
- `_build_role_priority_candidates` 方法（仅 global_linear 使用）
- `_map_pitch_global_linear` 方法
- `_compress_time_slice_globally` 方法
- `AudioPipelineConfig` 中的 `melody_target_octave`、`harmony_target_octave`、`bass_target_octave` 字段
- CLI 参数 `--melody-target-octave`、`--harmony-target-octave`、`--bass-target-octave`

---

## 七、代码变更范围

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `audio_to_yaml_converter.py` | 删除 | ~200 行废弃代码 |
| `audio_to_yaml_converter.py` | 新增 | `_build_hands_decoupled_grouped_notes` + 6 个辅助方法 ~300 行 |
| `audio_to_yaml_converter.py` | 修改 | `_build_score_events` 分支逻辑、`_adaptive_octave_fold_slice` 增加窗口参数 |
| `audio_to_yaml_converter.py` | 修改 | `AudioPipelineConfig` 新增 4 字段、删除 3 字段 |
| `audio_to_yaml_converter.py` | 修改 | CLI 参数新增 4 个、删除 3 个 |

---

## 八、关键设计决策理由

### 决策 1：保留统一 k 平移范式

游戏没有延音和力度，和弦内部音程关系是唯一可依靠的品质底线。渲染范式（方案F）虽然灵活，但放弃了这一底线。

### 决策 2：段内独立 + 段间 global_ref 重置

全局趋势线保证跨段听感一致，段内独立 ref_pitch 消除角色串扰。段间重置时以 global_ref 为锚点，避免累积漂移。

### 决策 3：双峰检测 + 运动模式交叉验证

纯双峰检测（方案D）在交叉声部时可能误判，纯运动模式（方案E）计算量更大。交叉验证用轻量计算获得比两者单独使用更高的鲁棒性。

### 决策 4：右手不裁剪 + 左手 IIP 裁剪

右手承担旋律和主歌辨识度，裁剪会直接伤害听感。左手和声功能可通过 IIP 用最少音符表达。
