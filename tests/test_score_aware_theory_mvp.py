"""
模块名称：test_score_aware_theory_mvp
功能描述：
    验证乐谱语义感知缩编第一阶段 MVP 的真实 MIDI 到 MusicXML 合规化流程。

主要组件：
    - test_canonicalizer_merges_overlap_and_clusters_chord_onsets: 验证 MIDI 清洗会合并同音碎片并聚类和弦起音。
    - test_score_regularization_pipeline_exports_valid_musicxml: 验证清洗、节拍网格、量化、MusicXML 导出与回读校验闭环。

依赖说明：
    - pretty_midi: 构造真实 MIDI 文件并验证读取逻辑。
    - music21: 构建、导出并回读真实 MusicXML 乐谱。
    - pytest: 执行断言。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pretty_midi
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

BeatGridEstimator = import_module("beat_grid_estimator").BeatGridEstimator
MidiEventCanonicalizer = import_module("midi_event_canonicalizer").MidiEventCanonicalizer
ScoreAwareTheoryMvpPipeline = import_module("score_aware_theory_pipeline").ScoreAwareTheoryMvpPipeline
ScoreRegularizationConfig = import_module("score_model").ScoreRegularizationConfig
ScoreNotationValidator = import_module("score_notation_validator").ScoreNotationValidator
ScoreQuantizer = import_module("score_quantizer").ScoreQuantizer
StaffNotationBuilder = import_module("staff_notation_builder").StaffNotationBuilder


def _write_real_midi(midi_path: Path) -> None:
    """
    写入真实 pretty_midi 文件，作为 MVP 测试输入。

    该 MIDI 同时包含三类情况：
    1. 同一和弦内 C/E/G 存在毫秒级起音偏差。
    2. 高音 C5 被转录成两个相邻碎片，需要合并。
    3. 四个四分音符形成完整 4/4 小节，可被导出为合规 MusicXML。

    :param midi_path: 测试 MIDI 输出路径
    """
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(program=pretty_midi.instrument_name_to_program("Acoustic Grand Piano"))
    for pitch, start, end, velocity in (
        (60, 0.000, 0.490, 86),
        (64, 0.018, 0.500, 82),
        (67, 0.037, 0.515, 80),
        (62, 0.500, 0.990, 78),
        (64, 1.000, 1.490, 78),
        (65, 1.500, 1.990, 78),
        (72, 2.000, 2.230, 70),
        (72, 2.245, 2.500, 73),
    ):
        instrument.notes.append(
            pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=start,
                end=end,
            )
        )
    midi_data.instruments.append(instrument)
    midi_data.write(str(midi_path))


def _base_config(tmp_path: Path):
    """
    构造乐谱合规化 MVP 测试配置。

    :param tmp_path: pytest 临时目录
    :return: 测试配置对象
    """
    return ScoreRegularizationConfig(
        bpm=120.0,
        beat_unit=4,
        quantize_beat=0.25,
        time_signature="4/4",
        key_signature_sharps=0,
        merge_sustained_gap_beats=0.08,
        onset_cluster_window_beats=0.10,
        min_noise_duration_beats=0.03,
        min_noise_velocity=8,
        work_dir=tmp_path,
    )


def test_canonicalizer_merges_overlap_and_clusters_chord_onsets(tmp_path: Path) -> None:
    """
    验证 MIDI 清洗会合并同音碎片，并将近起音和弦聚类到统一拍点。
    """
    midi_path = tmp_path / "machine_transcription.mid"
    _write_real_midi(midi_path=midi_path)
    config = _base_config(tmp_path=tmp_path)

    result = MidiEventCanonicalizer().canonicalize(midi_path=midi_path, config=config)

    first_chord = [note for note in result.notes if note.pitch in {60, 64, 67} and note.start_beat < 0.2]
    merged_c5 = [note for note in result.notes if note.pitch == 72]

    assert result.report.raw_note_count == 8
    assert result.report.merged_note_count == 1
    assert result.report.onset_clustered_group_count >= 1
    assert len(first_chord) == 3
    assert {round(note.start_beat, 6) for note in first_chord} == {round(first_chord[0].start_beat, 6)}
    assert len(merged_c5) == 1
    assert merged_c5[0].duration_beats == pytest.approx(1.0, abs=0.12)


def test_score_regularization_pipeline_exports_valid_musicxml(tmp_path: Path) -> None:
    """
    验证第一阶段 MVP 能从真实 MIDI 生成可导出、可回读、可校验的 MusicXML。
    """
    midi_path = tmp_path / "machine_transcription.mid"
    musicxml_path = tmp_path / "score" / "generated.musicxml"
    _write_real_midi(midi_path=midi_path)
    config = _base_config(tmp_path=tmp_path)

    canonical_result = MidiEventCanonicalizer().canonicalize(midi_path=midi_path, config=config)
    beat_grid = BeatGridEstimator().estimate(notes=canonical_result.notes, config=config)
    quantized_score = ScoreQuantizer().quantize(notes=canonical_result.notes, beat_grid=beat_grid, config=config)
    build_result = StaffNotationBuilder().build_and_export(
        quantized_score=quantized_score,
        config=config,
        output_musicxml_path=musicxml_path,
    )
    validation_report = ScoreNotationValidator().validate(build_result=build_result, config=config)

    assert musicxml_path.is_file()
    assert build_result.measure_count == 2
    assert build_result.note_count == len(quantized_score.notes)
    assert validation_report.is_valid is True
    assert validation_report.musicxml_exported is True
    assert validation_report.musicxml_reloaded is True
    assert validation_report.invalid_measure_count == 0
    assert validation_report.unassigned_note_count == 0


def test_score_aware_theory_mvp_pipeline_writes_validation_report(tmp_path: Path) -> None:
    """
    验证独立 MVP 管线会输出 MusicXML 与真实 JSON 合规报告，便于后续 CLI 接入。
    """
    midi_path = tmp_path / "machine_transcription.mid"
    musicxml_path = tmp_path / "score" / "generated.musicxml"
    report_path = tmp_path / "audit" / "notation_validation_report.json"
    _write_real_midi(midi_path=midi_path)
    config = _base_config(tmp_path=tmp_path)

    result = ScoreAwareTheoryMvpPipeline().run(
        midi_path=midi_path,
        config=config,
        output_musicxml_path=musicxml_path,
        validation_report_path=report_path,
    )

    assert result.musicxml_path == musicxml_path
    assert result.validation_report_path == report_path
    assert result.validation_report.is_valid is True
    assert report_path.is_file()
    assert '"is_valid": true' in report_path.read_text(encoding="utf-8")
    assert '"raw_note_count": 8' in report_path.read_text(encoding="utf-8")


def test_score_aware_theory_mvp_pipeline_can_emit_arrangement(tmp_path: Path) -> None:
    """
    验证独立 MVP 管线可以在 MusicXML 合规化后继续输出基础乐理缩编结果。
    """
    midi_path = tmp_path / "machine_transcription.mid"
    musicxml_path = tmp_path / "score" / "generated.musicxml"
    report_path = tmp_path / "audit" / "notation_validation_report.json"
    _write_real_midi(midi_path=midi_path)
    config = _base_config(tmp_path=tmp_path)

    result = ScoreAwareTheoryMvpPipeline().run(
        midi_path=midi_path,
        config=config,
        output_musicxml_path=musicxml_path,
        validation_report_path=report_path,
        build_arrangement=True,
        max_chord_notes=4,
        allow_accidentals=True,
    )

    assert result.semantic_model is not None
    assert result.arrangement_result is not None
    assert result.arrangement_result.validation.is_valid is True
    assert result.arrangement_result.score_events
