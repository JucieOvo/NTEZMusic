"""
模块名称：audio_to_yaml_converter
功能描述：
    提供从真实音频到 YAML 曲谱的离线转换流水线。
    流水线依次执行 Demucs 分轨、钢琴音频转 MIDI、MIDI 到项目 YAML 曲谱转换。

主要组件：
    - AudioPipelineConfig: 音频转换流水线配置
    - DemucsSeparator: Demucs 真实分轨执行器
    - PianoTranscriber: 钢琴音频转 MIDI 执行器
    - MidiToYamlConverter: MIDI 到 YAML 曲谱转换器
    - AudioToYamlPipeline: 端到端转换流水线调度器

    依赖说明：
        - PyYAML: 用于写出 YAML 曲谱文件
        - pretty_midi: 用于读取真实 MIDI note 事件
        - librosa: 用于读取音频采样数组
        - demucs: 用于真实音频分轨，需要通过命令行调用
        - transkun: 用于真实钢琴音频转 MIDI (Neural Semi-CRF Transformer V2)

作者：JucieOvo
创建日期：2026-04-27
修改记录：
    - 2026-04-27 JucieOvo: 新增音频分轨、钢琴转录与 YAML 转换流水线
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pretty_midi
import yaml
import librosa

from piano_auto_player import PianoConfigLoader
from score_aware_theory_pipeline import ScoreAwareTheoryMvpPipeline
from score_model import ScoreRegularizationConfig


AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac"}


def _stage_demucs_input_if_needed(audio_path: Path, work_dir: Path) -> Path:
    """
    为 Windows 不允许作为目录名结尾的音频 stem 创建真实暂存副本。

    Demucs 使用输入文件 stem 作为输出目录名。Windows 会自动裁剪目录名末尾的
    句点与空格，导致 libsndfile 无法打开 Demucs 计算出的目标路径。仅当 stem
    含有该类尾随字符时，本函数才复制真实输入文件；普通文件直接返回原路径。

    :param audio_path: 已存在的真实音频输入路径
    :param work_dir: 当前流水线工作目录
    :return: 可安全交给 Demucs 的原路径或真实暂存副本路径
    :raises FileNotFoundError: 输入音频不存在时触发
    :raises OSError: 暂存目录创建或真实文件复制失败时触发
    """
    if not audio_path.is_file():
        raise FileNotFoundError(f"输入音频不存在: {audio_path}")

    safe_stem = audio_path.stem.rstrip(". ")
    if safe_stem == audio_path.stem:
        return audio_path
    if not safe_stem:
        safe_stem = "demucs_input"

    staging_dir = work_dir / "demucs_input"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / f"{safe_stem}{audio_path.suffix}"
    shutil.copy2(audio_path, staged_path)
    return staged_path


def _find_matching_audio(midi_path: Path) -> list[Path]:
    """
    在 MIDI 文件所在目录及项目根目录中查找同名音频文件。

    用于 BPM 自动检测：当 MIDI 速度为转录工具默认值 120 时，
    回退到原始音频文件的 librosa tempo 检测。

    :param midi_path: MIDI 文件路径
    :return: 匹配到的音频文件路径列表（按优先级排序）
    """
    candidates: list[Path] = []
    midi_stem = midi_path.stem

    # 搜索目录：MIDI 文件目录、父目录、项目根目录
    search_dirs: list[Path] = []
    search_dirs.append(midi_path.parent)
    if midi_path.parent.parent != midi_path.parent:
        search_dirs.append(midi_path.parent.parent)
    # 项目根目录（向上找到包含 src/ 的目录）
    current = midi_path.parent
    for _ in range(5):
        if (current / "src").is_dir():
            search_dirs.append(current)
            break
        if current.parent == current:
            break
        current = current.parent

    for search_dir in search_dirs:
        try:
            for entry in search_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                # 精确前缀匹配：短的一方是长的一方的开始（处理"曲名.mp3"与"曲名 – 详细标题.mid"）
                if entry.stem.startswith(midi_stem) or midi_stem.startswith(entry.stem):
                    # 前缀长度越长的越优先（更精确的匹配）
                    candidates.append(entry)
        except (OSError, PermissionError):
            continue

    # 按匹配前缀长度降序排列
    candidates.sort(key=lambda p: min(len(p.stem), len(midi_path.stem)), reverse=True)
    return candidates


def _estimate_audio_bpm(audio_path: Path) -> float:
    """
    从真实音频文件估计全局 BPM。

    使用 librosa onset 强度包络和动态规划 beat tracker。旧实现调用了不存在的
    librosa.feature.rhythm.tempo 并静默回退到 120，本函数改为失败即报错，避免把
    错误 BPM 写入 YAML。

    :param audio_path: 输入音频路径
    :return: 检测到的 BPM，保留一位小数
    :raises FileNotFoundError: 当音频文件不存在时触发
    :raises RuntimeError: 当 librosa 无法读取或无法估计 BPM 时触发
    """
    if not audio_path.is_file():
        raise FileNotFoundError(f"用于 BPM 检测的音频不存在: {audio_path}")

    try:
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo_value, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        if isinstance(tempo_value, (list, tuple)) or hasattr(tempo_value, "__len__"):
            if len(tempo_value) == 0:
                raise RuntimeError("librosa beat_track 未返回 tempo")
            tempo_float = float(tempo_value[0])
        else:
            tempo_float = float(tempo_value)
    except Exception as exc:
        raise RuntimeError(f"音频 BPM 检测失败: {audio_path}") from exc

    if not math.isfinite(tempo_float) or tempo_float <= 0:
        raise RuntimeError(f"音频 BPM 检测结果无效: {tempo_float}")
    if beats is None or len(beats) == 0:
        raise RuntimeError(f"音频 BPM 检测未找到有效 beat: {audio_path}")
    return round(tempo_float, 1)


DEFAULT_KEYBOARD_MAPPING = {
    "high": {"1": "q", "2": "w", "3": "e", "4": "r", "5": "t", "6": "y", "7": "u"},
    "middle": {"1": "a", "2": "s", "3": "d", "4": "f", "5": "g", "6": "h", "7": "j"},
    "low": {"1": "z", "2": "x", "3": "c", "4": "v", "5": "b", "6": "n", "7": "m"},
}

MIN_GAME_PITCH = 48
MAX_GAME_PITCH = 83
MIDI_MAX_VELOCITY = 127.0
NATURAL_PITCH_CLASSES = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
SHARP_PITCH_CLASSES = {1: "#1", 3: "b3", 6: "#4", 8: "#5", 10: "b7"}
SHARP_ONLY_PITCH_CLASSES = {1: "#1", 3: "#2", 6: "#4", 8: "#5", 10: "#6"}
FLAT_ONLY_PITCH_CLASSES = {1: "b2", 3: "b3", 6: "b5", 8: "b6", 10: "b7"}
ZONE_NAMES_BY_OCTAVE = {3: "low", 4: "middle", 5: "high"}
ZONE_PREFIX_BY_NAME = {"low": "-", "middle": "", "high": "+"}
SVSEP_MPDR_MODE = "svsep_mpdr"
SCORE_AWARE_THEORY_MODE = "score_aware_theory"


@dataclass(frozen=True)
class AudioPipelineConfig:
    """
    音频到 YAML 转换流水线配置。

    职责：
        集中保存外部命令、输出路径、乐谱参数与可执行范围限制，避免在转换逻辑中写死业务参数。

    属性：
        audio_path (Path): 输入音频路径
        input_midi_path (Path | None): 已有 MIDI 路径，提供时跳过 Demucs 与钢琴转录
        output_yaml_path (Path): 输出 YAML 曲谱路径
        work_dir (Path): 中间文件与报告输出目录
        song_name (str): YAML 曲谱名称
        bpm (float): 输出曲谱 BPM
        beat_unit (int): 输出曲谱节拍单位
        start_delay_seconds (float): 自动弹奏启动延迟
        key_press_seconds (float): 单次按键保持时间
        demucs_model (str): Demucs 模型名称
        demucs_stem (str): 用于转录的 Demucs stem 名称
         transcription_checkpoint (Path | None): Transkun 模型权重路径 (.pt)，未提供时使用内置默认权重
        transcription_device (str): 转录推理设备，"cpu" 或 "cuda"
        transcription_segment_hop_size (float | None): segment 步长（秒），None 使用模型默认
        transcription_segment_size (float | None): segment 尺寸（秒），None 使用模型默认
        quantize_beat (float): MIDI 事件量化节拍单位
        max_chord_notes (int): 单个和弦最大音符数
        max_score_events (int): 最大 score 事件数量
        allow_accidentals (bool): 是否允许输出升半音 token
        out_of_range_policy (str): 超范围处理策略，当前只允许 error
        pitch_compression_mode (str): 音高压缩模式
        ref_smoothing (float): ref_pitch 指数平滑系数，仅 adaptive_octave_fold / hands_decoupled 使用
        left_max_chord_notes (int): 左手轨 IIP 裁剪后最大音符数，仅 hands_decoupled 使用
        phrase_gap_beats (float): 休止符分割阈值（拍），仅 hands_decoupled 使用
        global_trend_alpha (float): 全局趋势线平滑系数，仅 hands_decoupled 使用
        global_trend_window_beats (float): 全局趋势采样窗口（拍），仅 hands_decoupled 使用
        svsep_model_path (Path | None): piano_svsep 模型权重路径，仅 svsep_mpdr 使用
        svsep_device (str): piano_svsep 推理设备，仅 svsep_mpdr 使用
        mpdr_candidate_count (int): MPDR 参数候选数量上限
        mpdr_scan_delta_ratio (float): MPDR 精排参数扫描相对扰动比例
        mpdr_right_ref_pitch (float): 右手独立折叠参考中心
        mpdr_left_ref_pitch (float): 左手独立折叠参考中心
        mpdr_left_window_high (int): MPDR 左手折叠窗口最高音
        mpdr_right_window_low (int): MPDR 右手折叠窗口最低音
        mpdr_melody_protection_strength (float): 主旋律保护强度
        mpdr_melody_onset_guard_beats (float): 主旋律起音保护窗口拍数
        mpdr_left_opacity_base (float): 左手基础感知密度预算
        mpdr_left_opacity_min (float): 左手最小感知密度预算
        mpdr_left_opacity_max (float): 左手最大感知密度预算
        mpdr_melody_rest_bonus (float): 右手休止时给左手增加的预算
        mpdr_bass_anchor_weight (float): 低音锚点权重
        mpdr_harmony_color_weight (float): 和声色彩音权重
        mpdr_voice_leading_weight (float): 左手声部连接权重
        mpdr_velocity_weight (float): velocity 分析权重
        mpdr_duration_weight (float): 时值分析权重
        mpdr_duplicate_penalty (float): 八度重复惩罚权重
        mpdr_onset_collision_penalty (float): 左手与主旋律同起音惩罚
        mpdr_register_collision_penalty (float): 左手靠近主旋律音区惩罚
        mpdr_register_collision_semitones (float): 音区碰撞判定半音距离
        mpdr_low_mud_penalty (float): 左手低音浑浊惩罚
        mpdr_low_mud_pitch (int): 低音浑浊判定音高阈值
        mpdr_density_penalty (float): 左手局部密度惩罚
        mpdr_duration_ducking_strength (float): 长左手音覆盖主旋律惩罚
        mpdr_repeat_suppression_beats (float): 非主旋律同音重复起音抑制窗口
        mpdr_repeat_overlap_tolerance_beats (float): 判断延音重叠的拍点容差
        merge_sustained_notes (bool): 是否在读取 MIDI 时合并同音重叠碎片
        merge_sustained_gap_beats (float): 同音合并允许的最大间隔拍数
        onset_cluster_enabled (bool): 是否启用 MIDI 起音聚类，将毫秒级偏差的物理和弦合并为同一量化格
        onset_cluster_window_beats (float): 起音聚类窗口拍数，组内相邻音符起音间隔上限
        onset_cluster_max_span_beats (float): 起音聚类最大跨度拍数，组内首尾音符起音间隔上限
         mpdr_score_melody_weight (float): 精排主旋律完整度权重
         mpdr_score_masking_weight (float): 精排遮蔽规避权重
         mpdr_score_harmony_weight (float): 精排和声完整度权重
         mpdr_score_bass_weight (float): 精排低音连续性权重
         mpdr_score_register_weight (float): 精排音区清晰度权重
         sustain_split_enabled (bool): 是否启用长低音续打击键
         sustain_split_threshold_beats (float): 触发拆分的 duration_beats 下限
         sustain_split_max_midpoint_density (int): 中点位置最大同拍音符数
         sustain_split_strike_duration_beats (float): 续打击键的评分用 duration
        mpdr_melody_pitch_weight (float): 主旋律音高显著性权重
        mpdr_melody_duration_weight (float): 主旋律时值权重
        mpdr_melody_velocity_weight (float): 主旋律力度权重
        mpdr_melody_beat_weight (float): 主旋律强拍匹配权重
        mpdr_melody_continuity_weight (float): 主旋律相邻音连续性转移权重
        mpdr_melody_large_jump_penalty (float): 主旋律大跳惩罚权重
        mpdr_melody_repetition_penalty (float): 主旋律同音连续重复惩罚权重
    """

    audio_path: Path | None
    input_midi_path: Path | None
    output_yaml_path: Path
    work_dir: Path
    song_name: str
    bpm: float
    beat_unit: int
    start_delay_seconds: float
    key_press_seconds: float
    demucs_model: str
    demucs_stem: str
    transcription_checkpoint: Path | None
    transcription_device: str
    transcription_segment_hop_size: float | None
    transcription_segment_size: float | None
    quantize_beat: float
    max_chord_notes: int
    max_score_events: int
    allow_accidentals: bool
    out_of_range_policy: str
    pitch_compression_mode: str
    ref_smoothing: float
    left_max_chord_notes: int
    phrase_gap_beats: float
    global_trend_alpha: float
    global_trend_window_beats: float
    svsep_model_path: Path | None = None
    svsep_device: str = "cpu"
    mpdr_candidate_count: int = 12
    mpdr_scan_delta_ratio: float = 0.15
    mpdr_right_ref_pitch: float = 67.0
    mpdr_left_ref_pitch: float = 55.0
    mpdr_left_window_high: int = 67
    mpdr_right_window_low: int = 60
    mpdr_melody_protection_strength: float = 1.0
    mpdr_melody_onset_guard_beats: float = 0.5
    mpdr_left_opacity_base: float = 3.6
    mpdr_left_opacity_min: float = 1.2
    mpdr_left_opacity_max: float = 9.0
    mpdr_melody_rest_bonus: float = 2.5
    mpdr_bass_anchor_weight: float = 1.4
    mpdr_harmony_color_weight: float = 1.1
    mpdr_voice_leading_weight: float = 0.7
    mpdr_velocity_weight: float = 0.45
    mpdr_duration_weight: float = 0.35
    mpdr_duplicate_penalty: float = 0.9
    mpdr_onset_collision_penalty: float = 1.2
    mpdr_register_collision_penalty: float = 1.1
    mpdr_register_collision_semitones: float = 12.0
    mpdr_low_mud_penalty: float = 0.8
    mpdr_low_mud_pitch: int = 48
    mpdr_density_penalty: float = 0.3
    mpdr_duration_ducking_strength: float = 0.7
    mpdr_repeat_suppression_beats: float = 0.5
    mpdr_repeat_overlap_tolerance_beats: float = 0.05
    merge_sustained_notes: bool = True
    merge_sustained_gap_beats: float = 0.25
    onset_cluster_enabled: bool = True
    onset_cluster_window_beats: float = 0.08
    onset_cluster_max_span_beats: float = 0.12
    mpdr_score_melody_weight: float = 0.30
    mpdr_score_masking_weight: float = 0.25
    mpdr_score_harmony_weight: float = 0.20
    mpdr_score_bass_weight: float = 0.15
    mpdr_score_register_weight: float = 0.10
    sustain_split_enabled: bool = False
    sustain_split_threshold_beats: float = 1.0
    sustain_split_max_midpoint_density: int = 3
    sustain_split_strike_duration_beats: float = 0.25
    mpdr_melody_pitch_weight: float = 1.0
    mpdr_melody_duration_weight: float = 0.35
    mpdr_melody_velocity_weight: float = 0.45
    mpdr_melody_beat_weight: float = 0.20
    mpdr_melody_continuity_weight: float = 0.70
    mpdr_melody_large_jump_penalty: float = 0.35
    mpdr_melody_repetition_penalty: float = 0.20


@dataclass(frozen=True)
class MidiNoteEvent:
    """
    MIDI 音符事件。

    职责：
        保存从 MIDI 文件读取到的单个音符信息，为量化、合并和校验提供统一数据结构。

    属性：
        pitch (int): MIDI 音高编号
        start_beat (float): 音符开始拍点
        end_beat (float): 音符结束拍点
        velocity (int): MIDI 力度值 0-127
        duration_beats (float): 音符持续拍数 (end_beat - start_beat)
    """

    pitch: int
    start_beat: float
    end_beat: float
    velocity: int
    duration_beats: float


@dataclass(frozen=True)
class MpdrNoteCandidate:
    """
    MPDR 中间音符候选。

    职责：
        保存单个音符在主旋律保护型动态缩编中的分析结果。
        该结构只服务 svsep_mpdr 新管线，不参与旧压缩模式。

    属性：
        note (MidiNoteEvent): 原始 MIDI 音符事件
        hand (str): 声部来源，right 表示右手，left 表示左手
        start_index (int): 量化后的起音时间片索引
        mapped_pitch (int): 折叠到游戏 36 键音域后的 MIDI 音高
        is_primary_melody (bool): 是否为主旋律路径上的音符
        utility (float): 音符保留效用，越高越应保留
        masking_risk (float): 对主旋律的遮蔽风险，越高越应削弱
        perceptual_cost (float): 在无力度、无延音环境中的感知成本
        keep_score (float): 综合保留分数，用于冲突消解排序
        is_bass_anchor (bool): 是否为左手低音锚点，用于 token 冲突消解优先级判定
    """

    note: MidiNoteEvent
    hand: str
    start_index: int
    mapped_pitch: int
    is_primary_melody: bool
    utility: float
    masking_risk: float
    perceptual_cost: float
    keep_score: float
    is_bass_anchor: bool = False


@dataclass(frozen=True)
class MpdrBuildResult:
    """
    MPDR 候选构建结果。

    职责：
        同时携带候选 YAML token、全局质量分和统计信息，供精排阶段选择最优结果。

    属性：
        grouped_notes (dict[int, list[str]]): 时间片到 YAML token 列表的映射
        global_score (float): 候选全局质量分
        stats (dict[str, float | int]): 候选统计指标
    """

    grouped_notes: dict[int, list[str]]
    global_score: float
    stats: dict[str, float | int]


class DemucsSeparator:
    """
    Demucs 真实分轨执行器。

    职责：
        调用本机 Demucs 命令完成音频分轨，并校验目标 stem 文件真实存在。
    """

    def separate(self, config: AudioPipelineConfig) -> Path:
        """
        执行 Demucs 分轨并返回目标 stem 文件路径。

        :param config: 音频转换流水线配置
        :return: 用于钢琴转录的 stem wav 路径
        :raises FileNotFoundError: 当输入音频不存在或目标 stem 不存在时触发
        :raises RuntimeError: 当 Demucs 命令执行失败时触发
        """
        if config.audio_path is None:
            raise ValueError("执行 Demucs 分轨时必须提供 audio_path")
        if not config.audio_path.is_file():
            raise FileNotFoundError(f"输入音频不存在: {config.audio_path}")

        demucs_input_path = _stage_demucs_input_if_needed(config.audio_path, config.work_dir)
        separated_dir = config.work_dir / "demucs"
        separated_dir.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            "-m",
            "demucs",
            "--name",
            config.demucs_model,
            "--out",
            str(separated_dir),
            str(demucs_input_path),
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Demucs 分轨失败:\n{result.stderr.strip()}")

        stem_path = separated_dir / config.demucs_model / demucs_input_path.stem / f"{config.demucs_stem}.wav"
        if not stem_path.is_file():
            raise FileNotFoundError(f"Demucs 未生成目标 stem: {stem_path}")
        return stem_path


class PianoTranscriber:
    """
    钢琴音频转 MIDI 执行器。

    职责：
        调用 Transkun (Neural Semi-CRF Transformer V2) 的真实转录能力，将钢琴 stem 转为 MIDI 文件。
    """

    def transcribe(self, audio_path: Path, config: AudioPipelineConfig) -> Path:
        """
        将钢琴音频转录为 MIDI。

        使用 Transkun 模型执行推理。采样率按 model.fs 自动适配，支持自定义权重路径与分段参数。

        :param audio_path: Demucs 输出的目标 stem 路径
        :param config: 音频转换流水线配置
        :return: 转录生成的 MIDI 文件路径
        :raises FileNotFoundError: 当音频、权重或配置文件不存在时触发
        :raises RuntimeError: 当依赖缺失或推理失败时触发
        """
        if not audio_path.is_file():
            raise FileNotFoundError(f"钢琴转录输入音频不存在: {audio_path}")
        if config.transcription_checkpoint is not None and not config.transcription_checkpoint.exists():
            raise FileNotFoundError(f"Transkun 权重文件不存在: {config.transcription_checkpoint}")

        midi_dir = config.work_dir / "midi"
        midi_dir.mkdir(parents=True, exist_ok=True)
        midi_stem = audio_path.stem if config.audio_path is None else config.audio_path.stem
        midi_path = midi_dir / f"{midi_stem}.mid"

        # 1. 加载音频数据，保留原始采样率
        raw_audio, original_sr = self._load_audio_samples(audio_path=audio_path)

        # 2. 加载 Transkun 模型与推理设备
        model, device = self._load_transkun_model(config=config)

        # 3. 采样率适配：若不匹配模型要求的采样率，使用 soxr 重采样
        try:
            import soxr
        except ImportError:
            raise RuntimeError(
                "soxr 依赖缺失，无法将音频重采样到 Transkun 模型要求的采样率。"
                "soxr 是 transkun 的传递依赖，请确认 transkun 已正确安装。"
            )
        if original_sr != model.fs:
            raw_audio = soxr.resample(raw_audio, original_sr, model.fs)

        # 4. 将 numpy 数组转为 torch tensor 并执行推理
        try:
            import torch
        except ImportError:
            raise RuntimeError("PyTorch 依赖缺失，Transkun 需要 PyTorch 进行推理")

        audio_tensor = torch.from_numpy(raw_audio).to(device)
        # Transkun 期望输入形状为 (采样数, 通道数)，单声道需补维度
        if audio_tensor.ndim == 1:
            audio_tensor = audio_tensor.unsqueeze(-1)

        try:
            notes_est = model.transcribe(
                audio_tensor,
                stepInSecond=config.transcription_segment_hop_size,
                segmentSizeInSecond=config.transcription_segment_size,
                discardSecondHalf=False,
            )
        except Exception as exc:
            raise RuntimeError(f"Transkun 转录推理失败: {exc}") from exc

        # 5. 将转录结果写入 MIDI 文件
        try:
            from transkun.Data import writeMidi
        except ImportError:
            raise RuntimeError("无法导入 transkun.Data，请确认 transkun 已正确安装")

        output_midi = writeMidi(notes_est)
        output_midi.write(str(midi_path))

        if not midi_path.is_file():
            raise FileNotFoundError(f"Transkun 转录未生成 MIDI: {midi_path}")

        # 6. 若需自动检测 BPM，对输出 MIDI 修正 tempo 轨道
        if config.audio_path is not None:
            self._fix_midi_tempo(midi_path=midi_path, audio_path=config.audio_path)

        return midi_path

    def _load_transkun_model(self, config: AudioPipelineConfig) -> tuple:
        """
        加载 Transkun 模型与推理设备。

        支持两种权重来源：
        1. 用户通过 --transcription-checkpoint 指定的 .pt 文件（同时查找同目录 .conf 配置）
        2. transkun pip 包内置的 pretrained/2.0.pt 与 pretrained/2.0.conf

        :param config: 音频转换流水线配置
        :return: (TransKun 模型实例, torch 设备对象)
        :raises ImportError: 当 transkun 或其依赖未安装时触发
        :raises FileNotFoundError: 当权重或配置文件不存在时触发
        """
        try:
            import os as _os
            import transkun as _transkun
            import moduleconf
            import torch
        except ImportError as exc:
            raise ImportError(f"Transkun 相关依赖缺失: {exc}. 请执行 pip install transkun") from exc

        # 获取 transkun 包安装目录，用于定位内置默认模型权重
        _pkg_dir = _os.path.dirname(_transkun.__file__)

        # 确定权重路径与配置路径
        if config.transcription_checkpoint is not None:
            weight_path = str(config.transcription_checkpoint)
            # 在同目录查找 .conf 配置文件
            conf_dir = config.transcription_checkpoint.parent
            conf_candidates = list(conf_dir.glob("*.conf"))
            if conf_candidates:
                conf_path = str(conf_candidates[0])
            else:
                # 回退到 transkun 内置默认配置
                conf_path = _os.path.join(_pkg_dir, "pretrained", "2.0.conf")
        else:
            weight_path = _os.path.join(_pkg_dir, "pretrained", "2.0.pt")
            conf_path = _os.path.join(_pkg_dir, "pretrained", "2.0.conf")

        # 校验文件存在性
        if not Path(weight_path).exists():
            raise FileNotFoundError(f"Transkun 权重文件不存在: {weight_path}")
        if not Path(conf_path).exists():
            raise FileNotFoundError(f"Transkun 配置文件不存在: {conf_path}")

        # 从配置文件加载模型类定义
        conf_manager = moduleconf.parseFromFile(conf_path)
        TransKun = conf_manager["Model"].module.TransKun
        conf = conf_manager["Model"].config

        # 确定推理设备
        if config.transcription_device == "cuda" and torch.cuda.is_available():
            device = torch.device("cuda")
        else:
            if config.transcription_device == "cuda" and not torch.cuda.is_available():
                print("CUDA 不可用，已回退到 CPU 推理")
            device = torch.device("cpu")

        # 加载模型权重
        checkpoint = torch.load(weight_path, map_location=device)
        model = TransKun(conf=conf).to(device)

        if "best_state_dict" in checkpoint:
            model.load_state_dict(checkpoint["best_state_dict"], strict=False)
        else:
            model.load_state_dict(checkpoint["state_dict"], strict=False)

        model.eval()
        torch.set_grad_enabled(False)

        return model, device

    def _fix_midi_tempo(self, midi_path: Path, audio_path: Path) -> None:
        """
        从原始音频检测 BPM 并写入 MIDI tempo 轨道。

        转录工具默认将 tempo 设为 120，本方法用 librosa 检测真实 BPM，
        覆盖 MIDI 中的 tempo 值，使后续 --input-midi 复用时不需重新检测。

        :param midi_path: 转录生成的 MIDI 文件路径
        :param audio_path: 原始音频文件路径（用于 BPM 检测）
        :raises RuntimeError: 当 BPM 检测或 MIDI tempo 写入失败时触发
        """
        try:
            import mido

            detected_bpm = _estimate_audio_bpm(audio_path=audio_path)
            midi_file = mido.MidiFile(str(midi_path))
            tempo_meta = mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(detected_bpm), time=0)

            has_tempo = False
            for track in midi_file.tracks:
                for message in track:
                    if message.type == "set_tempo":
                        message.tempo = tempo_meta.tempo
                        has_tempo = True
                        break
                if has_tempo:
                    break
            if not has_tempo:
                if not midi_file.tracks:
                    midi_file.tracks.append(mido.MidiTrack())
                midi_file.tracks[0].insert(0, tempo_meta)

            midi_file.save(str(midi_path))
            print(f"MIDI tempo 已更新为 {detected_bpm} BPM（从音频检测）")
        except Exception as exc:
            raise RuntimeError(f"MIDI tempo 写入失败: {midi_path}") from exc

    def _load_audio_samples(self, audio_path: Path) -> tuple:
        """
        读取音频采样数组，保留原始采样率。

        与旧版不同，不再强制采样到固定频率，而是保留原始采样率，
        由 transcribe() 在加载模型后按 model.fs 做按需适配。

        :param audio_path: Demucs 输出的目标 stem 路径
        :return: (音频采样数组, 原始采样率 Hz)
        :raises RuntimeError: 当音频读取失败时触发
        """
        try:
            raw_audio, original_sr = librosa.load(str(audio_path), sr=None, mono=True)
        except Exception as exc:
            raise RuntimeError(f"读取钢琴 stem 音频失败: {audio_path}") from exc
        return raw_audio, original_sr


class MidiToYamlConverter:
    """
    MIDI 到 YAML 曲谱转换器。

    职责：
        读取真实 MIDI 音符事件，将其量化并转换为当前自动弹奏器可校验、可执行的 YAML 曲谱。
    """

    def __init__(self) -> None:
        """
        初始化 MIDI 转换器。

        职责：
            准备转换统计容器，便于报告输出真实压缩、裁剪和迁移结果。
        """
        self.conversion_stats: dict[str, int | str] = {}

    def convert(self, midi_path: Path, config: AudioPipelineConfig) -> dict[str, Any]:
        """
        将 MIDI 文件转换为 YAML 数据结构。

        :param midi_path: 真实 MIDI 文件路径
        :param config: 音频转换流水线配置
        :return: 可直接写入 YAML 的字典
        :raises FileNotFoundError: 当 MIDI 文件不存在时触发
        :raises ValueError: 当 MIDI 为空或输出超出可执行范围时触发
        """
        if not midi_path.is_file():
            raise FileNotFoundError(f"MIDI 文件不存在: {midi_path}")
        if config.pitch_compression_mode == SCORE_AWARE_THEORY_MODE and config.bpm <= 0:
            raise ValueError("score_aware_theory 模式要求 bpm 已经解析为正数；请通过 AudioToYamlPipeline.run 执行自动 BPM 检测")

        midi_data = pretty_midi.PrettyMIDI(str(midi_path))
        midi_notes = self._read_midi_notes(midi_data=midi_data, config=config)
        self.conversion_stats = {
            "pitch_compression_mode": config.pitch_compression_mode,
            "raw_note_count": len(midi_notes),
            "output_note_count": 0,
            "remapped_notes": 0,
            "octave_moved_notes": 0,
            "deduplicated_notes": 0,
            "clipped_notes": 0,
            "max_original_chord_notes": 0,
            "max_output_chord_notes": 0,
            "collisions_avoided": 0,
            "ref_pitch_trajectory_start": 0.0,
            "ref_pitch_trajectory_end": 0.0,
            "onset_clustered_notes": 0,
            "onset_clustered_groups": 0,
        }
        if config.pitch_compression_mode == SCORE_AWARE_THEORY_MODE:
            score_events = self._build_score_aware_theory_score_events(midi_path=midi_path, config=config)
        elif config.pitch_compression_mode == SVSEP_MPDR_MODE:
            left_notes, right_notes = self._separate_hands_with_svsep(midi_path=midi_path, config=config)
            if config.merge_sustained_notes:
                merge_gap = max(0.0, config.merge_sustained_gap_beats)
                left_notes = list(self._merge_sustained_midi_notes(
                    midi_notes=tuple(sorted(left_notes, key=lambda item: (item.pitch, item.start_beat))),
                    config=replace(config, merge_sustained_gap_beats=merge_gap),
                ))
                right_notes = list(self._merge_sustained_midi_notes(
                    midi_notes=tuple(sorted(right_notes, key=lambda item: (item.pitch, item.start_beat))),
                    config=replace(config, merge_sustained_gap_beats=merge_gap),
                ))
            score_events = self._build_svsep_mpdr_score_events(
                left_midi_notes=tuple(sorted(left_notes, key=lambda item: (item.start_beat, item.pitch))),
                right_midi_notes=tuple(sorted(right_notes, key=lambda item: (item.start_beat, item.pitch))),
                config=config,
            )
        else:
            score_events = self._build_score_events(midi_notes=midi_notes, config=config)
        self._validate_playback_timing(score_events=score_events, config=config)

        return {
            "song": {"name": config.song_name, "bpm": config.bpm, "beat_unit": config.beat_unit},
            "playback": {
                "start_delay_seconds": config.start_delay_seconds,
                "key_press_seconds": config.key_press_seconds,
            },
            "keyboard": DEFAULT_KEYBOARD_MAPPING,
            "score": score_events,
        }

    def _build_score_aware_theory_score_events(
        self,
        midi_path: Path,
        config: AudioPipelineConfig,
    ) -> list[dict[str, int | float | str]]:
        """
        使用乐谱语义感知管线构建 YAML score 事件。

        职责：
            将现有 AudioPipelineConfig 转换为 ScoreRegularizationConfig，执行 MIDI 清洗、MusicXML 合规化、
            基础语义分析与 36 键缩编，并返回现有自动弹奏器可解析的 score 事件。

        :param midi_path: 输入 MIDI 路径
        :param config: 音频转换流水线配置
        :return: YAML score 事件列表
        """
        score_work_dir = config.work_dir / "score_aware_theory"
        result = ScoreAwareTheoryMvpPipeline().run(
            midi_path=midi_path,
            config=ScoreRegularizationConfig(
                bpm=config.bpm,
                beat_unit=config.beat_unit,
                quantize_beat=config.quantize_beat,
                time_signature=f"4/{config.beat_unit}",
                key_signature_sharps=0,
                merge_sustained_gap_beats=config.merge_sustained_gap_beats,
                onset_cluster_window_beats=config.onset_cluster_window_beats,
                min_noise_duration_beats=0.03,
                min_noise_velocity=8,
                work_dir=score_work_dir,
            ),
            output_musicxml_path=score_work_dir / "generated.musicxml",
            validation_report_path=score_work_dir / "notation_validation_report.json",
            build_arrangement=True,
            max_chord_notes=config.max_chord_notes,
            allow_accidentals=config.allow_accidentals,
        )
        if result.arrangement_result is None:
            raise RuntimeError("score_aware_theory 未生成 36 键缩编结果")
        self.conversion_stats.update(
            {
                "pitch_compression_mode": SCORE_AWARE_THEORY_MODE,
                "output_note_count": result.arrangement_result.report.output_note_count,
                "remapped_notes": result.arrangement_result.report.octave_moved_note_count,
                "octave_moved_notes": result.arrangement_result.report.octave_moved_note_count,
                "clipped_notes": result.arrangement_result.report.clipped_note_count,
                "max_output_chord_notes": result.arrangement_result.validation.max_chord_notes,
            }
        )
        return [dict(event) for event in result.arrangement_result.score_events]

    def _read_midi_notes(self, midi_data: pretty_midi.PrettyMIDI, config: AudioPipelineConfig) -> tuple[MidiNoteEvent, ...]:
        """
        从 MIDI 对象读取音符并换算为拍点。

        :param midi_data: pretty_midi 读取出的 MIDI 对象
        :param config: 音频转换流水线配置
        :return: MIDI 音符事件元组
        :raises ValueError: 当 MIDI 不包含音符时触发
        """
        seconds_per_beat = 60.0 / config.bpm
        notes: list[MidiNoteEvent] = []
        for instrument in midi_data.instruments:
            if instrument.is_drum:
                continue
            for note in instrument.notes:
                if note.end <= note.start:
                    continue
                notes.append(
                    MidiNoteEvent(
                        pitch=int(note.pitch),
                        start_beat=note.start / seconds_per_beat,
                        end_beat=note.end / seconds_per_beat,
                        velocity=int(note.velocity),
                        duration_beats=(note.end - note.start) / seconds_per_beat,
                    )
                )

        if not notes:
            raise ValueError("MIDI 中没有可转换的非鼓音符事件")

        sorted_notes = tuple(sorted(notes, key=lambda item: (item.pitch, item.start_beat)))
        if config.merge_sustained_notes:
            sorted_notes = self._merge_sustained_midi_notes(midi_notes=sorted_notes, config=config)

        clustered_notes = self._cluster_midi_note_onsets(midi_notes=sorted_notes, config=config)
        return tuple(sorted(clustered_notes, key=lambda item: (item.start_beat, item.pitch)))

    def _merge_sustained_midi_notes(
        self,
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> tuple[MidiNoteEvent, ...]:
        """
        合并由延音拆片、转录残响产生的同音重叠碎音。

        同 pitch 上端点重叠或间隔在 merge_sustained_gap_beats 内的相邻音符，
        被合并为一个 MidiNoteEvent，保留较高的 velocity 和最宽的起止区间。
        该方法在 _read_midi_notes 阶段运行，从源的 MIDI 数量级减少重复起音数量。

        :param midi_notes: 已按音高排序的 MIDI 音符
        :param config: 音频转换流水线配置
        :return: 合并后的 MIDI 音符元组
        """
        if not midi_notes:
            return tuple()

        gap_beats = max(0.0, config.merge_sustained_gap_beats)
        merged: list[MidiNoteEvent] = []
        current_pitch: int | None = None
        current_start: float = 0.0
        current_end: float = 0.0
        current_velocity: int = 64

        def _finish_current_merging():
            """
            将当前正在合并的音符写入 merged 列表。
            """
            nonlocal current_pitch, current_start, current_end, current_velocity
            if current_pitch is not None:
                merged.append(
                    MidiNoteEvent(
                        pitch=current_pitch,
                        start_beat=current_start,
                        end_beat=current_end,
                        velocity=current_velocity,
                        duration_beats=current_end - current_start,
                    )
                )
                current_pitch = None

        for note in midi_notes:
            if current_pitch is None:
                current_pitch = note.pitch
                current_start = note.start_beat
                current_end = note.end_beat
                current_velocity = note.velocity
                continue

            if note.pitch != current_pitch:
                _finish_current_merging()
                current_pitch = note.pitch
                current_start = note.start_beat
                current_end = note.end_beat
                current_velocity = note.velocity
                continue

            # 同音：如果时间上重叠或足够靠近，则扩展合并区间
            if note.start_beat <= current_end + gap_beats:
                current_end = max(current_end, note.end_beat)
                current_velocity = max(current_velocity, note.velocity)
                continue

            # 同音但没有重叠，结束当前合并并开始下一个
            _finish_current_merging()
            current_pitch = note.pitch
            current_start = note.start_beat
            current_end = note.end_beat
            current_velocity = note.velocity

        _finish_current_merging()
        return tuple(merged)

    def _cluster_midi_note_onsets(
        self,
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> tuple[MidiNoteEvent, ...]:
        """
        将毫秒级起音偏差的同一物理和弦合并为统一的起音拍点。

        解决由转录工具或演奏技巧（如琶音实际是同时踩下）导致同一和弦内各音符
        start_beat 存在亚量化格级差异，被拆分到多个量化格的问题。

        聚类条件：
            当前音符与组首音符 start_beat 差值 <= onset_cluster_max_span_beats，
            且与组末音符 start_beat 差值 <= onset_cluster_window_beats，则归入同组。

        每组若多于 1 个音符：
            - cluster_start_beat 使用组内 velocity 加权平均（velocity 总和 <= 0 时退化为算术平均）
            - 各音符 end_beat 按同样 delta 平移，duration_beats 重新计算，最短不小于 quantize_beat * 0.25

        :param midi_notes: 待聚类的 MIDI 音符事件元组
        :param config: 音频转换流水线配置
        :return: 聚类后的 MIDI 音符事件元组，按 (start_beat, pitch) 排序
        :raises ValueError: 当 onset_cluster_window_beats < 0
                            或 onset_cluster_max_span_beats < onset_cluster_window_beats 时触发
        """
        # 若聚类功能关闭，直接原样返回
        if not config.onset_cluster_enabled or not midi_notes:
            return midi_notes

        # 参数合法性校验
        if config.onset_cluster_window_beats < 0:
            raise ValueError(
                f"onset_cluster_window_beats 必须 >= 0，当前值: {config.onset_cluster_window_beats}"
            )
        if config.onset_cluster_max_span_beats < config.onset_cluster_window_beats:
            raise ValueError(
                f"onset_cluster_max_span_beats ({config.onset_cluster_max_span_beats}) "
                f"必须 >= onset_cluster_window_beats ({config.onset_cluster_window_beats})"
            )

        # 按 start_beat 升序排序，start_beat 相同则按 pitch 排序
        sorted_notes = sorted(midi_notes, key=lambda item: (item.start_beat, item.pitch))

        min_duration_beats = max(0.0, config.quantize_beat * 0.25)

        clustered_notes: list[MidiNoteEvent] = []
        clustered_count = 0
        cluster_groups = 0

        index = 0
        total_notes = len(sorted_notes)
        while index < total_notes:
            # 当前组初始化：以当前音符作为组首
            group_first = sorted_notes[index]
            group: list[MidiNoteEvent] = [group_first]
            group_end_index = index + 1

            # 扫描后续音符，检查是否可并入当前组
            while group_end_index < total_notes:
                candidate = sorted_notes[group_end_index]
                # 条件 1：候选与组首 start_beat 差值不超过 max_span
                span_from_first = candidate.start_beat - group_first.start_beat
                if span_from_first > config.onset_cluster_max_span_beats:
                    break
                # 条件 2：候选与组末 start_beat 差值不超过 window
                gap_from_last = candidate.start_beat - group[-1].start_beat
                if gap_from_last > config.onset_cluster_window_beats:
                    break
                group.append(candidate)
                group_end_index += 1

            # 若组内只有单一音符，原样保留
            if len(group) == 1:
                clustered_notes.append(group[0])
            else:
                # 多音符组：计算 velocity 加权平均 start_beat
                total_velocity = sum(note.velocity for note in group)
                if total_velocity > 0:
                    weighted_sum = sum(note.start_beat * note.velocity for note in group)
                    cluster_start_beat = weighted_sum / total_velocity
                else:
                    # velocity 总和为 0，退化为算术平均
                    cluster_start_beat = sum(note.start_beat for note in group) / len(group)

                for note in group:
                    # 原始 start_beat 与 cluster_start_beat 的偏移量
                    delta = cluster_start_beat - note.start_beat
                    new_end_beat = note.end_beat + delta
                    new_duration_beats = max(min_duration_beats, new_end_beat - cluster_start_beat)
                    clustered_notes.append(
                        MidiNoteEvent(
                            pitch=note.pitch,
                            start_beat=cluster_start_beat,
                            end_beat=new_end_beat,
                            velocity=note.velocity,
                            duration_beats=new_duration_beats,
                        )
                    )
                    clustered_count += 1
                cluster_groups += 1

            # 移动到下一个未处理的音符
            index = group_end_index

        # 安全累加统计量
        if "onset_clustered_notes" in self.conversion_stats:
            self.conversion_stats["onset_clustered_notes"] = (
                int(self.conversion_stats["onset_clustered_notes"]) + clustered_count
            )
        else:
            self.conversion_stats["onset_clustered_notes"] = clustered_count

        if "onset_clustered_groups" in self.conversion_stats:
            self.conversion_stats["onset_clustered_groups"] = (
                int(self.conversion_stats["onset_clustered_groups"]) + cluster_groups
            )
        else:
            self.conversion_stats["onset_clustered_groups"] = cluster_groups

        # 返回按 (start_beat, pitch) 排序的结果
        return tuple(sorted(clustered_notes, key=lambda item: (item.start_beat, item.pitch)))

    def _build_score_events(
        self,
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> list[dict[str, Any]]:
        """
        将 MIDI 音符事件量化为 YAML score 事件。

        :param midi_notes: MIDI 音符事件元组
        :param config: 音频转换流水线配置
        :return: YAML score 事件列表
        :raises ValueError: 当和弦、节拍或事件数量超出限制时触发
        """
        grouped_notes: dict[int, list[str]] = {}
        grouped_pitches: dict[int, list[int]] = {}
        needs_deferred_compression = config.pitch_compression_mode in {
            "adaptive_octave_fold",
            "hands_decoupled",
            "attention_weighted",
        }
        for midi_note in midi_notes:
            start_index = self._quantize_to_index(value=midi_note.start_beat, config=config)
            grouped_pitches.setdefault(start_index, []).append(midi_note.pitch)
            if needs_deferred_compression:
                continue
            token = self._pitch_to_token(pitch=midi_note.pitch, config=config)
            grouped_notes.setdefault(start_index, []).append(token)

        if grouped_pitches:
            self.conversion_stats["max_original_chord_notes"] = max(len(pitches) for pitches in grouped_pitches.values())

        if config.pitch_compression_mode == "adaptive_octave_fold":
            grouped_notes = self._build_adaptive_octave_fold_grouped_notes(grouped_pitches=grouped_pitches, config=config)
        if config.pitch_compression_mode == "hands_decoupled":
            grouped_notes = self._build_hands_decoupled_grouped_notes(
                grouped_pitches=grouped_pitches,
                midi_notes=midi_notes,
                config=config,
            )
        if config.pitch_compression_mode == "attention_weighted":
            grouped_notes = self._build_attention_weighted_grouped_notes(
                grouped_pitches=grouped_pitches,
                midi_notes=midi_notes,
                config=config,
            )

        if not grouped_notes:
            raise ValueError("量化后没有可输出的音符事件")

        score_events: list[dict[str, Any]] = []
        current_index = min(grouped_notes)
        for start_index in sorted(grouped_notes):
            if start_index > current_index:
                rest_beat = (start_index - current_index) * config.quantize_beat
                score_events.append({"notes": "0", "beat": self._normalize_beat(rest_beat)})

            tokens = tuple(dict.fromkeys(grouped_notes[start_index]))
            self.conversion_stats["max_output_chord_notes"] = max(
                int(self.conversion_stats["max_output_chord_notes"]),
                len(tokens),
            )
            self.conversion_stats["output_note_count"] = int(self.conversion_stats["output_note_count"]) + len(tokens)

            notes_text = tokens[0] if len(tokens) == 1 else f"[{' '.join(tokens)}]"
            score_events.append({"notes": notes_text, "beat": self._normalize_beat(config.quantize_beat)})
            current_index = start_index + 1

            if len(score_events) > config.max_score_events:
                raise ValueError(f"score 事件数量超过限制 {config.max_score_events}")

        return score_events

    def _separate_hands_with_svsep(
        self,
        midi_path: Path,
        config: AudioPipelineConfig,
    ) -> tuple[list[MidiNoteEvent], list[MidiNoteEvent]]:
        """
        使用 piano_svsep 将 MIDI 拆分为左右手音符。

        该方法只服务 svsep_mpdr 新管线。分离失败时直接抛出异常，避免旧启发式分割
        静默介入而污染新管线评估结果。

        :param midi_path: 输入 MIDI 文件路径
        :param config: 音频转换流水线配置
        :return: (left_notes, right_notes) 左右手音符列表
        :raises ValueError: 当 svsep 配置缺失或分离结果无效时触发
        """
        if config.svsep_model_path is None:
            raise ValueError("svsep_mpdr 模式必须提供 --svsep-model-path")

        from svsep_hand_separator import SvsepHandSeparator

        separator = SvsepHandSeparator(model_path=config.svsep_model_path, device=config.svsep_device)
        result = separator.separate(
            midi_path=midi_path,
            work_dir=config.work_dir / "svsep",
            bpm=config.bpm,
        )
        left_notes = result.left_notes
        right_notes = result.right_notes
        # result.audit 已在 separate() 内部调用了 validate()
        if not left_notes and not right_notes:
            raise ValueError("piano_svsep 未返回任何左右手音符")

        self.conversion_stats["svsep_left_note_count"] = len(left_notes)
        self.conversion_stats["svsep_right_note_count"] = len(right_notes)
        self.conversion_stats["svsep_total_note_array_count"] = result.audit.total_note_array_count
        self.conversion_stats["svsep_predicted_staff_count"] = result.audit.predicted_staff_count
        self.conversion_stats["svsep_matched_original_note_count"] = result.audit.matched_original_note_count
        self.conversion_stats["svsep_unmatched_original_note_count"] = result.audit.unmatched_original_note_count
        self.conversion_stats["svsep_match_ratio"] = round(result.audit.match_ratio, 6)
        self.conversion_stats["svsep_unknown_staff_count"] = result.audit.unknown_staff_count
        return left_notes, right_notes

    def _split_svsep_sustained_notes(
        self,
        left_midi_notes: tuple[MidiNoteEvent, ...],
        right_midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> tuple[tuple[MidiNoteEvent, ...], tuple[MidiNoteEvent, ...]]:
        """
        为 SVSEP MPDR 管线中的左手长低音追加中点续打击键。

        在无延音踏板的游戏环境中，长低音在播放层被截断为 1ms 短击后会失去和声持续性。
        本方法选择性地为满足条件的左手最低音（duration >= 阈值 + 中点密度不超标）
        在原 note 的中间位置插入一次额外的起音，作为 onset 击键的"续打提醒"。

        条件：
            1. 左手音符且为当前时间片的"最低音"
            2. duration_beats >= sustain_split_threshold_beats
            3. 中点位置的同拍音符数 <= sustain_split_max_midpoint_density

        :param left_midi_notes: SVSEP 判定的左手音符
        :param right_midi_notes: SVSEP 判定的右手音符
        :param config: 音频转换流水线配置
        :return: 追加续打击键后的 (左手音符, 右手音符) 元组
        """
        if not config.sustain_split_enabled:
            return left_midi_notes, right_midi_notes

        if config.sustain_split_threshold_beats <= 0:
            return left_midi_notes, right_midi_notes

        # 步骤 1: 构建左手时间片分组，用于确定每时间片的最低音
        left_by_time: dict[int, list[MidiNoteEvent]] = {}
        for note in left_midi_notes:
            start_index = self._quantize_to_index(value=note.start_beat, config=config)
            left_by_time.setdefault(start_index, []).append(note)

        # 步骤 2: 构建全曲密度映射（左右手合并），用于中点密度过滤
        density_map: dict[int, int] = {}
        all_notes: list[MidiNoteEvent] = list(left_midi_notes) + list(right_midi_notes)
        for note in all_notes:
            start_index = self._quantize_to_index(value=note.start_beat, config=config)
            density_map[start_index] = density_map.get(start_index, 0) + 1

        # 步骤 3: 筛选并生成续打击键
        split_notes: list[MidiNoteEvent] = []
        for note in left_midi_notes:
            start_index = self._quantize_to_index(value=note.start_beat, config=config)
            left_at_slice = left_by_time.get(start_index, [])
            if not left_at_slice:
                continue

            # 条件 1: 必须是当前时间片左手的最低音
            bass_pitch = min(item.pitch for item in left_at_slice)
            is_bass = note.pitch == bass_pitch
            if not is_bass:
                continue

            # 条件 2: 持续时间达到阈值
            if note.duration_beats < config.sustain_split_threshold_beats:
                continue

            # 条件 3: 中点位置密度不超过上限
            midpoint_beat = note.start_beat + note.duration_beats / 2.0
            midpoint_index = self._quantize_to_index(value=midpoint_beat, config=config)
            midpoint_density = density_map.get(midpoint_index, 0)
            if midpoint_density > config.sustain_split_max_midpoint_density:
                continue

            # 生成续打击键: pitch 与 velocity 继承原低音，duration 为短促的评分用值
            split_note = MidiNoteEvent(
                pitch=note.pitch,
                start_beat=midpoint_beat,
                end_beat=midpoint_beat + config.sustain_split_strike_duration_beats,
                velocity=note.velocity,
                duration_beats=config.sustain_split_strike_duration_beats,
            )
            split_notes.append(split_note)

        if not split_notes:
            return left_midi_notes, right_midi_notes

        # 步骤 4: 合并续打击键到左手列表，重新排序
        merged_left = list(left_midi_notes) + split_notes
        merged_left.sort(key=lambda item: (item.start_beat, item.pitch))

        self.conversion_stats["sustain_split_count"] = len(split_notes)
        return tuple(merged_left), right_midi_notes

    def _build_svsep_mpdr_score_events(
        self,
        left_midi_notes: tuple[MidiNoteEvent, ...],
        right_midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> list[dict[str, Any]]:
        """
        构建 svsep_mpdr 新管线的 YAML score 事件。

        流程：
            1. 基于 svsep 已分离的左右手音符生成多组 MPDR 候选。
            2. 使用主旋律完整度、遮蔽规避、和声完整度、低音连续性与音区清晰度精排。
            3. 将最优候选的 token 时间片写成 YAML score 事件。

        :param left_midi_notes: piano_svsep 判定的左手音符
        :param right_midi_notes: piano_svsep 判定的右手音符
        :param config: 音频转换流水线配置
        :return: YAML score 事件列表
        :raises ValueError: 当无法生成有效候选时触发
        """
        # 在候选生成之前对左手长低音做续打击键拆分
        left_midi_notes, right_midi_notes = self._split_svsep_sustained_notes(
            left_midi_notes=left_midi_notes,
            right_midi_notes=right_midi_notes,
            config=config,
        )
        candidates = self._generate_svsep_mpdr_candidates(
            left_midi_notes=left_midi_notes,
            right_midi_notes=right_midi_notes,
            config=config,
        )
        if not candidates:
            raise ValueError("svsep_mpdr: 未生成任何有效候选曲谱")

        best_result = max(candidates, key=lambda item: item.global_score)
        if not best_result.grouped_notes:
            raise ValueError("svsep_mpdr: 最优候选没有可输出的音符事件")

        self.conversion_stats["pitch_compression_mode"] = SVSEP_MPDR_MODE
        self.conversion_stats["svsep_mpdr_global_score"] = round(best_result.global_score, 6)
        for key, value in best_result.stats.items():
            self.conversion_stats[f"svsep_mpdr_{key}"] = value
        remapped_notes = int(best_result.stats.get("remapped_notes", 0))
        token_collision_drops = int(best_result.stats.get("token_collision_drops", 0))
        total_left_notes = int(best_result.stats.get("total_left_notes", 0))
        kept_left_notes = int(best_result.stats.get("kept_left_notes", 0))
        total_right_notes = int(best_result.stats.get("total_right_notes", 0))
        kept_right_notes = int(best_result.stats.get("kept_right_notes", 0))
        self.conversion_stats["remapped_notes"] = remapped_notes
        self.conversion_stats["octave_moved_notes"] = remapped_notes
        self.conversion_stats["collisions_avoided"] = token_collision_drops
        self.conversion_stats["clipped_notes"] = max(
            0,
            total_left_notes - kept_left_notes + total_right_notes - kept_right_notes + token_collision_drops,
        )
        self.conversion_stats["ref_pitch_trajectory_start"] = config.mpdr_right_ref_pitch
        self.conversion_stats["ref_pitch_trajectory_end"] = best_result.stats.get("right_ref_pitch_end", 0.0)

        score_events: list[dict[str, Any]] = []
        current_index = min(best_result.grouped_notes)
        for start_index in sorted(best_result.grouped_notes):
            if start_index > current_index:
                rest_beat = (start_index - current_index) * config.quantize_beat
                score_events.append({"notes": "0", "beat": self._normalize_beat(rest_beat)})

            tokens = tuple(dict.fromkeys(best_result.grouped_notes[start_index]))
            self.conversion_stats["max_output_chord_notes"] = max(
                int(self.conversion_stats["max_output_chord_notes"]),
                len(tokens),
            )
            self.conversion_stats["output_note_count"] = int(self.conversion_stats["output_note_count"]) + len(tokens)

            notes_text = tokens[0] if len(tokens) == 1 else f"[{' '.join(tokens)}]"
            score_events.append({"notes": notes_text, "beat": self._normalize_beat(config.quantize_beat)})
            current_index = start_index + 1

            if len(score_events) > config.max_score_events:
                raise ValueError(f"score 事件数量超过限制 {config.max_score_events}")

        return score_events

    def _generate_svsep_mpdr_candidates(
        self,
        left_midi_notes: tuple[MidiNoteEvent, ...],
        right_midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> list[MpdrBuildResult]:
        """
        生成 svsep_mpdr 参数扫描候选。

        扫描主旋律保护强度、左手基础预算和音区碰撞惩罚三个核心参数。
        扫描只影响新管线，不会调用或修改旧压缩算法。

        :param left_midi_notes: 左手音符
        :param right_midi_notes: 右手音符
        :param config: 音频转换流水线配置
        :return: MPDR 候选结果列表
        """
        delta = config.mpdr_scan_delta_ratio
        if delta < 0:
            raise ValueError("mpdr_scan_delta_ratio 必须大于等于 0")

        def _variants(value: float) -> list[float]:
            """
            基于相对扰动比例生成参数扫描值。

            :param value: 原始参数值
            :return: 去重后的候选参数值
            """
            raw_values = [value * (1.0 - delta), value, value * (1.0 + delta)]
            return sorted({max(0.0, round(item, 6)) for item in raw_values})

        variant_configs: list[AudioPipelineConfig] = []
        for protection in _variants(config.mpdr_melody_protection_strength):
            for opacity_base in _variants(config.mpdr_left_opacity_base):
                for register_penalty in _variants(config.mpdr_register_collision_penalty):
                    variant_configs.append(
                        replace(
                            config,
                            mpdr_melody_protection_strength=protection,
                            mpdr_left_opacity_base=opacity_base,
                            mpdr_register_collision_penalty=register_penalty,
                        )
                    )

        if len(variant_configs) > config.mpdr_candidate_count:
            step = len(variant_configs) / config.mpdr_candidate_count
            variant_configs = [variant_configs[int(index * step)] for index in range(config.mpdr_candidate_count)]

        candidates: list[MpdrBuildResult] = []
        for variant_config in variant_configs:
            result = self._build_svsep_mpdr_candidate(
                left_midi_notes=left_midi_notes,
                right_midi_notes=right_midi_notes,
                config=variant_config,
            )
            if result.grouped_notes:
                candidates.append(result)
        return candidates

    def _build_svsep_mpdr_candidate(
        self,
        left_midi_notes: tuple[MidiNoteEvent, ...],
        right_midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> MpdrBuildResult:
        """
        构建单个 MPDR 候选。

        该方法以 svsep 左右手分离结果为权威输入。右手音符默认完整进入候选，左手
        按主旋律活动、和声功能、遮蔽风险和感知成本动态缩编。

        :param left_midi_notes: 左手音符
        :param right_midi_notes: 右手音符
        :param config: 音频转换流水线配置
        :return: MPDR 候选构建结果
        """
        left_groups = self._group_midi_notes_by_index(midi_notes=left_midi_notes, config=config)
        right_groups = self._group_midi_notes_by_index(midi_notes=right_midi_notes, config=config)
        all_time_indices = sorted(set(left_groups) | set(right_groups))
        if not all_time_indices:
            return MpdrBuildResult(grouped_notes={}, global_score=0.0, stats={})

        max_original_chord = 0
        for time_index in all_time_indices:
            original_count = len(left_groups.get(time_index, [])) + len(right_groups.get(time_index, []))
            max_original_chord = max(max_original_chord, original_count)
        self.conversion_stats["max_original_chord_notes"] = max(
            int(self.conversion_stats["max_original_chord_notes"]),
            max_original_chord,
        )

        all_grouped_pitches: dict[int, list[int]] = {}
        for time_index in all_time_indices:
            all_grouped_pitches[time_index] = [
                note.pitch for note in left_groups.get(time_index, []) + right_groups.get(time_index, [])
            ]
        global_ref = self._extract_global_trend(
            grouped_pitches=all_grouped_pitches,
            time_indices=all_time_indices,
            config=config,
        )

        melody_by_index = self._extract_primary_melody_path(right_groups=right_groups, config=config)
        grouped_notes: dict[int, list[str]] = {}
        previous_left_selected: list[int] = []
        ref_pitch = global_ref.get(all_time_indices[0], config.mpdr_right_ref_pitch)
        repeat_memory: dict[tuple[str, int], MidiNoteEvent] = {}

        stats: dict[str, float | int] = {
            "total_melody_notes": len(melody_by_index),
            "kept_melody_notes": 0,
            "total_right_notes": len(right_midi_notes),
            "kept_right_notes": 0,
            "total_left_notes": len(left_midi_notes),
            "kept_left_notes": 0,
            "total_left_utility": 0.0,
            "kept_left_utility": 0.0,
            "total_masking_risk": 0.0,
            "kept_masking_risk": 0.0,
            "left_active_slices": len(left_groups),
            "bass_kept_slices": 0,
            "token_collision_drops": 0,
            "svsep_mpdr_token_conflict_dropped_notes": 0,
            "remapped_notes": 0,
            "suppressed_repeated_notes": 0,
            # 新增 MPDR 审计字段：低音锚点、音区碰撞、起音碰撞与低音浑浊事件计数
            "total_bass_anchor_notes": 0,
            "kept_bass_anchor_notes": 0,
            "low_mud_penalty_events": 0,
            "register_collision_events": 0,
            "onset_collision_events": 0,
        }

        for time_index in all_time_indices:
            right_notes = right_groups.get(time_index, [])
            left_notes = left_groups.get(time_index, [])
            melody_note = melody_by_index.get(time_index)
            melody_pitch = None if melody_note is None else melody_note.pitch

            right_candidates = self._select_mpdr_right_notes(
                time_index=time_index,
                right_notes=right_notes,
                left_notes=left_notes,
                melody_note=melody_note,
                config=config,
            )

            left_candidates = self._select_mpdr_left_notes(
                time_index=time_index,
                left_notes=left_notes,
                right_notes=right_notes,
                melody_by_index=melody_by_index,
                previous_left_selected=previous_left_selected,
                config=config,
            )
            # 累计低音锚点统计：左手当前时间片最低音是否被候选选中
            if left_notes:
                bass_pitch = min(note.pitch for note in left_notes)
                stats["total_bass_anchor_notes"] = int(stats["total_bass_anchor_notes"]) + 1
                if any(candidate.note.pitch == bass_pitch for candidate in left_candidates):
                    stats["kept_bass_anchor_notes"] = int(stats["kept_bass_anchor_notes"]) + 1
            for note in left_notes:
                utility = self._score_mpdr_left_note(
                    note=note,
                    left_notes=left_notes,
                    previous_left_selected=previous_left_selected,
                    config=config,
                )
                masking_risk = self._compute_mpdr_masking_risk(
                    note=note,
                    time_index=time_index,
                    left_notes=left_notes,
                    melody_pitch=melody_pitch,
                    melody_by_index=melody_by_index,
                    right_groups=right_groups,
                    config=config,
                )
                stats["total_left_utility"] = float(stats["total_left_utility"]) + utility
                stats["total_masking_risk"] = float(stats["total_masking_risk"]) + masking_risk
                # 累计遮蔽风险惩罚事件：音区碰撞、起音碰撞与低音浑浊
                if masking_risk > 0:
                    if melody_pitch is not None:
                        distance = abs(note.pitch - melody_pitch)
                        if distance < config.mpdr_register_collision_semitones:
                            stats["register_collision_events"] = int(stats["register_collision_events"]) + 1
                        if melody_note is not None and abs(note.start_beat - melody_note.start_beat) < config.quantize_beat:
                            stats["onset_collision_events"] = int(stats["onset_collision_events"]) + 1
                    if note.pitch <= config.mpdr_low_mud_pitch and len(left_notes) >= 3:
                        stats["low_mud_penalty_events"] = int(stats["low_mud_penalty_events"]) + 1

            merged_candidates = right_candidates + left_candidates
            merged_candidates, suppressed_count = self._suppress_mpdr_repeated_candidates(
                candidates=merged_candidates,
                repeat_memory=repeat_memory,
                config=config,
            )
            if suppressed_count > 0:
                stats["suppressed_repeated_notes"] = int(stats["suppressed_repeated_notes"]) + suppressed_count
            if not merged_candidates:
                previous_left_selected = []
                continue

            target_ref_pitch = global_ref.get(time_index, ref_pitch)
            ref_pitch = (1 - config.ref_smoothing) * ref_pitch + config.ref_smoothing * target_ref_pitch
            folded_candidates = self._fold_mpdr_candidates_unified(
                candidates=merged_candidates,
                ref_pitch=ref_pitch,
                config=config,
            )
            mapped_values = [candidate.mapped_pitch for candidate in folded_candidates]
            if mapped_values:
                avg_mapped = sum(mapped_values) / len(mapped_values)
                ref_pitch = (1 - config.ref_smoothing) * ref_pitch + config.ref_smoothing * avg_mapped

            for candidate in folded_candidates:
                if candidate.mapped_pitch != candidate.note.pitch:
                    stats["remapped_notes"] = int(stats["remapped_notes"]) + 1

            tokens, kept_candidates, dropped_count = self._candidates_to_modifier_safe_tokens(
                candidates=folded_candidates,
                config=config,
            )
            if dropped_count > 0:
                stats["token_collision_drops"] = int(stats["token_collision_drops"]) + dropped_count
                # 累加 svsep_mpdr 专属 token 冲突丢弃计数器，便于 MPDR 管线独立审计
                if "svsep_mpdr_token_conflict_dropped_notes" in stats:
                    stats["svsep_mpdr_token_conflict_dropped_notes"] = int(stats["svsep_mpdr_token_conflict_dropped_notes"]) + dropped_count

            if tokens:
                grouped_notes[time_index] = tokens
                for candidate in kept_candidates:
                    if candidate.hand == "right":
                        stats["kept_right_notes"] = int(stats["kept_right_notes"]) + 1
                    if candidate.hand == "left":
                        stats["kept_left_notes"] = int(stats["kept_left_notes"]) + 1
                        stats["kept_left_utility"] = float(stats["kept_left_utility"]) + candidate.utility
                        stats["kept_masking_risk"] = float(stats["kept_masking_risk"]) + candidate.masking_risk
                if any(candidate.is_primary_melody for candidate in kept_candidates):
                    stats["kept_melody_notes"] = int(stats["kept_melody_notes"]) + 1
                if left_notes:
                    original_bass = min(note.pitch for note in left_notes)
                    if any(candidate.hand == "left" and candidate.note.pitch == original_bass for candidate in kept_candidates):
                        stats["bass_kept_slices"] = int(stats["bass_kept_slices"]) + 1

            previous_left_selected = [candidate.note.pitch for candidate in kept_candidates if candidate.hand == "left"]

        stats["right_ref_pitch_end"] = round(ref_pitch, 6)
        stats["left_ref_pitch_end"] = round(ref_pitch, 6)
        global_score = self._score_svsep_mpdr_stats(stats=stats, config=config)
        return MpdrBuildResult(grouped_notes=grouped_notes, global_score=global_score, stats=stats)

    def _group_midi_notes_by_index(
        self,
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> dict[int, list[MidiNoteEvent]]:
        """
        按量化时间片分组 MIDI 音符。

        :param midi_notes: MIDI 音符事件元组
        :param config: 音频转换流水线配置
        :return: 时间片索引到音符列表的映射
        """
        grouped: dict[int, list[MidiNoteEvent]] = {}
        for note in midi_notes:
            time_index = self._quantize_to_index(value=note.start_beat, config=config)
            grouped.setdefault(time_index, []).append(note)
        for notes_at_time in grouped.values():
            notes_at_time.sort(key=lambda item: (item.pitch, item.start_beat))
        return grouped

    def _extract_primary_melody_path(
        self,
        right_groups: dict[int, list[MidiNoteEvent]],
        config: AudioPipelineConfig,
    ) -> dict[int, MidiNoteEvent]:
        """
        从右手音符中提取主旋律路径。

        使用动态规划在每个右手有音时间片选择一个旋律候选。目标是兼顾高音突出、
        时值、velocity 与相邻音符连续性，避免把短促装饰音或跳跃内声部误判为主旋律。

        :param right_groups: 右手时间片音符分组
        :param config: 音频转换流水线配置
        :return: 时间片索引到主旋律音符的映射
        """
        time_indices = sorted(right_groups)
        if not time_indices:
            return {}

        scores_by_time: dict[int, list[float]] = {}
        previous_choice: dict[tuple[int, int], tuple[int, int] | None] = {}
        previous_time_index: int | None = None

        for time_index in time_indices:
            notes_at_time = right_groups[time_index]
            current_scores: list[float] = []
            for note_index, note in enumerate(notes_at_time):
                base_score = self._score_primary_melody_note(
                    note=note,
                    notes_at_time=notes_at_time,
                    time_index=time_index,
                    config=config,
                )
                if previous_time_index is None:
                    current_scores.append(base_score)
                    previous_choice[(time_index, note_index)] = None
                    continue

                previous_notes = right_groups[previous_time_index]
                best_score = -float("inf")
                best_previous: tuple[int, int] | None = None
                for previous_note_index, previous_note in enumerate(previous_notes):
                    interval = abs(note.pitch - previous_note.pitch)
                    continuity_score = max(0.0, 1.0 - interval / config.mpdr_register_collision_semitones)
                    candidate_score = (
                        scores_by_time[previous_time_index][previous_note_index]
                        + base_score
                        + config.mpdr_melody_continuity_weight * continuity_score
                    )
                    # 大跳惩罚：当前音符与上一时间片候选音符的半音间隔超出碰撞阈值时扣分
                    if interval > config.mpdr_register_collision_semitones:
                        candidate_score -= config.mpdr_melody_large_jump_penalty
                    # 重复惩罚：同一 pitch 在相邻时间片连续出现时扣分
                    if note.pitch == previous_note.pitch:
                        time_gap = abs(note.start_beat - previous_note.start_beat)
                        if time_gap <= config.mpdr_repeat_suppression_beats:
                            candidate_score -= config.mpdr_melody_repetition_penalty
                    if candidate_score > best_score:
                        best_score = candidate_score
                        best_previous = (previous_time_index, previous_note_index)
                current_scores.append(best_score)
                previous_choice[(time_index, note_index)] = best_previous
            scores_by_time[time_index] = current_scores
            previous_time_index = time_index

        last_time_index = time_indices[-1]
        last_scores = scores_by_time[last_time_index]
        if not last_scores:
            return {}
        best_last_note_index = max(range(len(last_scores)), key=lambda index: last_scores[index])

        melody_by_index: dict[int, MidiNoteEvent] = {}
        cursor: tuple[int, int] | None = (last_time_index, best_last_note_index)
        while cursor is not None:
            time_index, note_index = cursor
            melody_by_index[time_index] = right_groups[time_index][note_index]
            cursor = previous_choice.get(cursor)
        return melody_by_index

    def _score_primary_melody_note(
        self,
        note: MidiNoteEvent,
        notes_at_time: list[MidiNoteEvent],
        time_index: int,
        config: AudioPipelineConfig,
    ) -> float:
        """
        计算右手音符成为主旋律的基础显著性。

        :param note: 待评分音符
        :param notes_at_time: 当前时间片右手音符
        :param time_index: 当前时间片索引
        :param config: 音频转换流水线配置
        :return: 主旋律显著性分数
        """
        if not notes_at_time:
            return 0.0

        pitches = [item.pitch for item in notes_at_time]
        pitch_span = max(pitches) - min(pitches)
        pitch_score = 1.0 if pitch_span == 0 else (note.pitch - min(pitches)) / pitch_span

        duration_window = max(config.quantize_beat * config.beat_unit, config.quantize_beat)
        duration_score = min(note.duration_beats / duration_window, 1.0)
        velocity_score = max(0.0, min(note.velocity / MIDI_MAX_VELOCITY, 1.0))

        beat_position = (time_index * config.quantize_beat) % config.beat_unit
        beat_distance = min(beat_position, config.beat_unit - beat_position)
        beat_score = max(0.0, 1.0 - beat_distance / config.beat_unit)

        return (
            config.mpdr_melody_pitch_weight * pitch_score
            + config.mpdr_melody_duration_weight * duration_score
            + config.mpdr_melody_velocity_weight * velocity_score
            + config.mpdr_melody_beat_weight * beat_score
        )

    def _compute_mpdr_melody_activity(
        self,
        time_index: int,
        melody_by_index: dict[int, MidiNoteEvent],
        right_groups: dict[int, list[MidiNoteEvent]],
        config: AudioPipelineConfig,
    ) -> float:
        """
        计算当前时间片的右手主旋律活动强度。

        :param time_index: 当前时间片索引
        :param melody_by_index: 主旋律路径映射
        :param right_groups: 右手时间片音符分组
        :param config: 音频转换流水线配置
        :return: 活动强度，范围尽量归一到 0-1
        """
        guard_indices = max(1, int(math.ceil(config.mpdr_melody_onset_guard_beats / config.quantize_beat)))
        nearest_activity = 0.0
        for melody_index in melody_by_index:
            distance = abs(time_index - melody_index)
            if distance <= guard_indices:
                nearest_activity = max(nearest_activity, 1.0 - distance / (guard_indices + 1))

        right_density = len(right_groups.get(time_index, [])) / max(1, config.beat_unit)
        return max(nearest_activity, min(right_density, 1.0))

    def _suppress_mpdr_repeated_candidates(
        self,
        candidates: list[MpdrNoteCandidate],
        repeat_memory: dict[tuple[str, int], MidiNoteEvent],
        config: AudioPipelineConfig,
    ) -> tuple[list[MpdrNoteCandidate], int]:
        """
        抑制由延音拆片或转录残响造成的重复起音。

        规则只作用于左手和右手内声。主旋律候选不被抑制，避免破坏真实旋律重复。
        同手同原始音高若在短窗口内再次出现，并且前一个音符与当前音符重叠、接近重叠
        或前后任一音符本身具有较长时值，则认为是延音/残响连击并删除后一个起音。

        :param candidates: 当前时间片已选候选音符
        :param repeat_memory: 跨时间片保存的最近保留音符
        :param config: 音频转换流水线配置
        :return: (抑制后的候选列表, 被删除数量)
        """
        if not candidates:
            return [], 0

        kept_candidates: list[MpdrNoteCandidate] = []
        suppressed_count = 0
        sorted_candidates = sorted(candidates, key=lambda candidate: (candidate.note.start_beat, candidate.hand, candidate.note.pitch))
        for candidate in sorted_candidates:
            key = (candidate.hand, candidate.note.pitch)
            previous_note = repeat_memory.get(key)
            should_suppress = False

            if previous_note is not None and not candidate.is_primary_melody:
                start_gap = candidate.note.start_beat - previous_note.start_beat
                silence_gap = candidate.note.start_beat - previous_note.end_beat
                overlaps_previous = silence_gap <= config.mpdr_repeat_overlap_tolerance_beats
                close_repeat = 0.0 <= start_gap <= config.mpdr_repeat_suppression_beats
                sustained_context = (
                    previous_note.duration_beats >= config.mpdr_repeat_suppression_beats
                    or candidate.note.duration_beats >= config.mpdr_repeat_suppression_beats
                )

                # 左手铺底更容易把延音残响变成连击，因此同音短间隔直接抑制。
                if candidate.hand == "left" and close_repeat:
                    should_suppress = True
                # 右手内声更保守，仅在存在重叠或明显长音上下文时抑制。
                elif close_repeat and (overlaps_previous or sustained_context):
                    should_suppress = True

            if should_suppress:
                suppressed_count += 1
                continue

            kept_candidates.append(candidate)
            previous_note = repeat_memory.get(key)
            if previous_note is None or candidate.note.end_beat >= previous_note.end_beat:
                repeat_memory[key] = candidate.note

        kept_candidates.sort(key=lambda candidate: (candidate.start_index, candidate.mapped_pitch, candidate.note.pitch))
        return kept_candidates, suppressed_count

    def _select_mpdr_right_notes(
        self,
        time_index: int,
        right_notes: list[MidiNoteEvent],
        left_notes: list[MidiNoteEvent],
        melody_note: MidiNoteEvent | None,
        config: AudioPipelineConfig,
    ) -> list[MpdrNoteCandidate]:
        """
        对右手音符做保守型内声削弱。

        主旋律音符始终保留。右手普通和弦默认完整保留，只有在右手同一时间片非常密集时，
        才按旋律遮蔽风险和和声价值轻量删除内声，避免无力度环境中右手自身盖住旋律。

        :param time_index: 当前时间片索引
        :param right_notes: 当前时间片右手音符
        :param left_notes: 当前时间片左手音符
        :param melody_note: 当前时间片主旋律音符
        :param config: 音频转换流水线配置
        :return: 右手候选音符列表
        """
        if not right_notes:
            return []

        candidates: list[MpdrNoteCandidate] = []
        for note in right_notes:
            is_melody = melody_note is note
            utility = self._score_primary_melody_note(
                note=note,
                notes_at_time=right_notes,
                time_index=time_index,
                config=config,
            )
            if is_melody:
                utility += config.mpdr_melody_protection_strength + config.mpdr_bass_anchor_weight

            masking_risk = 0.0
            if melody_note is not None and not is_melody:
                distance = abs(note.pitch - melody_note.pitch)
                if distance < config.mpdr_register_collision_semitones:
                    masking_risk += config.mpdr_register_collision_penalty * (
                        1.0 - distance / config.mpdr_register_collision_semitones
                    )
                masking_risk += config.mpdr_density_penalty * max(0, len(right_notes) - 4)

            perceptual_cost = 1.0 + masking_risk
            keep_score = utility - masking_risk
            candidates.append(
                MpdrNoteCandidate(
                    note=note,
                    hand="right",
                    start_index=time_index,
                    mapped_pitch=note.pitch,
                    is_primary_melody=is_melody,
                    utility=utility,
                    masking_risk=masking_risk,
                    perceptual_cost=perceptual_cost,
                    keep_score=keep_score,
                )
            )

        # 普通右手和弦不做改写，避免新管线与原管线听感差异过大。
        if len(candidates) <= 6:
            return candidates

        forced = [candidate for candidate in candidates if candidate.is_primary_melody]
        optional = [candidate for candidate in candidates if not candidate.is_primary_melody]
        optional.sort(key=lambda candidate: (candidate.keep_score, candidate.utility), reverse=True)

        # 右手预算较宽，只在极密集片段抽掉最低价值内声；不是硬性键数上限。
        budget = max(config.mpdr_left_opacity_base, float(len(candidates)) * 0.65)
        if not left_notes:
            budget += config.mpdr_melody_rest_bonus * 0.5

        selected = list(forced)
        used_cost = sum(min(candidate.perceptual_cost, 1.0) for candidate in forced)
        for candidate in optional:
            if used_cost + candidate.perceptual_cost <= budget:
                selected.append(candidate)
                used_cost += candidate.perceptual_cost
        if not selected:
            selected.append(max(candidates, key=lambda candidate: candidate.keep_score))
        selected.sort(key=lambda candidate: candidate.note.pitch)
        return selected

    def _select_mpdr_left_notes(
        self,
        time_index: int,
        left_notes: list[MidiNoteEvent],
        right_notes: list[MidiNoteEvent],
        melody_by_index: dict[int, MidiNoteEvent],
        previous_left_selected: list[int],
        config: AudioPipelineConfig,
    ) -> list[MpdrNoteCandidate]:
        """
        根据 MPDR 预算选择当前时间片左手音符。

        选择目标不是限制键数，而是在感知预算内最大化左手和声效用并最小化主旋律遮蔽。

        :param time_index: 当前时间片索引
        :param left_notes: 当前时间片左手音符
        :param right_notes: 当前时间片右手音符
        :param melody_by_index: 主旋律路径映射
        :param previous_left_selected: 上一有效时间片保留的左手原始音高
        :param config: 音频转换流水线配置
        :return: 被选中的左手候选列表
        """
        if not left_notes:
            return []

        right_groups = {time_index: right_notes}
        melody_note = melody_by_index.get(time_index)
        melody_pitch = None if melody_note is None else melody_note.pitch
        melody_activity = self._compute_mpdr_melody_activity(
            time_index=time_index,
            melody_by_index=melody_by_index,
            right_groups=right_groups,
            config=config,
        )
        rest_bonus = config.mpdr_melody_rest_bonus if not right_notes else 0.0
        budget = config.mpdr_left_opacity_base - config.mpdr_melody_protection_strength * melody_activity + rest_bonus
        budget = max(config.mpdr_left_opacity_min, min(config.mpdr_left_opacity_max, budget))

        scored_candidates: list[MpdrNoteCandidate] = []
        for note in left_notes:
            utility = self._score_mpdr_left_note(
                note=note,
                left_notes=left_notes,
                previous_left_selected=previous_left_selected,
                config=config,
            )
            masking_risk = self._compute_mpdr_masking_risk(
                note=note,
                time_index=time_index,
                left_notes=left_notes,
                melody_pitch=melody_pitch,
                melody_by_index=melody_by_index,
                right_groups=right_groups,
                config=config,
            )
            perceptual_cost = self._compute_mpdr_perceptual_cost(
                note=note,
                left_notes=left_notes,
                masking_risk=masking_risk,
                config=config,
            )
            keep_score = utility - config.mpdr_melody_protection_strength * masking_risk
            scored_candidates.append(
                MpdrNoteCandidate(
                    note=note,
                    hand="left",
                    start_index=time_index,
                    mapped_pitch=note.pitch,
                    is_primary_melody=False,
                    utility=utility,
                    masking_risk=masking_risk,
                    perceptual_cost=perceptual_cost,
                    keep_score=keep_score,
                )
            )

        selected: list[MpdrNoteCandidate] = []
        bass_pitch = min(note.pitch for note in left_notes)
        bass_candidates = [candidate for candidate in scored_candidates if candidate.note.pitch == bass_pitch]
        if bass_candidates:
            # 选择效用最高的低音作为锚点，并重建冻结实例以标记 is_bass_anchor
            bass_candidate = max(bass_candidates, key=lambda candidate: candidate.utility)
            bass_candidate = MpdrNoteCandidate(
                note=bass_candidate.note,
                hand=bass_candidate.hand,
                start_index=bass_candidate.start_index,
                mapped_pitch=bass_candidate.mapped_pitch,
                is_primary_melody=bass_candidate.is_primary_melody,
                utility=bass_candidate.utility,
                masking_risk=bass_candidate.masking_risk,
                perceptual_cost=bass_candidate.perceptual_cost,
                keep_score=bass_candidate.keep_score,
                is_bass_anchor=True,
            )
            selected.append(bass_candidate)

        fifth_candidates = [
            candidate
            for candidate in scored_candidates
            if candidate.note.pitch != bass_pitch and (candidate.note.pitch - bass_pitch) % 12 == 7
        ]
        if fifth_candidates:
            fifth_candidates.sort(key=lambda candidate: (abs(candidate.note.pitch - (bass_pitch + 7)), -candidate.note.pitch))
            fifth_candidate = fifth_candidates[0]
            if fifth_candidate.perceptual_cost <= budget or not right_notes:
                selected.append(fifth_candidate)

        if not selected and scored_candidates:
            selected.append(max(scored_candidates, key=lambda candidate: candidate.utility))
        selected.sort(key=lambda candidate: candidate.note.pitch)
        return selected

    def _score_mpdr_left_note(
        self,
        note: MidiNoteEvent,
        left_notes: list[MidiNoteEvent],
        previous_left_selected: list[int],
        config: AudioPipelineConfig,
    ) -> float:
        """
        计算左手音符的和声与声部效用。

        :param note: 待评分左手音符
        :param left_notes: 当前时间片全部左手音符
        :param previous_left_selected: 上一有效时间片保留的左手音高
        :param config: 音频转换流水线配置
        :return: 左手效用分数
        """
        if not left_notes:
            return 0.0

        sorted_pitches = sorted(item.pitch for item in left_notes)
        bass_pitch = sorted_pitches[0]
        bass_score = config.mpdr_bass_anchor_weight if note.pitch == bass_pitch else 0.0
        harmony_score = config.mpdr_harmony_color_weight * self._compute_mpdr_harmony_color(
            pitch=note.pitch,
            bass_pitch=bass_pitch,
        )
        voice_score = config.mpdr_voice_leading_weight * self._compute_mpdr_voice_leading(
            pitch=note.pitch,
            previous_left_selected=previous_left_selected,
            config=config,
        )
        velocity_score = config.mpdr_velocity_weight * max(0.0, min(note.velocity / MIDI_MAX_VELOCITY, 1.0))

        duration_window = max(config.quantize_beat * config.beat_unit, config.quantize_beat)
        duration_score = config.mpdr_duration_weight * min(note.duration_beats / duration_window, 1.0)

        duplicate_penalty = 0.0
        if note.pitch != bass_pitch:
            lower_same_class = [pitch for pitch in sorted_pitches if pitch < note.pitch and pitch % 12 == note.pitch % 12]
            if lower_same_class:
                duplicate_penalty = config.mpdr_duplicate_penalty

        return bass_score + harmony_score + voice_score + velocity_score + duration_score - duplicate_penalty

    def _compute_mpdr_harmony_color(self, pitch: int, bass_pitch: int) -> float:
        """
        计算相对低音的和声色彩价值。

        :param pitch: 待评估音高
        :param bass_pitch: 当前左手最低音
        :return: 和声色彩分数
        """
        interval_class = (pitch - bass_pitch) % 12
        if pitch == bass_pitch:
            return 1.0
        if interval_class in (3, 4):
            return 1.0
        if interval_class in (10, 11):
            return 0.9
        if interval_class in (5, 6):
            return 0.7
        if interval_class in (8, 9):
            return 0.55
        if interval_class == 7:
            return 0.45
        if interval_class in (1, 2):
            return 0.25
        if interval_class == 0:
            return 0.1
        return 0.4

    def _compute_mpdr_voice_leading(
        self,
        pitch: int,
        previous_left_selected: list[int],
        config: AudioPipelineConfig,
    ) -> float:
        """
        计算左手与前一时间片保留音的连接平滑度。

        :param pitch: 当前音高
        :param previous_left_selected: 上一有效时间片左手音高
        :param config: 音频转换流水线配置
        :return: 声部连接分数
        """
        if not previous_left_selected:
            return 0.5
        min_distance = min(abs(pitch - previous_pitch) for previous_pitch in previous_left_selected)
        return max(0.0, 1.0 - min_distance / config.mpdr_register_collision_semitones)

    def _compute_mpdr_masking_risk(
        self,
        note: MidiNoteEvent,
        time_index: int,
        left_notes: list[MidiNoteEvent],
        melody_pitch: int | None,
        melody_by_index: dict[int, MidiNoteEvent],
        right_groups: dict[int, list[MidiNoteEvent]],
        config: AudioPipelineConfig,
    ) -> float:
        """
        计算左手音符对右手主旋律的遮蔽风险。

        风险来源包括同起音、靠近旋律音区、低音区密集堆叠、左手局部密度以及长音覆盖。

        :param note: 待评估左手音符
        :param time_index: 当前时间片索引
        :param left_notes: 当前时间片左手音符
        :param melody_pitch: 当前时间片主旋律音高，None 表示当前无主旋律起音
        :param melody_by_index: 主旋律路径映射
        :param right_groups: 右手时间片音符分组
        :param config: 音频转换流水线配置
        :return: 遮蔽风险分数
        """
        melody_activity = self._compute_mpdr_melody_activity(
            time_index=time_index,
            melody_by_index=melody_by_index,
            right_groups=right_groups,
            config=config,
        )
        onset_collision = config.mpdr_onset_collision_penalty if melody_pitch is not None else 0.0

        register_collision = 0.0
        if melody_pitch is not None:
            distance = abs(note.pitch - melody_pitch)
            if distance < config.mpdr_register_collision_semitones:
                register_collision = config.mpdr_register_collision_penalty * (
                    1.0 - distance / config.mpdr_register_collision_semitones
                )

        low_mud = 0.0
        if note.pitch < config.mpdr_low_mud_pitch and len(left_notes) > 1:
            low_distance = (config.mpdr_low_mud_pitch - note.pitch) / max(1, config.beat_unit)
            low_mud = config.mpdr_low_mud_penalty * (1.0 + low_distance)

        density_risk = config.mpdr_density_penalty * max(0, len(left_notes) - 1)
        duration_risk = 0.0
        if note.duration_beats > config.quantize_beat:
            duration_risk = (
                config.mpdr_duration_ducking_strength
                * melody_activity
                * (note.duration_beats / max(config.quantize_beat, 1e-9))
            )

        return melody_activity * (onset_collision + register_collision + low_mud + density_risk + duration_risk)

    def _compute_mpdr_perceptual_cost(
        self,
        note: MidiNoteEvent,
        left_notes: list[MidiNoteEvent],
        masking_risk: float,
        config: AudioPipelineConfig,
    ) -> float:
        """
        计算左手音符在游戏无力度、无延音环境中的感知成本。

        :param note: 待评估左手音符
        :param left_notes: 当前时间片左手音符
        :param masking_risk: 已计算的遮蔽风险
        :param config: 音频转换流水线配置
        :return: 感知成本
        """
        phrase_window = max(config.quantize_beat * config.beat_unit, config.quantize_beat)
        duration_cost = 0.6 + min(note.duration_beats / phrase_window, 1.0)
        low_register_cost = 0.4
        if note.pitch < config.mpdr_low_mud_pitch and len(left_notes) > 2:
            low_register_cost += (config.mpdr_low_mud_pitch - note.pitch) / max(12.0, config.mpdr_register_collision_semitones)

        duplicate_cost = 0.0
        sorted_pitches = sorted(item.pitch for item in left_notes)
        if any(pitch < note.pitch and pitch % 12 == note.pitch % 12 for pitch in sorted_pitches):
            duplicate_cost = config.mpdr_duplicate_penalty

        return duration_cost + low_register_cost + duplicate_cost + 0.5 * masking_risk

    def _fold_mpdr_candidates(
        self,
        candidates: list[MpdrNoteCandidate],
        right_ref_pitch: float,
        left_ref_pitch: float,
        config: AudioPipelineConfig,
    ) -> list[MpdrNoteCandidate]:
        """
        对 MPDR 候选分手执行八度折叠。

        右手和左手使用不同参考中心，优先保持主旋律清晰和双手分层。该策略只用于
        svsep_mpdr，新旧管线的统一和弦折叠逻辑不受影响。

        :param candidates: 待折叠候选
        :param right_ref_pitch: 右手参考中心
        :param left_ref_pitch: 左手参考中心
        :param config: 音频转换流水线配置
        :return: 更新 mapped_pitch 后的候选列表
        """
        right_candidates = [candidate for candidate in candidates if candidate.hand == "right"]
        left_candidates = [candidate for candidate in candidates if candidate.hand == "left"]

        folded: list[MpdrNoteCandidate] = []
        if left_candidates:
            left_mapped = self._adaptive_octave_fold_slice_mpdr(
                pitches=[candidate.note.pitch for candidate in left_candidates],
                ref_pitch=left_ref_pitch,
                window_low=MIN_GAME_PITCH,
                window_high=MAX_GAME_PITCH,
                config=config,
            )
            folded.extend(
                replace(candidate, mapped_pitch=mapped_pitch)
                for candidate, mapped_pitch in zip(left_candidates, left_mapped)
            )
        if right_candidates:
            right_mapped = self._adaptive_octave_fold_slice_mpdr(
                pitches=[candidate.note.pitch for candidate in right_candidates],
                ref_pitch=right_ref_pitch,
                window_low=MIN_GAME_PITCH,
                window_high=MAX_GAME_PITCH,
                config=config,
            )
            folded.extend(
                replace(candidate, mapped_pitch=mapped_pitch)
                for candidate, mapped_pitch in zip(right_candidates, right_mapped)
            )
        folded.sort(key=lambda candidate: (candidate.mapped_pitch, candidate.hand))
        return folded

    def _fold_mpdr_candidates_unified(
        self,
        candidates: list[MpdrNoteCandidate],
        ref_pitch: float,
        config: AudioPipelineConfig,
    ) -> list[MpdrNoteCandidate]:
        """
        对 MPDR 候选执行全局统一八度折叠。

        v2 不再让左右手各自漂移，而是使用同一个全曲趋势参考，把 svsep 只作为声部标签，
        保持原曲的整体音区关系和旧管线的听感连续性。

        :param candidates: 待折叠候选
        :param ref_pitch: 全局趋势参考音高
        :param config: 音频转换流水线配置
        :return: 更新 mapped_pitch 后的候选列表
        """
        if not candidates:
            return []
        folded: list[MpdrNoteCandidate] = []
        left_candidates = [candidate for candidate in candidates if candidate.hand == "left"]
        right_candidates = [candidate for candidate in candidates if candidate.hand == "right"]

        if left_candidates:
            left_mapped = self._adaptive_octave_fold_slice_mpdr(
                pitches=[candidate.note.pitch for candidate in left_candidates],
                ref_pitch=config.mpdr_left_ref_pitch,
                window_low=MIN_GAME_PITCH,
                window_high=min(MAX_GAME_PITCH, config.mpdr_left_window_high),
                config=config,
            )
            folded.extend(
                replace(candidate, mapped_pitch=mapped_pitch)
                for candidate, mapped_pitch in zip(left_candidates, left_mapped)
            )

        if right_candidates:
            right_mapped = self._adaptive_octave_fold_slice_mpdr(
                pitches=[candidate.note.pitch for candidate in right_candidates],
                ref_pitch=max(ref_pitch, config.mpdr_right_ref_pitch),
                window_low=max(MIN_GAME_PITCH, config.mpdr_right_window_low),
                window_high=MAX_GAME_PITCH,
                config=config,
            )
            folded.extend(
                replace(candidate, mapped_pitch=mapped_pitch)
                for candidate, mapped_pitch in zip(right_candidates, right_mapped)
            )

        folded.sort(key=lambda candidate: (candidate.mapped_pitch, candidate.hand))
        return folded

    def _adaptive_octave_fold_slice_mpdr(
        self,
        pitches: list[int],
        ref_pitch: float,
        window_low: int,
        window_high: int,
        config: AudioPipelineConfig,
    ) -> list[int]:
        """
        svsep_mpdr 专用八度折叠。

        与旧共享折叠函数隔离，避免新管线修改既有模式行为。和弦可整体迁移时优先
        选择映射后平均音高最接近参考中心的八度偏移。

        :param pitches: 待折叠原始音高列表
        :param ref_pitch: 当前声部参考音高
        :param window_low: 游戏音域下界
        :param window_high: 游戏音域上界
        :param config: 音频转换流水线配置
        :return: 折叠后的音高列表
        """
        if not pitches:
            return []

        window_size = window_high - window_low
        original_span = max(pitches) - min(pitches)
        if original_span <= window_size:
            common_ks: set[int] | None = None
            for pitch in pitches:
                pitch_ks: set[int] = set()
                for octave_shift in range(-6, 7):
                    mapped_pitch = pitch + 12 * octave_shift
                    if window_low <= mapped_pitch <= window_high:
                        pitch_ks.add(octave_shift)
                common_ks = pitch_ks if common_ks is None else common_ks.intersection(pitch_ks)
                if not common_ks:
                    break
            if common_ks:
                avg_original = sum(pitches) / len(pitches)
                best_shift = min(common_ks, key=lambda shift: abs((avg_original + 12 * shift) - ref_pitch))
                return [pitch + 12 * best_shift for pitch in pitches]

        mapped_pitches: list[int] = []
        for pitch in pitches:
            best_mapped: int | None = None
            best_distance = float("inf")
            for octave_shift in range(-6, 7):
                mapped_pitch = pitch + 12 * octave_shift
                if window_low <= mapped_pitch <= window_high:
                    distance = abs(mapped_pitch - ref_pitch)
                    if distance < best_distance:
                        best_distance = distance
                        best_mapped = mapped_pitch
            if best_mapped is None:
                if config.out_of_range_policy == "error":
                    raise ValueError(f"MIDI 音高 {pitch} 无法按八度迁移到 C3 到 B5")
                best_mapped = max(window_low, min(window_high, pitch))
            mapped_pitches.append(best_mapped)
        return mapped_pitches

    @staticmethod
    def _mpdr_candidate_priority(candidate: MpdrNoteCandidate) -> tuple[int, float, float, int]:
        """
        计算 MPDR 候选音符的 token 冲突消解优先级。

        优先级从高到低：
            主旋律 (rank=5) > 低音锚点 (rank=4) > 右手非旋律 (rank=3) > 左手和声 (rank=2) > 其他 (rank=1)。
            同 rank 时按 keep_score、utility 降序和音高升序进一步区分。

        :param candidate: MPDR 候选音符
        :return: 越大越优先的排序元组 (rank, keep_score, utility, -pitch)
        """
        # 根据候选音符的声部角色和旋律属性确定基础优先级 rank
        if candidate.is_primary_melody:
            rank = 5
        elif candidate.is_bass_anchor:
            rank = 4
        elif candidate.hand == "right":
            rank = 3
        elif candidate.hand == "left":
            rank = 2
        else:
            rank = 1
        # 若 rank 相同，进一步使用 keep_score、utility 与音高细化区分
        return (rank, candidate.keep_score, candidate.utility, -candidate.note.pitch)

    def _candidates_to_modifier_safe_tokens(
        self,
        candidates: list[MpdrNoteCandidate],
        config: AudioPipelineConfig,
    ) -> tuple[list[str], list[MpdrNoteCandidate], int]:
        """
        将候选音符转换为规范 YAML token。

        token 必须遵循固定半音顺序：1、#1、2、b3、3、4、#4、5、#5、6、b7、7。
        这里不再为了统一修饰键风格改写为 #2、#6、b2、b5、b6，避免破坏游戏内
        固定键位听感。若同一物理键位发生自然音与变音冲突，按音乐优先级排序保留。
        优先级：主旋律 > 低音锚点 > 右手非旋律 > 左手和声 > 其他。

        :param candidates: 已折叠的候选音符
        :param config: 音频转换流水线配置
        :return: (token 列表, 实际保留候选, 因键位冲突丢弃数量)
        """
        if not candidates:
            return [], [], 0

        keyed_items: dict[str, tuple[str, MpdrNoteCandidate]] = {}
        for candidate in candidates:
            token = self._pitch_to_token(pitch=candidate.mapped_pitch, config=config)
            key_identity = self._token_key_identity(token=token)
            existing = keyed_items.get(key_identity)
            # 使用音乐优先级替代仅比较 keep_score，确保主旋律和低音锚点不被挤占
            if existing is None or self._mpdr_candidate_priority(candidate) > self._mpdr_candidate_priority(existing[1]):
                keyed_items[key_identity] = (token, candidate)

        # 物理键位去重后仍可能超过游戏允许的统一和弦上限。先按音乐优先级
        # 保留主旋律、低音锚点和右手骨架，再按音高排序以稳定输出 token。
        priority_items = sorted(
            keyed_items.values(),
            key=lambda item: self._mpdr_candidate_priority(item[1]),
            reverse=True,
        )[: config.max_chord_notes]
        selected_items = sorted(priority_items, key=lambda item: item[1].mapped_pitch)
        tokens = [token for token, _ in selected_items]
        kept_candidates = [candidate for _, candidate in selected_items]
        dropped = len(candidates) - len(kept_candidates)
        return tokens, kept_candidates, dropped

    def _pitch_to_token_with_accidental_style(
        self,
        pitch: int,
        accidental_style: str,
        config: AudioPipelineConfig,
    ) -> str:
        """
        按指定升降号风格将 MIDI 音高转换为 YAML token。

        :param pitch: 已折叠到游戏音域的 MIDI 音高
        :param accidental_style: sharp 或 flat
        :param config: 音频转换流水线配置
        :return: YAML token
        :raises ValueError: 当音高或风格无法映射时触发
        """
        pitch = self._normalize_pitch_range(pitch=pitch, config=config)
        octave = (pitch // 12) - 1
        pitch_class = pitch % 12
        zone_name = ZONE_NAMES_BY_OCTAVE.get(octave)
        if zone_name is None:
            raise ValueError(f"MIDI 音高 {pitch} 超出默认可执行音域 C3 到 B5")

        natural_token = NATURAL_PITCH_CLASSES.get(pitch_class)
        if natural_token is not None:
            return f"{ZONE_PREFIX_BY_NAME[zone_name]}{natural_token}"

        if not config.allow_accidentals:
            raise ValueError(f"MIDI 音高 {pitch} 需要半音 token，但当前已禁用半音输出")
        accidental_map = SHARP_ONLY_PITCH_CLASSES if accidental_style == "sharp" else FLAT_ONLY_PITCH_CLASSES
        accidental_token = accidental_map.get(pitch_class)
        if accidental_token is None:
            raise ValueError(f"MIDI 音高 {pitch} 无法映射为 YAML token")
        return f"{ZONE_PREFIX_BY_NAME[zone_name]}{accidental_token}"

    def _token_key_identity(self, token: str) -> str:
        """
        提取 token 对应的物理基础键身份。

        :param token: YAML token
        :return: 音区与自然音级组成的键位身份
        """
        prefix = ""
        body = token
        if token.startswith(("+", "-")):
            prefix = token[0]
            body = token[1:]
        if body.startswith(("#", "b", "B")):
            body = body[1:]
        return f"{prefix}{body}"

    def _score_svsep_mpdr_stats(self, stats: dict[str, float | int], config: AudioPipelineConfig) -> float:
        """
        根据 MPDR 统计指标计算候选全局质量分。

        :param stats: 候选统计指标
        :param config: 音频转换流水线配置
        :return: 全局质量分，范围尽量归一到 0-1
        """
        total_melody = int(stats.get("total_melody_notes", 0))
        kept_melody = int(stats.get("kept_melody_notes", 0))
        melody_integrity = 1.0 if total_melody == 0 else kept_melody / total_melody

        total_mask = float(stats.get("total_masking_risk", 0.0))
        kept_mask = float(stats.get("kept_masking_risk", 0.0))
        masking_avoidance = 1.0 if total_mask <= 0 else max(0.0, 1.0 - kept_mask / total_mask)

        total_utility = float(stats.get("total_left_utility", 0.0))
        kept_utility = float(stats.get("kept_left_utility", 0.0))
        left_harmonic_completeness = 1.0 if total_utility <= 0 else max(0.0, min(kept_utility / total_utility, 1.0))

        total_right = int(stats.get("total_right_notes", 0))
        kept_right = int(stats.get("kept_right_notes", 0))
        right_texture_preservation = 1.0 if total_right == 0 else kept_right / total_right
        harmonic_completeness = 0.75 * left_harmonic_completeness + 0.25 * right_texture_preservation

        active_slices = int(stats.get("left_active_slices", 0))
        bass_kept = int(stats.get("bass_kept_slices", 0))
        bass_continuity = 1.0 if active_slices == 0 else bass_kept / active_slices

        total_output_candidates = kept_melody + int(stats.get("kept_left_notes", 0))
        token_drops = int(stats.get("token_collision_drops", 0))
        register_clarity = 1.0 if total_output_candidates == 0 else max(
            0.0,
            1.0 - token_drops / max(1, total_output_candidates + token_drops),
        )

        weight_sum = (
            config.mpdr_score_melody_weight
            + config.mpdr_score_masking_weight
            + config.mpdr_score_harmony_weight
            + config.mpdr_score_bass_weight
            + config.mpdr_score_register_weight
        )
        if weight_sum <= 0:
            raise ValueError("MPDR 精排权重之和必须大于 0")

        weighted_score = (
            config.mpdr_score_melody_weight * melody_integrity
            + config.mpdr_score_masking_weight * masking_avoidance
            + config.mpdr_score_harmony_weight * harmonic_completeness
            + config.mpdr_score_bass_weight * bass_continuity
            + config.mpdr_score_register_weight * register_clarity
        )
        stats["melody_integrity"] = round(melody_integrity, 6)
        stats["masking_avoidance"] = round(masking_avoidance, 6)
        stats["left_harmonic_completeness"] = round(left_harmonic_completeness, 6)
        stats["right_texture_preservation"] = round(right_texture_preservation, 6)
        stats["harmonic_completeness"] = round(harmonic_completeness, 6)
        stats["bass_continuity"] = round(bass_continuity, 6)
        stats["register_clarity"] = round(register_clarity, 6)
        # 新增审计指标：低音锚点完整性
        total_bass_anchor = int(stats.get("total_bass_anchor_notes", stats.get("selected_original_bass", 0)))
        kept_bass_anchor = int(stats.get("kept_bass_anchor_notes", stats.get("kept_original_bass", 0)))
        bass_anchor_integrity = 1.0 if total_bass_anchor == 0 else kept_bass_anchor / total_bass_anchor
        stats["bass_anchor_integrity"] = round(bass_anchor_integrity, 6)
        return weighted_score / weight_sum

    def _pitch_to_token(self, pitch: int, config: AudioPipelineConfig) -> str:
        """
        将 MIDI 音高转换为 YAML 简谱 token。

        :param pitch: MIDI 音高编号
        :param config: 音频转换流水线配置
        :return: YAML 简谱 token
        :raises ValueError: 当音高超出 C3 到 B5 或半音被禁用时触发
        """
        pitch = self._normalize_pitch_range(pitch=pitch, config=config)
        octave = (pitch // 12) - 1
        pitch_class = pitch % 12
        zone_name = ZONE_NAMES_BY_OCTAVE.get(octave)
        if zone_name is None:
            raise ValueError(f"MIDI 音高 {pitch} 超出默认可执行音域 C3 到 B5")

        natural_token = NATURAL_PITCH_CLASSES.get(pitch_class)
        if natural_token is not None:
            return f"{ZONE_PREFIX_BY_NAME[zone_name]}{natural_token}"

        accidental_token = SHARP_PITCH_CLASSES.get(pitch_class)
        if accidental_token is None:
            raise ValueError(f"MIDI 音高 {pitch} 无法映射为 YAML token")
        if not config.allow_accidentals:
            raise ValueError(f"MIDI 音高 {pitch} 需要半音 token，但当前已禁用半音输出")
        return f"{ZONE_PREFIX_BY_NAME[zone_name]}{accidental_token}"

    def _normalize_pitch_range(self, pitch: int, config: AudioPipelineConfig) -> int:
        """
        根据配置处理超出 C3 到 B5 的 MIDI 音高。

        :param pitch: 原始 MIDI 音高编号
        :param config: 音频转换流水线配置
        :return: 可映射到 C3 到 B5 的 MIDI 音高编号
        :raises ValueError: 当策略为 error 且音高超范围时触发
        """
        min_pitch = 48
        max_pitch = 83
        if min_pitch <= pitch <= max_pitch:
            return pitch
        if config.out_of_range_policy == "error" and config.pitch_compression_mode != "octave_fold":
            raise ValueError(f"MIDI 音高 {pitch} 超出默认可执行音域 C3 到 B5")

        normalized_pitch = pitch
        while normalized_pitch < min_pitch:
            normalized_pitch += 12
        while normalized_pitch > max_pitch:
            normalized_pitch -= 12
        if not min_pitch <= normalized_pitch <= max_pitch:
            raise ValueError(f"MIDI 音高 {pitch} 无法按八度迁移到 C3 到 B5")
        return normalized_pitch

    def _quantize_to_index(self, value: float, config: AudioPipelineConfig) -> int:
        """
        将拍点量化为整数网格索引。

        :param value: 原始拍点
        :param config: 音频转换流水线配置
        :return: 量化后的网格索引
        :raises ValueError: 当量化结果为负数时触发
        """
        quantized_index = int(round(value / config.quantize_beat))
        if quantized_index < 0:
            raise ValueError(f"MIDI 拍点不能为负数: {value}")
        return quantized_index

    def _normalize_beat(self, beat: float) -> int | float:
        """
        规范化 beat 数值，避免 YAML 中出现不必要的长浮点。

        :param beat: 原始 beat 数值
        :return: 整数或保留六位小数的浮点数
        """
        if math.isclose(beat, round(beat), abs_tol=1e-9):
            return int(round(beat))
        return round(beat, 6)

    def _build_adaptive_octave_fold_grouped_notes(
        self,
        grouped_pitches: dict[int, list[int]],
        config: AudioPipelineConfig,
    ) -> dict[int, list[str]]:
        """
        自适应八度折叠: 将完整 MIDI 实时折叠到游戏 3 八度键盘。

        职责：
            维护一个平滑参考中心 ref_pitch，逐时间片寻找能将全部音符折叠进
            [48, 83] (C3-B5) 的最优统一八度偏移 k。
            和弦统一偏移保证和弦内部音程精确保留。
            ref_pitch 指数平滑保证相邻时间片八度切换自然隐晦。

        :param grouped_pitches: 量化拍点到原始 MIDI 音高列表的映射
        :param config: 音频转换流水线配置
        :return: 量化拍点到 YAML token 列表的映射
        """
        ref_pitch = 65.5  # F4, 36 键窗口中心
        grouped_notes: dict[int, list[str]] = {}

        self.conversion_stats["ref_pitch_trajectory_start"] = ref_pitch
        self.conversion_stats["collisions_avoided"] = 0

        for start_index in sorted(grouped_pitches):
            pitches = grouped_pitches[start_index]
            if not pitches:
                continue

            # 自适应八度折叠: 为整个时间片找到统一八度偏移
            mapped_pitches = self._adaptive_octave_fold_slice(
                pitches=pitches,
                ref_pitch=ref_pitch,
                window_low=48,
                window_high=83,
                config=config,
            )

            # 更新参考中心 (指数平滑)
            if mapped_pitches:
                avg_mapped = sum(mapped_pitches) / len(mapped_pitches)
                ref_pitch = (1 - config.ref_smoothing) * ref_pitch + config.ref_smoothing * avg_mapped

            # 统计映射
            for orig, mapped in zip(pitches, mapped_pitches):
                if orig != mapped:
                    self.conversion_stats["remapped_notes"] = int(
                        self.conversion_stats["remapped_notes"]
                    ) + 1
                    self.conversion_stats["octave_moved_notes"] = int(
                        self.conversion_stats["octave_moved_notes"]
                    ) + 1

            # 转换为 token（去重处理）
            tokens: list[str] = []
            used_tokens: set[str] = set()
            for pitch in sorted(mapped_pitches):
                token = self._pitch_to_token(pitch=pitch, config=config)
                if token not in used_tokens:
                    used_tokens.add(token)
                    tokens.append(token)
                else:
                    self.conversion_stats["collisions_avoided"] = int(
                        self.conversion_stats["collisions_avoided"]
                    ) + 1

            # 和弦密度裁剪
            if len(tokens) > config.max_chord_notes:
                # 优先保留音高跨度最大的音符（最高 + 最低 + 内部填充）
                sorted_mapped = sorted(mapped_pitches)
                priority_pitches: list[int] = []
                if sorted_mapped:
                    priority_pitches.append(sorted_mapped[-1])  # 最高音（旋律候选）
                if len(sorted_mapped) >= 2:
                    priority_pitches.append(sorted_mapped[0])   # 最低音（低音候选）
                # 中间音按离最高音的距离排序
                middle_pitches = [p for p in sorted_mapped if p not in priority_pitches]
                middle_pitches.sort(key=lambda p: abs(p - sorted_mapped[-1]))
                for mp in middle_pitches:
                    if len(priority_pitches) < config.max_chord_notes:
                        priority_pitches.append(mp)

                # 重新生成裁剪后的 token 列表
                tokens = []
                used_in_clip: set[str] = set()
                for pitch in priority_pitches:
                    token = self._pitch_to_token(pitch=pitch, config=config)
                    if token not in used_in_clip:
                        used_in_clip.add(token)
                        tokens.append(token)
                self.conversion_stats["clipped_notes"] = int(
                    self.conversion_stats["clipped_notes"]
                ) + max(0, len(mapped_pitches) - len(tokens))

            grouped_notes[start_index] = tokens

        self.conversion_stats["ref_pitch_trajectory_end"] = ref_pitch
        return grouped_notes

    def _adaptive_octave_fold_slice(
        self,
        pitches: list[int],
        ref_pitch: float,
        window_low: int,
        window_high: int,
        config: AudioPipelineConfig,
    ) -> list[int]:
        """
        对单个时间片的音符集执行自适应八度折叠。

        职责：
            寻找能使全部音符落入指定窗口的统一八度偏移 k。
            优先选择映射后平均音高最接近 ref_pitch 的 k。
            若不存在统一 k（和弦自身跨度 > 窗口宽度），各自独立映射。

        :param pitches: 同一时间片的原始 MIDI 音高列表
        :param ref_pitch: 当前平滑参考中心
        :param window_low: 输出窗口最低 MIDI 音高
        :param window_high: 输出窗口最高 MIDI 音高
        :param config: 音频转换流水线配置
        :return: 映射后的 MIDI 音高列表（保持原始顺序）
        """
        if not pitches:
            return []

        window_size = window_high - window_low  # 通常 35 (36 半音)

        # 若音符自身跨度不超窗口: 寻找统一 k
        original_span = max(pitches) - min(pitches)
        if original_span <= window_size:
            # 收集每个音符的合法 k
            common_ks: set[int] | None = None
            for pitch in pitches:
                pitch_ks: set[int] = set()
                for k in range(-6, 7):
                    mapped = pitch + 12 * k
                    if window_low <= mapped <= window_high:
                        pitch_ks.add(k)
                if common_ks is None:
                    common_ks = pitch_ks
                else:
                    common_ks = common_ks.intersection(pitch_ks)
                if not common_ks:
                    break

            if common_ks:
                # 选择使平均音高最接近 ref_pitch 的 k
                avg_original = sum(pitches) / len(pitches)
                best_k = min(common_ks, key=lambda k: abs((avg_original - 12 * k) - ref_pitch))
                return [pitch + 12 * best_k for pitch in pitches]

        # 无统一 k: 各自独立映射到最接近 ref_pitch 的位置
        result: list[int] = []
        for pitch in pitches:
            best_mapped: int | None = None
            best_dist = float("inf")
            for k in range(-6, 7):
                mapped = pitch + 12 * k
                if window_low <= mapped <= window_high:
                    dist = abs(mapped - ref_pitch)
                    if dist < best_dist:
                        best_dist = dist
                        best_mapped = mapped
            if best_mapped is None:
                # 兜底: clamp 到窗口内
                best_mapped = max(window_low, min(window_high, pitch))
            result.append(best_mapped)
        return result

    def _build_hands_decoupled_grouped_notes(
        self,
        grouped_pitches: dict[int, list[int]],
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> dict[int, list[str]]:
        """
        hands_decoupled v2: 信号增强型角色解耦双层注意力折叠。

        职责：
            阶段1: 提取全曲非因果双向平滑趋势线 global_ref[t]
            阶段2: 休止符分段 + 三重交叉验证(双峰+运动+velocity)确定分割线
            阶段3: 右手完整保留 / 左手和声功能优先级简化 + 右手密度动态约束
            阶段4: 统一折叠到C3-B5
            阶段5: Token输出 + 总密度控制

        核心原则: 左右手区分仅用于决定"该简化谁"，简化后不做音区分区。

        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param midi_notes: 原始MIDI音符事件 (用于velocity/duration信号)
        :param config: 音频转换流水线配置
        :return: 量化拍点到YAML token列表的映射
        """
        self.conversion_stats["pitch_compression_mode"] = "hands_decoupled"
        self.conversion_stats["collisions_avoided"] = 0

        # 预处理: 按时间片索引，构建velocity和duration辅助字典
        # grouped_v: {time_index -> [velocity, ...]} 与 grouped_pitches 同位置对应
        # grouped_d: {time_index -> [duration_beats, ...]} 与 grouped_pitches 同位置对应
        grouped_v: dict[int, list[int]] = {}
        grouped_d: dict[int, list[float]] = {}
        for note in midi_notes:
            ti = self._quantize_to_index(value=note.start_beat, config=config)
            grouped_v.setdefault(ti, []).append(note.velocity)
            grouped_d.setdefault(ti, []).append(note.duration_beats)

        # 阶段1: 提取全局趋势线
        time_indices = sorted(grouped_pitches)
        if not time_indices:
            raise ValueError("hands_decoupled: 没有可用MIDI音符")
        global_ref = self._extract_global_trend(
            grouped_pitches=grouped_pitches,
            time_indices=time_indices,
            config=config,
        )

        # 阶段2: 小节切分（替代原休止符分割）
        bar_ranges = self._compute_bar_windows(
            time_indices=time_indices,
            config=config,
        )

        # 阶段3: 每小节内先做左手和声功能简化（原始MIDI上），再统一折叠
        grouped_notes: dict[int, list[str]] = {}
        for bar_start, bar_end in bar_ranges:
            bar_indices = [ti for ti in time_indices if bar_start <= ti <= bar_end]
            if not bar_indices:
                continue

            # 滑动窗口分割线（三重交叉验证: 双峰+运动+velocity）
            split_pitch = self._compute_bar_split_pitch(
                bar_start=bar_start,
                bar_end=bar_end,
                all_time_indices=time_indices,
                grouped_pitches=grouped_pitches,
                grouped_v=grouped_v,
                config=config,
            )

            ref_pitch = global_ref.get(bar_start, 65.5)

            for ti in bar_indices:
                pitches = grouped_pitches.get(ti, [])
                if not pitches:
                    continue

                velocities_ti = grouped_v.get(ti, [])
                durations_ti = grouped_d.get(ti, [])

                # 按分割线分离左右手（保持velocity/duration对应关系）
                rh_pitches: list[int] = []
                lh_pitches: list[int] = []
                lh_durations: list[float] = []
                lh_velocities: list[int] = []
                for idx, p in enumerate(pitches):
                    if p >= split_pitch:
                        rh_pitches.append(p)
                    else:
                        lh_pitches.append(p)
                        if idx < len(durations_ti):
                            lh_durations.append(durations_ti[idx])
                        if idx < len(velocities_ti):
                            lh_velocities.append(velocities_ti[idx])

                # 阶段3a: 左手和声功能优先级简化 + 动态密度约束
                if lh_pitches:
                    # 计算右手duration加权密度 → 左手动态上限
                    lh_density_cap = self._compute_lh_density_cap(
                        ti=ti,
                        split_pitch=split_pitch,
                        grouped_pitches=grouped_pitches,
                        grouped_durations=grouped_d,
                        config=config,
                    )
                    lh_max = min(config.left_max_chord_notes, lh_density_cap)
                    if lh_max > 0 and len(lh_pitches) > lh_max:
                        simplified_lh = self._simplify_by_harmonic_priority(
                            pitches=lh_pitches,
                            durations=lh_durations if len(lh_durations) == len(lh_pitches) else [],
                            velocities=lh_velocities if len(lh_velocities) == len(lh_pitches) else [],
                            max_notes=lh_max,
                        )
                        clipped = len(lh_pitches) - len(simplified_lh)
                        if clipped > 0:
                            self.conversion_stats["clipped_notes"] = int(
                                self.conversion_stats["clipped_notes"]
                            ) + clipped
                        lh_pitches = simplified_lh

                # 合并简化后的左手 + 完整右手
                simplified_pitches = rh_pitches + lh_pitches

                # 阶段4: 统一折叠到 C3-B5
                mapped_pitches = self._adaptive_octave_fold_slice(
                    pitches=simplified_pitches,
                    ref_pitch=ref_pitch,
                    window_low=48,
                    window_high=83,
                    config=config,
                )

                if mapped_pitches:
                    avg_mapped = sum(mapped_pitches) / len(mapped_pitches)
                    ref_pitch = (1 - config.ref_smoothing) * ref_pitch + config.ref_smoothing * avg_mapped

                for orig, mapped in zip(simplified_pitches, mapped_pitches):
                    if orig != mapped:
                        self.conversion_stats["remapped_notes"] = int(
                            self.conversion_stats["remapped_notes"]
                        ) + 1
                        self.conversion_stats["octave_moved_notes"] = int(
                            self.conversion_stats["octave_moved_notes"]
                        ) + 1

                # 阶段5: Token输出 + 总密度控制
                tokens: list[str] = []
                used_tokens: set[str] = set()
                sorted_mapped = sorted(mapped_pitches)
                for pitch in sorted_mapped:
                    token = self._pitch_to_token(pitch=pitch, config=config)
                    if token not in used_tokens:
                        used_tokens.add(token)
                        tokens.append(token)
                    else:
                        self.conversion_stats["collisions_avoided"] = int(
                            self.conversion_stats["collisions_avoided"]
                        ) + 1

                if len(tokens) > config.max_chord_notes:
                    priority_pitches: list[int] = []
                    if sorted_mapped:
                        priority_pitches.append(sorted_mapped[-1])
                    if len(sorted_mapped) >= 2 and sorted_mapped[0] not in priority_pitches:
                        priority_pitches.append(sorted_mapped[0])
                    middle = [p for p in sorted_mapped if p not in priority_pitches]
                    middle.sort(key=lambda p: abs(p - sorted_mapped[-1]))
                    for mp in middle:
                        if len(priority_pitches) < config.max_chord_notes:
                            priority_pitches.append(mp)
                    tokens = []
                    used = set()
                    for p in priority_pitches:
                        t = self._pitch_to_token(pitch=p, config=config)
                        if t not in used:
                            used.add(t)
                            tokens.append(t)
                    extra = len(mapped_pitches) - len(tokens)
                    if extra > 0:
                        self.conversion_stats["clipped_notes"] = int(
                            self.conversion_stats["clipped_notes"]
                        ) + extra

                if tokens:
                    grouped_notes[ti] = tokens

        return grouped_notes

    def _build_attention_weighted_inner(
        self,
        grouped_pitches: dict[int, list[int]],
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> dict[int, list[str]]:
        """
        交叉注意力内嵌压缩管线（attention_weighted 模式的核心引擎）。

        与 _build_hands_decoupled_grouped_notes 共享 95% 的结构（分段、分割线、
        折叠），但在两个关键决策点用交叉注意力评分替代固定规则：
            1. 左手和声简化:         固定音程优先级查表 → 交叉注意力综合评分
            2. 最终和弦密度裁剪:     最高音+最低音贪心 → 交叉注意力综合评分

        评分维度：和声功能(0.30) + 全局注意力(0.25) + 段内注意力(0.25) + 声部进行(0.20)

        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param midi_notes: 原始MIDI音符事件 (用于velocity/duration信号)
        :param config: 音频转换流水线配置
        :return: 量化拍点到YAML token列表的映射
        """
        from note_scorer import NoteScoringContext, select_chord_notes

        self.conversion_stats["collisions_avoided"] = 0

        # 预处理: 按时间片索引，构建velocity和duration辅助字典
        grouped_v: dict[int, list[int]] = {}
        grouped_d: dict[int, list[float]] = {}
        for note in midi_notes:
            ti = self._quantize_to_index(value=note.start_beat, config=config)
            grouped_v.setdefault(ti, []).append(note.velocity)
            grouped_d.setdefault(ti, []).append(note.duration_beats)

        # 阶段1: 提取全局趋势线
        time_indices = sorted(grouped_pitches)
        if not time_indices:
            raise ValueError("attention_weighted: 没有可用MIDI音符")
        global_ref = self._extract_global_trend(
            grouped_pitches=grouped_pitches,
            time_indices=time_indices,
            config=config,
        )

        # 阶段2: 小节切分（替代原休止符分割）
        bar_ranges = self._compute_bar_windows(
            time_indices=time_indices,
            config=config,
        )

        # 阶段3: 每小节内交叉注意力评分选择 + 统一折叠
        grouped_notes: dict[int, list[str]] = {}
        prev_selected_pitches: list[int] = []

        for bar_start, bar_end in bar_ranges:
            bar_indices = [ti for ti in time_indices if bar_start <= ti <= bar_end]
            if not bar_indices:
                continue

            # 滑动窗口分割线（三重交叉验证）
            split_pitch = self._compute_bar_split_pitch(
                bar_start=bar_start,
                bar_end=bar_end,
                all_time_indices=time_indices,
                grouped_pitches=grouped_pitches,
                grouped_v=grouped_v,
                config=config,
            )

            # 计算本小节的滑动窗口时间片（段内交叉注意力用宽上下文）
            slices_per_bar = max(1, int(4.0 / config.quantize_beat))
            window_start = max(time_indices[0], bar_start - 2 * slices_per_bar)
            window_end = min(time_indices[-1], bar_end + 2 * slices_per_bar)
            bar_window_indices = [ti for ti in time_indices if window_start <= ti <= window_end]

            ref_pitch = global_ref.get(bar_start, 65.5)

            for idx_in_bar, ti in enumerate(bar_indices):
                pitches = grouped_pitches.get(ti, [])
                if not pitches:
                    prev_selected_pitches = []
                    continue

                durations_ti = grouped_d.get(ti, [])
                velocities_ti = grouped_v.get(ti, [])

                # 按分割线分离左右手（保持velocity/duration对应关系）
                rh_pitches: list[int] = []
                lh_pitches: list[int] = []
                lh_durations: list[float] = []
                lh_velocities: list[int] = []
                for i, p in enumerate(pitches):
                    if p >= split_pitch:
                        rh_pitches.append(p)
                    else:
                        lh_pitches.append(p)
                        if i < len(durations_ti):
                            lh_durations.append(durations_ti[i])
                        if i < len(velocities_ti):
                            lh_velocities.append(velocities_ti[i])

                # ---- 交叉注意力替换点 1: 左手和声简化 ----
                if lh_pitches:
                    lh_density_cap = self._compute_lh_density_cap(
                        ti=ti,
                        split_pitch=split_pitch,
                        grouped_pitches=grouped_pitches,
                        grouped_durations=grouped_d,
                        config=config,
                    )
                    lh_max = min(config.left_max_chord_notes, lh_density_cap)
                    if lh_max > 0 and len(lh_pitches) > lh_max:
                        # 后一拍原始音高（用于声部进行计算）
                        next_idx = idx_in_bar + 1
                        next_pitches = grouped_pitches.get(bar_indices[next_idx], []) if next_idx < len(bar_indices) else []

                        # 构建 velocity 映射（交叉手应对：保护左手高力度旋律音）
                        lh_vel_map: dict[int, int] = {}
                        if len(lh_velocities) == len(lh_pitches):
                            lh_vel_map = dict(zip(lh_pitches, lh_velocities))

                        lh_context = NoteScoringContext(
                            orig_grouped_pitches=grouped_pitches,
                            orig_grouped_durations=grouped_d,
                            quantize_beat=config.quantize_beat,
                            seg_indices=bar_window_indices,
                            prev_chord_notes=prev_selected_pitches,
                            next_orig_pitches=next_pitches,
                            max_chord_notes=lh_max,
                            velocities_ti=lh_vel_map,
                        )
                        simplified_lh = select_chord_notes(
                            pitches=lh_pitches,
                            time_index=ti,
                            context=lh_context,
                            max_notes=lh_max,
                        )
                        clipped = len(lh_pitches) - len(simplified_lh)
                        if clipped > 0:
                            self.conversion_stats["clipped_notes"] = int(
                                self.conversion_stats["clipped_notes"]
                            ) + clipped
                        lh_pitches = simplified_lh

                # 合并简化后的左手 + 完整右手
                simplified_pitches = rh_pitches + lh_pitches

                # 阶段4: 统一折叠到 C3-B5
                mapped_pitches = self._adaptive_octave_fold_slice(
                    pitches=simplified_pitches,
                    ref_pitch=ref_pitch,
                    window_low=48,
                    window_high=83,
                    config=config,
                )

                if mapped_pitches:
                    avg_mapped = sum(mapped_pitches) / len(mapped_pitches)
                    ref_pitch = (1 - config.ref_smoothing) * ref_pitch + config.ref_smoothing * avg_mapped

                for orig, mapped in zip(simplified_pitches, mapped_pitches):
                    if orig != mapped:
                        self.conversion_stats["remapped_notes"] = int(
                            self.conversion_stats["remapped_notes"]
                        ) + 1
                        self.conversion_stats["octave_moved_notes"] = int(
                            self.conversion_stats["octave_moved_notes"]
                        ) + 1

                # ---- 交叉注意力替换点 2: 最终和弦密度裁剪 ----
                if len(mapped_pitches) > config.max_chord_notes:
                    # 后一拍原始音高
                    next_idx = idx_in_bar + 1
                    next_pitches_orig = grouped_pitches.get(bar_indices[next_idx], []) if next_idx < len(bar_indices) else []

                    # 构建 velocity 映射（基于当前拍原始音高，折叠后通过 pitch class 就近匹配）
                    orig_vel_map: dict[int, int] = {}
                    for i, p in enumerate(pitches):
                        if i < len(velocities_ti):
                            orig_vel_map[p] = velocities_ti[i]

                    final_context = NoteScoringContext(
                        orig_grouped_pitches=grouped_pitches,
                        orig_grouped_durations=grouped_d,
                        quantize_beat=config.quantize_beat,
                        seg_indices=bar_window_indices,
                        prev_chord_notes=prev_selected_pitches,
                        next_orig_pitches=next_pitches_orig,
                        max_chord_notes=config.max_chord_notes,
                        velocities_ti=orig_vel_map,
                    )
                    selected_final = select_chord_notes(
                        pitches=mapped_pitches,
                        time_index=ti,
                        context=final_context,
                        max_notes=config.max_chord_notes,
                    )
                    clipped = len(mapped_pitches) - len(selected_final)
                    if clipped > 0:
                        self.conversion_stats["clipped_notes"] = int(
                            self.conversion_stats["clipped_notes"]
                        ) + clipped
                    mapped_pitches = selected_final

                # 阶段5: Token 输出（去重）
                tokens: list[str] = []
                used_tokens: set[str] = set()
                for pitch in sorted(mapped_pitches):
                    token = self._pitch_to_token(pitch=pitch, config=config)
                    if token not in used_tokens:
                        used_tokens.add(token)
                        tokens.append(token)
                    else:
                        self.conversion_stats["collisions_avoided"] = int(
                            self.conversion_stats["collisions_avoided"]
                        ) + 1

                if tokens:
                    grouped_notes[ti] = tokens
                    prev_selected_pitches = mapped_pitches
                else:
                    prev_selected_pitches = []

        return grouped_notes

    def _build_attention_weighted_grouped_notes(
        self,
        grouped_pitches: dict[int, list[int]],
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> dict[int, list[str]]:
        """
        attention_weighted: 交叉注意力精排模式。

        职责：
            阶段1: 用 hands_decoupled 算法扫描多组参数生成 K 个候选曲谱
            阶段2: 用全局质量分（旋律保持度 + 和声完整度 + 密度平衡度 + 声部进行质量）
                   对每个候选评分
            阶段3: 选择得分最高的候选作为最终输出

        本质是将 attention 的"评分-选择"机制通过加权质量分实现，
        不依赖神经网络学习，纯算法计算。

        :param grouped_pitches: 量化拍点到原始 MIDI 音高列表的映射
        :param midi_notes: 原始 MIDI 音符事件
        :param config: 音频转换流水线配置
        :return: 最优候选的 {时间片索引: [token 列表]}
        :raises ValueError: 当无可用候选时触发
        """
        from reranker import generate_candidates, select_best_candidate

        self.conversion_stats["pitch_compression_mode"] = "attention_weighted"

        # 预处理: 构建时长辅助字典（供全局质量分使用）
        grouped_durations: dict[int, list[float]] = {}
        for note in midi_notes:
            ti = self._quantize_to_index(value=note.start_beat, config=config)
            grouped_durations.setdefault(ti, []).append(note.duration_beats)

        # 保存关键统计快照——候选生成会调用 _build_hands_decoupled_grouped_notes
        # 从而修改 self.conversion_stats，需保护这些字段不被覆盖
        saved_raw_note_count = self.conversion_stats.get("raw_note_count", 0)
        saved_max_orig_chord = self.conversion_stats.get("max_original_chord_notes", 0)

        # 阶段1: 参数扫描生成候选（使用交叉注意力内嵌管线）
        candidates = generate_candidates(
            grouped_pitches=grouped_pitches,
            midi_notes=midi_notes,
            config=config,
            build_candidate_fn=self._build_attention_weighted_inner,
            max_candidates=12,
        )

        if not candidates:
            raise ValueError("attention_weighted: 未生成任何有效候选曲谱")

        # 阶段2-3: 评分并选择最优候选
        best_candidate = select_best_candidate(
            candidates=candidates,
            orig_grouped_pitches=grouped_pitches,
            quantize_beat=config.quantize_beat,
        )

        # 修复被候选生成覆盖的统计字段
        self.conversion_stats["pitch_compression_mode"] = "attention_weighted"
        self.conversion_stats["raw_note_count"] = saved_raw_note_count
        self.conversion_stats["max_original_chord_notes"] = saved_max_orig_chord

        # 从最优候选直接推算裁剪量（候选生成累加了12次，需校正）
        output_tokens = sum(len(tokens) for tokens in best_candidate.values())
        total_orig = sum(len(pitches) for pitches in grouped_pitches.values())
        self.conversion_stats["clipped_notes"] = max(0, total_orig - output_tokens)
        self.conversion_stats["remapped_notes"] = max(0, total_orig - output_tokens)
        self.conversion_stats["collisions_avoided"] = 0

        return best_candidate

    def _extract_global_trend(
        self,
        grouped_pitches: dict[int, list[int]],
        time_indices: list[int],
        config: AudioPipelineConfig,
    ) -> dict[int, float]:
        """
        提取全曲非因果双向平滑趋势线 global_ref[t]。

        步骤:
        1. 按粗粒度窗口(global_trend_window_beats)滑动采样音高中位数
        2. 对采样序列做非因果双向指数平滑
        3. 插值回每个时间片粒度

        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param time_indices: 有序时间片索引列表
        :param config: 音频转换流水线配置
        :return: {时间片索引: 全局参考音高}
        """
        if not time_indices:
            return {}

        # 窗口大小转换为时间片数量
        window_indices = max(1, int(config.global_trend_window_beats / config.quantize_beat))
        alpha = config.global_trend_alpha

        # 1. 粗粒度窗口采样
        samples: list[float] = []
        sample_positions: list[int] = []
        ti_min = time_indices[0]
        ti_max = time_indices[-1]
        current_pos = ti_min
        while current_pos <= ti_max:
            window_pitches: list[int] = []
            for ti in range(current_pos, current_pos + window_indices):
                if ti in grouped_pitches:
                    window_pitches.extend(grouped_pitches[ti])
            if window_pitches:
                # 中位数抗异常值
                sorted_pitches = sorted(window_pitches)
                median_pitch = sorted_pitches[len(sorted_pitches) // 2]
                samples.append(float(median_pitch))
                sample_positions.append(current_pos + window_indices // 2)
            current_pos += window_indices

        if not samples:
            return {}

        # 2. 非因果双向指数平滑
        smoothed = list(samples)
        # 前向平滑
        for i in range(1, len(smoothed)):
            smoothed[i] = (1 - alpha) * smoothed[i - 1] + alpha * samples[i]
        # 后向平滑
        for i in range(len(smoothed) - 2, -1, -1):
            smoothed[i] = (1 - alpha) * smoothed[i + 1] + alpha * samples[i]

        # 3. 线性插值回每个时间片
        global_ref: dict[int, float] = {}
        for ti in time_indices:
            # 二分查找最近采样点
            if ti <= sample_positions[0]:
                global_ref[ti] = smoothed[0]
            elif ti >= sample_positions[-1]:
                global_ref[ti] = smoothed[-1]
            else:
                # 线性插值
                for j in range(len(sample_positions) - 1):
                    if sample_positions[j] <= ti <= sample_positions[j + 1]:
                        frac = (ti - sample_positions[j]) / (sample_positions[j + 1] - sample_positions[j])
                        global_ref[ti] = smoothed[j] + frac * (smoothed[j + 1] - smoothed[j])
                        break
                else:
                    global_ref[ti] = smoothed[0]

        return global_ref

    def _compute_bar_windows(
        self,
        time_indices: list[int],
        config: AudioPipelineConfig,
    ) -> list[tuple[int, int]]:
        """
        按小节切分时间片索引为等长窗口。

        小节定义：4/4 拍号下，1 小节 = 4 拍 = floor(4.0 / quantize_beat) 个时间片。
        所有曲目均为 4/4 拍，当前无需从 MIDI 拍号读取。

        边界对齐：第一个小节从 time_indices[0] 开始（可能不是 0），
        最后一个小节延伸到 time_indices[-1]。

        :param time_indices: 有序时间片索引列表
        :param config: 音频转换流水线配置
        :return: [(bar_start_ti, bar_end_ti), ...] 各小节的起止时间片索引
        """
        if not time_indices:
            return []

        # 每小节含 4 拍，每拍含 1/quantize_beat 个时间片
        slices_per_bar = max(1, int(4.0 / config.quantize_beat))
        ti_min = time_indices[0]
        ti_max = time_indices[-1]

        bar_ranges: list[tuple[int, int]] = []
        bar_start = ti_min
        while bar_start <= ti_max:
            bar_end = bar_start + slices_per_bar - 1
            # 截断到最后一个时间片
            if bar_end > ti_max:
                bar_end = ti_max
            bar_ranges.append((bar_start, bar_end))
            bar_start = bar_end + 1

        return bar_ranges

    def _compute_bar_split_pitch(
        self,
        bar_start: int,
        bar_end: int,
        all_time_indices: list[int],
        grouped_pitches: dict[int, list[int]],
        grouped_v: dict[int, list[int]],
        config: AudioPipelineConfig,
        window_bars: int = 4,
    ) -> int:
        """
        用滑动窗口为当前小节计算左右手分割线。

        窗口以当前小节为中心，向前后各扩展 window_bars//2 个小节，
        收集窗口内所有音符后调用 _compute_split_pitch 做三重交叉验证。

        窗口在首尾小节处自动截断。

        :param bar_start: 当前小节起始时间片索引
        :param bar_end: 当前小节结束时间片索引
        :param all_time_indices: 全曲有序时间片索引
        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param grouped_v: 量化拍点到velocity列表的映射
        :param config: 音频转换流水线配置
        :param window_bars: 滑动窗口宽度（小节数，默认 4）
        :return: 当前小节的左右手分割线 MIDI 音高
        """
        slices_per_bar = max(1, int(4.0 / config.quantize_beat))
        ti_min = all_time_indices[0]
        ti_max = all_time_indices[-1]

        half_window = window_bars // 2
        window_start = max(ti_min, bar_start - half_window * slices_per_bar)
        window_end = min(ti_max, bar_end + half_window * slices_per_bar)

        # 收集窗口内所有音符
        window_pitches: list[int] = []
        window_velocities: list[int] = []
        window_indices: list[int] = []
        for ti in range(window_start, window_end + 1):
            if ti not in grouped_pitches:
                continue
            pitches = grouped_pitches[ti]
            if not pitches:
                continue
            window_indices.append(ti)
            window_pitches.extend(pitches)
            velocities = grouped_v.get(ti, [])
            window_velocities.extend(velocities)

        if not window_pitches:
            return 60

        return self._compute_split_pitch(
            seg_pitches_all=window_pitches,
            seg_velocities_all=window_velocities,
            grouped_pitches=grouped_pitches,
            seg_indices=window_indices,
            config=config,
        )

    def _compute_split_pitch(
        self,
        seg_pitches_all: list[int],
        seg_velocities_all: list[int],
        grouped_pitches: dict[int, list[int]],
        seg_indices: list[int],
        config: AudioPipelineConfig,
    ) -> int:
        """
        段内自适应左右手分割线: 双峰检测 + 运动模式交叉验证 + velocity验证。

        步骤:
        1. 构建段内音高直方图，平滑后寻找双峰
        2. 若存在明显双峰，取谷底作为候选分割线
        3. 运动模式交叉验证
        4. velocity交叉验证
        5. 验证通过则采纳; 任一失败则退化为段内中位数; 兜底MIDI 60

        :param seg_pitches_all: 段内全部原始MIDI音高列表
        :param seg_velocities_all: 段内全部原始velocity列表 (与seg_pitches_all同长度)
        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param seg_indices: 段内时间片索引列表
        :param config: 音频转换流水线配置
        :return: 左右手分割线MIDI音高
        """
        if not seg_pitches_all:
            return 60

        min_pitch = min(seg_pitches_all)
        max_pitch = max(seg_pitches_all)
        if max_pitch - min_pitch < 12:
            sorted_pitches = sorted(seg_pitches_all)
            return sorted_pitches[len(sorted_pitches) // 2]

        histogram_size = max_pitch - min_pitch + 1
        histogram = [0] * histogram_size
        for p in seg_pitches_all:
            histogram[p - min_pitch] += 1

        smoothed = list(histogram)
        for i in range(1, len(smoothed) - 1):
            smoothed[i] = (histogram[i - 1] + histogram[i] + histogram[i + 1]) / 3.0

        peaks: list[int] = []
        for i in range(1, len(smoothed) - 1):
            if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1]:
                peaks.append(i + min_pitch)
        if not peaks:
            sorted_pitches = sorted(seg_pitches_all)
            return sorted_pitches[len(sorted_pitches) // 2]

        if len(peaks) < 2:
            sorted_pitches = sorted(seg_pitches_all)
            return sorted_pitches[len(sorted_pitches) // 2]

        sorted_peaks = sorted(peaks)
        low_peak = sorted_peaks[0]
        high_peak = sorted_peaks[-1]
        if high_peak - low_peak < 8:
            sorted_pitches = sorted(seg_pitches_all)
            return sorted_pitches[len(sorted_pitches) // 2]

        valley_range_start = low_peak - min_pitch
        valley_range_end = high_peak - min_pitch
        valley_idx = min(
            range(valley_range_start, valley_range_end + 1),
            key=lambda i: smoothed[i],
        )
        candidate_split = valley_idx + min_pitch

        # 双重交叉验证: 运动模式 + velocity
        motion_ok = self._verify_motion_split(
            candidate_split=candidate_split,
            grouped_pitches=grouped_pitches,
            seg_indices=seg_indices,
            config=config,
        )
        velocity_ok = self._verify_velocity_split(
            candidate_split=candidate_split,
            seg_pitches=seg_pitches_all,
            seg_velocities=seg_velocities_all,
        )

        if motion_ok and velocity_ok:
            return candidate_split

        # 任一验证失败 → 段内中位数
        sorted_pitches = sorted(seg_pitches_all)
        return sorted_pitches[len(sorted_pitches) // 2]

    def _verify_motion_split(
        self,
        candidate_split: int,
        grouped_pitches: dict[int, list[int]],
        seg_indices: list[int],
        config: AudioPipelineConfig,
    ) -> bool:
        """
        运动模式交叉验证: 检查级进(旋律性)是否集中于分割线上方、跳进(低音性)是否集中于下方。

        :param candidate_split: 候选分割线MIDI音高
        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param seg_indices: 段内时间片索引列表
        :param config: 音频转换流水线配置
        :return: 验证是否通过
        """
        steps_above = 0
        steps_below = 0
        leaps_above = 0
        leaps_below = 0

        for idx in range(len(seg_indices) - 1):
            ti_curr = seg_indices[idx]
            ti_next = seg_indices[idx + 1]
            if ti_curr not in grouped_pitches or ti_next not in grouped_pitches:
                continue

            curr_pitches = grouped_pitches[ti_curr]
            next_pitches = grouped_pitches[ti_next]
            if not curr_pitches or not next_pitches:
                continue

            # 取当前拍最高音与下一拍最高音(旋律接近度)
            curr_max = max(curr_pitches)
            next_max = max(next_pitches)
            interval_max = abs(curr_max - next_max)
            if interval_max <= 2:
                if curr_max >= candidate_split:
                    steps_above += 1
                else:
                    steps_below += 1

            # 取当前拍最低音与下一拍最低音(低音跳进度)
            curr_min = min(curr_pitches)
            next_min = min(next_pitches)
            interval_min = abs(curr_min - next_min)
            if 5 <= interval_min <= 7:
                if curr_min < candidate_split:
                    leaps_below += 1
                else:
                    leaps_above += 1

        total_steps = steps_above + steps_below
        total_leaps = leaps_above + leaps_below

        # 若采样太少(段太短)，直接通过
        if total_steps < 2 and total_leaps < 2:
            return True

        # 级进应显著集中在上方，跳进应显著集中在下方
        step_ratio_above = steps_above / total_steps if total_steps > 0 else 0.0
        leap_ratio_below = leaps_below / total_leaps if total_leaps > 0 else 0.0

        return step_ratio_above >= 0.5 and leap_ratio_below >= 0.5

    def _verify_velocity_split(
        self,
        candidate_split: int,
        seg_pitches: list[int],
        seg_velocities: list[int],
    ) -> bool:
        """
        velocity交叉验证: 高力度音符应集中于分割线上方(旋律)，低力度集中于下方(伴奏)。

        :param candidate_split: 候选分割线MIDI音高
        :param seg_pitches: 段内原始MIDI音高列表
        :param seg_velocities: 段内velocity列表 (与seg_pitches同长度)
        :return: 验证是否通过
        """
        if not seg_pitches or len(seg_pitches) != len(seg_velocities):
            return True
        if not seg_velocities:
            return True

        # 计算velocity中位数作为高低力度分界
        sorted_velocities = sorted(seg_velocities)
        median_velocity = sorted_velocities[len(sorted_velocities) // 2]

        high_above = 0
        high_total = 0
        low_below = 0
        low_total = 0

        for pitch, vel in zip(seg_pitches, seg_velocities):
            if vel >= median_velocity:
                high_total += 1
                if pitch >= candidate_split:
                    high_above += 1
            else:
                low_total += 1
                if pitch < candidate_split:
                    low_below += 1

        high_ratio = high_above / high_total if high_total > 0 else 0.5
        low_ratio = low_below / low_total if low_total > 0 else 0.5

        # 高力度应显著在上方，低力度应显著在下方
        return high_ratio >= 0.5 and low_ratio >= 0.5

    def _simplify_by_harmonic_priority(
        self,
        pitches: list[int],
        durations: list[float],
        velocities: list[int],
        max_notes: int,
    ) -> list[int]:
        """
        基于和声功能优先级裁剪左手和弦。

        优先级(从高到低):
        1. 最低音 (bass/根音) -- 和声根基
        2. 与bass差3-4半音 (三度音) -- 决定和弦品质
        3. 与bass差10-11半音 (七度音) -- 决定和弦类型
        4. 其他音程按美学排序
        5. 与bass差7半音 (纯五度) -- 信息量最低，首先丢弃
        6. 八度重复 -- 必定丢弃

        同优先级内: velocity 高 > duration 长 > 音高低。

        交叉手应对: velocity 加权保证左手高力度旋律音不被误裁。

        :param pitches: 左手原始MIDI音高列表
        :param durations: 对应duration列表 (可为空)
        :param velocities: 对应velocity列表 (可为空，范围 0-127)
        :param max_notes: 最大保留音符数
        :return: 简化后的音高列表
        """
        if len(pitches) <= max_notes:
            return list(pitches)

        if not pitches or max_notes <= 0:
            return []

        sorted_pitches = sorted(pitches)
        bass = sorted_pitches[0]

        # 为每个音符计算优先级分数 (分数越小越优先保留)
        scored: list[tuple[float, float, int]] = []
        for idx, p in enumerate(sorted_pitches):
            interval = p - bass

            if interval == 0:
                base_score = 0.0
            elif interval % 12 == 0:
                base_score = 100.0
            elif interval in (3, 4, 15, 16):
                base_score = 10.0
            elif interval in (10, 11, 22, 23):
                base_score = 15.0
            elif interval == 7:
                base_score = 80.0
            elif interval in (5, 6, 8, 9):
                base_score = 40.0
            elif interval in (1, 2):
                base_score = 60.0
            else:
                base_score = 50.0

            # duration加权: 长音符降分 (更优先保留)
            dur_weight = 0.0
            if durations and idx < len(durations):
                dur_weight = min(durations[idx], 4.0) * 0.5

            # velocity加权: 高力度降分 (交叉手场景下保护左手旋律)
            vel_weight = 0.0
            if velocities and idx < len(velocities):
                vel_weight = (1.0 - velocities[idx] / 127.0) * 8.0

            scored.append((base_score - dur_weight - vel_weight, float(interval), p))

        # 按优先级升序 (分数最小=最优) + 同分按音高升序
        scored.sort(key=lambda x: (x[0], x[2]))
        selected = scored[:max_notes]
        selected.sort(key=lambda x: x[2])

        return [p for _, _, p in selected]

    def _compute_lh_density_cap(
        self,
        ti: int,
        split_pitch: int,
        grouped_pitches: dict[int, list[int]],
        grouped_durations: dict[int, list[float]],
        config: AudioPipelineConfig,
    ) -> int:
        """
        计算左手当前时间片的动态密度上限。

        基于前后各半拍的滑动窗口中，右手音符的 duration加权密度。

        :param ti: 当前时间片索引
        :param split_pitch: 左右手分割线
        :param grouped_pitches: 量化拍点到原始MIDI音高列表的映射
        :param grouped_durations: 量化拍点到duration列表的映射
        :param config: 音频转换流水线配置
        :return: 左手动态密度上限 (1-4)
        """
        # 半拍窗口对应的时间片数量
        half_beat_indices = max(1, int(0.5 / config.quantize_beat))
        window_start = ti - half_beat_indices
        window_end = ti + half_beat_indices

        rh_weighted = 0.0
        for wti in range(window_start, window_end + 1):
            if wti not in grouped_pitches:
                continue
            pitches = grouped_pitches[wti]
            durations = grouped_durations.get(wti, [])
            for idx, p in enumerate(pitches):
                if p >= split_pitch:
                    dur = durations[idx] if idx < len(durations) else 0.0
                    # 权重: clamp到quantize_beat以内，长音符=1.0，短音符按比例
                    weight = min(dur / config.quantize_beat, 1.0) if config.quantize_beat > 0 else 1.0
                    rh_weighted += weight

        # 映射到左手密度上限
        if rh_weighted <= 2.5:
            return 1
        elif rh_weighted <= 5.0:
            return 2
        elif rh_weighted <= 7.5:
            return 3
        return 4

    def _validate_playback_timing(self, score_events: list[dict[str, Any]], config: AudioPipelineConfig) -> None:
        """
        校验输出 score 的节拍与按键持续时间是否可执行。

        :param score_events: YAML score 事件列表
        :param config: 音频转换流水线配置
        :raises ValueError: 当节拍过短或按键持续时间过长时触发
        """
        if config.quantize_beat <= 0:
            raise ValueError("quantize_beat 必须大于 0")
        seconds_per_beat = 60.0 / config.bpm
        shortest_event_seconds = min(float(event["beat"]) * seconds_per_beat for event in score_events)
        if config.key_press_seconds >= shortest_event_seconds:
            raise ValueError(
                "playback.key_press_seconds 必须小于最短事件持续时间，"
                f"当前最短事件 {shortest_event_seconds:.4f} 秒"
            )


class AudioToYamlPipeline:
    """
    端到端音频到 YAML 转换流水线。

    职责：
        串联真实分轨、真实转录、MIDI 转 YAML、写出报告与 YAML 回读校验。
    """

    def __init__(self) -> None:
        """
        初始化流水线组件。
        """
        self.separator = DemucsSeparator()
        self.transcriber = PianoTranscriber()
        self.converter = MidiToYamlConverter()

    def run(self, config: AudioPipelineConfig) -> None:
        """
        执行完整转换流程。

        :param config: 音频转换流水线配置
        :raises ValueError: 当配置非法或输出 YAML 无法回读时触发
        """
        self._validate_config(config=config)
        config.work_dir.mkdir(parents=True, exist_ok=True)
        config.output_yaml_path.parent.mkdir(parents=True, exist_ok=True)

        # BPM 自动检测
        resolved_config = config
        if config.bpm <= 0:
            audio_for_bpm = config.audio_path
            if audio_for_bpm is None and config.input_midi_path is not None:
                audio_for_bpm = config.input_midi_path
            if audio_for_bpm is not None and audio_for_bpm.is_file():
                detected_bpm = self._detect_bpm(audio_for_bpm)
                print(f"自动检测 BPM: {detected_bpm:.1f}")
                resolved_config = replace(config, bpm=detected_bpm)

        stem_path: Path | None = None
        if resolved_config.input_midi_path is not None:
            midi_path = resolved_config.input_midi_path
        else:
            stem_path = self.separator.separate(config=resolved_config)
            midi_path = self.transcriber.transcribe(audio_path=stem_path, config=resolved_config)
        yaml_data = self.converter.convert(midi_path=midi_path, config=resolved_config)

        with config.output_yaml_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(yaml_data, file, allow_unicode=True, sort_keys=False)

        PianoConfigLoader().load(config.output_yaml_path)
        self._write_report(config=resolved_config, stem_path=stem_path, midi_path=midi_path, yaml_data=yaml_data)

    def _detect_bpm(self, audio_path: Path) -> float:
        """
        自动检测音频或 MIDI 文件的 BPM。

        职责：
            音频文件使用 librosa onset 强度包络 + 动态编程估计全局 tempo。
            MIDI 文件从 pretty_midi 读取 tempo 信息。若 MIDI tempo 为 120.0
            （转录工具默认值）且存在同名音频文件，则改用音频 librosa 检测。

        :param audio_path: 音频或 MIDI 文件路径
        :return: 检测到的 BPM
        :raises RuntimeError: 当自动检测失败时触发
        """
        # MIDI 文件: 从 pretty_midi 提取 tempo
        if audio_path.suffix.lower() in {".mid", ".midi"}:
            try:
                midi_data = pretty_midi.PrettyMIDI(str(audio_path))
                tempo_changes = midi_data.get_tempo_changes()
                if len(tempo_changes[1]) > 0:
                    midi_tempo = round(float(tempo_changes[1][0]), 1)
                    # 若为转录工具默认值 120，尝试用原始音频检测
                    if midi_tempo == 120.0:
                        audio_candidates = _find_matching_audio(audio_path)
                        for audio_cand in audio_candidates:
                            try:
                                detected = _estimate_audio_bpm(audio_path=audio_cand)
                                print(f"MIDI 速度为默认 120，从音频检测到 BPM: {detected:.1f}")
                                return detected
                            except Exception:
                                continue
                    return midi_tempo
            except Exception as exc:
                raise RuntimeError(f"MIDI BPM 检测失败: {audio_path}") from exc

        # 音频文件: librosa tempo 检测
        return _estimate_audio_bpm(audio_path=audio_path)

    def _validate_config(self, config: AudioPipelineConfig) -> None:
        """
        校验流水线基础配置。

        :param config: 音频转换流水线配置
        :raises ValueError: 当配置不符合真实执行要求时触发
        """
        if not config.song_name.strip():
            raise ValueError("song_name 必须是非空字符串")
        if config.input_midi_path is None and config.audio_path is None:
            raise ValueError("必须提供 audio_path 或 input_midi_path")
        if config.input_midi_path is not None and not config.input_midi_path.is_file():
            raise FileNotFoundError(f"输入 MIDI 不存在: {config.input_midi_path}")
        if config.bpm < 0:
            raise ValueError("bpm 必须大于等于 0，0 表示自动检测")
        if config.beat_unit <= 0:
            raise ValueError("beat_unit 必须大于 0")
        if config.start_delay_seconds < 0:
            raise ValueError("start_delay_seconds 必须大于等于 0")
        if config.key_press_seconds < 0:
            raise ValueError("key_press_seconds 必须大于等于 0")
        if config.quantize_beat <= 0:
            raise ValueError("quantize_beat 必须大于 0")
        if config.max_chord_notes <= 0:
            raise ValueError("max_chord_notes 必须大于 0")
        if config.max_score_events <= 0:
            raise ValueError("max_score_events 必须大于 0")
        if config.out_of_range_policy not in {"error", "octave_fold"}:
            raise ValueError("out_of_range_policy 只允许 error 或 octave_fold")
        if config.audio_path is not None and config.demucs_stem != "piano":
            raise ValueError("demucs_stem 必须为 piano，钢琴转录器只能处理钢琴轨")
        if config.pitch_compression_mode not in {"none", "octave_fold", "adaptive_octave_fold", "hands_decoupled", "attention_weighted", SVSEP_MPDR_MODE, SCORE_AWARE_THEORY_MODE}:
            raise ValueError("pitch_compression_mode 只允许 none、octave_fold、adaptive_octave_fold、hands_decoupled、attention_weighted、svsep_mpdr 或 score_aware_theory")
        if config.pitch_compression_mode in {"hands_decoupled", "attention_weighted"}:
            if config.left_max_chord_notes < 1:
                raise ValueError("left_max_chord_notes 必须 >= 1")
            if config.phrase_gap_beats <= 0:
                raise ValueError("phrase_gap_beats 必须 > 0")
            if not 0.0 < config.global_trend_alpha <= 1.0:
                raise ValueError("global_trend_alpha 必须在 (0, 1] 之间")
            if config.global_trend_window_beats <= 0:
                raise ValueError("global_trend_window_beats 必须 > 0")
        if config.pitch_compression_mode == SVSEP_MPDR_MODE:
            if config.svsep_model_path is None:
                raise ValueError("svsep_mpdr 模式必须提供 svsep_model_path")
            if not config.svsep_model_path.is_file():
                raise FileNotFoundError(f"piano_svsep 模型权重不存在: {config.svsep_model_path}")
            if config.svsep_device not in {"cpu", "cuda"}:
                raise ValueError("svsep_device 只允许 cpu 或 cuda")
            if config.mpdr_candidate_count <= 0:
                raise ValueError("mpdr_candidate_count 必须大于 0")
            if config.mpdr_scan_delta_ratio < 0:
                raise ValueError("mpdr_scan_delta_ratio 必须大于等于 0")
            if config.mpdr_melody_onset_guard_beats <= 0:
                raise ValueError("mpdr_melody_onset_guard_beats 必须大于 0")
            if not 0 < config.mpdr_left_opacity_min <= config.mpdr_left_opacity_base <= config.mpdr_left_opacity_max:
                raise ValueError("MPDR 左手预算必须满足 0 < min <= base <= max")
            if config.mpdr_register_collision_semitones <= 0:
                raise ValueError("mpdr_register_collision_semitones 必须大于 0")
            if config.mpdr_repeat_suppression_beats <= 0:
                raise ValueError("mpdr_repeat_suppression_beats 必须大于 0")
            if config.mpdr_repeat_overlap_tolerance_beats < 0:
                raise ValueError("mpdr_repeat_overlap_tolerance_beats 必须大于等于 0")
            if config.sustain_split_enabled:
                if config.sustain_split_threshold_beats <= 0:
                    raise ValueError("sustain_split_threshold_beats 必须大于 0")
                if config.sustain_split_max_midpoint_density < 0:
                    raise ValueError("sustain_split_max_midpoint_density 必须大于等于 0")
            if config.merge_sustained_gap_beats < 0:
                raise ValueError("merge_sustained_gap_beats 必须大于等于 0")
            if not MIN_GAME_PITCH <= config.mpdr_left_window_high <= MAX_GAME_PITCH:
                raise ValueError("mpdr_left_window_high 必须位于 C3 到 B5 音域内")
            if not MIN_GAME_PITCH <= config.mpdr_right_window_low <= MAX_GAME_PITCH:
                raise ValueError("mpdr_right_window_low 必须位于 C3 到 B5 音域内")
            mpdr_non_negative_values = {
                "mpdr_melody_protection_strength": config.mpdr_melody_protection_strength,
                "mpdr_melody_rest_bonus": config.mpdr_melody_rest_bonus,
                "mpdr_bass_anchor_weight": config.mpdr_bass_anchor_weight,
                "mpdr_harmony_color_weight": config.mpdr_harmony_color_weight,
                "mpdr_voice_leading_weight": config.mpdr_voice_leading_weight,
                "mpdr_velocity_weight": config.mpdr_velocity_weight,
                "mpdr_duration_weight": config.mpdr_duration_weight,
                "mpdr_duplicate_penalty": config.mpdr_duplicate_penalty,
                "mpdr_onset_collision_penalty": config.mpdr_onset_collision_penalty,
                "mpdr_register_collision_penalty": config.mpdr_register_collision_penalty,
                "mpdr_low_mud_penalty": config.mpdr_low_mud_penalty,
                "mpdr_density_penalty": config.mpdr_density_penalty,
                "mpdr_duration_ducking_strength": config.mpdr_duration_ducking_strength,
                "mpdr_repeat_overlap_tolerance_beats": config.mpdr_repeat_overlap_tolerance_beats,
                "mpdr_score_melody_weight": config.mpdr_score_melody_weight,
                "mpdr_score_masking_weight": config.mpdr_score_masking_weight,
                "mpdr_score_harmony_weight": config.mpdr_score_harmony_weight,
                "mpdr_score_bass_weight": config.mpdr_score_bass_weight,
                "mpdr_score_register_weight": config.mpdr_score_register_weight,
            }
            for name, value in mpdr_non_negative_values.items():
                if value < 0:
                    raise ValueError(f"{name} 必须大于等于 0")
            if (
                config.mpdr_score_melody_weight
                + config.mpdr_score_masking_weight
                + config.mpdr_score_harmony_weight
                + config.mpdr_score_bass_weight
                + config.mpdr_score_register_weight
            ) <= 0:
                raise ValueError("MPDR 精排权重之和必须大于 0")

    def _write_report(
        self,
        config: AudioPipelineConfig,
        stem_path: Path | None,
        midi_path: Path,
        yaml_data: dict[str, Any],
    ) -> None:
        """
        写出转换报告。

        :param config: 音频转换流水线配置
        :param stem_path: Demucs 输出 stem 路径
        :param midi_path: 钢琴转录输出 MIDI 路径
        :param yaml_data: 输出 YAML 数据
        """
        report_path = config.work_dir / "conversion_report.json"
        report_data = {
            "audio_path": None if config.audio_path is None else str(config.audio_path),
            "input_midi_path": None if config.input_midi_path is None else str(config.input_midi_path),
            "stem_path": None if stem_path is None else str(stem_path),
            "midi_path": str(midi_path),
            "output_yaml_path": str(config.output_yaml_path),
            "transcription_checkpoint": None
            if config.transcription_checkpoint is None
            else str(config.transcription_checkpoint),
            "song_name": config.song_name,
            "bpm": config.bpm,
            "quantize_beat": config.quantize_beat,
            "score_events": len(yaml_data["score"]),
            "allow_accidentals": config.allow_accidentals,
            "out_of_range_policy": config.out_of_range_policy,
            "pitch_compression_mode": config.pitch_compression_mode,
            "ref_smoothing": config.ref_smoothing,
            "left_max_chord_notes": config.left_max_chord_notes,
            "phrase_gap_beats": config.phrase_gap_beats,
            "global_trend_alpha": config.global_trend_alpha,
            "global_trend_window_beats": config.global_trend_window_beats,
            "svsep_model_path": None if config.svsep_model_path is None else str(config.svsep_model_path),
            "svsep_device": config.svsep_device,
            "mpdr_candidate_count": config.mpdr_candidate_count,
            "mpdr_scan_delta_ratio": config.mpdr_scan_delta_ratio,
            "mpdr_left_window_high": config.mpdr_left_window_high,
            "mpdr_right_window_low": config.mpdr_right_window_low,
            "mpdr_melody_protection_strength": config.mpdr_melody_protection_strength,
            "mpdr_melody_onset_guard_beats": config.mpdr_melody_onset_guard_beats,
            "mpdr_left_opacity_base": config.mpdr_left_opacity_base,
            "mpdr_left_opacity_min": config.mpdr_left_opacity_min,
            "mpdr_left_opacity_max": config.mpdr_left_opacity_max,
            "mpdr_repeat_suppression_beats": config.mpdr_repeat_suppression_beats,
            "mpdr_repeat_overlap_tolerance_beats": config.mpdr_repeat_overlap_tolerance_beats,
            "merge_sustained_notes": config.merge_sustained_notes,
            "merge_sustained_gap_beats": config.merge_sustained_gap_beats,
            "conversion_stats": self.converter.conversion_stats,
        }
        with report_path.open("w", encoding="utf-8") as file:
            json.dump(report_data, file, ensure_ascii=False, indent=2)


def parse_arguments() -> argparse.Namespace:
    """
    解析命令行参数。

    :return: 命令行参数命名空间
    """
    argument_parser = argparse.ArgumentParser(description="将音频经 Demucs 与 Transkun 转录转换为可执行 YAML 曲谱")
    argument_parser.add_argument("--audio", type=Path, help="真实输入音频路径")
    argument_parser.add_argument("--input-midi", type=Path, help="已有 MIDI 路径，提供时跳过 Demucs 与钢琴转录")
    argument_parser.add_argument("--output-yaml", required=True, type=Path, help="输出 YAML 曲谱路径")
    argument_parser.add_argument("--work-dir", required=True, type=Path, help="中间文件与报告目录")
    argument_parser.add_argument("--song-name", required=True, help="输出 YAML 曲谱名称")
    argument_parser.add_argument("--bpm", default=0, type=float, help="输出 YAML BPM，0 表示自动检测")
    argument_parser.add_argument("--beat-unit", default=4, type=int, help="输出 YAML 节拍单位")
    argument_parser.add_argument("--start-delay-seconds", default=3.0, type=float, help="自动弹奏启动延迟")
    argument_parser.add_argument("--key-press-seconds", default=0.0, type=float, help="单次按键保持时间")
    argument_parser.add_argument("--demucs-model", default="htdemucs_6s", help="Demucs 模型名称")
    argument_parser.add_argument("--demucs-stem", default="piano", help="分轨目标，固定为 piano")
    argument_parser.add_argument("--transcription-checkpoint", type=Path, help="Transkun 模型权重 .pt 路径，未提供时使用内置默认")
    argument_parser.add_argument("--transcription-device", default="cpu", choices=("cpu", "cuda"), help="Transkun 推理设备，默认 cpu")
    argument_parser.add_argument(
        "--transcription-segment-hop-size",
        type=float,
        default=None,
        help="Transkun segment 步长（秒），None 使用模型默认",
    )
    argument_parser.add_argument(
        "--transcription-segment-size",
        type=float,
        default=None,
        help="Transkun segment 尺寸（秒），None 使用模型默认",
    )
    argument_parser.add_argument("--quantize-beat", default=0.25, type=float, help="MIDI 量化节拍单位")
    argument_parser.add_argument("--max-chord-notes", default=6, type=int, help="单个和弦最大音符数")
    argument_parser.add_argument("--max-score-events", default=5000, type=int, help="最大 score 事件数量")
    argument_parser.add_argument("--allow-accidentals", action="store_true", help="允许输出升半音 token")
    argument_parser.add_argument(
        "--out-of-range-policy",
        default="error",
        choices=("error", "octave_fold"),
        help="超范围音符处理策略，octave_fold 会按八度迁移到 C3-B5",
    )
    argument_parser.add_argument(
        "--pitch-compression-mode",
        default="adaptive_octave_fold",
        choices=("none", "octave_fold", "adaptive_octave_fold", "hands_decoupled", "attention_weighted", SVSEP_MPDR_MODE, SCORE_AWARE_THEORY_MODE),
        help="音高压缩模式，默认 adaptive_octave_fold；score_aware_theory 启用乐谱语义感知缩编管线",
    )
    argument_parser.add_argument("--ref-smoothing", default=0.2, type=float, help="ref_pitch 指数平滑系数 (仅 adaptive_octave_fold / hands_decoupled)")
    argument_parser.add_argument("--left-max-chord-notes", default=3, type=int, help="左手轨最大音符数 (仅 hands_decoupled / attention_weighted)")
    argument_parser.add_argument("--phrase-gap-beats", default=0.5, type=float, help="休止符分割阈值拍数 (仅 hands_decoupled)")
    argument_parser.add_argument("--global-trend-alpha", default=0.05, type=float, help="全局趋势线平滑系数 (仅 hands_decoupled)")
    argument_parser.add_argument("--global-trend-window-beats", default=4.0, type=float, help="全局趋势采样窗口拍数 (仅 hands_decoupled)")
    argument_parser.add_argument("--svsep-model-path", type=Path, help="piano_svsep 模型权重 .ckpt 路径 (仅 svsep_mpdr)")
    argument_parser.add_argument("--svsep-device", default="cpu", choices=("cpu", "cuda"), help="piano_svsep 推理设备 (仅 svsep_mpdr)")
    argument_parser.add_argument("--mpdr-candidate-count", default=12, type=int, help="MPDR 参数扫描候选数量上限")
    argument_parser.add_argument("--mpdr-scan-delta-ratio", default=0.15, type=float, help="MPDR 参数扫描相对扰动比例")
    argument_parser.add_argument("--mpdr-right-ref-pitch", default=67.0, type=float, help="MPDR 右手折叠参考中心")
    argument_parser.add_argument("--mpdr-left-ref-pitch", default=55.0, type=float, help="MPDR 左手折叠参考中心")
    argument_parser.add_argument("--mpdr-left-window-high", default=67, type=int, help="MPDR 左手折叠窗口最高 MIDI 音高")
    argument_parser.add_argument("--mpdr-right-window-low", default=60, type=int, help="MPDR 右手折叠窗口最低 MIDI 音高")
    argument_parser.add_argument("--mpdr-melody-protection-strength", default=1.0, type=float, help="MPDR 主旋律保护强度")
    argument_parser.add_argument("--mpdr-melody-onset-guard-beats", default=0.5, type=float, help="MPDR 主旋律起音保护窗口拍数")
    argument_parser.add_argument("--mpdr-left-opacity-base", default=3.6, type=float, help="MPDR 左手基础感知密度预算")
    argument_parser.add_argument("--mpdr-left-opacity-min", default=1.2, type=float, help="MPDR 左手最小感知密度预算")
    argument_parser.add_argument("--mpdr-left-opacity-max", default=9.0, type=float, help="MPDR 左手最大感知密度预算")
    argument_parser.add_argument("--mpdr-melody-rest-bonus", default=2.5, type=float, help="MPDR 右手休止时左手预算增量")
    argument_parser.add_argument("--mpdr-bass-anchor-weight", default=1.4, type=float, help="MPDR 低音锚点权重")
    argument_parser.add_argument("--mpdr-harmony-color-weight", default=1.1, type=float, help="MPDR 和声色彩权重")
    argument_parser.add_argument("--mpdr-voice-leading-weight", default=0.7, type=float, help="MPDR 声部连接权重")
    argument_parser.add_argument("--mpdr-velocity-weight", default=0.45, type=float, help="MPDR velocity 分析权重")
    argument_parser.add_argument("--mpdr-duration-weight", default=0.35, type=float, help="MPDR 时值分析权重")
    argument_parser.add_argument("--mpdr-duplicate-penalty", default=0.9, type=float, help="MPDR 八度重复惩罚")
    argument_parser.add_argument("--mpdr-onset-collision-penalty", default=1.2, type=float, help="MPDR 同起音遮蔽惩罚")
    argument_parser.add_argument("--mpdr-register-collision-penalty", default=1.1, type=float, help="MPDR 音区碰撞惩罚")
    argument_parser.add_argument("--mpdr-register-collision-semitones", default=12.0, type=float, help="MPDR 音区碰撞半音阈值")
    argument_parser.add_argument("--mpdr-low-mud-penalty", default=0.8, type=float, help="MPDR 低音浑浊惩罚")
    argument_parser.add_argument("--mpdr-low-mud-pitch", default=48, type=int, help="MPDR 低音浑浊判定音高阈值")
    argument_parser.add_argument("--mpdr-density-penalty", default=0.3, type=float, help="MPDR 左手局部密度惩罚")
    argument_parser.add_argument("--mpdr-duration-ducking-strength", default=0.7, type=float, help="MPDR 长音覆盖主旋律惩罚")
    argument_parser.add_argument("--mpdr-repeat-suppression-beats", default=0.5, type=float, help="MPDR 非主旋律同音重复起音抑制窗口")
    argument_parser.add_argument("--mpdr-repeat-overlap-tolerance-beats", default=0.05, type=float, help="MPDR 延音重叠判定容差")
    argument_parser.add_argument("--merge-sustained-notes", action="store_true", default=True, help="在读取 MIDI 时合并同音重叠碎片，消除延音连击")
    argument_parser.add_argument("--no-merge-sustained-notes", action="store_false", dest="merge_sustained_notes", help="关闭同音重叠合并")
    argument_parser.add_argument("--merge-sustained-gap-beats", default=0.25, type=float, help="同音合并允许的最大间隔拍数")
    argument_parser.add_argument("--mpdr-score-melody-weight", default=0.30, type=float, help="MPDR 精排主旋律完整度权重")
    argument_parser.add_argument("--mpdr-score-masking-weight", default=0.25, type=float, help="MPDR 精排遮蔽规避权重")
    argument_parser.add_argument("--mpdr-score-harmony-weight", default=0.20, type=float, help="MPDR 精排和声完整度权重")
    argument_parser.add_argument("--mpdr-score-bass-weight", default=0.15, type=float, help="MPDR 精排低音连续性权重")
    argument_parser.add_argument("--mpdr-score-register-weight", default=0.10, type=float, help="MPDR 精排音区清晰度权重")
    argument_parser.add_argument("--sustain-split", action="store_true", default=False, help="启用长低音续打击键，在持音中点追加一次起音 (仅 svsep_mpdr)")
    argument_parser.add_argument("--sustain-split-threshold", default=1.0, type=float, help="触发连续打击的 duration_beats 下限 (仅 svsep_mpdr)")
    argument_parser.add_argument("--sustain-split-max-density", default=3, type=int, help="中点位置最大同拍音符数，超过则不续打 (仅 svsep_mpdr)")
    # --- 新增 onset 聚类参数（来自 T-001）---
    argument_parser.add_argument("--disable-onset-cluster", action="store_true", help="禁用 MIDI 起音聚类")
    argument_parser.add_argument("--onset-cluster-window-beats", default=0.08, type=float, help="起音聚类窗口拍数")
    argument_parser.add_argument("--onset-cluster-max-span-beats", default=0.12, type=float, help="起音聚类最大跨度拍数")
    # --- 新增主旋律 DP 权重参数（来自 T-003）---
    argument_parser.add_argument("--mpdr-melody-pitch-weight", default=1.0, type=float, help="MPDR 主旋律音高权重")
    argument_parser.add_argument("--mpdr-melody-duration-weight", default=0.35, type=float, help="MPDR 主旋律时值权重")
    argument_parser.add_argument("--mpdr-melody-velocity-weight", default=0.45, type=float, help="MPDR 主旋律力度权重")
    argument_parser.add_argument("--mpdr-melody-beat-weight", default=0.20, type=float, help="MPDR 主旋律强拍权重")
    argument_parser.add_argument("--mpdr-melody-continuity-weight", default=0.70, type=float, help="MPDR 主旋律连续性权重")
    argument_parser.add_argument("--mpdr-melody-large-jump-penalty", default=0.35, type=float, help="MPDR 主旋律大跳惩罚")
    argument_parser.add_argument("--mpdr-melody-repetition-penalty", default=0.20, type=float, help="MPDR 主旋律重复惩罚")
    return argument_parser.parse_args()


def main() -> None:
    """
    程序入口函数。

    职责：
        从命令行参数构造配置并执行完整音频到 YAML 转换流水线。
    """
    arguments = parse_arguments()
    config = AudioPipelineConfig(
        audio_path=arguments.audio,
        input_midi_path=arguments.input_midi,
        output_yaml_path=arguments.output_yaml,
        work_dir=arguments.work_dir,
        song_name=arguments.song_name,
        bpm=arguments.bpm,
        beat_unit=arguments.beat_unit,
        start_delay_seconds=arguments.start_delay_seconds,
        key_press_seconds=arguments.key_press_seconds,
        demucs_model=arguments.demucs_model,
        demucs_stem=arguments.demucs_stem,
        transcription_checkpoint=arguments.transcription_checkpoint,
        transcription_device=arguments.transcription_device,
        transcription_segment_hop_size=arguments.transcription_segment_hop_size,
        transcription_segment_size=arguments.transcription_segment_size,
        quantize_beat=arguments.quantize_beat,
        max_chord_notes=arguments.max_chord_notes,
        max_score_events=arguments.max_score_events,
        allow_accidentals=arguments.allow_accidentals,
        out_of_range_policy=arguments.out_of_range_policy,
        pitch_compression_mode=arguments.pitch_compression_mode,
        ref_smoothing=arguments.ref_smoothing,
        left_max_chord_notes=arguments.left_max_chord_notes,
        phrase_gap_beats=arguments.phrase_gap_beats,
        global_trend_alpha=arguments.global_trend_alpha,
        global_trend_window_beats=arguments.global_trend_window_beats,
        svsep_model_path=arguments.svsep_model_path,
        svsep_device=arguments.svsep_device,
        mpdr_candidate_count=arguments.mpdr_candidate_count,
        mpdr_scan_delta_ratio=arguments.mpdr_scan_delta_ratio,
        mpdr_right_ref_pitch=arguments.mpdr_right_ref_pitch,
        mpdr_left_ref_pitch=arguments.mpdr_left_ref_pitch,
        mpdr_left_window_high=arguments.mpdr_left_window_high,
        mpdr_right_window_low=arguments.mpdr_right_window_low,
        mpdr_melody_protection_strength=arguments.mpdr_melody_protection_strength,
        mpdr_melody_onset_guard_beats=arguments.mpdr_melody_onset_guard_beats,
        mpdr_left_opacity_base=arguments.mpdr_left_opacity_base,
        mpdr_left_opacity_min=arguments.mpdr_left_opacity_min,
        mpdr_left_opacity_max=arguments.mpdr_left_opacity_max,
        mpdr_melody_rest_bonus=arguments.mpdr_melody_rest_bonus,
        mpdr_bass_anchor_weight=arguments.mpdr_bass_anchor_weight,
        mpdr_harmony_color_weight=arguments.mpdr_harmony_color_weight,
        mpdr_voice_leading_weight=arguments.mpdr_voice_leading_weight,
        mpdr_velocity_weight=arguments.mpdr_velocity_weight,
        mpdr_duration_weight=arguments.mpdr_duration_weight,
        mpdr_duplicate_penalty=arguments.mpdr_duplicate_penalty,
        mpdr_onset_collision_penalty=arguments.mpdr_onset_collision_penalty,
        mpdr_register_collision_penalty=arguments.mpdr_register_collision_penalty,
        mpdr_register_collision_semitones=arguments.mpdr_register_collision_semitones,
        mpdr_low_mud_penalty=arguments.mpdr_low_mud_penalty,
        mpdr_low_mud_pitch=arguments.mpdr_low_mud_pitch,
        mpdr_density_penalty=arguments.mpdr_density_penalty,
        mpdr_duration_ducking_strength=arguments.mpdr_duration_ducking_strength,
        mpdr_repeat_suppression_beats=arguments.mpdr_repeat_suppression_beats,
        mpdr_repeat_overlap_tolerance_beats=arguments.mpdr_repeat_overlap_tolerance_beats,
        merge_sustained_notes=arguments.merge_sustained_notes,
        merge_sustained_gap_beats=arguments.merge_sustained_gap_beats,
        mpdr_score_melody_weight=arguments.mpdr_score_melody_weight,
        mpdr_score_masking_weight=arguments.mpdr_score_masking_weight,
        mpdr_score_harmony_weight=arguments.mpdr_score_harmony_weight,
        mpdr_score_bass_weight=arguments.mpdr_score_bass_weight,
        mpdr_score_register_weight=arguments.mpdr_score_register_weight,
        sustain_split_enabled=arguments.sustain_split,
        sustain_split_threshold_beats=arguments.sustain_split_threshold,
        sustain_split_max_midpoint_density=arguments.sustain_split_max_density,
        onset_cluster_enabled=not arguments.disable_onset_cluster,
        onset_cluster_window_beats=arguments.onset_cluster_window_beats,
        onset_cluster_max_span_beats=arguments.onset_cluster_max_span_beats,
        mpdr_melody_pitch_weight=arguments.mpdr_melody_pitch_weight,
        mpdr_melody_duration_weight=arguments.mpdr_melody_duration_weight,
        mpdr_melody_velocity_weight=arguments.mpdr_melody_velocity_weight,
        mpdr_melody_beat_weight=arguments.mpdr_melody_beat_weight,
        mpdr_melody_continuity_weight=arguments.mpdr_melody_continuity_weight,
        mpdr_melody_large_jump_penalty=arguments.mpdr_melody_large_jump_penalty,
        mpdr_melody_repetition_penalty=arguments.mpdr_melody_repetition_penalty,
    )
    AudioToYamlPipeline().run(config=config)
    print(f"YAML 曲谱已生成: {config.output_yaml_path}")


if __name__ == "__main__":
    main()
