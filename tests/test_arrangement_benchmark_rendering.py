"""
模块名称：test_arrangement_benchmark_rendering
功能描述：
    使用真实 MIDI、真实采样 SoundFont、FluidSynth 与 ffmpeg 验证离线钢琴渲染。

主要组件：
    - test_real_midi_renders_to_wav_and_mp3: 验证完整真实渲染链路

依赖说明：
    - arrangement_benchmark.rendering: 被测渲染模块
    - soundfile: 回读无损 WAV 元数据

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path

import soundfile

from arrangement_benchmark.config_loader import load_benchmark_config
from arrangement_benchmark.rendering import render_midi_to_audio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_benchmark_20260711.yaml"
REAL_MIDI_PATH = PROJECT_ROOT / "work" / "score_audit" / "cruel_angel" / "target_reduced.mid"


def test_real_midi_renders_to_wav_and_mp3(tmp_path: Path) -> None:
    """
    验证真实 MIDI 可通过真实采样钢琴音源输出可回读 WAV 与 MP3。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 任一真实渲染产物缺失或无效时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    output_wav_path = tmp_path / "rendered_piano.wav"
    output_mp3_path = tmp_path / "rendered_piano.mp3"

    result = render_midi_to_audio(
        midi_path=REAL_MIDI_PATH,
        output_wav_path=output_wav_path,
        output_mp3_path=output_mp3_path,
        soundfont_path=config.soundfont.path,
        fluidsynth_command=config.tools.fluidsynth,
        ffmpeg_command=config.tools.ffmpeg,
        sample_rate=config.rendering.sample_rate,
        mp3_bitrate=config.rendering.mp3_bitrate,
    )

    wav_info = soundfile.info(str(output_wav_path))
    assert output_wav_path.is_file() and output_wav_path.stat().st_size > 0
    assert output_mp3_path.is_file() and output_mp3_path.stat().st_size > 0
    assert wav_info.frames > 0
    assert wav_info.samplerate == result.sample_rate
    assert result.duration_seconds > 0
    assert result.wav_sha256
    assert result.mp3_sha256
