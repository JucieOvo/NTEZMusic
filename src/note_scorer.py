"""
模块名称：note_scorer
功能描述：
    候选音符综合评分器。将和声功能优先级、全局交叉注意力、段内交叉注意力、
    声部进行平滑度四个维度加权求和，为每个候选音符生成统一的保留优先级分数。

主要组件：
    - NoteScoringContext: 评分所需的上下文数据容器
    - score_note: 对单音符计算综合评分
    - select_chord_notes: 从和弦候选中选择得分最高的音符

依赖说明：
    - cross_attention: 全局/段内交叉注意力计算
    - 无外部依赖

作者：JucieOvo
创建日期：2026-04-28
"""


class NoteScoringContext:
    """
    音符评分所需的全部上下文数据。

    职责：
        将分散的原始 MIDI 信息、段分割结果、前后时间片状态集中到
        一个不可变容器中，方便评分函数统一访问。

    属性：
        orig_grouped_pitches (dict[int, list[int]]): 原始 MIDI 的 {时间片索引: [音高]}
        orig_grouped_durations (dict[int, list[float]]): 原始 MIDI 的 {时间片索引: [时长]}
        quantize_beat (float): 量化步长（拍）
        seg_indices (list[int]): 当前段内时间片索引列表
        prev_chord_notes (list[int]): 前一时间片的已选音符音高（用于声部进行计算）
        next_orig_pitches (list[int]): 后一时间片的原始音符音高（用于声部进行计算）
        max_chord_notes (int): 最大和弦音符数
        chord_function_scores (dict[int, float]): 预计算的每个音符的和声功能优先级分数
        global_attention_cache (dict[int, float]): 预计算的每个音符的全局交叉注意力分数
        segment_attention_cache (dict[int, float]): 预计算的每个音符的段内交叉注意力分数
    """

    def __init__(
        self,
        orig_grouped_pitches: dict[int, list[int]],
        orig_grouped_durations: dict[int, list[float]],
        quantize_beat: float,
        seg_indices: list[int],
        prev_chord_notes: list[int],
        next_orig_pitches: list[int],
        max_chord_notes: int,
        velocities_ti: dict[int, int] | None = None,
    ) -> None:
        """
        初始化评分上下文。

        :param orig_grouped_pitches: 原始 MIDI 音高分组
        :param orig_grouped_durations: 原始 MIDI 时长分组
        :param quantize_beat: 量化步长
        :param seg_indices: 当前段内时间片索引
        :param prev_chord_notes: 前一时间片已选音符
        :param next_orig_pitches: 后一时间片原始音符
        :param max_chord_notes: 最大和弦音数
        :param velocities_ti: 当前时间片 {音高: velocity} 映射（交叉手应对）
        """
        self.orig_grouped_pitches = orig_grouped_pitches
        self.orig_grouped_durations = orig_grouped_durations
        self.quantize_beat = quantize_beat
        self.seg_indices = seg_indices
        self.prev_chord_notes = prev_chord_notes
        self.next_orig_pitches = next_orig_pitches
        self.max_chord_notes = max_chord_notes
        self.velocities_ti = velocities_ti if velocities_ti is not None else {}

        # 缓存容器，延迟填充
        self.chord_function_scores: dict[int, float] = {}
        self.global_attention_cache: dict[int, float] = {}
        self.segment_attention_cache: dict[int, float] = {}


def _compute_chord_function_score(pitch: int, all_pitches: list[int], velocity: int | None = None) -> float:
    """
    计算音符的和声功能优先级分数。

    基于 pitch class 音程距离：
        - 根音/最低音:        1.0（和声根基）
        - 三度音（3-4半音）:   0.85（决定和弦品质）
        - 七度音（10-11半音）: 0.75（决定和弦类型）
        - 纯四/增四（5-6）:    0.60
        - 小六/大六（8-9）:    0.55
        - 纯五度（7半音）:     0.45（信息量低）
        - 小二/大二（1-2）:    0.30（密集排列）
        - 八度重复:            0.10
        - 其他延伸音:          0.40

    velocity 加权: 高力度音符在交叉手场景下应优先保留（旋律特征），
    对 bass/根音之外的非八度音施加 velocity 向上修正，上限 +0.15。

    :param pitch: 待评估音高
    :param all_pitches: 同一时间片的全部候选音高
    :param velocity: MIDI velocity 0-127（可选）
    :return: 和声功能分数，范围 [0, 1]
    """
    if not all_pitches or len(all_pitches) <= 1:
        return 1.0

    bass = min(all_pitches)
    interval = pitch - bass
    semi_distance = interval % 12

    if pitch == bass:
        return 1.0

    if interval % 12 == 0:
        base = 0.10
    elif semi_distance in (3, 4):
        base = 0.85
    elif semi_distance in (10, 11):
        base = 0.75
    elif semi_distance in (5, 6):
        base = 0.60
    elif semi_distance in (8, 9):
        base = 0.55
    elif semi_distance == 7:
        base = 0.45
    elif semi_distance in (1, 2):
        base = 0.30
    else:
        base = 0.40

    # velocity 加权: 高力度音符向上修正（交叉手场景保护左手旋律）
    if velocity is not None and velocity > 0:
        base += (velocity / 127.0) * 0.15
    return min(base, 1.0)


def _compute_voice_leading_score(
    pitch: int,
    prev_chord_notes: list[int],
    next_orig_pitches: list[int],
) -> float:
    """
    计算音符的声部进行平滑度分数。

    同时考虑前一时间片和后一时间片：
        - 若与前后音符能形成级进（≤ 2 半音），得分高
        - 大跳则得分低
    取前后两个方向的平均值。

    :param pitch: 当前候选音符音高
    :param prev_chord_notes: 前一时间片的已选音符音高
    :param next_orig_pitches: 后一时间片的原始音符音高
    :return: 声部进行平滑度分数，范围 [0, 1]
    """
    if not prev_chord_notes and not next_orig_pitches:
        return 0.5

    scores: list[float] = []

    # 前向平滑度：与前一拍最近声部的最小半音距离
    if prev_chord_notes:
        min_dist_prev = min(abs(pitch - prev_p) for prev_p in prev_chord_notes)
        # 级进（≤2 半音）得高分，超过 12 半音得 0
        scores.append(max(0.0, 1.0 - min_dist_prev / 12.0))

    # 后向平滑度：与后一拍原始音符的最小半音距离
    if next_orig_pitches:
        min_dist_next = min(abs(pitch - next_p) for next_p in next_orig_pitches)
        scores.append(max(0.0, 1.0 - min_dist_next / 12.0))

    if not scores:
        return 0.5

    return sum(scores) / len(scores)


def score_note(
    pitch: int,
    time_index: int,
    all_pitches: list[int],
    context: NoteScoringContext,
    weights: tuple[float, float, float, float] = (0.30, 0.25, 0.25, 0.20),
) -> float:
    """
    计算单个候选音符的综合保留优先级分数。

    四个评分维度：
        1. 和声功能 (alpha):   基于 pitch class 音程的乐理先验
        2. 全局交叉注意力 (beta):  在全曲范围内衡量信息保存支持度
        3. 段内交叉注意力 (gamma): 在当前乐段内衡量和声功能重要性
        4. 声部进行 (delta):   与前后时间片的平滑连接度

    :param pitch: 候选音符的 MIDI 音高
    :param time_index: 候选音符所在时间片索引
    :param all_pitches: 该时间片全部候选音高
    :param context: 评分上下文
    :param weights: 四项权重 (alpha, beta, gamma, delta)，总和应为 1.0
    :return: 综合评分，范围 [0, 1]
    """
    alpha, beta, gamma, delta = weights

    # 维度 1: 和声功能优先级（含 velocity 加权）
    vel = context.velocities_ti.get(pitch)
    chord_func = _compute_chord_function_score(pitch, all_pitches, velocity=vel)

    # 维度 2: 全局交叉注意力（使用缓存）
    if pitch not in context.global_attention_cache:
        from cross_attention import compute_global_attention

        context.global_attention_cache[pitch] = compute_global_attention(
            pitch=pitch,
            time_index=time_index,
            orig_grouped_pitches=context.orig_grouped_pitches,
            orig_grouped_durations=context.orig_grouped_durations,
            quantize_beat=context.quantize_beat,
        )

    # 维度 3: 段内交叉注意力（使用缓存）
    if pitch not in context.segment_attention_cache:
        from cross_attention import compute_segment_attention

        context.segment_attention_cache[pitch] = compute_segment_attention(
            pitch=pitch,
            time_index=time_index,
            seg_indices=context.seg_indices,
            orig_grouped_pitches=context.orig_grouped_pitches,
            orig_grouped_durations=context.orig_grouped_durations,
            quantize_beat=context.quantize_beat,
        )

    # 维度 4: 声部进行平滑度
    voice_leading = _compute_voice_leading_score(
        pitch=pitch,
        prev_chord_notes=context.prev_chord_notes,
        next_orig_pitches=context.next_orig_pitches,
    )

    # 全局/段内注意力归一化
    # 取同时间片所有候选中的最大值做归一化参考（动态范围压缩）
    global_attn = context.global_attention_cache[pitch]
    segment_attn = context.segment_attention_cache[pitch]

    return (
        alpha * chord_func
        + beta * global_attn
        + gamma * segment_attn
        + delta * voice_leading
    )


def select_chord_notes(
    pitches: list[int],
    time_index: int,
    context: NoteScoringContext,
    max_notes: int,
    weights: tuple[float, float, float, float] = (0.30, 0.25, 0.25, 0.20),
) -> list[int]:
    """
    从和弦候选音符中选择得分最高的音符。

    对所有候选按综合评分降序排列，取前 max_notes 个。
    若音符数 ≤ max_notes，全保留。

    :param pitches: 该时间片的候选音高列表
    :param time_index: 当前时间片索引
    :param context: 评分上下文
    :param max_notes: 最大保留音符数
    :param weights: 四项评分权重
    :return: 选中的音高列表
    """
    if not pitches:
        return []
    if len(pitches) <= max_notes:
        return list(pitches)

    # 计算每个音符的综合评分
    scored: list[tuple[float, int]] = []
    for p in pitches:
        s = score_note(
            pitch=p,
            time_index=time_index,
            all_pitches=pitches,
            context=context,
            weights=weights,
        )
        scored.append((s, p))

    # 按评分降序排列
    scored.sort(key=lambda x: x[0], reverse=True)

    # 选取前 max_notes 个
    selected = [p for _, p in scored[:max_notes]]

    # 保持音高升序（便于后续 token 生成）
    selected.sort()
    return selected
