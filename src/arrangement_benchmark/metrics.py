"""
模块名称：arrangement_benchmark.metrics
功能描述：
    基于真实原始 MIDI、缩编 MIDI 与同源 WAV 计算六维原曲保真指标。

主要组件：
    - MidiFidelityResult: 四项 MIDI 结构指标结果
    - calculate_midi_fidelity: 计算旋律、节奏、低音和声部音区指标

依赖说明：
    - audio_to_yaml_converter: 复用当前 MPDR 主旋律动态规划与量化逻辑
    - pretty_midi: 读取真实 MIDI
    - numpy: 计算相关系数与有限数校验

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np
import pretty_midi
import librosa

from audio_to_yaml_converter import AudioPipelineConfig, MidiNoteEvent, MidiToYamlConverter


@dataclass(frozen=True)
class MidiFidelityResult:
    """真实 MIDI 对的四项结构保真结果。"""

    values: Mapping[str, float]
    reference_note_count: int
    reduced_note_count: int


@dataclass(frozen=True)
class AudioFidelityResult:
    """同一真实钢琴音源渲染结果的两项音频保真指标。"""

    values: Mapping[str, float]
    sample_rate: int
    compared_duration_seconds: float


def _fold_pitch_to_game_range(pitch: int) -> int:
    """按八度等价关系将音高折叠到 C3-B5。"""
    folded_pitch = pitch
    while folded_pitch < 48:
        folded_pitch += 12
    while folded_pitch > 83:
        folded_pitch -= 12
    if not 48 <= folded_pitch <= 83:
        raise ValueError(f"音高无法折叠到 C3-B5: {pitch}")
    return folded_pitch


def _pearson_similarity(first: tuple[float, ...], second: tuple[float, ...], metric_name: str) -> float:
    """将皮尔逊相关系数映射到 [0,1]，无定义时直接报错。"""
    if len(first) != len(second) or len(first) < 2:
        raise ValueError(f"{metric_name} 相关序列长度不足或不一致")
    first_array = np.asarray(first, dtype=float)
    second_array = np.asarray(second, dtype=float)
    if np.std(first_array) == 0.0 or np.std(second_array) == 0.0:
        raise ValueError(f"{metric_name} 相关序列为常量，无法计算皮尔逊相关")
    correlation = float(np.corrcoef(first_array, second_array)[0, 1])
    if not math.isfinite(correlation):
        raise ValueError(f"{metric_name} 皮尔逊相关结果非有限数")
    return max(0.0, min(1.0, (correlation + 1.0) / 2.0))


def _presence_f1(reference_indices: set[int], reduced_indices: set[int]) -> float:
    """计算量化起音集合的 F1。"""
    if not reference_indices or not reduced_indices:
        raise ValueError("起音集合为空，无法计算节奏 F1")
    matched_count = len(reference_indices & reduced_indices)
    precision = matched_count / len(reduced_indices)
    recall = matched_count / len(reference_indices)
    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _read_grouped_notes(
    midi_path: Path,
    converter: MidiToYamlConverter,
    config: AudioPipelineConfig,
) -> tuple[tuple[MidiNoteEvent, ...], dict[int, list[MidiNoteEvent]]]:
    """读取真实 MIDI，并按当前转换器的量化规则分组。"""
    if not midi_path.is_file():
        raise FileNotFoundError(f"指标输入 MIDI 不存在: {midi_path}")
    midi_data = pretty_midi.PrettyMIDI(str(midi_path))
    notes = converter._read_midi_notes(midi_data=midi_data, config=config)
    if not notes:
        raise ValueError(f"指标输入 MIDI 不包含音符: {midi_path}")
    groups = converter._group_midi_notes_by_index(midi_notes=notes, config=config)
    if not groups:
        raise ValueError(f"指标输入 MIDI 量化后为空: {midi_path}")
    return notes, groups


def _calculate_melody_score(
    reference_groups: dict[int, list[MidiNoteEvent]],
    reduced_groups: dict[int, list[MidiNoteEvent]],
    converter: MidiToYamlConverter,
    config: AudioPipelineConfig,
) -> float:
    """使用现有 MPDR 动态规划提取参考旋律并计算音高/轮廓保真度。"""
    melody_path = converter._extract_primary_melody_path(right_groups=reference_groups, config=config)
    if len(melody_path) < 3:
        raise ValueError("MPDR 主旋律路径长度不足，无法计算 D1")

    expected_pitches: list[float] = []
    selected_reduced_pitches: list[float] = []
    matched_pitch_classes = 0
    for time_index, reference_note in sorted(melody_path.items()):
        reduced_notes = reduced_groups.get(time_index)
        if not reduced_notes:
            continue
        expected_pitch = _fold_pitch_to_game_range(reference_note.pitch)
        selected_note = min(reduced_notes, key=lambda item: abs(item.pitch - expected_pitch))
        expected_pitches.append(float(expected_pitch))
        selected_reduced_pitches.append(float(selected_note.pitch))
        if selected_note.pitch % 12 == expected_pitch % 12:
            matched_pitch_classes += 1

    if len(expected_pitches) < 3:
        raise ValueError("缩编结果与参考旋律的共同时间片不足，无法计算 D1")
    pitch_retention = matched_pitch_classes / len(melody_path)
    expected_intervals = tuple(np.diff(np.asarray(expected_pitches, dtype=float)).tolist())
    reduced_intervals = tuple(np.diff(np.asarray(selected_reduced_pitches, dtype=float)).tolist())
    contour_similarity = _pearson_similarity(expected_intervals, reduced_intervals, "D1 旋律轮廓")
    return 0.6 * pitch_retention + 0.4 * contour_similarity


def _calculate_bass_score(
    reference_groups: dict[int, list[MidiNoteEvent]],
    reduced_groups: dict[int, list[MidiNoteEvent]],
) -> float:
    """计算共同时间片的低音 pitch class 保留和轮廓相似度。"""
    common_indices = sorted(set(reference_groups) & set(reduced_groups))
    if len(common_indices) < 3:
        raise ValueError("共同低音时间片不足，无法计算 D4")
    reference_bass = tuple(float(min(note.pitch for note in reference_groups[index])) for index in common_indices)
    reduced_bass = tuple(float(min(note.pitch for note in reduced_groups[index])) for index in common_indices)
    pitch_class_retention = sum(
        1
        for reference_pitch, reduced_pitch in zip(reference_bass, reduced_bass)
        if int(reference_pitch) % 12 == int(reduced_pitch) % 12
    ) / len(common_indices)
    reference_intervals = tuple(np.diff(np.asarray(reference_bass, dtype=float)).tolist())
    reduced_intervals = tuple(np.diff(np.asarray(reduced_bass, dtype=float)).tolist())
    contour_similarity = _pearson_similarity(reference_intervals, reduced_intervals, "D4 低音轮廓")
    return 0.5 * pitch_class_retention + 0.5 * contour_similarity


def _calculate_voice_register_score(
    reference_groups: dict[int, list[MidiNoteEvent]],
    reduced_groups: dict[int, list[MidiNoteEvent]],
) -> float:
    """计算共同事件的高低声部 pitch class 与音区跨度保留。"""
    common_indices = sorted(set(reference_groups) & set(reduced_groups))
    if not common_indices:
        raise ValueError("没有共同事件，无法计算 D5")
    event_scores: list[float] = []
    for index in common_indices:
        reference_pitches = sorted(note.pitch for note in reference_groups[index])
        reduced_pitches = sorted(note.pitch for note in reduced_groups[index])
        extreme_matches = (
            int(reference_pitches[0] % 12 == reduced_pitches[0] % 12)
            + int(reference_pitches[-1] % 12 == reduced_pitches[-1] % 12)
        ) / 2.0
        reference_span = reference_pitches[-1] - reference_pitches[0]
        reduced_span = reduced_pitches[-1] - reduced_pitches[0]
        span_denominator = max(reference_span, reduced_span, 1)
        span_similarity = 1.0 - min(abs(reference_span - reduced_span) / span_denominator, 1.0)
        event_scores.append(0.5 * extreme_matches + 0.5 * span_similarity)
    return float(np.mean(np.asarray(event_scores, dtype=float)))


def calculate_midi_fidelity(
    reference_midi_path: Path,
    reduced_midi_path: Path,
    config: AudioPipelineConfig,
) -> MidiFidelityResult:
    """
    计算真实 MIDI 对的 D1、D3、D4、D5 四项结构保真指标。

    :param reference_midi_path: 不可变 88 键参考 MIDI
    :param reduced_midi_path: 候选算法36键缩编 MIDI
    :param config: 当前算法正式量化与 MPDR 权重配置
    :return: 四项归一化指标和音符统计
    :raises ValueError: 任一指标无法由真实数据确定时触发
    """
    converter = MidiToYamlConverter()
    reference_notes, reference_groups = _read_grouped_notes(reference_midi_path, converter, config)
    reduced_notes, reduced_groups = _read_grouped_notes(reduced_midi_path, converter, config)

    metric_values = {
        "melody": _calculate_melody_score(reference_groups, reduced_groups, converter, config),
        "rhythm": _presence_f1(set(reference_groups), set(reduced_groups)),
        "bass": _calculate_bass_score(reference_groups, reduced_groups),
        "voice_register": _calculate_voice_register_score(reference_groups, reduced_groups),
    }
    for metric_name, metric_value in metric_values.items():
        if not math.isfinite(metric_value) or not 0.0 <= metric_value <= 1.0:
            raise ValueError(f"{metric_name} 指标无效: {metric_value}")

    return MidiFidelityResult(
        values=MappingProxyType(metric_values),
        reference_note_count=len(reference_notes),
        reduced_note_count=len(reduced_notes),
    )


def _mean_frame_cosine(
    reference_features: np.ndarray,
    reduced_features: np.ndarray,
    metric_name: str,
    map_signed_similarity: bool,
) -> float:
    """计算两组逐帧特征的平均余弦相似度。"""
    if reference_features.ndim != 2 or reduced_features.ndim != 2:
        raise ValueError(f"{metric_name} 特征必须是二维矩阵")
    frame_count = min(reference_features.shape[1], reduced_features.shape[1])
    if frame_count <= 0:
        raise ValueError(f"{metric_name} 没有共同特征帧")
    reference = reference_features[:, :frame_count]
    reduced = reduced_features[:, :frame_count]
    reference_norms = np.linalg.norm(reference, axis=0)
    reduced_norms = np.linalg.norm(reduced, axis=0)
    valid_mask = (reference_norms > 0.0) & (reduced_norms > 0.0)
    if not np.any(valid_mask):
        raise ValueError(f"{metric_name} 所有共同帧均为静音或零向量")
    similarities = np.sum(reference[:, valid_mask] * reduced[:, valid_mask], axis=0) / (
        reference_norms[valid_mask] * reduced_norms[valid_mask]
    )
    mean_similarity = float(np.mean(np.clip(similarities, -1.0, 1.0)))
    if map_signed_similarity:
        mean_similarity = (mean_similarity + 1.0) / 2.0
    normalized_similarity = max(0.0, min(1.0, mean_similarity))
    if not math.isfinite(normalized_similarity):
        raise ValueError(f"{metric_name} 结果非有限数")
    return normalized_similarity


def calculate_audio_fidelity(
    reference_wav_path: Path,
    reduced_wav_path: Path,
    frame_seconds: float,
    n_mfcc: int,
) -> AudioFidelityResult:
    """
    计算同一真实采样钢琴音源渲染 WAV 的 D2 与 D6。

    :param reference_wav_path: 原始 88 键参考 WAV
    :param reduced_wav_path: 36 键缩编 WAV
    :param frame_seconds: 特征帧移秒数
    :param n_mfcc: MFCC 维数
    :return: harmony 与 audio_features 两项指标
    :raises FileNotFoundError: WAV 不存在时触发
    :raises ValueError: 音频为空、采样率不一致或特征无法计算时触发
    """
    for wav_path in (reference_wav_path, reduced_wav_path):
        if not wav_path.is_file():
            raise FileNotFoundError(f"音频指标输入 WAV 不存在: {wav_path}")
    if not math.isfinite(frame_seconds) or frame_seconds <= 0:
        raise ValueError("frame_seconds 必须是有限正数")
    if not isinstance(n_mfcc, int) or isinstance(n_mfcc, bool) or n_mfcc <= 0:
        raise ValueError("n_mfcc 必须是正整数")

    reference_audio, reference_sample_rate = librosa.load(str(reference_wav_path), sr=None, mono=True)
    reduced_audio, reduced_sample_rate = librosa.load(str(reduced_wav_path), sr=None, mono=True)
    if reference_sample_rate != reduced_sample_rate:
        raise ValueError(
            f"WAV 采样率不一致: reference={reference_sample_rate}, reduced={reduced_sample_rate}"
        )
    sample_rate = int(reference_sample_rate)
    common_sample_count = min(reference_audio.size, reduced_audio.size)
    if common_sample_count <= 0:
        raise ValueError("WAV 没有共同有效采样")
    reference_audio = reference_audio[:common_sample_count]
    reduced_audio = reduced_audio[:common_sample_count]
    hop_length = int(round(sample_rate * frame_seconds))
    if hop_length <= 0:
        raise ValueError("音频特征 hop_length 非法")

    reference_chroma = librosa.feature.chroma_stft(
        y=reference_audio,
        sr=sample_rate,
        hop_length=hop_length,
    )
    reduced_chroma = librosa.feature.chroma_stft(
        y=reduced_audio,
        sr=sample_rate,
        hop_length=hop_length,
    )
    reference_mfcc = librosa.feature.mfcc(
        y=reference_audio,
        sr=sample_rate,
        n_mfcc=n_mfcc,
        hop_length=hop_length,
    )
    reduced_mfcc = librosa.feature.mfcc(
        y=reduced_audio,
        sr=sample_rate,
        n_mfcc=n_mfcc,
        hop_length=hop_length,
    )

    metric_values = {
        "harmony": _mean_frame_cosine(
            reference_chroma,
            reduced_chroma,
            "D2 色度相似度",
            map_signed_similarity=False,
        ),
        "audio_features": _mean_frame_cosine(
            reference_mfcc,
            reduced_mfcc,
            "D6 MFCC 相似度",
            map_signed_similarity=True,
        ),
    }
    return AudioFidelityResult(
        values=MappingProxyType(metric_values),
        sample_rate=sample_rate,
        compared_duration_seconds=common_sample_count / sample_rate,
    )


def calculate_weighted_score(
    metric_values: Mapping[str, float],
    metric_weights: Mapping[str, float],
) -> float:
    """
    使用设计文档的六维权重计算单曲候选总分。

    :param metric_values: 六维真实指标
    :param metric_weights: 集中配置中的六维固定权重
    :return: [0,1] 加权总分
    :raises ValueError: 维度不一致、权重非法或指标无效时触发
    """
    if set(metric_values) != set(metric_weights):
        raise ValueError("指标维度与权重维度不一致")
    if not math.isclose(sum(metric_weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("六维权重和必须等于 1.0")
    for metric_name, metric_value in metric_values.items():
        if not math.isfinite(metric_value) or not 0.0 <= metric_value <= 1.0:
            raise ValueError(f"指标 {metric_name} 超出 [0,1]: {metric_value}")
    weighted_score = sum(metric_values[name] * metric_weights[name] for name in metric_weights)
    if not math.isfinite(weighted_score) or not 0.0 <= weighted_score <= 1.0:
        raise ValueError(f"六维加权总分无效: {weighted_score}")
    return weighted_score
