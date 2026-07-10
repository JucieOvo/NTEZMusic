"""
模块名称：arrangement_benchmark.rendering
功能描述：
    使用真实采样钢琴 SoundFont、FluidSynth 和 ffmpeg 离线生成 WAV 与 MP3。

主要组件：
    - AudioRenderResult: 真实渲染产物统计
    - render_midi_to_audio: 执行 MIDI→WAV→MP3 全链路

依赖说明：
    - FluidSynth: 加载真实采样 SoundFont 并生成无损 WAV
    - ffmpeg: 将 WAV 编码为 MP3
    - librosa / soundfile: 回读验证真实音频文件

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import subprocess

import librosa
import soundfile


@dataclass(frozen=True)
class AudioRenderResult:
    """真实钢琴渲染产物与回读统计。"""

    output_wav_path: Path
    output_mp3_path: Path
    sample_rate: int
    duration_seconds: float
    wav_sha256: str
    mp3_sha256: str


def _sha256_file(file_path: Path) -> str:
    """流式计算真实文件 SHA-256。"""
    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_executable(command: str) -> str:
    """解析可执行文件，不存在时直接报错。"""
    command_path = Path(command)
    if command_path.is_absolute():
        if not command_path.is_file():
            raise FileNotFoundError(f"外部工具不存在: {command_path}")
        return str(command_path)
    resolved_command = shutil.which(command)
    if resolved_command is None:
        raise FileNotFoundError(f"外部工具不在 PATH: {command}")
    return resolved_command


def _run_checked(command: list[str], stage_name: str) -> None:
    """执行真实外部命令，失败时保留标准输出和错误信息。"""
    completed_process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed_process.returncode != 0:
        raise RuntimeError(
            f"{stage_name} 失败，退出码 {completed_process.returncode}\n"
            f"stdout:\n{completed_process.stdout}\n"
            f"stderr:\n{completed_process.stderr}"
        )


def render_midi_to_audio(
    midi_path: Path,
    output_wav_path: Path,
    output_mp3_path: Path,
    soundfont_path: Path,
    fluidsynth_command: str,
    ffmpeg_command: str,
    sample_rate: int,
    mp3_bitrate: str,
) -> AudioRenderResult:
    """
    将真实 MIDI 渲染为真实采样钢琴 WAV，并编码为 MP3。

    :param midi_path: 输入 MIDI 路径
    :param output_wav_path: 无损 WAV 输出路径
    :param output_mp3_path: MP3 输出路径
    :param soundfont_path: 许可证已验证的真实采样 SoundFont
    :param fluidsynth_command: FluidSynth 命令或绝对路径
    :param ffmpeg_command: ffmpeg 命令或绝对路径
    :param sample_rate: WAV 采样率
    :param mp3_bitrate: MP3 码率，例如 320k
    :return: 回读验证后的真实产物统计
    :raises FileNotFoundError: MIDI、SoundFont 或工具不存在时触发
    :raises RuntimeError: 渲染、编码或回读失败时触发
    """
    if not midi_path.is_file():
        raise FileNotFoundError(f"渲染输入 MIDI 不存在: {midi_path}")
    if not soundfont_path.is_file():
        raise FileNotFoundError(f"真实采样 SoundFont 不存在: {soundfont_path}")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise ValueError("sample_rate 必须是正整数")
    if not mp3_bitrate[:-1].isdigit() or not mp3_bitrate.endswith("k"):
        raise ValueError("mp3_bitrate 格式非法")

    fluidsynth_executable = _resolve_executable(fluidsynth_command)
    ffmpeg_executable = _resolve_executable(ffmpeg_command)
    output_wav_path.parent.mkdir(parents=True, exist_ok=True)
    output_mp3_path.parent.mkdir(parents=True, exist_ok=True)

    _run_checked(
        [
            fluidsynth_executable,
            "-ni",
            "-F",
            str(output_wav_path),
            "-T",
            "wav",
            "-r",
            str(sample_rate),
            str(soundfont_path),
            str(midi_path),
        ],
        "FluidSynth WAV 渲染",
    )
    if not output_wav_path.is_file() or output_wav_path.stat().st_size == 0:
        raise RuntimeError(f"FluidSynth 未生成有效 WAV: {output_wav_path}")

    wav_info = soundfile.info(str(output_wav_path))
    if wav_info.frames <= 0 or wav_info.samplerate != sample_rate:
        raise RuntimeError(f"WAV 回读校验失败: {output_wav_path}")

    _run_checked(
        [
            ffmpeg_executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(output_wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            mp3_bitrate,
            str(output_mp3_path),
        ],
        "ffmpeg MP3 编码",
    )
    if not output_mp3_path.is_file() or output_mp3_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg 未生成有效 MP3: {output_mp3_path}")

    mp3_samples, mp3_sample_rate = librosa.load(str(output_mp3_path), sr=None, mono=False)
    if mp3_samples.size == 0 or mp3_sample_rate <= 0:
        raise RuntimeError(f"MP3 回读校验失败: {output_mp3_path}")

    return AudioRenderResult(
        output_wav_path=output_wav_path,
        output_mp3_path=output_mp3_path,
        sample_rate=wav_info.samplerate,
        duration_seconds=wav_info.duration,
        wav_sha256=_sha256_file(output_wav_path),
        mp3_sha256=_sha256_file(output_mp3_path),
    )
