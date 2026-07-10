"""
模块名称：staff_notation_builder
功能描述：
    将量化乐谱事件构建为 music21 Score，并导出可回读的 MusicXML 文件。

主要组件：
    - StaffNotationBuilder: 构建双谱表五线谱对象并完成 MusicXML 导出回读。

依赖说明：
    - music21: 构建 Score、Part、Measure、Note、Chord、Rest 并导出 MusicXML。
    - score_model: 复用量化乐谱与构建结果数据模型。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from music21 import chord, converter, key, note, stream
from music21.stream.base import Score
from music21.meter.base import TimeSignature

from score_model import QuantizedNoteEvent, QuantizedScore, ScoreRegularizationConfig, StaffNotationBuildResult


class StaffNotationBuilder:
    """
    五线谱构建器。

    职责：
        将 QuantizedScore 转换为双谱表 music21 Score，导出 MusicXML 并立即回读验证文件可解析。
    """

    def build_and_export(
        self,
        quantized_score: QuantizedScore,
        config: ScoreRegularizationConfig,
        output_musicxml_path: Path,
    ) -> StaffNotationBuildResult:
        """
        构建并导出 MusicXML。

        :param quantized_score: 量化乐谱
        :param config: 乐谱合规化配置
        :param output_musicxml_path: MusicXML 输出路径
        :return: 构建结果
        """
        output_musicxml_path.parent.mkdir(parents=True, exist_ok=True)
        score = stream.Score(id="score_aware_theory_mvp")
        score.insert(0, self._build_part(quantized_score=quantized_score, config=config, staff_id=1, part_id="right_hand"))
        score.insert(0, self._build_part(quantized_score=quantized_score, config=config, staff_id=2, part_id="left_hand"))
        score.write("musicxml", fp=str(output_musicxml_path))
        reloaded_score = converter.parse(str(output_musicxml_path))
        if not isinstance(reloaded_score, Score):
            raise TypeError(f"MusicXML 回读结果不是 Score: {type(reloaded_score).__name__}")
        return StaffNotationBuildResult(
            score=score,
            reloaded_score=reloaded_score,
            musicxml_path=output_musicxml_path,
            quantized_score=quantized_score,
            measure_count=quantized_score.beat_grid.bar_count,
            note_count=len(quantized_score.notes),
        )

    def _build_part(
        self,
        quantized_score: QuantizedScore,
        config: ScoreRegularizationConfig,
        staff_id: int,
        part_id: str,
    ) -> stream.Part:
        """
        构建单个谱表对应的 Part。

        :param quantized_score: 量化乐谱
        :param config: 乐谱合规化配置
        :param staff_id: 谱表编号
        :param part_id: Part 标识
        :return: music21 Part
        """
        part = stream.Part(id=part_id)
        notes_by_bar = self._group_notes_by_bar(notes=quantized_score.notes, staff_id=staff_id)
        for bar_index in range(quantized_score.beat_grid.bar_count):
            measure = stream.Measure(number=bar_index + 1)
            if bar_index == 0:
                measure.insert(0, TimeSignature(config.time_signature))
                measure.insert(0, key.KeySignature(config.key_signature_sharps))
            self._append_measure_events(
                measure=measure,
                bar_notes=notes_by_bar.get(bar_index, tuple()),
                bar_start=bar_index * quantized_score.beat_grid.beats_per_bar,
                beats_per_bar=quantized_score.beat_grid.beats_per_bar,
            )
            part.append(measure)
        return part

    def _group_notes_by_bar(
        self,
        notes: tuple[QuantizedNoteEvent, ...],
        staff_id: int,
    ) -> dict[int, tuple[QuantizedNoteEvent, ...]]:
        """
        按小节收集指定谱表的音符。

        :param notes: 量化音符列表
        :param staff_id: 谱表编号
        :return: 小节到音符元组的映射
        """
        grouped: dict[int, list[QuantizedNoteEvent]] = defaultdict(list)
        for quantized_note in notes:
            if quantized_note.staff_id == staff_id:
                grouped[quantized_note.bar_index].append(quantized_note)
        return {
            bar_index: tuple(sorted(bar_notes, key=lambda item: (item.start_beat, item.pitch)))
            for bar_index, bar_notes in grouped.items()
        }

    def _append_measure_events(
        self,
        measure: stream.Measure,
        bar_notes: tuple[QuantizedNoteEvent, ...],
        bar_start: float,
        beats_per_bar: float,
    ) -> None:
        """
        向小节中按顺序追加休止、单音或和弦。

        :param measure: 待写入的小节
        :param bar_notes: 当前小节音符
        :param bar_start: 当前小节起始拍
        :param beats_per_bar: 当前小节容量
        """
        events_by_start: dict[float, list[QuantizedNoteEvent]] = defaultdict(list)
        for quantized_note in bar_notes:
            events_by_start[quantized_note.start_beat].append(quantized_note)
        current_offset = 0.0
        for event_start in sorted(events_by_start):
            local_offset = max(0.0, event_start - bar_start)
            if local_offset > current_offset:
                measure.append(note.Rest(quarterLength=local_offset - current_offset))
                current_offset = local_offset
            event_notes = sorted(events_by_start[event_start], key=lambda item: item.pitch)
            event_duration = min(item.duration_beats for item in event_notes)
            event_duration = min(event_duration, beats_per_bar - current_offset)
            if event_duration <= 0:
                continue
            if len(event_notes) == 1:
                music_note = note.Note(event_notes[0].pitch, quarterLength=event_duration)
                measure.append(music_note)
            else:
                music_chord = chord.Chord([item.pitch for item in event_notes], quarterLength=event_duration)
                measure.append(music_chord)
            current_offset += event_duration
        if current_offset < beats_per_bar:
            measure.append(note.Rest(quarterLength=beats_per_bar - current_offset))
