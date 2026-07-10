"""
模块名称：arrangement_benchmark.models
功能描述：
    定义多歌曲缩编算法基准实验使用的不可变配置数据模型。

主要组件：
    - SongSpec: 单首真实输入歌曲定义
    - CandidateSpec: 单个正式候选算法定义
    - CommonParameters: 所有算法共享的固定参数
    - SoundFontSpec: 真实采样钢琴音源声明
    - ToolSpec: 外部工具命令声明
    - BenchmarkConfig: 完整实验配置

依赖说明：
    - dataclasses: 构建冻结数据模型
    - pathlib: 保存 Windows 绝对路径

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class SongSpec:
    """
    单首真实输入歌曲定义。

    :param slug: 目录与报告使用的稳定英文标识
    :param name: 用户可读曲名
    :param source_mp3: 原始 MP3 绝对路径
    """

    slug: str
    name: str
    source_mp3: Path


@dataclass(frozen=True)
class CandidateSpec:
    """
    正式候选缩编算法定义。

    :param mode: 传递给 MidiToYamlConverter 的压缩模式名称
    """

    mode: str


@dataclass(frozen=True)
class CommonParameters:
    """
    所有候选算法共享的固定参数。

    这些参数集中来自实验 YAML，避免基准实现自行写入魔法数字。
    """

    beat_unit: int
    start_delay_seconds: float
    key_press_seconds: float
    quantize_beat: float
    max_chord_notes: int
    max_score_events: int
    allow_accidentals: bool
    out_of_range_policy: str
    ref_smoothing: float
    left_max_chord_notes: int
    phrase_gap_beats: float
    global_trend_alpha: float
    global_trend_window_beats: float


@dataclass(frozen=True)
class SoundFontSpec:
    """
    真实采样大钢琴音源声明。

    :param path: 执行阶段保存或读取 SoundFont 的绝对路径
    :param source_url: 许可证清晰的上游来源页面
    :param license_url: 上游许可证说明页面
    """

    path: Path
    source_url: str
    license_url: str
    expected_sha256: str


@dataclass(frozen=True)
class ToolSpec:
    """
    外部渲染工具命令声明。

    :param fluidsynth: FluidSynth 可执行文件名或绝对路径
    :param ffmpeg: ffmpeg 可执行文件名或绝对路径
    """

    fluidsynth: str
    ffmpeg: str


@dataclass(frozen=True)
class RenderingParameters:
    """
    真实钢琴离线渲染参数。

    :param sample_rate: FluidSynth 输出 WAV 采样率
    :param mp3_bitrate: ffmpeg 输出 MP3 目标码率
    """

    sample_rate: int
    mp3_bitrate: str


@dataclass(frozen=True)
class AudioMetricParameters:
    """
    WAV 音频指标提取参数。

    :param frame_seconds: 特征帧移对应的秒数
    :param n_mfcc: MFCC 维数
    """

    frame_seconds: float
    n_mfcc: int


@dataclass(frozen=True)
class RuntimeParameters:
    """
    音频前端与候选算法真实执行环境。

    :param python_path: 固定 Python 3.10 可执行文件
    :param raw_midi_generator_path: 原始 B MIDI 生成器脚本
    :param svsep_model_path: piano_svsep 模型权重
    :param transcription_checkpoint: 可选 Transkun 自定义权重
    :param transcription_device: Transkun 固定推理设备
    :param svsep_device: piano_svsep 固定推理设备
    :param demucs_model: Demucs 固定模型
    :param demucs_stem: Demucs 固定目标 stem
    """

    python_path: Path
    raw_midi_generator_path: Path
    svsep_model_path: Path
    transcription_checkpoint: Path | None
    transcription_device: str
    svsep_device: str
    demucs_model: str
    demucs_stem: str


@dataclass(frozen=True)
class BenchmarkConfig:
    """
    完整多歌曲基准实验配置。

    :param experiment_id: 实验唯一标识
    :param project_root: 项目根目录
    :param output_root: 全部实验产物根目录
    :param reduced_velocity: 缩编 MIDI 统一力度
    :param songs: 两首真实输入歌曲
    :param candidates: 五个正式候选算法
    :param score_aware_mode: 需要先通过就绪门槛的实验模式
    :param metric_weights: 六维指标固定权重
    :param common_parameters: 候选算法共享参数
    :param soundfont: 真实采样钢琴音源声明
    :param tools: 外部工具命令
    """

    experiment_id: str
    project_root: Path
    output_root: Path
    reduced_velocity: int
    songs: tuple[SongSpec, ...]
    candidates: tuple[CandidateSpec, ...]
    score_aware_mode: str | None
    metric_weights: Mapping[str, float]
    common_parameters: CommonParameters
    soundfont: SoundFontSpec
    tools: ToolSpec
    rendering: RenderingParameters
    audio_metrics: AudioMetricParameters
    runtime: RuntimeParameters
