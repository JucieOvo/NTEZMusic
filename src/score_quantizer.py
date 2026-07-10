"""
模块名称：score_quantizer
功能描述：
    将清洗后的 MIDI 音符事件量化到五线谱节拍网格。

主要组件：
    - ScoreQuantizer: 负责起音、时值、谱表和声部的第一阶段确定性量化。

依赖说明：
    - math: 用于小节编号计算。
    - score_model: 复用乐谱合规化数据模型。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

import math

from score_model import BeatGrid, CanonicalNoteEvent, QuantizedNoteEvent, QuantizedScore, ScoreRegularizationConfig


class ScoreQuantizer:
    """
    乐谱量化器。

    职责：
        将清洗后的浮点拍点音符映射到固定量化网格，并补充 staff 与 voice 元数据。
    """

    def quantize(
        self,
        notes: tuple[CanonicalNoteEvent, ...],
        beat_grid: BeatGrid,
        config: ScoreRegularizationConfig,
    ) -> QuantizedScore:
        """
        执行确定性量化。

        :param notes: 清洗后的音符事件
        :param beat_grid: 节拍网格
        :param config: 乐谱合规化配置
        :return: 量化乐谱
        :raises ValueError: 当没有可量化音符时触发
        """
        if not notes:
            raise ValueError("无法量化空音符序列")
        quantized_notes = tuple(
            sorted(
                (self._quantize_note(note=note, beat_grid=beat_grid, config=config) for note in notes),
                key=lambda item: (item.start_beat, item.pitch),
            )
        )
        return QuantizedScore(
            notes=quantized_notes,
            beat_grid=beat_grid,
            time_signature=config.time_signature,
            key_signature_sharps=config.key_signature_sharps,
        )

    def _quantize_note(
        self,
        note: CanonicalNoteEvent,
        beat_grid: BeatGrid,
        config: ScoreRegularizationConfig,
    ) -> QuantizedNoteEvent:
        """
        量化单个音符。

        :param note: 清洗后的音符
        :param beat_grid: 节拍网格
        :param config: 乐谱合规化配置
        :return: 量化音符
        """
        quantized_start = self._round_to_grid(value=note.start_beat, unit=config.quantize_beat)
        quantized_duration = max(
            config.quantize_beat,
            self._round_to_grid(value=note.duration_beats, unit=config.quantize_beat),
        )
        max_duration = max(config.quantize_beat, beat_grid.total_beats - quantized_start)
        safe_duration = min(quantized_duration, max_duration)
        staff_id = 1 if note.pitch >= 60 else 2
        voice_id = "RH-1" if staff_id == 1 else "LH-1"
        bar_index = int(math.floor(quantized_start / beat_grid.beats_per_bar))
        return QuantizedNoteEvent(
            note_id=note.note_id,
            pitch=note.pitch,
            start_beat=quantized_start,
            duration_beats=safe_duration,
            bar_index=bar_index,
            staff_id=staff_id,
            voice_id=voice_id,
            source_note_ids=note.source_note_ids,
        )

    def _round_to_grid(self, value: float, unit: float) -> float:
        """
        将浮点拍点对齐到量化网格。

        :param value: 原始拍点值
        :param unit: 量化单位
        :return: 量化后的拍点值
        """
        return round(value / unit) * unit
