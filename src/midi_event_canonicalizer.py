"""
模块名称：midi_event_canonicalizer
功能描述：
    将机器转录得到的真实 MIDI 文件清洗为稳定的乐谱音符事件。

主要组件：
    - MidiEventCanonicalizer: 执行 MIDI 读取、同音碎片合并、噪声过滤与起音聚类。

依赖说明：
    - pretty_midi: 读取真实 MIDI 文件。
    - score_model: 复用乐谱合规化 MVP 数据模型。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pretty_midi

from score_model import (
    CanonicalizationReport,
    CanonicalizationResult,
    CanonicalNoteEvent,
    ScoreRegularizationConfig,
)


class MidiEventCanonicalizer:
    """
    MIDI 清洗器。

    职责：
        把机器转录 MIDI 中的浮点时间事件转换为可审计、可量化的 CanonicalNoteEvent。
    """

    def canonicalize(self, midi_path: Path, config: ScoreRegularizationConfig) -> CanonicalizationResult:
        """
        读取并清洗真实 MIDI 文件。

        :param midi_path: 输入 MIDI 路径
        :param config: 乐谱合规化配置
        :return: 清洗结果与统计报告
        :raises FileNotFoundError: 当 MIDI 文件不存在时触发
        :raises ValueError: 当 MIDI 无有效音符或配置不合法时触发
        """
        self._validate_config(config=config)
        if not midi_path.is_file():
            raise FileNotFoundError(f"MIDI 文件不存在: {midi_path}")

        raw_notes = self._read_raw_notes(midi_path=midi_path, config=config)
        merged_notes, merged_count = self._merge_sustained_notes(notes=raw_notes, config=config)
        filtered_notes, filtered_count = self._filter_noise_notes(notes=merged_notes, config=config)
        clustered_notes, clustered_group_count = self._cluster_onsets(notes=filtered_notes, config=config)
        output_notes = tuple(
            replace(note, note_id=index)
            for index, note in enumerate(sorted(clustered_notes, key=lambda item: (item.start_beat, item.pitch)))
        )
        report = CanonicalizationReport(
            raw_note_count=len(raw_notes),
            merged_note_count=merged_count,
            filtered_noise_count=filtered_count,
            onset_clustered_group_count=clustered_group_count,
            output_note_count=len(output_notes),
        )
        return CanonicalizationResult(notes=output_notes, report=report)

    def _validate_config(self, config: ScoreRegularizationConfig) -> None:
        """
        校验清洗配置，避免隐藏降级。

        :param config: 乐谱合规化配置
        :raises ValueError: 当配置非法时触发
        """
        if config.bpm <= 0:
            raise ValueError("bpm 必须大于 0")
        if config.quantize_beat <= 0:
            raise ValueError("quantize_beat 必须大于 0")
        if config.merge_sustained_gap_beats < 0:
            raise ValueError("merge_sustained_gap_beats 必须大于等于 0")
        if config.onset_cluster_window_beats < 0:
            raise ValueError("onset_cluster_window_beats 必须大于等于 0")
        if config.min_noise_duration_beats < 0:
            raise ValueError("min_noise_duration_beats 必须大于等于 0")

    def _read_raw_notes(self, midi_path: Path, config: ScoreRegularizationConfig) -> tuple[CanonicalNoteEvent, ...]:
        """
        从 MIDI 文件读取非鼓音符并转换为拍点。

        :param midi_path: 输入 MIDI 路径
        :param config: 乐谱合规化配置
        :return: 原始音符事件
        :raises ValueError: 当 MIDI 中没有可用音符时触发
        """
        midi_data = pretty_midi.PrettyMIDI(str(midi_path))
        seconds_per_beat = 60.0 / config.bpm
        notes: list[CanonicalNoteEvent] = []
        source_index = 0
        for instrument in midi_data.instruments:
            if instrument.is_drum:
                continue
            for midi_note in instrument.notes:
                if midi_note.end <= midi_note.start:
                    continue
                start_beat = float(midi_note.start / seconds_per_beat)
                end_beat = float(midi_note.end / seconds_per_beat)
                notes.append(
                    CanonicalNoteEvent(
                        note_id=source_index,
                        pitch=int(midi_note.pitch),
                        start_beat=start_beat,
                        end_beat=end_beat,
                        velocity=int(midi_note.velocity),
                        duration_beats=end_beat - start_beat,
                        source_note_ids=(source_index,),
                        confidence=1.0,
                    )
                )
                source_index += 1
        if not notes:
            raise ValueError("MIDI 中没有可用于乐谱合规化的非鼓音符")
        return tuple(sorted(notes, key=lambda item: (item.pitch, item.start_beat)))

    def _merge_sustained_notes(
        self,
        notes: tuple[CanonicalNoteEvent, ...],
        config: ScoreRegularizationConfig,
    ) -> tuple[tuple[CanonicalNoteEvent, ...], int]:
        """
        合并同音重叠或极近间隔碎片。

        :param notes: 按 pitch 与 start_beat 排序的音符
        :param config: 乐谱合规化配置
        :return: 合并后音符与合并次数
        """
        if not notes:
            return tuple(), 0
        sorted_notes = tuple(sorted(notes, key=lambda item: (item.pitch, item.start_beat)))
        merged_notes: list[CanonicalNoteEvent] = []
        merge_count = 0
        current = sorted_notes[0]
        for note in sorted_notes[1:]:
            same_pitch = note.pitch == current.pitch
            close_gap = note.start_beat <= current.end_beat + config.merge_sustained_gap_beats
            if same_pitch and close_gap:
                current = CanonicalNoteEvent(
                    note_id=current.note_id,
                    pitch=current.pitch,
                    start_beat=min(current.start_beat, note.start_beat),
                    end_beat=max(current.end_beat, note.end_beat),
                    velocity=max(current.velocity, note.velocity),
                    duration_beats=max(current.end_beat, note.end_beat) - min(current.start_beat, note.start_beat),
                    source_note_ids=current.source_note_ids + note.source_note_ids,
                    confidence=min(current.confidence, note.confidence),
                )
                merge_count += 1
                continue
            merged_notes.append(current)
            current = note
        merged_notes.append(current)
        return tuple(merged_notes), merge_count

    def _filter_noise_notes(
        self,
        notes: tuple[CanonicalNoteEvent, ...],
        config: ScoreRegularizationConfig,
    ) -> tuple[tuple[CanonicalNoteEvent, ...], int]:
        """
        过滤极短且极弱的噪声音。

        :param notes: 待过滤音符
        :param config: 乐谱合规化配置
        :return: 过滤后音符与过滤数量
        """
        kept_notes: list[CanonicalNoteEvent] = []
        filtered_count = 0
        for note in notes:
            is_noise = (
                note.duration_beats < config.min_noise_duration_beats
                and note.velocity < config.min_noise_velocity
            )
            if is_noise:
                filtered_count += 1
                continue
            kept_notes.append(note)
        if not kept_notes:
            raise ValueError("MIDI 清洗后没有剩余音符，无法生成五线谱")
        return tuple(kept_notes), filtered_count

    def _cluster_onsets(
        self,
        notes: tuple[CanonicalNoteEvent, ...],
        config: ScoreRegularizationConfig,
    ) -> tuple[tuple[CanonicalNoteEvent, ...], int]:
        """
        将同一和弦内的近起音音符聚类到统一起点。

        :param notes: 待聚类音符
        :param config: 乐谱合规化配置
        :return: 聚类后音符与发生聚类的组数
        """
        sorted_notes = sorted(notes, key=lambda item: (item.start_beat, item.pitch))
        clustered: list[CanonicalNoteEvent] = []
        clustered_group_count = 0
        index = 0
        while index < len(sorted_notes):
            group = [sorted_notes[index]]
            group_start = sorted_notes[index].start_beat
            index += 1
            while index < len(sorted_notes):
                candidate = sorted_notes[index]
                if candidate.start_beat - group_start > config.onset_cluster_window_beats:
                    break
                group.append(candidate)
                index += 1
            if len(group) > 1:
                clustered_group_count += 1
                total_velocity = sum(max(1, note.velocity) for note in group)
                cluster_start = sum(note.start_beat * max(1, note.velocity) for note in group) / total_velocity
                for note in group:
                    clustered.append(
                        CanonicalNoteEvent(
                            note_id=note.note_id,
                            pitch=note.pitch,
                            start_beat=cluster_start,
                            end_beat=cluster_start + note.duration_beats,
                            velocity=note.velocity,
                            duration_beats=note.duration_beats,
                            source_note_ids=note.source_note_ids,
                            confidence=note.confidence,
                        )
                    )
            else:
                clustered.append(group[0])
        return tuple(clustered), clustered_group_count
