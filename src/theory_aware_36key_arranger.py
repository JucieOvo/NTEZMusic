"""
模块名称：theory_aware_36key_arranger
功能描述：
    基于基础乐理语义将量化乐谱缩编为《异环》36 键可执行 YAML 事件。

主要组件：
    - TheoryAware36KeyArranger: 执行八度折叠、密度控制、token 映射与输出校验。

依赖说明：
    - collections: 用于按起音拍点分组。
    - score_model: 使用语义模型与缩编结果数据结构。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from collections import defaultdict

from score_model import (
    ArrangedNoteEvent,
    ArrangementReport,
    ArrangementValidationReport,
    ScoreRegularizationConfig,
    ScoreSemanticModel,
    TheoryArrangementResult,
)


NATURAL_PITCH_CLASSES = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
SHARP_PITCH_CLASSES = {1: "#1", 3: "b3", 6: "#4", 8: "#5", 10: "b7"}
ZONE_NAMES_BY_OCTAVE = {3: "low", 4: "middle", 5: "high"}
ZONE_PREFIX_BY_NAME = {"low": "-", "middle": "", "high": "+"}


class TheoryAware36KeyArranger:
    """
    36 键乐理缩编器。

    职责：
        根据 ScoreSemanticAnalyzer 给出的基础保留分，优先保留主旋律、低音骨架、根音、三度与七度，
        并将所有输出音符限制到 C3-B5。
    """

    def arrange(
        self,
        semantic_model: ScoreSemanticModel,
        config: ScoreRegularizationConfig,
        max_chord_notes: int,
        allow_accidentals: bool,
    ) -> TheoryArrangementResult:
        """
        执行 36 键乐理缩编。

        :param semantic_model: 基础乐谱语义模型
        :param config: 乐谱合规化配置
        :param max_chord_notes: 单时间片最大输出音符数
        :param allow_accidentals: 是否允许半音 token
        :return: 36 键缩编结果
        :raises ValueError: 当参数非法或 token 无法映射时触发
        """
        if max_chord_notes <= 0:
            raise ValueError("max_chord_notes 必须大于 0")
        semantics_by_id = {item.note_id: item for item in semantic_model.note_semantics}
        arranged_candidates: list[ArrangedNoteEvent] = []
        octave_moved_count = 0
        for note in semantic_model.quantized_score.notes:
            semantic = semantics_by_id[note.note_id]
            mapped_pitch = self._normalize_pitch_to_36_key(pitch=note.pitch)
            if mapped_pitch != note.pitch:
                octave_moved_count += 1
            arranged_candidates.append(
                ArrangedNoteEvent(
                    note_id=note.note_id,
                    original_pitch=note.pitch,
                    mapped_pitch=mapped_pitch,
                    token=self._pitch_to_token(pitch=mapped_pitch, allow_accidentals=allow_accidentals),
                    start_beat=note.start_beat,
                    duration_beats=note.duration_beats,
                    keep_score=semantic.keep_score,
                )
            )
        arranged_notes_by_start, clipped_count = self._limit_density_by_start(
            arranged_notes=tuple(arranged_candidates),
            max_chord_notes=max_chord_notes,
        )
        arranged_notes = tuple(
            note
            for start_beat in sorted(arranged_notes_by_start)
            for note in arranged_notes_by_start[start_beat]
        )
        score_events = self._build_score_events(arranged_notes_by_start=arranged_notes_by_start)
        validation = self._validate_arrangement(
            arranged_notes_by_start=arranged_notes_by_start,
            max_chord_notes=max_chord_notes,
        )
        report = ArrangementReport(
            input_note_count=len(semantic_model.quantized_score.notes),
            output_note_count=len(arranged_notes),
            octave_moved_note_count=octave_moved_count,
            clipped_note_count=clipped_count,
        )
        return TheoryArrangementResult(
            arranged_notes=arranged_notes,
            arranged_notes_by_start=arranged_notes_by_start,
            score_events=score_events,
            report=report,
            validation=validation,
        )

    def _normalize_pitch_to_36_key(self, pitch: int) -> int:
        """
        通过八度迁移将 MIDI 音高移动到 C3-B5。

        :param pitch: 原始 MIDI 音高
        :return: 映射后的 MIDI 音高
        :raises ValueError: 当无法映射到 36 键范围时触发
        """
        mapped_pitch = pitch
        while mapped_pitch < 48:
            mapped_pitch += 12
        while mapped_pitch > 83:
            mapped_pitch -= 12
        if not 48 <= mapped_pitch <= 83:
            raise ValueError(f"MIDI 音高 {pitch} 无法映射到 C3-B5")
        return mapped_pitch

    def _pitch_to_token(self, pitch: int, allow_accidentals: bool) -> str:
        """
        将 36 键范围内的 MIDI 音高转换为现有 YAML token。

        :param pitch: MIDI 音高
        :param allow_accidentals: 是否允许半音 token
        :return: YAML token
        """
        octave = (pitch // 12) - 1
        pitch_class = pitch % 12
        zone_name = ZONE_NAMES_BY_OCTAVE.get(octave)
        if zone_name is None:
            raise ValueError(f"MIDI 音高 {pitch} 超出 C3-B5")
        natural_token = NATURAL_PITCH_CLASSES.get(pitch_class)
        if natural_token is not None:
            return f"{ZONE_PREFIX_BY_NAME[zone_name]}{natural_token}"
        accidental_token = SHARP_PITCH_CLASSES.get(pitch_class)
        if accidental_token is None:
            raise ValueError(f"MIDI 音高 {pitch} 无法映射为 YAML token")
        if not allow_accidentals:
            raise ValueError(f"MIDI 音高 {pitch} 需要半音 token，但当前禁止半音")
        return f"{ZONE_PREFIX_BY_NAME[zone_name]}{accidental_token}"

    def _limit_density_by_start(
        self,
        arranged_notes: tuple[ArrangedNoteEvent, ...],
        max_chord_notes: int,
    ) -> tuple[dict[float, tuple[ArrangedNoteEvent, ...]], int]:
        """
        按起音拍点控制和弦密度。

        :param arranged_notes: 候选缩编音符
        :param max_chord_notes: 最大和弦音数
        :return: 分组后的音符与被裁剪数量
        """
        grouped: dict[float, list[ArrangedNoteEvent]] = defaultdict(list)
        for note in arranged_notes:
            grouped[note.start_beat].append(note)
        clipped_count = 0
        limited: dict[float, tuple[ArrangedNoteEvent, ...]] = {}
        for start_beat, notes in grouped.items():
            deduplicated = self._deduplicate_token(notes=tuple(notes))
            ranked = tuple(sorted(deduplicated, key=lambda item: (-item.keep_score, item.mapped_pitch)))
            kept = tuple(sorted(ranked[:max_chord_notes], key=lambda item: item.mapped_pitch))
            clipped_count += max(0, len(notes) - len(kept))
            limited[start_beat] = kept
        return limited, clipped_count

    def _deduplicate_token(self, notes: tuple[ArrangedNoteEvent, ...]) -> tuple[ArrangedNoteEvent, ...]:
        """
        去除同一时间片映射到相同 token 的重复音。

        :param notes: 同起音候选音符
        :return: 每个 token 仅保留最高分音符后的结果
        """
        best_by_token: dict[str, ArrangedNoteEvent] = {}
        for note in sorted(notes, key=lambda item: (-item.keep_score, item.mapped_pitch)):
            if note.token not in best_by_token:
                best_by_token[note.token] = note
        return tuple(best_by_token.values())

    def _build_score_events(
        self,
        arranged_notes_by_start: dict[float, tuple[ArrangedNoteEvent, ...]],
    ) -> tuple[dict[str, int | float | str], ...]:
        """
        构建现有 YAML score 事件。

        :param arranged_notes_by_start: 按起音分组的缩编音符
        :return: YAML score 事件
        """
        score_events: list[dict[str, int | float | str]] = []
        last_start: float | None = None
        last_duration = 0.0
        for start_beat in sorted(arranged_notes_by_start):
            if last_start is not None:
                rest_beat = start_beat - last_start - last_duration
                if rest_beat > 1e-9:
                    score_events.append({"notes": "0", "beat": self._normalize_beat(rest_beat)})
            tokens = tuple(note.token for note in arranged_notes_by_start[start_beat])
            notes_text = tokens[0] if len(tokens) == 1 else f"[{' '.join(tokens)}]"
            event_duration = min(note.duration_beats for note in arranged_notes_by_start[start_beat])
            score_events.append({"notes": notes_text, "beat": self._normalize_beat(event_duration)})
            last_start = start_beat
            last_duration = event_duration
        return tuple(score_events)

    def _normalize_beat(self, beat: float) -> int | float:
        """
        规范化 beat 数值。

        :param beat: 原始 beat
        :return: 整数或六位小数浮点
        """
        if abs(beat - round(beat)) <= 1e-9:
            return int(round(beat))
        return round(beat, 6)

    def _validate_arrangement(
        self,
        arranged_notes_by_start: dict[float, tuple[ArrangedNoteEvent, ...]],
        max_chord_notes: int,
    ) -> ArrangementValidationReport:
        """
        校验 36 键缩编结果。

        :param arranged_notes_by_start: 按起音分组的缩编音符
        :param max_chord_notes: 最大和弦音数
        :return: 校验报告
        """
        out_of_range_count = 0
        duplicate_token_count = 0
        observed_max_chord_notes = 0
        for notes in arranged_notes_by_start.values():
            observed_max_chord_notes = max(observed_max_chord_notes, len(notes))
            tokens = [note.token for note in notes]
            duplicate_token_count += len(tokens) - len(set(tokens))
            out_of_range_count += sum(1 for note in notes if not 48 <= note.mapped_pitch <= 83)
        is_valid = (
            out_of_range_count == 0
            and duplicate_token_count == 0
            and observed_max_chord_notes <= max_chord_notes
        )
        return ArrangementValidationReport(
            is_valid=is_valid,
            out_of_range_note_count=out_of_range_count,
            max_chord_notes=observed_max_chord_notes,
            duplicate_token_count=duplicate_token_count,
        )
