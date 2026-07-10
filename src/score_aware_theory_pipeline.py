"""
模块名称：score_aware_theory_pipeline
功能描述：
    提供乐谱语义感知缩编第一阶段 MVP 的独立运行入口。

主要组件：
    - ScoreAwareTheoryMvpPipeline: 串联 MIDI 清洗、节拍网格、量化、MusicXML 构建与合规报告输出。

依赖说明：
    - json: 写出真实合规报告。
    - pathlib: 管理输出路径。
    - 本项目乐谱 MVP 模块: 负责具体转换阶段。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

import json
from pathlib import Path

from beat_grid_estimator import BeatGridEstimator
from midi_event_canonicalizer import MidiEventCanonicalizer
from score_model import ScoreAwareTheoryMvpResult, ScoreRegularizationConfig
from score_semantic_analyzer import ScoreSemanticAnalyzer
from score_notation_validator import ScoreNotationValidator
from score_quantizer import ScoreQuantizer
from staff_notation_builder import StaffNotationBuilder
from theory_aware_36key_arranger import TheoryAware36KeyArranger


class ScoreAwareTheoryMvpPipeline:
    """
    乐谱语义感知 MVP 独立管线。

    职责：
        为后续 CLI 与主转换器接入提供一个稳定的端到端入口，当前只覆盖
        MIDI 到 MusicXML 与合规报告，不直接生成 36 键 YAML。
    """

    def run(
        self,
        midi_path: Path,
        config: ScoreRegularizationConfig,
        output_musicxml_path: Path,
        validation_report_path: Path,
        build_arrangement: bool = False,
        max_chord_notes: int = 6,
        allow_accidentals: bool = True,
    ) -> ScoreAwareTheoryMvpResult:
        """
        执行第一阶段 MVP 全链路。

        :param midi_path: 输入 MIDI 路径
        :param config: 乐谱合规化配置
        :param output_musicxml_path: MusicXML 输出路径
        :param validation_report_path: JSON 合规报告输出路径
        :param build_arrangement: 是否继续执行基础语义分析与 36 键缩编
        :param max_chord_notes: 36 键缩编单时间片最大音数
        :param allow_accidentals: 是否允许输出半音 token
        :return: MVP 执行结果
        """
        canonicalization_result = MidiEventCanonicalizer().canonicalize(midi_path=midi_path, config=config)
        beat_grid = BeatGridEstimator().estimate(notes=canonicalization_result.notes, config=config)
        quantized_score = ScoreQuantizer().quantize(
            notes=canonicalization_result.notes,
            beat_grid=beat_grid,
            config=config,
        )
        build_result = StaffNotationBuilder().build_and_export(
            quantized_score=quantized_score,
            config=config,
            output_musicxml_path=output_musicxml_path,
        )
        validation_report = ScoreNotationValidator().validate(build_result=build_result, config=config)
        semantic_model = None
        arrangement_result = None
        if build_arrangement:
            semantic_model = ScoreSemanticAnalyzer().analyze(quantized_score=quantized_score, config=config)
            arrangement_result = TheoryAware36KeyArranger().arrange(
                semantic_model=semantic_model,
                config=config,
                max_chord_notes=max_chord_notes,
                allow_accidentals=allow_accidentals,
            )
        self._write_validation_report(
            report_path=validation_report_path,
            result_data={
                "is_valid": validation_report.is_valid,
                "musicxml_exported": validation_report.musicxml_exported,
                "musicxml_reloaded": validation_report.musicxml_reloaded,
                "invalid_measure_count": validation_report.invalid_measure_count,
                "unassigned_note_count": validation_report.unassigned_note_count,
                "messages": list(validation_report.messages),
                "raw_note_count": canonicalization_result.report.raw_note_count,
                "merged_note_count": canonicalization_result.report.merged_note_count,
                "filtered_noise_count": canonicalization_result.report.filtered_noise_count,
                "onset_clustered_group_count": canonicalization_result.report.onset_clustered_group_count,
                "output_note_count": canonicalization_result.report.output_note_count,
                "bpm": beat_grid.bpm,
                "time_signature": beat_grid.time_signature,
                "bar_count": beat_grid.bar_count,
                "quantized_note_count": len(quantized_score.notes),
                "musicxml_path": str(output_musicxml_path),
                "arrangement_enabled": build_arrangement,
                "arrangement_valid": None if arrangement_result is None else arrangement_result.validation.is_valid,
                "arrangement_output_note_count": None if arrangement_result is None else arrangement_result.report.output_note_count,
            },
        )
        return ScoreAwareTheoryMvpResult(
            canonicalization_result=canonicalization_result,
            beat_grid=beat_grid,
            quantized_score=quantized_score,
            build_result=build_result,
            validation_report=validation_report,
            semantic_model=semantic_model,
            arrangement_result=arrangement_result,
            musicxml_path=output_musicxml_path,
            validation_report_path=validation_report_path,
        )

    def _write_validation_report(self, report_path: Path, result_data: dict[str, object]) -> None:
        """
        写出 JSON 合规报告。

        :param report_path: 报告输出路径
        :param result_data: 可序列化报告内容
        """
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
