"""
模块名称：score_semantic_analyzer
功能描述：
    为乐谱语义感知缩编第二阶段提供基础确定性乐理分析。

主要组件：
    - ScoreSemanticAnalyzer: 识别每个时间片的主旋律、低音骨架与基础和弦角色。

依赖说明：
    - collections: 用于按起音拍点分组音符。
    - score_model: 使用量化乐谱与语义模型数据结构。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from collections import defaultdict

from score_model import (
    BasicChordSemantic,
    BasicPhraseBoundary,
    NoteSemanticRole,
    QuantizedNoteEvent,
    QuantizedScore,
    ScoreRegularizationConfig,
    ScoreSemanticModel,
)


class ScoreSemanticAnalyzer:
    """
    基础乐谱语义分析器。

    职责：
        在不调用 LLM 的前提下，用确定性规则为每个量化音符标记基础乐理角色。
    """

    def analyze(self, quantized_score: QuantizedScore, config: ScoreRegularizationConfig) -> ScoreSemanticModel:
        """
        分析量化乐谱的基础语义。

        :param quantized_score: 量化乐谱
        :param config: 乐谱合规化配置
        :return: 基础乐谱语义模型
        :raises ValueError: 当量化乐谱为空时触发
        """
        if not quantized_score.notes:
            raise ValueError("无法分析空量化乐谱")
        grouped_notes = self._group_notes_by_start(notes=quantized_score.notes)
        note_semantics: list[NoteSemanticRole] = []
        chord_semantics: list[BasicChordSemantic] = []
        for start_beat, notes in grouped_notes.items():
            sorted_notes = tuple(sorted(notes, key=lambda item: item.pitch))
            root_pitch = sorted_notes[0].pitch
            melody_pitch = sorted_notes[-1].pitch
            root_pitch_class = root_pitch % 12
            chord_semantics.append(
                BasicChordSemantic(
                    start_beat=start_beat,
                    root_pitch_class=root_pitch_class,
                    pitch_classes=tuple(sorted({note.pitch % 12 for note in sorted_notes})),
                    note_ids=tuple(note.note_id for note in sorted_notes),
                )
            )
            for note in sorted_notes:
                is_melody = note.pitch == melody_pitch
                is_bass_anchor = note.pitch == root_pitch
                chord_role = self._classify_chord_role(pitch=note.pitch, root_pitch_class=root_pitch_class)
                note_semantics.append(
                    NoteSemanticRole(
                        note_id=note.note_id,
                        pitch=note.pitch,
                        start_beat=note.start_beat,
                        duration_beats=note.duration_beats,
                        is_melody=is_melody,
                        is_bass_anchor=is_bass_anchor,
                        chord_role=chord_role,
                        keep_score=self._score_note(
                            is_melody=is_melody,
                            is_bass_anchor=is_bass_anchor,
                            chord_role=chord_role,
                            duration_beats=note.duration_beats,
                        ),
                    )
                )
        phrase_boundaries = (
            BasicPhraseBoundary(boundary_beat=0.0, boundary_type="start", confidence=1.0),
            BasicPhraseBoundary(
                boundary_beat=quantized_score.beat_grid.total_beats,
                boundary_type="end",
                confidence=1.0,
            ),
        )
        return ScoreSemanticModel(
            quantized_score=quantized_score,
            key_signature_sharps=config.key_signature_sharps,
            note_semantics=tuple(sorted(note_semantics, key=lambda item: (item.start_beat, item.pitch))),
            chord_semantics=tuple(sorted(chord_semantics, key=lambda item: item.start_beat)),
            phrase_boundaries=phrase_boundaries,
        )

    def _group_notes_by_start(self, notes: tuple[QuantizedNoteEvent, ...]) -> dict[float, tuple[QuantizedNoteEvent, ...]]:
        """
        按起音拍点分组音符。

        :param notes: 量化音符
        :return: 起音拍点到音符组的映射
        """
        grouped: dict[float, list[QuantizedNoteEvent]] = defaultdict(list)
        for note in notes:
            grouped[note.start_beat].append(note)
        return {start: tuple(items) for start, items in grouped.items()}

    def _classify_chord_role(self, pitch: int, root_pitch_class: int) -> str:
        """
        基于相对根音音程识别基础和弦角色。

        :param pitch: MIDI 音高
        :param root_pitch_class: 根音音级
        :return: 和弦角色标签
        """
        interval = (pitch % 12 - root_pitch_class) % 12
        if interval == 0:
            return "root"
        if interval in {3, 4}:
            return "third"
        if interval == 7:
            return "fifth"
        if interval in {10, 11}:
            return "seventh"
        if interval in {2, 5, 6, 8, 9}:
            return "color"
        return "non_chord"

    def _score_note(self, is_melody: bool, is_bass_anchor: bool, chord_role: str, duration_beats: float) -> float:
        """
        计算基础保留分。

        :param is_melody: 是否为主旋律
        :param is_bass_anchor: 是否为低音骨架
        :param chord_role: 和弦角色
        :param duration_beats: 持续拍数
        :return: 保留分
        """
        role_scores = {
            "root": 1.0,
            "third": 0.9,
            "seventh": 0.85,
            "fifth": 0.45,
            "color": 0.6,
            "non_chord": 0.25,
        }
        score = role_scores.get(chord_role, 0.25)
        if is_melody:
            score += 1.0
        if is_bass_anchor:
            score += 0.9
        score += min(duration_beats, 4.0) * 0.05
        return score
