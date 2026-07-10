"""
模块名称：beat_grid_estimator
功能描述：
    根据清洗后的 MIDI 音符事件构建第一阶段 MVP 使用的确定性节拍与小节网格。

主要组件：
    - BeatGridEstimator: 依据配置拍号和 BPM 生成覆盖全曲的 BeatGrid。

依赖说明：
    - math: 用于计算完整小节数量。
    - score_model: 复用 CanonicalNoteEvent、BeatGrid 与 ScoreRegularizationConfig。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

import math

from score_model import BeatGrid, CanonicalNoteEvent, ScoreRegularizationConfig


class BeatGridEstimator:
    """
    节拍网格估计器。

    职责：
        第一阶段先采用显式配置的 BPM 与拍号，生成确定性小节网格，后续版本再扩展候选拍号评分。
    """

    def estimate(self, notes: tuple[CanonicalNoteEvent, ...], config: ScoreRegularizationConfig) -> BeatGrid:
        """
        构建覆盖全部音符的节拍网格。

        :param notes: 清洗后的音符事件
        :param config: 乐谱合规化配置
        :return: 节拍网格
        :raises ValueError: 当输入音符为空或拍号非法时触发
        """
        if not notes:
            raise ValueError("无法根据空音符序列估计节拍网格")
        beats_per_bar = self._parse_beats_per_bar(time_signature=config.time_signature)
        max_end_beat = max(note.end_beat for note in notes)
        bar_count = max(1, math.ceil(max_end_beat / beats_per_bar))
        total_beats = bar_count * beats_per_bar
        grid_count = int(round(total_beats / config.quantize_beat))
        beat_positions = tuple(round(index * config.quantize_beat, 10) for index in range(grid_count + 1))
        return BeatGrid(
            bpm=config.bpm,
            time_signature=config.time_signature,
            beats_per_bar=beats_per_bar,
            total_beats=total_beats,
            bar_count=bar_count,
            beat_positions=beat_positions,
            confidence=1.0,
        )

    def _parse_beats_per_bar(self, time_signature: str) -> float:
        """
        从拍号文本解析每小节拍数。

        :param time_signature: 拍号文本，例如 4/4
        :return: 以四分音符为一拍时的小节容量
        :raises ValueError: 当拍号格式非法时触发
        """
        try:
            numerator_text, denominator_text = time_signature.split("/", maxsplit=1)
            numerator = int(numerator_text)
            denominator = int(denominator_text)
        except ValueError as exc:
            raise ValueError(f"拍号格式非法: {time_signature}") from exc
        if numerator <= 0 or denominator <= 0:
            raise ValueError(f"拍号必须为正数: {time_signature}")
        return numerator * (4.0 / denominator)
