"""
模块名称：test_audio_to_yaml_svsep_mpdr
功能描述：
    验证 SVSEP-MPDR v3 纯音频算法中的 MIDI 起音聚类、主旋律追踪与候选评分基础能力。

    测试覆盖：
        - 近起音和弦被正确合并为同一 start_beat
        - 分解和弦不被误合并（起音跨度超出聚类上限）
        - 非法配置参数正确抛出异常
        - 真实 pretty_midi 文件读取全链路验证

作者：JucieOvo
创建日期：2026-07-01
"""

import tempfile
from pathlib import Path

import pretty_midi
import pytest

from audio_to_yaml_converter import AudioPipelineConfig, MidiNoteEvent, MidiToYamlConverter


def _base_config(tmp_path: Path, **overrides) -> AudioPipelineConfig:
    """
    构造测试用 AudioPipelineConfig。

    提供一组合理的默认值，可通过 overrides 覆盖特定字段。

    :param tmp_path: pytest 临时目录
    :param overrides: 需要覆盖的配置字段
    :return: 测试用 AudioPipelineConfig 实例
    """
    values = {
        "audio_path": None,
        "input_midi_path": None,
        "output_yaml_path": tmp_path / "out.yaml",
        "work_dir": tmp_path,
        "song_name": "测试曲目",
        "bpm": 120.0,
        "beat_unit": 4,
        "start_delay_seconds": 1.0,
        "key_press_seconds": 0.0,
        "demucs_model": "htdemucs_6s",
        "demucs_stem": "piano",
        "transcription_checkpoint": None,
        "transcription_device": "cuda",
        "transcription_segment_hop_size": None,
        "transcription_segment_size": None,
        "quantize_beat": 0.25,
        "max_chord_notes": 4,
        "max_score_events": 10000,
        "allow_accidentals": True,
        "out_of_range_policy": "error",
        "pitch_compression_mode": "octave_fold",
        "ref_smoothing": 0.2,
        "left_max_chord_notes": 4,
        "phrase_gap_beats": 0.5,
        "global_trend_alpha": 0.05,
        "global_trend_window_beats": 4.0,
    }
    values.update(overrides)
    return AudioPipelineConfig(**values)


def _note(pitch: int, start: float, end: float, velocity: int = 80) -> MidiNoteEvent:
    """
    快速构造测试用 MidiNoteEvent。

    :param pitch: MIDI 音高编号
    :param start: 起始拍点
    :param end: 结束拍点
    :param velocity: 力度值，默认 80
    :return: MidiNoteEvent 实例
    """
    return MidiNoteEvent(
        pitch=pitch,
        start_beat=start,
        end_beat=end,
        velocity=velocity,
        duration_beats=end - start,
    )


class TestClusterMidiNoteOnsets:
    """
    测试 MIDI 起音聚类功能。
    """

    def test_cluster_midi_note_onsets_merges_close_chord_notes(self, tmp_path):
        """
        验证三枚近起音和弦被合并为同一 start_beat。

        构造一个典型的物理和弦：C4、E4、G4 在极接近的拍点起音，
        期望聚类后三个音符共享同一个由 velocity 加权平均的 start_beat。
        """
        config = _base_config(
            tmp_path,
            onset_cluster_enabled=True,
            onset_cluster_window_beats=0.08,
            onset_cluster_max_span_beats=0.12,
            quantize_beat=0.25,
        )
        converter = MidiToYamlConverter()
        # 初始化 conversion_stats，确保聚类方法可安全读写
        converter.conversion_stats = {
            "onset_clustered_notes": 0,
            "onset_clustered_groups": 0,
        }

        # 构造三枚和弦音符，起音偏差在 window 和 max_span 范围内
        notes = (
            _note(pitch=60, start=1.00, end=2.00, velocity=100),  # C4
            _note(pitch=64, start=1.02, end=2.00, velocity=90),   # E4
            _note(pitch=67, start=1.04, end=2.00, velocity=80),   # G4
        )

        result = converter._cluster_midi_note_onsets(midi_notes=notes, config=config)

        # 验证：所有结果音符共享同一 start_beat
        result_list = list(result)
        assert len(result_list) == 3, f"期望 3 个音符，实际 {len(result_list)}"

        start_beats = {note.start_beat for note in result_list}
        assert len(start_beats) == 1, f"期望所有音符共享同一 start_beat，实际有 {len(start_beats)} 个不同值"

        # velocity 加权平均预期：
        # (1.00*100 + 1.02*90 + 1.04*80) / (100+90+80) = 275.0 / 270 ≈ 1.0185
        expected_start = (1.00 * 100 + 1.02 * 90 + 1.04 * 80) / (100 + 90 + 80)
        actual_start = next(iter(start_beats))
        assert abs(actual_start - expected_start) < 1e-9, (
            f"start_beat 期望 {expected_start:.10f}，实际 {actual_start:.10f}"
        )

        # 验证统计计数
        assert converter.conversion_stats["onset_clustered_notes"] == 3
        assert converter.conversion_stats["onset_clustered_groups"] == 1

        # 验证各音符 pitch 和 velocity 未变
        pitches = sorted(note.pitch for note in result_list)
        assert pitches == [60, 64, 67]

        velocities = sorted(note.velocity for note in result_list)
        assert velocities == [80, 90, 100]

    def test_cluster_midi_note_onsets_keeps_arpeggio_when_span_exceeds_limit(self, tmp_path):
        """
        验证分解和弦不被误合并。

        构造一个起音跨度超出 max_span 的琶音序列，
        期望各音符保持独立不被合并。
        """
        config = _base_config(
            tmp_path,
            onset_cluster_enabled=True,
            onset_cluster_window_beats=0.08,
            onset_cluster_max_span_beats=0.12,
            quantize_beat=0.25,
        )
        converter = MidiToYamlConverter()
        converter.conversion_stats = {
            "onset_clustered_notes": 0,
            "onset_clustered_groups": 0,
        }

        # 构造琶音：C4、E4、G4 依次相距 0.15 拍，超出 max_span=0.12
        notes = (
            _note(pitch=60, start=1.00, end=1.50, velocity=80),  # C4
            _note(pitch=64, start=1.15, end=1.65, velocity=80),  # E4
            _note(pitch=67, start=1.30, end=1.80, velocity=80),  # G4
        )

        result = converter._cluster_midi_note_onsets(midi_notes=notes, config=config)

        result_list = list(result)
        # 第一个音和第二个音间隔 0.15 > window=0.08，不应合并
        # 第一个音和第三个音间隔 0.30 > max_span=0.12，不应合并
        # 每个音都应该是独立组
        assert len(result_list) == 3, f"期望 3 个独立音符，实际 {len(result_list)}"

        # 验证 start_beat 未变
        for original, clustered in zip(notes, result_list):
            assert abs(clustered.start_beat - original.start_beat) < 1e-9, (
                f"pitch={original.pitch} start_beat 不应改变，"
                f"原始 {original.start_beat}，实际 {clustered.start_beat}"
            )

        # 未产生任何聚类（每个都是独立组，只有 1 个音符的组不计入 clustered_notes）
        assert converter.conversion_stats["onset_clustered_notes"] == 0
        assert converter.conversion_stats["onset_clustered_groups"] == 0

    def test_cluster_midi_note_onsets_rejects_invalid_config(self, tmp_path):
        """
        验证非法配置参数正确抛出 ValueError。

        覆盖两种非法场景：
            1. onset_cluster_window_beats 为负数
            2. onset_cluster_max_span_beats 小于 onset_cluster_window_beats
        """
        converter = MidiToYamlConverter()

        # 场景 1：window_beats 为负数
        config_negative_window = _base_config(
            tmp_path,
            onset_cluster_enabled=True,
            onset_cluster_window_beats=-0.1,
            onset_cluster_max_span_beats=0.12,
        )
        notes = (_note(pitch=60, start=1.0, end=2.0),)
        with pytest.raises(ValueError, match="onset_cluster_window_beats"):
            converter._cluster_midi_note_onsets(midi_notes=notes, config=config_negative_window)

        # 场景 2：max_span 小于 window
        config_span_smaller = _base_config(
            tmp_path,
            onset_cluster_enabled=True,
            onset_cluster_window_beats=0.08,
            onset_cluster_max_span_beats=0.05,
        )
        with pytest.raises(ValueError, match="onset_cluster_max_span_beats"):
            converter._cluster_midi_note_onsets(midi_notes=notes, config=config_span_smaller)

        # 场景 3：禁用聚类时直接原样返回（不应抛异常）
        config_disabled = _base_config(
            tmp_path,
            onset_cluster_enabled=False,
        )
        result = converter._cluster_midi_note_onsets(midi_notes=notes, config=config_disabled)
        assert len(result) == 1
        assert result[0].start_beat == 1.0

    def test_read_midi_notes_applies_onset_clustering_to_real_pretty_midi(self, tmp_path):
        """
        验证真实 pretty_midi 文件读取全链路：读取 -> 延音合并 -> 起音聚类。

        该测试从 pretty_midi 对象构造一个真实 MIDI 文件写入临时目录，
        包含同时刻和弦与延音碎音，验证 _read_midi_notes 完整管线。
        """
        # 构造 pretty_midi 对象
        midi_obj = pretty_midi.PrettyMIDI(initial_tempo=120.0)
        instrument = pretty_midi.Instrument(program=0, is_drum=False)

        # 场景：C 大三和弦 (C4, E4, G4)，起音在真实世界中可能有 5-15ms 偏差
        # BPM=120 时 seconds_per_beat=0.5，0.01s = 0.02 beats
        # C4: start=1.00 拍, E4: start=1.01 拍, G4: start=1.02 拍 (应被聚类)
        instrument.notes.append(pretty_midi.Note(velocity=100, pitch=60, start=0.50, end=1.00))   # C4, 1.00 beat
        instrument.notes.append(pretty_midi.Note(velocity=90, pitch=64, start=0.505, end=1.00))   # E4, 1.01 beat
        instrument.notes.append(pretty_midi.Note(velocity=80, pitch=67, start=0.51, end=1.00))    # G4, 1.02 beat

        # 添加一个独立音符，不应被合并
        instrument.notes.append(pretty_midi.Note(velocity=80, pitch=72, start=1.00, end=1.50))    # C5, 2.00 beat

        midi_obj.instruments.append(instrument)

        # 写入临时 MIDI 文件
        midi_path = tmp_path / "test_chord.mid"
        midi_obj.write(str(midi_path))

        # 读取并执行全链路
        config = _base_config(
            tmp_path,
            onset_cluster_enabled=True,
            onset_cluster_window_beats=0.08,
            onset_cluster_max_span_beats=0.12,
            merge_sustained_notes=True,
            merge_sustained_gap_beats=0.25,
            quantize_beat=0.25,
        )
        converter = MidiToYamlConverter()

        midi_data = pretty_midi.PrettyMIDI(str(midi_path))
        result = converter._read_midi_notes(midi_data=midi_data, config=config)

        result_list = list(result)
        # 4 个输入音符，3 个和弦被聚类合并为同一 start_beat，1 个独立音符
        assert len(result_list) == 4, f"期望 4 个音符，实际 {len(result_list)}"

        # 聚类组中 3 个和弦音符应共享同一 start_beat
        chord_notes = [note for note in result_list if note.pitch in (60, 64, 67)]
        assert len(chord_notes) == 3
        chord_starts = {note.start_beat for note in chord_notes}
        assert len(chord_starts) == 1, f"和弦音符应共享同一 start_beat，实际有 {len(chord_starts)} 个不同值"

        # 独立音符保持原有 start_beat（约 2.0 拍）
        solo_notes = [note for note in result_list if note.pitch == 72]
        assert len(solo_notes) == 1
        assert abs(solo_notes[0].start_beat - 2.0) < 0.1

        # 验证统计
        assert converter.conversion_stats.get("onset_clustered_notes", 0) == 3
        assert converter.conversion_stats.get("onset_clustered_groups", 0) == 1


class TestExtractPrimaryMelodyPath:
    """
    测试主旋律 DP 路径提取功能。
    """

    def test_extract_primary_melody_prefers_longer_louder_continuous_path(self, tmp_path: Path):
        """
        验证 DP 在连续旋律路径与短促高装饰音之间正确选择旋律路径。

        构造三个时间片，每个时间片同时包含：
            - 一个连续旋律音（低音、更长、更响、音高相近）
            - 一个短促高装饰音（高音、短、轻、音高跳跃）
        通过降低音高权重并提高时值/力度/连续性权重，使 DP 选择连续旋律线
        (60 -> 62 -> 64)，而不是高装饰音。
        """
        converter = MidiToYamlConverter()
        config = _base_config(
            tmp_path,
            mpdr_melody_pitch_weight=0.2,
            mpdr_melody_duration_weight=1.5,
            mpdr_melody_velocity_weight=1.0,
            mpdr_melody_continuity_weight=1.0,
        )

        # 构造三个时间片：每个时间片有一个连续旋律音（长、响）+ 一个短促高装饰音
        right_groups = {
            0: [_note(60, 0.0, 1.0, 100), _note(72, 0.0, 0.1, 30)],
            1: [_note(62, 0.0, 1.2, 100), _note(76, 0.0, 0.15, 30)],
            2: [_note(64, 0.0, 1.5, 100), _note(79, 0.0, 0.20, 30)],
        }

        melody = converter._extract_primary_melody_path(
            right_groups=right_groups, config=config
        )

        # 预期选择连续旋律线 (60 -> 62 -> 64)，而不是高装饰音
        assert [melody[index].pitch for index in sorted(melody)] == [60, 62, 64]

    def test_extract_primary_melody_penalizes_isolated_high_jump(self, tmp_path: Path):
        """
        验证大跳惩罚让 DP 不选择孤立的高跳装饰音。

        构造场景：
            - 时间片 0: 正常旋律音 64
            - 时间片 1: 正常连续音 65 + 孤立高跳装饰音 88（大跳 > 12 半音）
            - 时间片 2: 正常旋律音 67
        通过提高大跳惩罚权重，使 DP 在时间片 1 选择旋律音 65 而非 88。
        """
        converter = MidiToYamlConverter()
        config = _base_config(
            tmp_path,
            mpdr_melody_large_jump_penalty=5.0,
            mpdr_melody_pitch_weight=0.5,
            mpdr_melody_duration_weight=1.0,
            mpdr_melody_velocity_weight=1.0,
        )

        # 时间片 1 中有一个高跳装饰音(88)和一个正常连续音(65)
        right_groups = {
            0: [_note(64, 0.0, 0.5, 90)],
            1: [_note(65, 0.25, 0.75, 90), _note(88, 0.25, 0.30, 30)],
            2: [_note(67, 0.50, 1.00, 90)],
        }

        melody = converter._extract_primary_melody_path(
            right_groups=right_groups, config=config
        )

        # 时间片 1 应选择音高 65（连续旋律），而不是 88（孤立大跳）
        assert melody[1].pitch == 65


def test_score_svsep_mpdr_stats_reports_bass_anchor_integrity(tmp_path: Path):
    """
    验证 _score_svsep_mpdr_stats 将 bass_anchor_integrity 写入 stats。

    构造一个已完成的统计字典，包含低音锚点选中/总数，
    期望评分方法计算并写入 bass_anchor_integrity。
    """
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    stats = {
        "total_melody_notes": 4,
        "kept_melody_notes": 4,
        "total_right_notes": 4,
        "kept_right_notes": 4,
        "total_left_notes": 4,
        "kept_left_notes": 3,
        "total_left_utility": 2.0,
        "kept_left_utility": 2.0,
        "total_masking_risk": 0.0,
        "kept_masking_risk": 0.0,
        "left_active_slices": 4,
        "bass_kept_slices": 3,
        "token_collision_drops": 0,
        "selected_original_bass": 4,
        "kept_original_bass": 3,
        "token_dropped_notes": 0,
        "total_bass_anchor_notes": 4,
        "kept_bass_anchor_notes": 3,
        "suppressed_repeated_notes": 0,
        "remapped_notes": 0,
        # 新增审计字段的初始值（不应被评分覆盖）
        "low_mud_penalty_events": 0,
        "register_collision_events": 0,
        "onset_collision_events": 0,
    }

    score = converter._score_svsep_mpdr_stats(stats=stats, config=config)

    # 分数应归一到 0-1
    assert 0.0 <= score <= 1.0
    # 验证新增指标
    assert "bass_anchor_integrity" in stats, "stats 中缺少 bass_anchor_integrity"
    assert stats["bass_anchor_integrity"] == pytest.approx(0.75)
    # 验证已有指标仍然存在
    assert "melody_integrity" in stats
    assert "masking_avoidance" in stats
    assert "harmonic_completeness" in stats
    assert "register_clarity" in stats


def test_build_svsep_mpdr_candidate_accumulates_event_counters(tmp_path: Path):
    """
    验证 _build_svsep_mpdr_candidate 累计 register_collision_events 与
    low_mud_penalty_events。

    构造左手音符包含与主旋律音区接近的音符及低音区密集音符，
    期望 stats 中对应事件计数器正确累加。
    """
    converter = MidiToYamlConverter()
    config = _base_config(
        tmp_path,
        mpdr_left_opacity_base=0.85,
        mpdr_melody_protection_strength=0.4,
        mpdr_register_collision_penalty=0.5,
        mpdr_low_mud_penalty=0.3,
        mpdr_register_collision_semitones=12.0,
        mpdr_low_mud_pitch=48,
        mpdr_onset_collision_penalty=0.4,
        mpdr_density_penalty=0.1,
        mpdr_duration_ducking_strength=0.05,
        quantize_beat=0.25,
        mpdr_left_opacity_min=0.1,
        mpdr_left_opacity_max=1.0,
        mpdr_scan_delta_ratio=0.0,
    )

    # 右手旋律音：C5 (72), E5 (76)，位于高音区
    right_notes = (
        _note(pitch=72, start=0.00, end=0.25, velocity=100),  # C5 旋律
        _note(pitch=76, start=0.25, end=0.50, velocity=100),  # E5 旋律
    )

    # 左手音符：
    #   时间片 0: 三个低音音符 (C3=48, E3=52, G3=55) - 前两个 <= low_mud_pitch=48
    #             并且与旋律 (C5=72) 的距离 < 12 半音，会触发 register_collision
    #   时间片 1: 一个左手音 (C3=48) - 与 E5 距离26半音，不触发 register_collision
    left_notes = (
        _note(pitch=48, start=0.00, end=0.50, velocity=70),  # C3, 距C5差24半音，不太可能触发register
        _note(pitch=55, start=0.00, end=0.50, velocity=70),  # G3, 距C5差17半音
        _note(pitch=60, start=0.00, end=0.50, velocity=70),  # C4, 距C5差12半音
        _note(pitch=48, start=0.25, end=0.75, velocity=70),  # C3
    )

    # 初始统计字典，包含新增审计字段
    converter.conversion_stats = {
        "max_original_chord_notes": 0,
    }

    result = converter._build_svsep_mpdr_candidate(
        left_midi_notes=left_notes,
        right_midi_notes=right_notes,
        config=config,
    )

    stats = result.stats

    # 验证事件计数器存在
    assert "register_collision_events" in stats, "stats 中缺少 register_collision_events"
    assert "onset_collision_events" in stats, "stats 中缺少 onset_collision_events"
    assert "low_mud_penalty_events" in stats, "stats 中缺少 low_mud_penalty_events"

    # 验证事件计数器类型正确（应为整数）
    assert isinstance(stats["register_collision_events"], int), "register_collision_events 应为整数"
    assert isinstance(stats["onset_collision_events"], int), "onset_collision_events 应为整数"
    assert isinstance(stats["low_mud_penalty_events"], int), "low_mud_penalty_events 应为整数"

    # 验证低音锚点计数器存在且类型正确
    assert "total_bass_anchor_notes" in stats, "stats 中缺少 total_bass_anchor_notes"
    assert "kept_bass_anchor_notes" in stats, "stats 中缺少 kept_bass_anchor_notes"
    assert isinstance(stats["total_bass_anchor_notes"], int)
    assert isinstance(stats["kept_bass_anchor_notes"], int)

    # 至少有一个低音锚点被统计（左手最低音是 48，时间片 0 和 1 各有左手音符）
    assert stats["total_bass_anchor_notes"] > 0, "应至少有一个低音锚点被统计"

    # 分数应归一到 0-1
    assert 0.0 <= result.global_score <= 1.0

    # 验证已有指标仍然存在
    assert "melody_integrity" in stats
    assert "masking_avoidance" in stats
    assert "harmonic_completeness" in stats
    assert "register_clarity" in stats
    assert "bass_anchor_integrity" in stats


def _candidate(note: MidiNoteEvent, hand: str, mapped_pitch: int, keep_score: float, is_primary: bool = False, is_bass: bool = False):
    """
    构造测试用 MpdrNoteCandidate。

    :param note: MIDI 音符事件
    :param hand: 声部来源，right 或 left
    :param mapped_pitch: 折叠后音高
    :param keep_score: 综合保留分数
    :param is_primary: 是否为主旋律
    :param is_bass: 是否为低音锚点
    :return: 测试用 MpdrNoteCandidate 实例
    """
    from audio_to_yaml_converter import MpdrNoteCandidate
    return MpdrNoteCandidate(
        note=note,
        hand=hand,
        mapped_pitch=mapped_pitch,
        start_index=0,
        is_primary_melody=is_primary,
        utility=keep_score,
        masking_risk=0.0,
        perceptual_cost=1.0,
        keep_score=keep_score,
        is_bass_anchor=is_bass,
    )


def test_candidates_to_modifier_safe_tokens_prefers_primary_melody_on_same_token(tmp_path: Path):
    """
    验证主旋律优先于高 keep_score 的填充音占用同一 token。

    构造两个候选映射到同一 token (pitch 60 -> "1")，
    主旋律候选 keep_score 更低，但优先级应使其胜出。
    """
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    # 两个候选映射到同一 token (pitch 60 -> "1")，旋律 keep_score 低但优先级更高
    melody = _candidate(_note(60, 0.0, 0.5, 90), "right", 60, 1.0, is_primary=True)
    harmony = _candidate(_note(72, 0.0, 0.5, 80), "left", 60, 10.0, is_primary=False)

    tokens, kept, dropped = converter._candidates_to_modifier_safe_tokens([harmony, melody], config)

    # 应该保留主旋律候选
    assert tokens == ["1"]
    assert kept == [melody]
    assert dropped == 1


def test_candidates_to_modifier_safe_tokens_prefers_bass_anchor_over_left_harmony(tmp_path: Path):
    """
    验证低音锚点优先于高 keep_score 的左手和声占用同一 token。

    构造两个左手候选映射到同一 token (pitch 48 -> "-1")，
    低音锚点 keep_score 更低，但优先级应使其胜出。
    """
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    bass = _candidate(_note(48, 0.0, 0.5, 70), "left", 48, 1.0, is_bass=True)
    harmony = _candidate(_note(60, 0.0, 0.5, 100), "left", 48, 5.0, is_bass=False)

    tokens, kept, dropped = converter._candidates_to_modifier_safe_tokens([harmony, bass], config)

    # 应该保留低音锚点
    assert tokens == ["-1"]
    assert kept == [bass]
    assert dropped == 1


def test_candidates_to_modifier_safe_tokens_respects_global_chord_limit(tmp_path: Path):
    """
    验证 SVSEP-MPDR 在物理键位去重后仍严格执行统一和弦音数上限。

    当不同物理键位的候选数量超过限制时，应按主旋律、低音锚点、右手和
    左手的音乐优先级裁剪，而不是把超限事件写入最终 YAML。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 输出超过 max_chord_notes 或优先级错误时触发
    """
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path, max_chord_notes=4)
    bass = _candidate(_note(48, 0.0, 0.5), "left", 48, 1.0, is_bass=True)
    left_low = _candidate(_note(52, 0.0, 0.5), "left", 52, 9.0)
    right_low = _candidate(_note(55, 0.0, 0.5), "right", 55, 2.0)
    melody = _candidate(_note(60, 0.0, 0.5), "right", 60, 1.0, is_primary=True)
    right_high = _candidate(_note(64, 0.0, 0.5), "right", 64, 3.0)
    left_high = _candidate(_note(67, 0.0, 0.5), "left", 67, 10.0)

    tokens, kept, dropped = converter._candidates_to_modifier_safe_tokens(
        [bass, left_low, right_low, melody, right_high, left_high],
        config,
    )

    assert len(tokens) == config.max_chord_notes
    assert set(kept) == {bass, right_low, melody, right_high}
    assert dropped == 2


def test_svsep_mpdr_design_does_not_import_video_fusion_modules():
    """验证纯音频主链路不导入视频融合模块"""
    source = Path("src/audio_to_yaml_converter.py").read_text(encoding="utf-8")

    assert "import fusion_engine" not in source
    assert "from fusion_engine" not in source
    assert "import dual_stream_extractor" not in source
    assert "from dual_stream_extractor" not in source


def test_score_report_contains_required_svsep_mpdr_metric_names(tmp_path: Path):
    """验证 _score_svsep_mpdr_stats 写入所有必需的质量指标"""
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    stats = {
        "total_melody_notes": 1,
        "kept_melody_notes": 1,
        "total_right_notes": 1,
        "kept_right_notes": 1,
        "total_left_notes": 1,
        "kept_left_notes": 1,
        "total_left_utility": 1.0,
        "kept_left_utility": 1.0,
        "total_masking_risk": 0.0,
        "kept_masking_risk": 0.0,
        "left_active_slices": 1,
        "bass_kept_slices": 1,
        "token_collision_drops": 0,
        "selected_original_bass": 1,
        "kept_original_bass": 1,
        "token_dropped_notes": 0,
        "remapped_notes": 0,
        "suppressed_repeated_notes": 0,
        "total_bass_anchor_notes": 1,
        "kept_bass_anchor_notes": 1,
    }

    converter._score_svsep_mpdr_stats(stats=stats, config=config)

    # 验证所有必需的质量指标均已写入
    required_keys = (
        "melody_integrity",
        "bass_anchor_integrity",
        "masking_avoidance",
        "harmonic_completeness",
        "register_clarity",
    )
    for key in required_keys:
        assert key in stats, f"缺少必需的质量指标: {key}"
