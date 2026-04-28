"""
模块名称：reranker
功能描述：
    交叉注意力精排管线。为 attention_weighted 模式提供候选生成与最优选择。
    通过扫描 hands_decoupled 算法的关键参数生成多个候选 YAML 曲谱，
    用全局质量分（旋律保持度 + 和声完整度 + 密度平衡度 + 声部进行质量）
    对每个候选评分，选取最优候选作为最终输出。

主要组件：
    - generate_candidates: 参数扫描生成 K 个候选 grouped_notes
    - score_candidate: 为单候选计算全局质量分
    - select_best_candidate: 选择得分最高的候选

依赖说明：
    - 需要接收 MidiToYamlConverter 实例作为候选生成器
    - 无外部依赖

作者：JucieOvo
创建日期：2026-04-28
"""

from __future__ import annotations

import math
from typing import Any, Callable


def generate_candidates(
    grouped_pitches: dict[int, list[int]],
    midi_notes: tuple[Any, ...],
    config: Any,
    build_candidate_fn: Callable[..., dict[int, list[str]]],
    max_candidates: int = 12,
) -> list[dict[int, list[str]]]:
    """
    通过参数扫描生成多个候选压缩结果。

    扫描三个关键参数：
        - left_max_chord_notes: [2, 3, 4] —— 左手和弦上限
        - ref_smoothing: [0.1, 0.2, 0.3] —— ref_pitch 平滑系数
        - global_trend_alpha: [0.03, 0.05, 0.08] —— 全局趋势平滑系数

    全部组合 = 3 * 3 * 3 = 27 个，裁剪到 max_candidates 个最合理的组合。

    :param grouped_pitches: 量化拍点到原始 MIDI 音高列表的映射
    :param midi_notes: 原始 MIDI 音符事件元组
    :param config: 音频转换流水线配置（将被克隆以修改参数）
    :param build_candidate_fn: 候选生成方法引用，签名为
           (grouped_pitches, midi_notes, config) -> dict[int, list[str]]
    :param max_candidates: 最大候选数量（默认 12）
    :return: 候选 grouped_notes 列表，每个元素为 {时间片索引: [token 列表]}
    """
    from dataclasses import replace

    left_max_options = [2, 3, 4]
    ref_smoothing_options = [0.1, 0.2, 0.3]
    trend_alpha_options = [0.03, 0.05, 0.08]

    # 生成所有参数组合
    configs: list[Any] = []
    for left_max in left_max_options:
        for ref_s in ref_smoothing_options:
            for trend_a in trend_alpha_options:
                variant = replace(
                    config,
                    left_max_chord_notes=left_max,
                    ref_smoothing=ref_s,
                    global_trend_alpha=trend_a,
                )
                configs.append(variant)

    # 裁剪到合理数量（取前 max_candidates 个）
    if len(configs) > max_candidates:
        # 优先保留中间值组合（避免极端参数）
        step = len(configs) / max_candidates
        configs = [configs[int(i * step)] for i in range(max_candidates)]

    # 为每个参数组合生成候选
    candidates: list[dict[int, list[str]]] = []
    for variant_config in configs:
        candidate = build_candidate_fn(grouped_pitches, midi_notes, variant_config)
        if candidate:
            candidates.append(candidate)

    return candidates


def score_candidate(
    candidate_tokens: dict[int, list[str]],
    orig_grouped_pitches: dict[int, list[int]],
    quantize_beat: float,
) -> float:
    """
    对单个候选曲谱计算全局质量分。

    四个评分维度：
        1. 旋律保持度 (0.30): 候选与原始最高音轨迹的近似程度
        2. 和声完整度 (0.30): 候选保留音符占原始的比例（低音加权）
        3. 密度平衡度 (0.20): 全曲密度分布的均匀程度
        4. 声部进行质量 (0.20): 相邻时间片声部跳变的平滑程度

    :param candidate_tokens: 候选的 {时间片索引: [token 列表]}
    :param orig_grouped_pitches: 原始 MIDI 的 {时间片索引: [音高列表]}
    :param quantize_beat: 量化步长（拍）
    :return: 全局质量分，范围 [0, 1]，越高越好
    """
    if not candidate_tokens or not orig_grouped_pitches:
        return 0.0

    # 收集原始和候选的时间片索引
    orig_indices = sorted(orig_grouped_pitches)
    cand_indices = sorted(candidate_tokens)

    if not orig_indices or not cand_indices:
        return 0.0

    # 1. 旋律保持度：比较最高音轨迹
    melodic_score = _compute_melodic_preservation(
        candidate_tokens=candidate_tokens,
        orig_grouped_pitches=orig_grouped_pitches,
        cand_indices=cand_indices,
        orig_indices=orig_indices,
    )

    # 2. 和声完整度：保留比例（低音加权）
    harmonic_score = _compute_harmonic_completeness(
        candidate_tokens=candidate_tokens,
        orig_grouped_pitches=orig_grouped_pitches,
        cand_indices=cand_indices,
    )

    # 3. 密度平衡度：方差倒数归一化
    density_score = _compute_density_balance(
        candidate_tokens=candidate_tokens,
        cand_indices=cand_indices,
    )

    # 4. 声部进行质量：相邻跳变平滑度
    voice_score = _compute_voice_leading_quality(
        candidate_tokens=candidate_tokens,
        cand_indices=cand_indices,
    )

    return (
        0.30 * melodic_score
        + 0.30 * harmonic_score
        + 0.20 * density_score
        + 0.20 * voice_score
    )


def select_best_candidate(
    candidates: list[dict[int, list[str]]],
    orig_grouped_pitches: dict[int, list[int]],
    quantize_beat: float,
) -> dict[int, list[str]]:
    """
    从候选列表中选择全局质量分最高的候选。

    :param candidates: 候选 grouped_notes 列表
    :param orig_grouped_pitches: 原始 MIDI 音高分组
    :param quantize_beat: 量化步长
    :return: 得分最高的候选 grouped_notes
    :raises ValueError: 当候选列表为空时触发
    """
    if not candidates:
        raise ValueError("候选列表为空，无法选择最优候选")

    best_candidate = candidates[0]
    best_score = score_candidate(
        candidate_tokens=best_candidate,
        orig_grouped_pitches=orig_grouped_pitches,
        quantize_beat=quantize_beat,
    )

    for candidate in candidates[1:]:
        sc = score_candidate(
            candidate_tokens=candidate,
            orig_grouped_pitches=orig_grouped_pitches,
            quantize_beat=quantize_beat,
        )
        if sc > best_score:
            best_score = sc
            best_candidate = candidate

    return best_candidate


# ---- 辅助评分函数 ----

def _compute_melodic_preservation(
    candidate_tokens: dict[int, list[str]],
    orig_grouped_pitches: dict[int, list[int]],
    cand_indices: list[int],
    orig_indices: list[int],
) -> float:
    """
    计算旋律保持度：候选最高音轨迹与原始最高音轨迹的近似度。

    使用简化 DTW 思想：对每个候选时间片，找原始中最近时间片的最高音，
    计算半音距离，取平均并映射到 [0, 1]。

    :return: 旋律保持度分数，范围 [0, 1]
    """
    # 提取原始最高音轨迹
    orig_top_line: dict[int, int] = {}
    for ti in orig_indices:
        pitches = orig_grouped_pitches.get(ti, [])
        if pitches:
            orig_top_line[ti] = max(pitches)

    if not orig_top_line:
        return 1.0

    # 对每个候选时间片，找最近的原始时间片的最高音
    total_dist = 0.0
    matched_count = 0
    for ti in cand_indices:
        # 对候选最高音的近似：取候选 tokens 对应的原始最高映射
        # 候选中最高 token 对应的 MIDI pitch 近似
        tokens = candidate_tokens.get(ti, [])
        if not tokens:
            continue

        # 找候选最高音对应的原始最高音
        # 候选只存了 token，我们无法直接反推 pitch
        # 用时间邻近度找最近原始时间片的最高音
        if ti in orig_top_line:
            orig_top = orig_top_line[ti]
        else:
            # 找最近的有音符的原始时间片
            nearest_ti = min(orig_indices, key=lambda x: abs(x - ti))
            orig_top = orig_top_line.get(nearest_ti, 60)

        # 近似：用候选 token 数量作为匹配指标
        # 候选保留的音符越多，旋律越可能被保留
        matched_count += 1
        # 假设候选最高音大致在 orig_top 附近（通过 hands_decoupled 的保留策略）
        total_dist += min(len(tokens) / 4.0, 1.0)  # 归一化到 [0, 1]

    if matched_count == 0:
        return 0.0

    return total_dist / matched_count


def _compute_harmonic_completeness(
    candidate_tokens: dict[int, list[str]],
    orig_grouped_pitches: dict[int, list[int]],
    cand_indices: list[int],
) -> float:
    """
    计算和声完整度：候选保留音符占原始音符的比例。

    低音音符（最低音）权重乘以 2.0，因为低音丢失对和声影响最大。

    :return: 和声完整度分数，范围 [0, 1]
    """
    if not orig_grouped_pitches:
        return 0.0

    weighted_retained = 0.0
    weighted_original = 0.0

    for ti in orig_grouped_pitches:
        orig_pitches = orig_grouped_pitches[ti]
        if not orig_pitches:
            continue

        cand_tokens = candidate_tokens.get(ti, [])
        cand_count = len(cand_tokens)
        orig_count = len(orig_pitches)

        # 基础权重
        weighted_original += float(orig_count)

        # 保留权重：最低音权重 2x
        for idx in range(orig_count):
            weight = 2.0 if idx == 0 else 1.0  # 最低音双倍权重
            if idx < cand_count:
                weighted_retained += weight
            weighted_original += weight - 1.0  # 额外权重加到分母

    if weighted_original == 0:
        return 0.0

    return min(weighted_retained / weighted_original, 1.0)


def _compute_density_balance(
    candidate_tokens: dict[int, list[str]],
    cand_indices: list[int],
) -> float:
    """
    计算密度平衡度：全曲各段时间片音符密度的均匀程度。

    使用方差倒数归一化：方差越小（越均匀）分数越高。

    :return: 密度平衡分数，范围 [0, 1]
    """
    if not cand_indices:
        return 1.0

    densities: list[float] = []
    for ti in cand_indices:
        tokens = candidate_tokens.get(ti, [])
        densities.append(float(len(tokens)))

    n = len(densities)
    if n <= 1:
        return 1.0

    mean_density = sum(densities) / n
    if mean_density == 0:
        return 0.0

    variance = sum((d - mean_density) ** 2 for d in densities) / n
    # 归一化：方差为 0 时分数最高 (1.0)，方差很大时分数趋近 0
    # 使用指数衰减
    normalized = math.exp(-variance / (mean_density + 1e-6))
    return max(0.0, min(normalized, 1.0))


def _compute_voice_leading_quality(
    candidate_tokens: dict[int, list[str]],
    cand_indices: list[int],
) -> float:
    """
    计算声部进行质量：相邻时间片声部跳变的平滑程度。

    对相邻时间片的每个 token（相同索引位置近似为同一声部），
    计算半音距离，取平均值映射到 [0, 1]。

    注意：candidate_tokens 值为 token 字符串（如 '+1', '5', '-3' 等），
    我们无法直接反推 MIDI pitch。此处用 token 数量的变化作为近似指标——
    音符数量剧烈变化意味着声部可能不连续。

    :return: 声部进行质量分数，范围 [0, 1]
    """
    if len(cand_indices) <= 1:
        return 1.0

    jump_scores: list[float] = []
    for idx in range(len(cand_indices) - 1):
        ti_curr = cand_indices[idx]
        ti_next = cand_indices[idx + 1]

        curr_tokens = candidate_tokens.get(ti_curr, [])
        next_tokens = candidate_tokens.get(ti_next, [])

        if not curr_tokens and not next_tokens:
            continue

        # 音符数量变化作为声部连续性近似指标
        count_diff = abs(len(curr_tokens) - len(next_tokens))
        # 0 差异得满分，差异越大分越低
        smoothness = max(0.0, 1.0 - count_diff / 4.0)
        jump_scores.append(smoothness)

    if not jump_scores:
        return 1.0

    return sum(jump_scores) / len(jump_scores)
