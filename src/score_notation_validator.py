"""
模块名称：score_notation_validator
功能描述：
    校验乐谱语义感知缩编第一阶段 MVP 生成的 MusicXML 是否满足硬性合规要求。

主要组件：
    - ScoreNotationValidator: 检查 MusicXML 导出、回读、小节时值与音符归属。

依赖说明：
    - music21: 用于识别 Note、Chord、Rest 等乐谱元素。
    - score_model: 复用 StaffNotationBuildResult 与 NotationValidationReport。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from music21 import chord, note

from score_model import NotationValidationReport, ScoreRegularizationConfig, StaffNotationBuildResult


class ScoreNotationValidator:
    """
    五线谱合规校验器。

    职责：
        对第一阶段 MVP 生成的 MusicXML 与内部量化乐谱执行硬校验，避免伪成功输出。
    """

    def validate(
        self,
        build_result: StaffNotationBuildResult,
        config: ScoreRegularizationConfig,
    ) -> NotationValidationReport:
        """
        校验 MusicXML 构建结果。

        :param build_result: MusicXML 构建与回读结果
        :param config: 乐谱合规化配置
        :return: 合规校验报告
        """
        messages: list[str] = []
        musicxml_exported = build_result.musicxml_path.is_file()
        musicxml_reloaded = build_result.reloaded_score is not None
        if not musicxml_exported:
            messages.append(f"MusicXML 未写出: {build_result.musicxml_path}")
        if not musicxml_reloaded:
            messages.append("MusicXML 回读失败")

        invalid_measure_count = self._count_invalid_measures(build_result=build_result)
        if invalid_measure_count > 0:
            messages.append(f"存在 {invalid_measure_count} 个时值不完整的小节")

        unassigned_note_count = self._count_unassigned_notes(build_result=build_result)
        if unassigned_note_count > 0:
            messages.append(f"存在 {unassigned_note_count} 个缺少 staff 或 voice 的音符")

        is_valid = (
            musicxml_exported
            and musicxml_reloaded
            and invalid_measure_count == 0
            and unassigned_note_count == 0
        )
        return NotationValidationReport(
            is_valid=is_valid,
            musicxml_exported=musicxml_exported,
            musicxml_reloaded=musicxml_reloaded,
            invalid_measure_count=invalid_measure_count,
            unassigned_note_count=unassigned_note_count,
            messages=tuple(messages),
        )

    def _count_invalid_measures(self, build_result: StaffNotationBuildResult) -> int:
        """
        统计时值不完整的小节数量。

        :param build_result: MusicXML 构建结果
        :return: 无效小节数量
        """
        invalid_count = 0
        expected_beats = build_result.quantized_score.beat_grid.beats_per_bar
        for part in build_result.score.parts:
            for measure in part.getElementsByClass("Measure"):
                measured_beats = 0.0
                for element in measure.notesAndRests:
                    if isinstance(element, (note.Note, note.Rest, chord.Chord)):
                        measured_beats += float(element.duration.quarterLength)
                if abs(measured_beats - expected_beats) > 1e-6:
                    invalid_count += 1
        return invalid_count

    def _count_unassigned_notes(self, build_result: StaffNotationBuildResult) -> int:
        """
        统计缺少谱表或声部元数据的量化音符数量。

        :param build_result: MusicXML 构建结果
        :return: 未分配音符数量
        """
        unassigned_count = 0
        for quantized_note in build_result.quantized_score.notes:
            if quantized_note.staff_id not in {1, 2} or not quantized_note.voice_id.strip():
                unassigned_count += 1
        return unassigned_count
