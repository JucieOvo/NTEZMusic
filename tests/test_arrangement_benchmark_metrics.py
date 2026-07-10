"""
模块名称：test_arrangement_benchmark_metrics
功能描述：
    使用真实原始 MIDI 与真实缩编 MIDI 验证四项 MIDI 结构保真指标。

主要组件：
    - test_real_midi_pair_produces_finite_structural_metrics: 验证真实 MIDI 指标

依赖说明：
    - audio_to_yaml_converter.AudioPipelineConfig: 使用当前算法正式默认参数
    - arrangement_benchmark.metrics: 被测指标模块

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

import math
from pathlib import Path

from arrangement_benchmark.config_loader import load_benchmark_config
from arrangement_benchmark.metrics import (
    calculate_audio_fidelity,
    calculate_midi_fidelity,
    calculate_weighted_score,
)
from arrangement_benchmark.rendering import render_midi_to_audio
from audio_to_yaml_converter import AudioPipelineConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_MIDI = PROJECT_ROOT / "work" / "score_audit" / "cruel_angel" / "existing_reference.mid"
REDUCED_MIDI = PROJECT_ROOT / "work" / "score_audit" / "cruel_angel" / "target_reduced.mid"
CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_benchmark_20260711.yaml"


def _build_real_metric_config() -> AudioPipelineConfig:
    """
    使用正式基准固定参数构造指标所需的现有转换器配置。

    :return: 可复用当前 MPDR 主旋律路径算法的真实配置
    """
    return AudioPipelineConfig(
        audio_path=PROJECT_ROOT / "【Animenz】残酷天使的行动纲领 – 新世纪福音战士 OP1 钢琴版.mp3",
        input_midi_path=REFERENCE_MIDI,
        output_yaml_path=PROJECT_ROOT / "config" / "latest_cruel_angel_thesis.yaml",
        work_dir=PROJECT_ROOT / "work" / "arrangement_benchmark_20260711" / "metric_test",
        song_name="残酷天使的行动纲领",
        bpm=129.2,
        beat_unit=4,
        start_delay_seconds=3.0,
        key_press_seconds=0.0,
        demucs_model="htdemucs_6s",
        demucs_stem="piano",
        transcription_checkpoint=None,
        transcription_device="cuda",
        transcription_segment_hop_size=None,
        transcription_segment_size=None,
        quantize_beat=0.25,
        max_chord_notes=6,
        max_score_events=5000,
        allow_accidentals=True,
        out_of_range_policy="octave_fold",
        pitch_compression_mode="attention_weighted",
        ref_smoothing=0.2,
        left_max_chord_notes=3,
        phrase_gap_beats=0.5,
        global_trend_alpha=0.05,
        global_trend_window_beats=4.0,
    )


def test_real_midi_pair_produces_finite_structural_metrics() -> None:
    """
    验证真实残酷天使原始/缩编 MIDI 可产生有限且归一化的结构指标。

    :return: 无返回值
    :raises AssertionError: 指标缺失、非有限或超出 [0,1] 时触发
    """
    result = calculate_midi_fidelity(
        reference_midi_path=REFERENCE_MIDI,
        reduced_midi_path=REDUCED_MIDI,
        config=_build_real_metric_config(),
    )

    assert result.reference_note_count > result.reduced_note_count > 0
    assert set(result.values) == {"melody", "rhythm", "bass", "voice_register"}
    assert all(math.isfinite(value) for value in result.values.values())
    assert all(0.0 <= value <= 1.0 for value in result.values.values())


def test_real_rendered_audio_completes_six_dimension_score(tmp_path: Path) -> None:
    """
    使用同一真实 SoundFont 渲染原始/缩编 MIDI，并完成六维加权总分。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 音频指标或六维总分无效时触发
    """
    benchmark_config = load_benchmark_config(CONFIG_PATH)
    reference_render = render_midi_to_audio(
        midi_path=REFERENCE_MIDI,
        output_wav_path=tmp_path / "reference.wav",
        output_mp3_path=tmp_path / "reference.mp3",
        soundfont_path=benchmark_config.soundfont.path,
        fluidsynth_command=benchmark_config.tools.fluidsynth,
        ffmpeg_command=benchmark_config.tools.ffmpeg,
        sample_rate=benchmark_config.rendering.sample_rate,
        mp3_bitrate=benchmark_config.rendering.mp3_bitrate,
    )
    reduced_render = render_midi_to_audio(
        midi_path=REDUCED_MIDI,
        output_wav_path=tmp_path / "reduced.wav",
        output_mp3_path=tmp_path / "reduced.mp3",
        soundfont_path=benchmark_config.soundfont.path,
        fluidsynth_command=benchmark_config.tools.fluidsynth,
        ffmpeg_command=benchmark_config.tools.ffmpeg,
        sample_rate=benchmark_config.rendering.sample_rate,
        mp3_bitrate=benchmark_config.rendering.mp3_bitrate,
    )
    midi_result = calculate_midi_fidelity(
        reference_midi_path=REFERENCE_MIDI,
        reduced_midi_path=REDUCED_MIDI,
        config=_build_real_metric_config(),
    )
    audio_result = calculate_audio_fidelity(
        reference_wav_path=reference_render.output_wav_path,
        reduced_wav_path=reduced_render.output_wav_path,
        frame_seconds=benchmark_config.audio_metrics.frame_seconds,
        n_mfcc=benchmark_config.audio_metrics.n_mfcc,
    )
    six_dimensions = {**midi_result.values, **audio_result.values}
    weighted_score = calculate_weighted_score(six_dimensions, benchmark_config.metric_weights)

    assert set(audio_result.values) == {"harmony", "audio_features"}
    assert set(six_dimensions) == set(benchmark_config.metric_weights)
    assert all(0.0 <= value <= 1.0 for value in audio_result.values.values())
    assert math.isfinite(weighted_score)
    assert 0.0 <= weighted_score <= 1.0
