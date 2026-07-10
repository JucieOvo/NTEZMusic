"""
模块名称：cross_attention
功能描述：
    实现全局交叉注意力与段内交叉注意力的纯算法计算。
    通过手设相似度函数（音高相似度、密度权重、时长权重、和声上下文权重）
    替代神经网络中的可学习投影矩阵，对原始 MIDI 做加权求和，得到候选音符的
    "信息保存支持度"分数。

主要组件：
    - compute_global_attention: 全局交叉注意力——在全曲范围内计算候选音符受支持度
    - compute_segment_attention: 段内交叉注意力——在当前乐段范围内计算候选音符的
      和声功能重要性

依赖说明：
    - positional_encoding: 时间邻近度计算
    - 无外部依赖

作者：JucieOvo
创建日期：2026-04-28
"""

import math
from positional_encoding import temporal_proximity


def _pitch_similarity(pitch_a: int, pitch_b: int) -> float:
    """
    计算两个 MIDI 音高的相似度。

    采用混合策略：
        - 0.7 权重: pitch class 匹配（同一音名，如 C4 和 C5）
        - 0.3 权重: 八度邻近度（八度差越小越相似）

    总和在 [0, 1] 区间。

    :param pitch_a: MIDI 音高 A
    :param pitch_b: MIDI 音高 B
    :return: 音高相似度，范围 [0, 1]
    """
    # pitch class 匹配
    same_class = 1.0 if (pitch_a % 12) == (pitch_b % 12) else 0.0

    # 八度邻近度
    octave_a = pitch_a // 12
    octave_b = pitch_b // 12
    octave_dist = abs(octave_a - octave_b)
    octave_prox = max(0.0, 1.0 - octave_dist / 4.0)

    return 0.7 * same_class + 0.3 * octave_prox


def _duration_weight(duration_beats: float) -> float:
    """
    计算音符时长的权重因子。

    长音符（>= 2 拍）权重为 1.0，承载和声骨架，丢弃影响大。
    短装饰音权重递减。

    :param duration_beats: 音符持续拍数
    :return: 时长权重，范围 [0, 1]
    """
    if duration_beats <= 0:
        return 0.0
    return min(duration_beats / 2.0, 1.0)


def _density_weight(note_count: int, max_density: int = 8) -> float:
    """
    计算时间片的密度权重。

    原曲密集段落中的音符携带更多和声信息，被保留的优先级应更高。

    :param note_count: 该时间片的原始音符数
    :param max_density: 饱和度上限（默认 8）
    :return: 密度权重，范围 [0, 1]
    """
    if note_count <= 0:
        return 0.0
    return min(note_count / max_density, 1.0)


def _harmonic_context_score(pitch: int, chord_pitches: list[int]) -> float:
    """
    计算音符在和弦上下文中的和声功能重要性。

    以和弦最低音为参考，根据 pitch class 音程距离评估功能角色：
        - 根音/同音：不可或缺，得分最高
        - 三度音（3-4 半音）：决定和弦品质，高优先级
        - 七度音（10-11 半音）：决定和弦类型，较高优先级
        - 五度音（7 半音）：和声支撑，中等优先级
        - 其他延伸音：中低优先级

    :param pitch: 待评估的 MIDI 音高
    :param chord_pitches: 该时间片的原始 MIDI 音高列表
    :return: 和声功能分数，范围 [0, 1]
    """
    if not chord_pitches:
        return 0.5
    if len(chord_pitches) == 1:
        return 1.0

    bass = min(chord_pitches)
    interval = pitch - bass

    # 按 pitch class 距离归一化到半音周期
    semi_distance = interval % 12

    if semi_distance == 0:
        # 根音 / 八度重复
        return 1.0
    elif semi_distance in (3, 4):
        # 三度音：决定大三/小三和弦品质
        return 0.85
    elif semi_distance in (10, 11):
        # 七度音：决定属七/大七/小七和弦类型
        return 0.75
    elif semi_distance == 7:
        # 纯五度：和声支撑但信息量较低
        return 0.45
    elif semi_distance in (5, 6):
        # 纯四度 / 增四度：中优先级
        return 0.55
    elif semi_distance in (8, 9):
        # 小六度 / 大六度：中优先级
        return 0.50
    elif semi_distance in (1, 2):
        # 小二度 / 大二度：低优先级（密集排列）
        return 0.30
    else:
        # 其他音程
        return 0.40


def compute_global_attention(
    pitch: int,
    time_index: int,
    orig_grouped_pitches: dict[int, list[int]],
    orig_grouped_durations: dict[int, list[float]],
    quantize_beat: float,
    pos_dim: int = 32,
    window_radius: int = 80,
) -> float:
    """
    计算候选音符的全局交叉注意力分数。

    在全曲范围内，对原始 MIDI 的每个时间片的每个音符，按以下因子加权求和：
        attention = Σ_t Σ_q  temporal_prox(t) * pitch_similarity(p, q)
                           * density_weight(|notes[t]|) * duration_weight(dur(q))

    分数越高，说明该候选音符在原始 MIDI 中有大量"支持证据"——
    即许多音高相近、时间邻近、密集段落中的长音符暗示这个音应该被保留。

    :param pitch: 候选音符的 MIDI 音高
    :param time_index: 候选音符所在的时间片索引
    :param orig_grouped_pitches: 原始 MIDI 的 {时间片索引: [音高列表]}
    :param orig_grouped_durations: 原始 MIDI 的 {时间片索引: [时长列表]}
    :param quantize_beat: 量化步长（拍）
    :param pos_dim: 位置编码维度（默认 32）
    :param window_radius: 时间片窗口半径，超出此范围 temporal_proximity 截断为 0
    :return: 全局交叉注意力分数，范围 [0, N]（N 为有效窗口内音符总数）
    """
    current_beat = time_index * quantize_beat
    score = 0.0

    # 迭代时间窗口内的所有时间片
    ti_min = time_index - window_radius
    ti_max = time_index + window_radius
    for ti in range(ti_min, ti_max + 1):
        if ti not in orig_grouped_pitches:
            continue

        orig_pitches = orig_grouped_pitches[ti]
        if not orig_pitches:
            continue

        orig_durations = orig_grouped_durations.get(ti, [])
        note_count = len(orig_pitches)

        # 时间邻近度（位置编码内积）
        neighbor_beat = ti * quantize_beat
        t_prox = temporal_proximity(
            beat_a=current_beat,
            beat_b=neighbor_beat,
            dim=pos_dim,
        )
        if t_prox <= 1e-6:
            continue

        # 密度权重
        dens_w = _density_weight(note_count)

        # 对该时间片的每个音符累加
        for idx, q in enumerate(orig_pitches):
            # 音高相似度
            pitch_sim = _pitch_similarity(pitch, q)

            # 时长权重
            dur_w = 0.5  # 默认中等权重
            if idx < len(orig_durations):
                dur_w = _duration_weight(orig_durations[idx])

            # 累加
            score += t_prox * pitch_sim * dens_w * dur_w

    return score


def compute_segment_attention(
    pitch: int,
    time_index: int,
    seg_indices: list[int],
    orig_grouped_pitches: dict[int, list[int]],
    orig_grouped_durations: dict[int, list[float]],
    quantize_beat: float,
    pos_dim: int = 32,
) -> float:
    """
    计算候选音符的段内交叉注意力分数。

    与全局注意力的计算公式相同，但：
        1. 求和范围限制在当前段内（seg_indices）
        2. 额外注入和声上下文权重——候选音符在原和弦中的功能角色影响其重要性
        3. 不做 window_radius 截断（段内全部参与）

    段内注意力回答："这个音符在本乐句的和声中有多重要？"

    :param pitch: 候选音符的 MIDI 音高
    :param time_index: 候选音符所在的时间片索引
    :param seg_indices: 当前段内所有时间片索引列表
    :param orig_grouped_pitches: 原始 MIDI 的 {时间片索引: [音高列表]}
    :param orig_grouped_durations: 原始 MIDI 的 {时间片索引: [时长列表]}
    :param quantize_beat: 量化步长（拍）
    :param pos_dim: 位置编码维度（默认 32）
    :return: 段内交叉注意力分数
    """
    current_beat = time_index * quantize_beat
    score = 0.0

    for ti in seg_indices:
        if ti not in orig_grouped_pitches:
            continue

        orig_pitches = orig_grouped_pitches[ti]
        if not orig_pitches:
            continue

        orig_durations = orig_grouped_durations.get(ti, [])

        # 时间邻近度
        neighbor_beat = ti * quantize_beat
        t_prox = temporal_proximity(
            beat_a=current_beat,
            beat_b=neighbor_beat,
            dim=pos_dim,
        )
        if t_prox <= 1e-6:
            continue

        # 对每个原始音符累加
        for idx, q in enumerate(orig_pitches):
            pitch_sim = _pitch_similarity(pitch, q)
            harmonic_w = _harmonic_context_score(pitch, orig_pitches)

            dur_w = 0.5
            if idx < len(orig_durations):
                dur_w = _duration_weight(orig_durations[idx])

            # 段内额外乘上和声上下文权重
            score += t_prox * pitch_sim * harmonic_w * dur_w

    return score
