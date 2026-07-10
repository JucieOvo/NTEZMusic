# SVSEP MPDR 延音选择性拆分方案

## 1. 问题重述

**管线基准**：SVSEP MPDR（GNN 区分左右手声部，piano_svsep 模型 + MPDR 预算精排）

**核心矛盾**：
- 游戏平台无延音踏板 + 无力度动态 → 所有音符在播放层被 `key_press_seconds` 截断为 1ms 短击
- 原曲中踏板兜住的 4 拍长音和 16 分短音在输出 YAML 中完全等价（各一个 token），听感上持续性和声完全消失
- 简单全量拆分（所有 `duration_beats >= N` 的音都加续打击键）→ 踏板段落中同一时刻可能有 8+ 个长音，全拆分 = 噪声洪水

**目标**：
- 选择性地为最关键的长音添加一次中间位置的续打击键（onset + 中点，共 2 击）
- 在"补充和声持续性"和"不引入过多噪声"之间取得平衡

## 2. 策略选择

### 推荐策略：仅低音锚音续打

**靶向目标**：仅对左手中**满足全部三个条件**的音符做续打：

| 条件 | 含义 | 理由 |
|------|------|------|
| `hand == "left"` | 左手声部 | SVSEP 确定性手部分配 |
| `duration_beats >= threshold` | 持续足够长 | 短音不需要续打 |
| 该音是当前时间片左手的**最低音**（\"bass note\"） | 和声根基音 | 仅此一音被移走影响最大 |

**不处理的对象**：
- 右手长音（旋律已由音符密度承载，续打产生突兀的重音）
- 左手内声长音（和声填充，续打只会增加嘈杂度）
- 短音（< 阈值）

**为什么仅低音就够**：
- 低音是全景和声的"地面"。低音消失 → 和声悬浮、段落断裂
- 正常钢琴演奏中，左手低音往往每小节仅 1-2 个，续打每小节最多增加 2 个 token
- SVSEP 管线已在 `_score_mpdr_left_note` 中使用 `mpdr_bass_anchor_weight` 为低音加权——与续打逻辑一致

## 3. 算法流程

```
输入: left_midi_notes (tuple[MidiNoteEvent]), right_midi_notes (tuple[MidiNoteEvent]), config

步骤 1: 构建密度映射
    all_notes = left_midi_notes + right_midi_notes
    density_map = {time_index: count} # 按 quantize_beat 量化后的每时间片音符数

步骤 2: 筛选候选左手音符
    对左手中的每个音符:
        start_index = round(start_beat / quantize_beat)
        获取该时间片的左手音符列表 left_at_slice
        最低音 pitch = min(left_at_slice.pitch)

        若 (note.pitch == 最低音 pitch)
        and (note.duration_beats >= sustain_split_threshold_beats)
        and (note.duration_beats > quantize_beat):
            → 标记为候选

步骤 3: 中点密度过滤
    对每个候选:
        midpoint_beat = note.start_beat + note.duration_beats / 2.0
        midpoint_index = round(midpoint_beat / quantize_beat)
        midpoint_density = density_map.get(midpoint_index, 0)

        若 midpoint_density <= sustain_split_max_midpoint_density:
            → 生成续打击键

步骤 4: 生成续打击键
    创建 MidiNoteEvent:
        pitch = note.pitch
        start_beat = midpoint_beat
        end_beat = midpoint_beat + sustain_split_strike_duration_beats
        velocity = note.velocity
        duration_beats = sustain_split_strike_duration_beats (0.25)

    追加到 left_midi_notes 中

步骤 5: 重新排序
    按 (start_beat, pitch) 重排 left_midi_notes
    返回新的 left_midi_notes
```

## 4. 插入位置

```
_separate_hands_with_svsep  (返回 left_notes, right_notes)
        │
        ▼
[_split_svsep_sustained_notes]  ← 新方法，在此调用
        │
        ▼
_build_svsep_mpdr_score_events
        │
        ├─ _generate_svsep_mpdr_candidates
        │    └─ _build_svsep_mpdr_candidate (x N 次参数扫描)
        │         ├─ _group_midi_notes_by_index
        │         ├─ _select_mpdr_right_notes   (右手选择)
        │         ├─ _select_mpdr_left_notes    (左手选择，续打击键参与评分)
        │         ├─ _suppress_mpdr_repeated_candidates (重复抑制)
        │         ├─ _fold_mpdr_candidates_unified      (八度折叠)
        │         └─ _candidates_to_modifier_safe_tokens (token 输出)
        └─ 精排 → 最优候选 → YAML
```

**为什么插在此处**：
- 在 SVSEP 分离之后，手部标签已确定
- 在候选生成之前，续打击键可参与评分-选择-裁剪全流程
- 续打击键 `duration_beats = 0.25`（短），MPDR 评分中 `duration_score` 低，自然在预算紧张时被裁掉
- 不做全量拆分，一次参数扫描仅多处理 ≤N 个续打击键（每小节 1-2 个）

## 5. 参数设计

| 参数 | 类型 | 默认值 | CLI 参数 | 说明 |
|------|------|--------|----------|------|
| `sustain_split_enabled` | bool | `False` | `--sustain-split` | 启用标志 |
| `sustain_split_threshold_beats` | float | `1.0` | `--sustain-split-threshold` | 触发拆分的 `duration_beats` 下限 |
| `sustain_split_max_midpoint_density` | int | `3` | `--sustain-split-max-density` | 中点位置最大同拍音符数 |
| `sustain_split_strike_duration_beats` | float | `0.25` | （不暴露） | 续打击键的评分用 duration |

**阈值 1.0 拍（四分音符）的选择理由**：
- 在 4/4 拍中，低于此值的音符通常是八分/十六分跑动，绝非踏板兜住的长音
- 若后续听感需要更激进，可调低至 0.75

**最大密度 3 的选择理由**：
- 中点位置 ≤3 个音符 → 续打不会造成和弦密度爆破
- 中点位置 >3 个音符 → 该时刻已足够"满"，续打会被淹没

## 6. 对管线的影响

### 参数扫描
- `_generate_svsep_mpdr_candidates` 会调用 `_build_svsep_mpdr_candidate` N 次
- 每次使用**同一份**已添加续打击键的 left_midi_notes
- 扫描不会重复插入，因为 `_split_svsep_sustained_notes` 在扫描外部执行一次

### 重复抑制 (_suppress_mpdr_repeated_candidates)
- 续打击键的 pitch 与原低音相同，start_beat 在中间
- 若中间位置与原低音的另一个 onset 在同一 time_index → 看是否出现在短抑制窗口 (0.5 拍)
- 续打击键的 start_beat 距离原低音 start_beat = `duration_beats/2`，通常 > 0.5 拍 → 不抑制
- 续打击键的 `duration_beats` = 0.25，不满足 `sustained_context` → 不会误触发

### 和弦密度裁剪
- 右手 ≥7 音符时的预算裁剪（`_select_mpdr_right_notes`）不受影响（续打击键在左手）
- 左手预算裁剪（`_select_mpdr_left_notes`）可能因续打击键消耗少量预算
- 续打击键 `duration_score` 低 → `utility` 低于真正低音 → 优先被裁

### Token 碰撞
- `_candidates_to_modifier_safe_tokens` 对同时间片同 pitch class 做去重
- 若中点位置已有同 pitch 音符（概率低），续打击键被丢弃

### max_score_events
- 增加量 ≤ 左手中符合条件的低音数（通常 < 总音节数 × 0.05）
- 5000 上限足够安全

## 7. 预期效果

**以残酷天使 126BPM 为例**：
- 左手部分每小节约有 8-16 个音符（分解和弦+低音）
- 其中符合条件的低音（duration ≥ 1.0 拍 + 最低音）预计每小节 1-2 个
- 续打击键每小节增加 2 个 token，全曲增加 ~200 token
- 听感变化：每小节和声根基在被截断后"回响"一次，段落不显得空洞

**不处理的内容**：
- 右手快速旋律跑动（duration 短，不触发）
- 左手密集分解和弦（仅最低音触发，内声不受影响）
- 踏板段落中非低音的长音（密度过滤 + 仅低音规则）

## 8. 风险与限制

| 风险 | 缓解措施 |
|------|----------|
| 阈值 1.0 太高，部分半小节低音被遗漏 | 可调低至 0.75 或 0.5 |
| 仅低音规则，个别高把位左手低音被忽略 | SVSEP 已区分左右手，左手最低音 = 正确的低音 |
| 中点位置密度判断依赖原始 MIDI 密度 | 密度映射基于原始音符，无歧义 |
| 续打击键在候选生成阶段被裁剪掉 | 预期行为——若预算不够，说明该时刻本来就不应该多打 |
