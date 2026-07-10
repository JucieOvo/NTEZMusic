"""
模块名称：test_arrangement_benchmark_config
功能描述：
    使用正式双歌曲基准配置验证配置加载、真实输入文件和固定实验约束。

主要组件：
    - test_load_official_benchmark_config: 验证正式配置可被严格加载

依赖说明：
    - arrangement_benchmark.config_loader: 被测配置加载器
    - pathlib: 定位项目内正式配置与真实 MP3

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

import math
from pathlib import Path

from arrangement_benchmark.config_loader import load_benchmark_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_benchmark_20260711.yaml"


def test_load_official_benchmark_config() -> None:
    """
    验证正式基准配置与用户指定的两首真实 MP3 完全一致。

    :return: 无返回值
    :raises AssertionError: 当配置、真实输入或固定实验约束不符合设计时触发
    """
    config = load_benchmark_config(CONFIG_PATH)

    assert config.experiment_id == "arrangement_benchmark_20260711"
    assert config.output_root == PROJECT_ROOT / "work" / "arrangement_benchmark_20260711"
    assert tuple(song.slug for song in config.songs) == ("cruel_angel", "tada_koe_hitotsu")
    assert all(song.source_mp3.is_file() for song in config.songs)
    assert tuple(candidate.mode for candidate in config.candidates) == (
        "octave_fold",
        "adaptive_octave_fold",
        "hands_decoupled",
        "attention_weighted",
        "svsep_mpdr",
    )
    assert config.score_aware_mode == "score_aware_theory"
    assert config.common_parameters.max_chord_notes == 6
    assert config.common_parameters.left_max_chord_notes == 3
    assert 1 <= config.reduced_velocity <= 127
    assert math.isclose(sum(config.metric_weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-9)
    assert config.metric_weights == {
        "melody": 0.25,
        "harmony": 0.20,
        "rhythm": 0.20,
        "bass": 0.15,
        "voice_register": 0.10,
        "audio_features": 0.10,
    }
    assert config.soundfont.path.is_absolute()
    assert config.soundfont.source_url.startswith("https://")
    assert config.soundfont.expected_sha256 == "712d0e681efbe5203a8014e9b3e84168f1908c82f2f6fb13bd2c77d6d72c70b7"
    assert config.tools.fluidsynth
    assert Path(config.tools.ffmpeg).is_file()
    assert config.rendering.sample_rate == 44100
    assert config.rendering.mp3_bitrate == "320k"
    assert config.audio_metrics.frame_seconds == 0.1
    assert config.audio_metrics.n_mfcc == 20
    assert config.runtime.python_path.is_file()
    assert config.runtime.raw_midi_generator_path.is_file()
    assert config.runtime.svsep_model_path.is_file()
    assert config.runtime.transcription_device == "cuda"
    assert config.runtime.svsep_device == "cuda"
