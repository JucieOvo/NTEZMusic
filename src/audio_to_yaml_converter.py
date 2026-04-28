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
    - librosa: 用于将音频文件读取为钢琴转录库需要的采样数组
    - demucs: 用于真实音频分轨，需要通过命令行调用
    - piano_transcription_inference: 用于真实钢琴音频转 MIDI

作者：JucieOvo
创建日期：2026-04-27
修改记录：
    - 2026-04-27 JucieOvo: 新增音频分轨、钢琴转录与 YAML 转换流水线
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pretty_midi
import yaml
import librosa

from piano_auto_player import PianoConfigLoader


AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac"}


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


DEFAULT_KEYBOARD_MAPPING = {
    "high": {"1": "q", "2": "w", "3": "e", "4": "r", "5": "t", "6": "y", "7": "u"},
    "middle": {"1": "a", "2": "s", "3": "d", "4": "f", "5": "g", "6": "h", "7": "j"},
    "low": {"1": "z", "2": "x", "3": "c", "4": "v", "5": "b", "6": "n", "7": "m"},
}

NATURAL_PITCH_CLASSES = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
SHARP_PITCH_CLASSES = {1: "#1", 3: "b3", 6: "#4", 8: "#5", 10: "b7"}
ZONE_NAMES_BY_OCTAVE = {3: "low", 4: "middle", 5: "high"}
ZONE_PREFIX_BY_NAME = {"low": "-", "middle": "", "high": "+"}


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
        transcription_checkpoint (Path | None): 钢琴转录模型权重路径，未提供时使用依赖库默认行为
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
            str(config.audio_path),
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Demucs 分轨失败:\n{result.stderr.strip()}")

        stem_path = separated_dir / config.demucs_model / config.audio_path.stem / f"{config.demucs_stem}.wav"
        if not stem_path.is_file():
            raise FileNotFoundError(f"Demucs 未生成目标 stem: {stem_path}")
        return stem_path


class PianoTranscriber:
    """
    钢琴音频转 MIDI 执行器。

    职责：
        调用 piano_transcription_inference 的真实转录能力，将钢琴 stem 转为 MIDI 文件。
    """

    def transcribe(self, audio_path: Path, config: AudioPipelineConfig) -> Path:
        """
        将钢琴音频转录为 MIDI。

        :param audio_path: Demucs 输出的目标 stem 路径
        :param config: 音频转换流水线配置
        :return: 转录生成的 MIDI 文件路径
        :raises FileNotFoundError: 当音频或 checkpoint 不存在时触发
        :raises RuntimeError: 当转录依赖缺失或推理失败时触发
        """
        if not audio_path.is_file():
            raise FileNotFoundError(f"钢琴转录输入音频不存在: {audio_path}")
        if config.transcription_checkpoint is not None and not config.transcription_checkpoint.exists():
            raise FileNotFoundError(f"钢琴转录 checkpoint 不存在: {config.transcription_checkpoint}")

        midi_dir = config.work_dir / "midi"
        midi_dir.mkdir(parents=True, exist_ok=True)
        midi_stem = audio_path.stem if config.audio_path is None else config.audio_path.stem
        midi_path = midi_dir / f"{midi_stem}.mid"

        try:
            inference_module = importlib.import_module("piano_transcription_inference.inference")
            transcriptor_class = getattr(inference_module, "PianoTranscription")
        except (ImportError, AttributeError) as exc:
            raise RuntimeError("无法导入 piano_transcription_inference，请确认依赖已真实安装") from exc

        try:
            checkpoint_path = None if config.transcription_checkpoint is None else str(config.transcription_checkpoint)
            audio_samples = self._load_audio_samples(audio_path=audio_path)
            transcriptor = transcriptor_class(device="cuda", checkpoint_path=checkpoint_path)
            transcriptor.transcribe(audio_samples, str(midi_path))
        except TypeError:
            try:
                checkpoint_path = None if config.transcription_checkpoint is None else str(config.transcription_checkpoint)
                audio_samples = self._load_audio_samples(audio_path=audio_path)
                transcriptor = transcriptor_class(checkpoint_path=checkpoint_path)
                transcriptor.transcribe(audio_samples, str(midi_path))
            except Exception as exc:
                raise RuntimeError(f"钢琴转录失败: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"钢琴转录失败: {exc}") from exc

        if not midi_path.is_file():
            raise FileNotFoundError(f"钢琴转录未生成 MIDI: {midi_path}")

        # 转录工具默认写入 tempo=120，从原始音频检测真实 BPM 并写入 MIDI
        if config.audio_path is not None:
            self._fix_midi_tempo(midi_path=midi_path, audio_path=config.audio_path)

        return midi_path

    def _fix_midi_tempo(self, midi_path: Path, audio_path: Path) -> None:
        """
        从原始音频检测 BPM 并写入 MIDI tempo 轨道。

        转录工具默认将 tempo 设为 120，本方法用 librosa 检测真实 BPM，
        覆盖 MIDI 中的 tempo 值，使后续 --input-midi 复用时不需重新检测。

        :param midi_path: 转录生成的 MIDI 文件路径
        :param audio_path: 原始音频文件路径（用于 BPM 检测）
        """
        try:
            y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            tempo_arr = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr)
            if tempo_arr is None or len(tempo_arr) == 0:
                return
            detected_bpm = round(float(tempo_arr[0]), 1)
            if abs(detected_bpm - 120.0) < 1.0:
                return  # 确实就是 120，无需修改

            midi_data = pretty_midi.PrettyMIDI(str(midi_path))
            # 清除原有 tempo 变化事件，写入检测值
            new_tempo = pretty_midi.TempoChange(tempo=detected_bpm, time=0.0)
            # pretty_midi 中 tempo 通过 tempo_changes 列表管理
            midi_data._tempo_changes = [(0.0, new_tempo)]
            # 重新计算 tick 相关时间
            midi_data._update_tick_scaling()
            midi_data.write(str(midi_path))
            print(f"MIDI tempo 已更新为 {detected_bpm} BPM（从音频检测）")
        except Exception:
            pass  # 静默失败，不影响主流程

    def _load_audio_samples(self, audio_path: Path) -> Any:
        """
        读取钢琴转录所需的音频采样数组。

        :param audio_path: Demucs 输出的目标 stem 路径
        :return: 16 kHz 单声道音频采样数组
        :raises RuntimeError: 当音频读取失败时触发
        """
        try:
            audio_samples, _ = librosa.load(str(audio_path), sr=16000, mono=True)
        except Exception as exc:
            raise RuntimeError(f"读取钢琴 stem 音频失败: {audio_path}") from exc
        return audio_samples


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
        }
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
        return tuple(sorted(notes, key=lambda item: (item.start_beat, item.pitch)))

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
                resolved_config = AudioPipelineConfig(
                    audio_path=config.audio_path,
                    input_midi_path=config.input_midi_path,
                    output_yaml_path=config.output_yaml_path,
                    work_dir=config.work_dir,
                    song_name=config.song_name,
                    bpm=detected_bpm,
                    beat_unit=config.beat_unit,
                    start_delay_seconds=config.start_delay_seconds,
                    key_press_seconds=config.key_press_seconds,
                    demucs_model=config.demucs_model,
                    demucs_stem=config.demucs_stem,
                    transcription_checkpoint=config.transcription_checkpoint,
                    quantize_beat=config.quantize_beat,
                    max_chord_notes=config.max_chord_notes,
                    max_score_events=config.max_score_events,
                    allow_accidentals=config.allow_accidentals,
                    out_of_range_policy=config.out_of_range_policy,
                    pitch_compression_mode=config.pitch_compression_mode,
                    ref_smoothing=config.ref_smoothing,
                    left_max_chord_notes=config.left_max_chord_notes,
                    phrase_gap_beats=config.phrase_gap_beats,
                    global_trend_alpha=config.global_trend_alpha,
                    global_trend_window_beats=config.global_trend_window_beats,
                )

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
        :return: 检测到的 BPM，若失败则返回 120 作为安全默认值
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
                                y, sr = librosa.load(str(audio_cand), sr=22050, mono=True)
                                onset_env = librosa.onset.onset_strength(y=y, sr=sr)
                                tempo_arr = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr)
                                if tempo_arr is not None and len(tempo_arr) > 0:
                                    detected = round(float(tempo_arr[0]), 1)
                                    if abs(detected - 120.0) > 1.0:
                                        print(f"MIDI 速度为默认 120，从音频检测到 BPM: {detected:.1f}")
                                        return detected
                            except Exception:
                                continue
                    return midi_tempo
            except Exception:
                pass
            return 120.0

        # 音频文件: librosa tempo 检测
        try:
            y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            tempo = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr)
            if tempo is not None and len(tempo) > 0:
                return round(float(tempo[0]), 1)
        except Exception:
            pass
        return 120.0  # 安全默认值

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
        if config.pitch_compression_mode not in {"none", "octave_fold", "adaptive_octave_fold", "hands_decoupled", "attention_weighted"}:
            raise ValueError("pitch_compression_mode 只允许 none、octave_fold、adaptive_octave_fold、hands_decoupled 或 attention_weighted")
        if config.pitch_compression_mode in {"hands_decoupled", "attention_weighted"}:
            if config.left_max_chord_notes < 1:
                raise ValueError("left_max_chord_notes 必须 >= 1")
            if config.phrase_gap_beats <= 0:
                raise ValueError("phrase_gap_beats 必须 > 0")
            if not 0.0 < config.global_trend_alpha <= 1.0:
                raise ValueError("global_trend_alpha 必须在 (0, 1] 之间")
            if config.global_trend_window_beats <= 0:
                raise ValueError("global_trend_window_beats 必须 > 0")

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
            "conversion_stats": self.converter.conversion_stats,
        }
        with report_path.open("w", encoding="utf-8") as file:
            json.dump(report_data, file, ensure_ascii=False, indent=2)


def parse_arguments() -> argparse.Namespace:
    """
    解析命令行参数。

    :return: 命令行参数命名空间
    """
    argument_parser = argparse.ArgumentParser(description="将音频经 Demucs 与钢琴转录转换为可执行 YAML 曲谱")
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
    argument_parser.add_argument("--transcription-checkpoint", type=Path, help="钢琴转录 checkpoint 路径")
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
        choices=("none", "octave_fold", "adaptive_octave_fold", "hands_decoupled", "attention_weighted"),
        help="音高压缩模式，默认 adaptive_octave_fold 自适应八度折叠",
    )
    argument_parser.add_argument("--ref-smoothing", default=0.2, type=float, help="ref_pitch 指数平滑系数 (仅 adaptive_octave_fold / hands_decoupled)")
    argument_parser.add_argument("--left-max-chord-notes", default=3, type=int, help="左手轨最大音符数 (仅 hands_decoupled / attention_weighted)")
    argument_parser.add_argument("--phrase-gap-beats", default=0.5, type=float, help="休止符分割阈值拍数 (仅 hands_decoupled)")
    argument_parser.add_argument("--global-trend-alpha", default=0.05, type=float, help="全局趋势线平滑系数 (仅 hands_decoupled)")
    argument_parser.add_argument("--global-trend-window-beats", default=4.0, type=float, help="全局趋势采样窗口拍数 (仅 hands_decoupled)")
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
    )
    AudioToYamlPipeline().run(config=config)
    print(f"YAML 曲谱已生成: {config.output_yaml_path}")


if __name__ == "__main__":
    main()
