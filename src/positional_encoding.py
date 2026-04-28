"""
模块名称：positional_encoding
功能描述：
    提供正弦位置编码（Sinusoidal Positional Encoding）及其时间邻近度计算。
    将时间片对应的绝对拍点值映射为固定维度向量，使不同时间片的编码向量内积
    自然反映它们在音乐时间上的邻近程度，无需手动设定时间窗口宽度。

主要组件：
    - compute_positional_encoding: 计算单个拍点值的位置编码向量
    - temporal_proximity: 计算两个拍点值之间的时间邻近度

依赖说明：
    - math: 三角函数计算
    - 无外部依赖

作者：JucieOvo
创建日期：2026-04-28
"""

import math


def compute_positional_encoding(beat: float, dim: int = 32) -> list[float]:
    """
    计算给定拍点值的正弦位置编码向量。

    公式（与原始 Transformer 一致，用 beat 替代 token index）：
        PE(beat, 2i)   = sin(beat / tau^(2i / dim))
        PE(beat, 2i+1) = cos(beat / tau^(2i / dim))

    其中 tau = 10000 为波长基数，dim 为编码维度。
    低频维度（小 i）波长长，对远距离时间差敏感；
    高频维度（大 i）波长短，对近距离时间差敏感。
    多频率分量自动形成多尺度时间视野。

    :param beat: 绝对拍点值 = 时间片索引 * quantize_beat
    :param dim: 编码维度，必须为偶数（默认 32）
    :return: 长度为 dim 的位置编码向量
    :raises ValueError: 当 dim 为奇数或 beat 为负数时触发
    """
    if dim % 2 != 0:
        raise ValueError(f"编码维度必须为偶数，当前值为 {dim}")
    if beat < 0:
        raise ValueError(f"拍点值不能为负数，当前值为 {beat}")

    tau = 10000.0
    encoding: list[float] = []
    for i in range(dim // 2):
        # 计算第 i 个频率分量的角度
        angle = beat / (tau ** (2.0 * i / dim))
        encoding.append(math.sin(angle))
        encoding.append(math.cos(angle))
    return encoding


def temporal_proximity(beat_a: float, beat_b: float, dim: int = 32) -> float:
    """
    计算两个拍点值之间的时间邻近度。

    通过两个位置编码向量的内积除以维度得到归一化邻近度。
    同拍点邻近度 ≈ 1.0，拍差越大邻近度越趋近于 0。

    性质：
        - 对称: proximity(a, b) = proximity(b, a)
        - 有界: proximity ∈ [0, 1]（经 ReLU 截断保证非负）
        - 多尺度: 低频维度捕获长程依赖，高频维度捕获短程邻接

    :param beat_a: 拍点值 A
    :param beat_b: 拍点值 B
    :param dim: 编码维度（默认 32）
    :return: 归一化时间邻近度，范围 [0, 1]
    """
    if beat_a == beat_b:
        return 1.0

    pe_a = compute_positional_encoding(beat=beat_a, dim=dim)
    pe_b = compute_positional_encoding(beat=beat_b, dim=dim)

    # 内积 / dim 归一化，ReLU 截断确保非负
    dot_product = sum(a * b for a, b in zip(pe_a, pe_b))
    normalized = dot_product / dim

    # 对于较大的时间差，内积可能略为负值，截断到 0
    return max(0.0, normalized)
