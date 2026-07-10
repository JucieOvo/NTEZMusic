"""
模块名称：test_score_aware_theory_arrangement
功能描述：
    验证乐谱语义感知缩编第二阶段的基础语义分析与 36 键缩编能力。

主要组件：
    - test_semantic_analyzer_identifies_melody_bass_and_chord_roles: 验证主旋律、低音骨架与和声角色识别。
    - test_theory_aware_arranger_outputs_36_key_yaml_events: 验证 88 键量化谱可缩编为 36 键 YAML 事件。

依赖说明：
    - pytest: 执行真实断言。
    - 项目 score_model: 构造真实 QuantizedScore 数据结构。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from importlib import import_module

QuantizedNoteEvent = import_module("score_model").QuantizedNoteEvent
QuantizedScore = import_module("score_model").QuantizedScore
BeatGrid = import_module("score_model").BeatGrid
ScoreRegularizationConfig = import_module("score_model").ScoreRegularizationConfig
ScoreSemanticAnalyzer = import_module("score_semantic_analyzer").ScoreSemanticAnalyzer
TheoryAware36KeyArranger = import_module("theory_aware_36key_arranger").TheoryAware36KeyArranger
audio_converter_module = import_module("audio_to_yaml_converter")
AudioPipelineConfig = audio_converter_module.AudioPipelineConfig
MidiToYamlConverter = audio_converter_module.MidiToYamlConverter


def _config():
    """
    构造第二阶段测试配置。

    :return: 乐谱合规化配置
    """
    from pathlib import Path

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
        work_dir=Path("."),
    )


def _quantized_score():
    """
    构造包含旋律、低音与和弦色彩音的真实量化乐谱。

    该乐谱刻意包含超出 36 键范围的高音 C6 与低音 C2，用于验证缩编器会按八度
    移动到 C3-B5，同时优先保留主旋律、低音根音、三度与七度。

    :return: 量化乐谱
    """
    beat_grid = BeatGrid(
        bpm=120.0,
        time_signature="4/4",
        beats_per_bar=4.0,
        total_beats=4.0,
        bar_count=1,
        beat_positions=tuple(index * 0.25 for index in range(17)),
        confidence=1.0,
    )
    notes = (
        QuantizedNoteEvent(1, 36, 0.0, 1.0, 0, 2, "LH-1", (1,)),
        QuantizedNoteEvent(2, 48, 0.0, 1.0, 0, 2, "LH-1", (2,)),
        QuantizedNoteEvent(3, 52, 0.0, 1.0, 0, 1, "RH-1", (3,)),
        QuantizedNoteEvent(4, 55, 0.0, 1.0, 0, 1, "RH-1", (4,)),
        QuantizedNoteEvent(5, 58, 0.0, 1.0, 0, 1, "RH-1", (5,)),
        QuantizedNoteEvent(6, 84, 0.0, 1.0, 0, 1, "RH-1", (6,)),
        QuantizedNoteEvent(7, 86, 1.0, 1.0, 0, 1, "RH-1", (7,)),
    )
    return QuantizedScore(
        notes=notes,
        beat_grid=beat_grid,
        time_signature="4/4",
        key_signature_sharps=0,
    )


def test_semantic_analyzer_identifies_melody_bass_and_chord_roles() -> None:
    """
    验证语义分析器能识别主旋律、低音骨架和基础和弦角色。
    """
    semantic_model = ScoreSemanticAnalyzer().analyze(quantized_score=_quantized_score(), config=_config())

    first_chord_roles = {note.note_id: note.chord_role for note in semantic_model.note_semantics if note.start_beat == 0.0}
    melody_ids = {note.note_id for note in semantic_model.note_semantics if note.is_melody}
    bass_ids = {note.note_id for note in semantic_model.note_semantics if note.is_bass_anchor}

    assert semantic_model.key_signature_sharps == 0
    assert semantic_model.phrase_boundaries[0].boundary_beat == 0.0
    assert 6 in melody_ids
    assert 1 in bass_ids
    assert first_chord_roles[1] == "root"
    assert first_chord_roles[3] == "third"
    assert first_chord_roles[5] == "seventh"


def test_theory_aware_arranger_outputs_36_key_yaml_events() -> None:
    """
    验证乐理缩编器输出满足 36 键范围、密度上限和现有 YAML 事件格式。
    """
    config = _config()
    semantic_model = ScoreSemanticAnalyzer().analyze(quantized_score=_quantized_score(), config=config)
    arranged = TheoryAware36KeyArranger().arrange(
        semantic_model=semantic_model,
        config=config,
        max_chord_notes=4,
        allow_accidentals=True,
    )

    first_event = arranged.score_events[0]

    assert arranged.validation.is_valid is True
    assert arranged.validation.out_of_range_note_count == 0
    assert arranged.validation.max_chord_notes == 4
    assert arranged.report.input_note_count == 7
    assert arranged.report.output_note_count <= 5
    assert "notes" in first_event
    assert "beat" in first_event
    assert first_event["beat"] == 1
    assert "+1" in first_event["notes"]
    assert len(arranged.arranged_notes_by_start[0.0]) <= 4


def test_theory_aware_arranger_uses_shortest_shared_chord_duration() -> None:
    """
    验证同一时间片存在异构时值时，YAML 和弦事件使用最短共同按键时值。

    当前游戏 YAML 事件格式不支持同一和弦内各音符独立释放，因此缩编器必须选择一个
    可执行的共同事件时值。这里固定为最短时值，避免较短旋律音被过度拉长。
    """
    config = _config()
    score = _quantized_score()
    varied_notes = tuple(
        QuantizedNoteEvent(
            note.note_id,
            note.pitch,
            note.start_beat,
            0.5 if note.note_id == 6 else note.duration_beats,
            note.bar_index,
            note.staff_id,
            note.voice_id,
            note.source_note_ids,
        )
        for note in score.notes
    )
    varied_score = QuantizedScore(
        notes=varied_notes,
        beat_grid=score.beat_grid,
        time_signature=score.time_signature,
        key_signature_sharps=score.key_signature_sharps,
    )
    semantic_model = ScoreSemanticAnalyzer().analyze(quantized_score=varied_score, config=config)

    arranged = TheoryAware36KeyArranger().arrange(
        semantic_model=semantic_model,
        config=config,
        max_chord_notes=4,
        allow_accidentals=True,
    )

    assert arranged.score_events[0]["beat"] == 0.5


def test_midi_to_yaml_converter_accepts_score_aware_theory_mode(tmp_path) -> None:
    """
    验证主 MIDI 转 YAML 转换器正式支持 score_aware_theory 模式。
    """
    import pretty_midi

    midi_path = tmp_path / "score_aware_input.mid"
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(program=pretty_midi.instrument_name_to_program("Acoustic Grand Piano"))
    for pitch, start, end in (
        (36, 0.0, 0.5),
        (52, 0.0, 0.5),
        (84, 0.0, 0.5),
        (86, 0.5, 1.0),
    ):
        instrument.notes.append(pretty_midi.Note(velocity=80, pitch=pitch, start=start, end=end))
    midi_data.instruments.append(instrument)
    midi_data.write(str(midi_path))
    config = AudioPipelineConfig(
        audio_path=None,
        input_midi_path=midi_path,
        output_yaml_path=tmp_path / "out.yaml",
        work_dir=tmp_path,
        song_name="乐理缩编测试",
        bpm=120.0,
        beat_unit=4,
        start_delay_seconds=1.0,
        key_press_seconds=0.0,
        demucs_model="htdemucs_6s",
        demucs_stem="piano",
        transcription_checkpoint=None,
        transcription_device="cpu",
        transcription_segment_hop_size=None,
        transcription_segment_size=None,
        quantize_beat=0.25,
        max_chord_notes=3,
        max_score_events=100,
        allow_accidentals=True,
        out_of_range_policy="octave_fold",
        pitch_compression_mode="score_aware_theory",
        ref_smoothing=0.2,
        left_max_chord_notes=3,
        phrase_gap_beats=0.5,
        global_trend_alpha=0.05,
        global_trend_window_beats=4.0,
    )

    yaml_data = MidiToYamlConverter().convert(midi_path=midi_path, config=config)

    assert yaml_data["song"]["name"] == "乐理缩编测试"
    assert yaml_data["score"]
    assert yaml_data["score"][0]["beat"] == 1
    assert "+1" in yaml_data["score"][0]["notes"]


def test_score_aware_theory_mode_requires_resolved_bpm(tmp_path) -> None:
    """
    验证 score_aware_theory 模式在直接调用 convert 时要求 BPM 已经解析完成。

    AudioToYamlPipeline.run 会在调用 convert 前处理 bpm=0 的自动检测；直接调用
    MidiToYamlConverter.convert 时不能把 0 传入乐谱合规化子管线。
    """
    import pretty_midi
    import pytest

    midi_path = tmp_path / "score_aware_input.mid"
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    instrument = pretty_midi.Instrument(program=pretty_midi.instrument_name_to_program("Acoustic Grand Piano"))
    instrument.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.0, end=0.5))
    midi_data.instruments.append(instrument)
    midi_data.write(str(midi_path))
    config = AudioPipelineConfig(
        audio_path=None,
        input_midi_path=midi_path,
        output_yaml_path=tmp_path / "out.yaml",
        work_dir=tmp_path,
        song_name="BPM 未解析测试",
        bpm=0.0,
        beat_unit=4,
        start_delay_seconds=1.0,
        key_press_seconds=0.0,
        demucs_model="htdemucs_6s",
        demucs_stem="piano",
        transcription_checkpoint=None,
        transcription_device="cpu",
        transcription_segment_hop_size=None,
        transcription_segment_size=None,
        quantize_beat=0.25,
        max_chord_notes=3,
        max_score_events=100,
        allow_accidentals=True,
        out_of_range_policy="octave_fold",
        pitch_compression_mode="score_aware_theory",
        ref_smoothing=0.2,
        left_max_chord_notes=3,
        phrase_gap_beats=0.5,
        global_trend_alpha=0.05,
        global_trend_window_beats=4.0,
    )

    with pytest.raises(ValueError, match="score_aware_theory 模式要求 bpm 已经解析为正数"):
        MidiToYamlConverter().convert(midi_path=midi_path, config=config)
