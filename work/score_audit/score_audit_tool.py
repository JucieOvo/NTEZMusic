"""
模块名称：score_audit_tool
功能描述：
    对 svsep_mpdr_v3 生成的游戏 36 键 YAML 曲谱进行只读谱面对照审查。

    本脚本不修改核心算法，不覆盖 config、work 原始 MIDI 或任何既有转换产物。
    它只在指定输出目录中生成审查副本、YAML 反解 MIDI、结构化统计和 Markdown 报告。

主要组件：
    - AuditNote: 统一谱面音符事件
    - EventRow: YAML score 事件反解后的定位记录
    - DecodedYamlScore: YAML 反解结果聚合对象
    - parse_note_token: 将游戏 token 映射为 MIDI pitch
    - decode_yaml_score: 将 YAML score 反解为谱面事件
    - detect_layout_issues: 基于参考 MIDI 与目标 MIDI 的窗口统计识别疑似精排问题
    - run_audit: 执行三首曲目的审查产物生成

依赖说明：
    - PyYAML: 读取项目 YAML 曲谱
    - pretty_midi: 读取与写出真实 MIDI 文件

作者：JucieOvo
创建日期：2026-07-01
修改记录：
    - 2026-07-01 JucieOvo: 创建只读谱面对照审查脚本
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pretty_midi
import yaml


# 游戏简谱自然音级到 C 大调 pitch class 的映射。
# 用途：将 YAML token 中的 1-7 反解为 MIDI pitch。
# 依赖关系：与 src/audio_to_yaml_converter.py 中的 NATURAL_PITCH_CLASSES 语义保持一致。
# 影响：该映射错误会导致所有 target_reduced.mid 音高错误，因此测试必须覆盖。
NATURAL_PITCH_CLASS_BY_TOKEN = {
    "1": 0,
    "2": 2,
    "3": 4,
    "4": 5,
    "5": 7,
    "6": 9,
    "7": 11,
}

# 游戏三音区到 MIDI 八度的映射。
# 用途：- 前缀表示 C3-B3，中音无前缀表示 C4-B4，+ 前缀表示 C5-B5。
# 依赖关系：与项目 YAML 指南和现有播放器 token 语义一致。
# 影响：该映射决定 36 键缩编谱反解后的五线谱音区。
OCTAVE_BY_ZONE_PREFIX = {
    "-": 3,
    "": 4,
    "+": 5,
}

# YAML notes 字段 token 拆分表达式。
# 用途：保持中括号和弦作为一个 token，其余非空白片段作为单独 token。
TOKEN_PATTERN = re.compile(r"\[[^\]]+\]|\S+")

# 审查窗口配置。
# 用途：先按 2 拍窗口近似观察 2/4 网格和局部织体，再按位置判断异常重音。
TWO_BEAT_WINDOW = 2.0
HALF_BEAT_ALIGNMENT_GRID = 0.5
ONSET_GROUP_GRID = 1.0 / 96.0
DEFAULT_NOTE_VELOCITY = 80
MIN_WRITTEN_NOTE_BEATS = 1.0 / 16.0
DENSE_CHORD_NOTE_THRESHOLD = 5
WEAK_BEAT_DENSE_NOTE_THRESHOLD = 4
LOW_ANCHOR_REFERENCE_PITCH_MAX = 55
TARGET_LOW_ANCHOR_MISSING_PITCH_MIN = 60
WIDE_REFERENCE_SPAN_THRESHOLD = 36
NARROW_TARGET_SPAN_THRESHOLD = 18
BLOCKY_REFERENCE_ONSET_MIN = 6
BLOCKY_TARGET_ONSET_MAX = 2
FRAGMENTED_TARGET_ONSET_MIN = 6
FRAGMENTED_REFERENCE_ONSET_MAX = 2


@dataclass(frozen=True)
class AuditTarget:
    """
    单首曲目的审查输入定义。

    职责：
        记录目标 YAML、现有参考 MIDI 和输出目录所需的曲目标识。

    属性：
        song_id (str): 曲目短标识，用于输出目录命名
        display_name (str): 报告中显示的曲目名称
        yaml_path (Path): 被审查的 svsep_mpdr_v3 YAML 路径
        reference_midi_path (Path): 项目现有 MIDI 参考路径
    """

    song_id: str
    display_name: str
    yaml_path: Path
    reference_midi_path: Path


@dataclass(frozen=True)
class AuditNote:
    """
    统一谱面音符事件。

    职责：
        表示来自 YAML 反解或 MIDI 读取的单个音符，统一使用拍点坐标便于对齐。

    属性：
        pitch (int): MIDI pitch
        start_beat (float): 起始拍点
        end_beat (float): 结束拍点
        velocity (int): MIDI 力度
        source_index (int | None): 原 YAML event 索引；参考 MIDI 没有该值
        token (str | None): 原 YAML token；参考 MIDI 没有该值
    """

    pitch: int
    start_beat: float
    end_beat: float
    velocity: int
    source_index: int | None
    token: str | None


@dataclass(frozen=True)
class EventRow:
    """
    YAML score 事件反解定位记录。

    职责：
        保存每个可执行 token 的拍点、音符列表和风险标记，便于报告回溯到 YAML event。

    属性：
        event_index (int): YAML score 中的原始事件索引
        start_beat (float): token 起始拍点
        end_beat (float): token 结束拍点
        beat (float): token 持续拍数
        raw (str): 原始 token 或和弦字符串
        tokens (tuple[str, ...]): 和弦内单音 token 列表；休止符为空
        pitches (tuple[int, ...]): 反解后的 MIDI pitch 列表
        note_count (int): 当前 token 同时发声的音符数量
        has_sharp (bool): 是否包含升半音 token
        has_flat (bool): 是否包含降半音 token
        duplicate_token (bool): 是否存在重复 token
        duplicate_pitch (bool): 是否存在重复目标 MIDI pitch
    """

    event_index: int
    start_beat: float
    end_beat: float
    beat: float
    raw: str
    tokens: tuple[str, ...]
    pitches: tuple[int, ...]
    note_count: int
    has_sharp: bool
    has_flat: bool
    duplicate_token: bool
    duplicate_pitch: bool


@dataclass(frozen=True)
class DecodedYamlScore:
    """
    YAML 曲谱反解结果。

    职责：
        聚合曲谱基础信息、反解音符、事件定位表和统计信息。

    属性：
        song_name (str): YAML 中的曲名
        bpm (float): YAML 中的 BPM
        beat_unit (int): YAML 中的节拍单位
        notes (tuple[AuditNote, ...]): 反解音符列表
        event_rows (tuple[EventRow, ...]): 反解事件定位表
        stats (dict[str, Any]): 统计信息
    """

    song_name: str
    bpm: float
    beat_unit: int
    notes: tuple[AuditNote, ...]
    event_rows: tuple[EventRow, ...]
    stats: dict[str, Any]


@dataclass(frozen=True)
class MidiScoreData:
    """
    MIDI 读取结果。

    职责：
        聚合 MIDI 音符、tempo 和基础统计，供对齐审查使用。

    属性：
        notes (tuple[AuditNote, ...]): MIDI 音符事件
        summary (dict[str, Any]): MIDI 统计信息
    """

    notes: tuple[AuditNote, ...]
    summary: dict[str, Any]


def midi_pitch_from_octave_and_pitch_class(octave: int, pitch_class: int) -> int:
    """
    将八度与 pitch class 转换为 MIDI pitch。

    :param octave: MIDI 八度编号，C4 对应 MIDI 60
    :param pitch_class: 半音级，允许临时升降导致小于 0 或大于 11
    :return: MIDI pitch
    """
    return 12 * (octave + 1) + pitch_class


def parse_note_token(token: str) -> int:
    """
    将单个游戏简谱 token 反解为 MIDI pitch。

    支持示例：`1`、`-1`、`+1`、`#1`、`-#1`、`+b3`。

    :param token: 单个音符 token，不包含和弦中括号
    :return: MIDI pitch
    :raises ValueError: 当 token 为空、音区前缀非法或音级非法时触发
    """
    raw_token = token.strip()
    if not raw_token:
        raise ValueError("音符 token 不能为空")

    zone_prefix = ""
    note_body = raw_token

    # 1. 音区前缀解析：`-` 是低音区，`+` 是高音区，无前缀是中音区
    if note_body[0] in {"-", "+"}:
        zone_prefix = note_body[0]
        note_body = note_body[1:]

    accidental_offset = 0
    # 2. 临时变音解析：`#` 升半音，`b/B/♭` 降半音
    if note_body.startswith("#"):
        accidental_offset = 1
        note_body = note_body[1:]
    elif note_body.startswith(("b", "B", "♭")):
        accidental_offset = -1
        note_body = note_body[1:]

    if zone_prefix not in OCTAVE_BY_ZONE_PREFIX:
        raise ValueError(f"不支持的音区前缀: {zone_prefix}")
    if note_body not in NATURAL_PITCH_CLASS_BY_TOKEN:
        raise ValueError(f"无法解析音符 token: {token}")

    octave = OCTAVE_BY_ZONE_PREFIX[zone_prefix]
    pitch_class = NATURAL_PITCH_CLASS_BY_TOKEN[note_body] + accidental_offset
    return midi_pitch_from_octave_and_pitch_class(octave=octave, pitch_class=pitch_class)


def split_score_tokens(notes_text: str) -> tuple[str, ...]:
    """
    拆分 YAML score.notes 字段。

    :param notes_text: YAML 中的 notes 字符串
    :return: token 元组，其中 `[1 3 5]` 保持为一个和弦 token
    :raises ValueError: 当 notes_text 为空或无法拆出 token 时触发
    """
    tokens = tuple(match.group(0) for match in TOKEN_PATTERN.finditer(notes_text.strip()))
    if not tokens:
        raise ValueError("notes 字段未解析出任何 token")
    return tokens


def expand_token_to_note_tokens(token: str) -> tuple[str, ...]:
    """
    将单个 token 展开为单音 token 列表。

    :param token: 单音、休止符或中括号和弦 token
    :return: 单音 token 元组；休止符返回空元组
    :raises ValueError: 当和弦为空时触发
    """
    normalized = token.strip()
    if normalized.lower() in {"0", "rest"}:
        return tuple()
    if normalized.startswith("[") and normalized.endswith("]"):
        body = normalized[1:-1].strip()
        if not body:
            raise ValueError("和弦不能为空")
        return tuple(piece for piece in body.split() if piece)
    return (normalized,)


def token_has_flat(note_token: str) -> bool:
    """
    判断单音 token 是否包含降半音标记。

    :param note_token: 单音 token
    :return: True 表示包含 b/B/♭ 降半音
    """
    body = note_token.strip()
    if body.startswith(("-", "+")):
        body = body[1:]
    return body.startswith(("b", "B", "♭"))


def decode_yaml_score(yaml_path: Path) -> DecodedYamlScore:
    """
    将目标 YAML 反解为 36 键 MIDI 音符事件。

    :param yaml_path: 输入 YAML 路径
    :return: 反解结果对象
    :raises FileNotFoundError: 当 YAML 不存在时触发
    :raises ValueError: 当 YAML 结构或 token 非法时触发
    """
    if not yaml_path.is_file():
        raise FileNotFoundError(f"目标 YAML 不存在: {yaml_path}")

    with yaml_path.open("r", encoding="utf-8") as file:
        raw_data = yaml.safe_load(file)
    if not isinstance(raw_data, dict):
        raise ValueError("YAML 顶层结构必须是对象")

    song_data = raw_data.get("song")
    score_data = raw_data.get("score")
    if not isinstance(song_data, dict):
        raise ValueError("YAML 缺少 song 对象")
    if not isinstance(score_data, list):
        raise ValueError("YAML 缺少 score 列表")

    song_name = str(song_data.get("name", yaml_path.stem)).strip()
    bpm = float(song_data.get("bpm"))
    beat_unit = int(song_data.get("beat_unit"))
    if bpm <= 0:
        raise ValueError("song.bpm 必须大于 0")
    if beat_unit <= 0:
        raise ValueError("song.beat_unit 必须大于 0")

    notes: list[AuditNote] = []
    event_rows: list[EventRow] = []
    cursor_beat = 0.0

    beat_counter: Counter[str] = Counter()
    token_counter: Counter[str] = Counter()
    rest_event_count = 0
    note_event_count = 0
    sharp_event_count = 0
    flat_event_count = 0
    mixed_accidental_event_count = 0
    duplicate_token_event_count = 0
    duplicate_pitch_event_count = 0
    max_event_note_count = 0

    for event_index, raw_event in enumerate(score_data):
        if not isinstance(raw_event, dict):
            raise ValueError(f"score 第 {event_index} 项必须是对象")
        raw_notes = str(raw_event.get("notes", "")).strip()
        beat = float(raw_event.get("beat"))
        if beat <= 0:
            raise ValueError(f"score 第 {event_index} 项 beat 必须大于 0")

        beat_counter[f"{beat:g}"] += 1
        for token in split_score_tokens(raw_notes):
            start_beat = cursor_beat
            end_beat = cursor_beat + beat
            note_tokens = expand_token_to_note_tokens(token)

            # 休止符：记录事件定位，但不生成 AuditNote
            if not note_tokens:
                rest_event_count += 1
                event_rows.append(
                    EventRow(
                        event_index=event_index,
                        start_beat=round(start_beat, 6),
                        end_beat=round(end_beat, 6),
                        beat=beat,
                        raw=token,
                        tokens=tuple(),
                        pitches=tuple(),
                        note_count=0,
                        has_sharp=False,
                        has_flat=False,
                        duplicate_token=False,
                        duplicate_pitch=False,
                    )
                )
                cursor_beat = end_beat
                continue

            pitches = tuple(parse_note_token(note_token) for note_token in note_tokens)
            for note_token, pitch in zip(note_tokens, pitches):
                token_counter[note_token] += 1
                notes.append(
                    AuditNote(
                        pitch=pitch,
                        start_beat=start_beat,
                        end_beat=end_beat,
                        velocity=DEFAULT_NOTE_VELOCITY,
                        source_index=event_index,
                        token=note_token,
                    )
                )

            has_sharp = any("#" in note_token for note_token in note_tokens)
            has_flat = any(token_has_flat(note_token) for note_token in note_tokens)
            duplicate_token = len(set(note_tokens)) != len(note_tokens)
            duplicate_pitch = len(set(pitches)) != len(pitches)

            note_event_count += 1
            sharp_event_count += int(has_sharp)
            flat_event_count += int(has_flat)
            mixed_accidental_event_count += int(has_sharp and has_flat)
            duplicate_token_event_count += int(duplicate_token)
            duplicate_pitch_event_count += int(duplicate_pitch)
            max_event_note_count = max(max_event_note_count, len(note_tokens))

            event_rows.append(
                EventRow(
                    event_index=event_index,
                    start_beat=round(start_beat, 6),
                    end_beat=round(end_beat, 6),
                    beat=beat,
                    raw=token,
                    tokens=tuple(note_tokens),
                    pitches=pitches,
                    note_count=len(note_tokens),
                    has_sharp=has_sharp,
                    has_flat=has_flat,
                    duplicate_token=duplicate_token,
                    duplicate_pitch=duplicate_pitch,
                )
            )
            cursor_beat = end_beat

    stats = {
        "event_count": len(score_data),
        "expanded_event_count": len(event_rows),
        "note_count": len(notes),
        "rest_event_count": rest_event_count,
        "note_event_count": note_event_count,
        "sharp_event_count": sharp_event_count,
        "flat_event_count": flat_event_count,
        "mixed_accidental_event_count": mixed_accidental_event_count,
        "duplicate_token_event_count": duplicate_token_event_count,
        "duplicate_pitch_event_count": duplicate_pitch_event_count,
        "max_event_note_count": max_event_note_count,
        "total_beats": round(cursor_beat, 6),
        "beat_values": dict(beat_counter.most_common()),
        "top_tokens": dict(token_counter.most_common(30)),
    }

    return DecodedYamlScore(
        song_name=song_name,
        bpm=bpm,
        beat_unit=beat_unit,
        notes=tuple(notes),
        event_rows=tuple(event_rows),
        stats=stats,
    )


def group_notes_by_onset(notes: tuple[AuditNote, ...], grid_beats: float = ONSET_GROUP_GRID) -> dict[float, list[AuditNote]]:
    """
    按近似起音拍点聚合音符。

    :param notes: 音符事件
    :param grid_beats: 起音聚合网格，默认 1/96 拍
    :return: 起音拍点到音符列表的映射
    """
    grouped: dict[float, list[AuditNote]] = defaultdict(list)
    for note in notes:
        onset_key = round(round(note.start_beat / grid_beats) * grid_beats, 6)
        grouped[onset_key].append(note)
    return dict(sorted(grouped.items()))


def summarize_notes(notes: tuple[AuditNote, ...], bpm: float) -> dict[str, Any]:
    """
    汇总一组谱面音符的基础统计。

    :param notes: 音符事件
    :param bpm: BPM，用于计算秒级时长
    :return: 统计字典
    """
    if not notes:
        return {
            "note_count": 0,
            "onset_count": 0,
            "total_beats": 0.0,
            "total_seconds": 0.0,
            "pitch_min": None,
            "pitch_max": None,
            "pitch_mean": None,
            "max_simultaneous_notes": 0,
            "avg_notes_per_onset": 0.0,
            "onsets_per_beat": 0.0,
            "notes_per_beat": 0.0,
            "onset_interval_top": {},
            "register_distribution": {},
        }

    grouped = group_notes_by_onset(notes)
    chord_sizes = [len(group) for group in grouped.values()]
    onset_positions = sorted(grouped)
    onset_intervals = [round(onset_positions[index + 1] - onset_positions[index], 6) for index in range(len(onset_positions) - 1)]
    interval_counter = Counter(f"{value:g}" for value in onset_intervals)
    pitches = [note.pitch for note in notes]
    total_beats = max(note.end_beat for note in notes)

    register_counter: Counter[str] = Counter()
    for pitch in pitches:
        if pitch < 48:
            register_counter["低于游戏音域"] += 1
        elif pitch <= 59:
            register_counter["低音区C3-B3"] += 1
        elif pitch <= 71:
            register_counter["中音区C4-B4"] += 1
        elif pitch <= 83:
            register_counter["高音区C5-B5"] += 1
        else:
            register_counter["高于游戏音域"] += 1

    return {
        "note_count": len(notes),
        "onset_count": len(grouped),
        "total_beats": round(total_beats, 6),
        "total_seconds": round(total_beats * 60.0 / bpm, 3),
        "pitch_min": min(pitches),
        "pitch_max": max(pitches),
        "pitch_mean": round(sum(pitches) / len(pitches), 3),
        "max_simultaneous_notes": max(chord_sizes),
        "avg_notes_per_onset": round(sum(chord_sizes) / len(chord_sizes), 3),
        "onsets_per_beat": round(len(grouped) / total_beats, 3) if total_beats > 0 else 0.0,
        "notes_per_beat": round(len(notes) / total_beats, 3) if total_beats > 0 else 0.0,
        "onset_interval_top": dict(interval_counter.most_common(12)),
        "register_distribution": dict(register_counter),
    }


def write_notes_to_midi(notes: tuple[AuditNote, ...], bpm: float, output_path: Path) -> None:
    """
    将反解音符写出为真实 MIDI 文件。

    :param notes: 反解音符事件
    :param bpm: MIDI 初始 tempo
    :param output_path: 输出 MIDI 路径
    :raises FileNotFoundError: 当写出后文件不存在时触发
    """
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    instrument = pretty_midi.Instrument(program=0, name="target_reduced_from_yaml")
    seconds_per_beat = 60.0 / bpm

    for note in notes:
        start_seconds = note.start_beat * seconds_per_beat
        end_beat = max(note.end_beat, note.start_beat + MIN_WRITTEN_NOTE_BEATS)
        end_seconds = end_beat * seconds_per_beat
        instrument.notes.append(
            pretty_midi.Note(
                velocity=note.velocity,
                pitch=note.pitch,
                start=start_seconds,
                end=end_seconds,
            )
        )

    midi_data.instruments.append(instrument)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    midi_data.write(str(output_path))
    if not output_path.is_file():
        raise FileNotFoundError(f"YAML 反解 MIDI 未生成: {output_path}")


def read_midi_score(midi_path: Path, fallback_bpm: float) -> MidiScoreData:
    """
    读取真实 MIDI 文件并转换为 AuditNote 事件。

    :param midi_path: MIDI 文件路径
    :param fallback_bpm: MIDI 没有 tempo 时使用的 BPM
    :return: MIDI 读取结果
    :raises FileNotFoundError: 当 MIDI 不存在时触发
    """
    if not midi_path.is_file():
        raise FileNotFoundError(f"MIDI 文件不存在: {midi_path}")

    midi_data = pretty_midi.PrettyMIDI(str(midi_path))
    tempo_values = midi_data.get_tempo_changes()[1]
    bpm = float(tempo_values[0]) if len(tempo_values) > 0 else fallback_bpm
    seconds_per_beat = 60.0 / bpm

    notes: list[AuditNote] = []
    for instrument in midi_data.instruments:
        if instrument.is_drum:
            continue
        for midi_note in instrument.notes:
            notes.append(
                AuditNote(
                    pitch=int(midi_note.pitch),
                    start_beat=midi_note.start / seconds_per_beat,
                    end_beat=midi_note.end / seconds_per_beat,
                    velocity=int(midi_note.velocity),
                    source_index=None,
                    token=None,
                )
            )

    notes.sort(key=lambda note: (note.start_beat, note.pitch, note.end_beat))
    summary = summarize_notes(tuple(notes), bpm=bpm)
    summary["tempo_bpm"] = round(bpm, 3)
    summary["tempo_change_count"] = len(tempo_values)
    return MidiScoreData(notes=tuple(notes), summary=summary)


def build_window_stats(notes: tuple[AuditNote, ...], window_beats: float, total_beats: float) -> list[dict[str, Any]]:
    """
    生成固定拍长窗口统计。

    :param notes: 音符事件
    :param window_beats: 窗口长度，单位拍
    :param total_beats: 统计覆盖的总拍数
    :return: 窗口统计列表
    """
    grouped = group_notes_by_onset(notes)
    window_count = int(math.ceil(total_beats / window_beats)) if total_beats > 0 else 0
    windows: list[dict[str, Any]] = []

    for window_index in range(window_count):
        start_beat = window_index * window_beats
        end_beat = start_beat + window_beats
        window_notes = [note for note in notes if start_beat <= note.start_beat < end_beat]
        window_onsets = [onset for onset in grouped if start_beat <= onset < end_beat]

        if window_notes:
            pitches = [note.pitch for note in window_notes]
            max_simultaneous = max(len(grouped[onset]) for onset in window_onsets) if window_onsets else 0
            windows.append(
                {
                    "index": window_index,
                    "start_beat": round(start_beat, 6),
                    "end_beat": round(end_beat, 6),
                    "note_count": len(window_notes),
                    "onset_count": len(window_onsets),
                    "pitch_min": min(pitches),
                    "pitch_max": max(pitches),
                    "pitch_span": max(pitches) - min(pitches),
                    "max_simultaneous": max_simultaneous,
                    "avg_pitch": round(sum(pitches) / len(pitches), 3),
                }
            )
        else:
            windows.append(
                {
                    "index": window_index,
                    "start_beat": round(start_beat, 6),
                    "end_beat": round(end_beat, 6),
                    "note_count": 0,
                    "onset_count": 0,
                    "pitch_min": None,
                    "pitch_max": None,
                    "pitch_span": 0,
                    "max_simultaneous": 0,
                    "avg_pitch": None,
                }
            )
    return windows


def event_rows_to_dicts(event_rows: tuple[EventRow, ...]) -> list[dict[str, Any]]:
    """
    将 EventRow 元组转换为 JSON 友好的字典列表。

    :param event_rows: 事件定位记录
    :return: 字典列表
    """
    return [asdict(row) for row in event_rows]


def event_dicts_to_rows(event_rows: list[dict[str, Any]] | tuple[EventRow, ...]) -> list[dict[str, Any]]:
    """
    将测试或生产路径中的事件行统一转换为字典。

    :param event_rows: EventRow 元组或字典列表
    :return: 字典列表
    """
    normalized_rows: list[dict[str, Any]] = []
    for row in event_rows:
        if isinstance(row, EventRow):
            normalized_rows.append(asdict(row))
        else:
            normalized_rows.append(dict(row))
    return normalized_rows


def detect_layout_issues(
    reference_notes: list[AuditNote] | tuple[AuditNote, ...],
    target_notes: list[AuditNote] | tuple[AuditNote, ...],
    target_events: list[dict[str, Any]] | tuple[EventRow, ...],
    total_beats: float,
) -> list[dict[str, Any]]:
    """
    检测参考谱与目标缩编谱之间的局部疑似问题。

    :param reference_notes: 参考 MIDI 音符
    :param target_notes: YAML 反解目标音符
    :param target_events: YAML 反解事件定位表
    :param total_beats: 对齐覆盖总拍数
    :return: issue 字典列表
    """
    reference_tuple = tuple(reference_notes)
    target_tuple = tuple(target_notes)
    event_dicts = event_dicts_to_rows(target_events)

    reference_windows = build_window_stats(reference_tuple, TWO_BEAT_WINDOW, total_beats)
    target_windows = build_window_stats(target_tuple, TWO_BEAT_WINDOW, total_beats)

    events_by_window: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in event_dicts:
        if int(row["note_count"]) <= 0:
            continue
        window_index = int(float(row["start_beat"]) // TWO_BEAT_WINDOW)
        events_by_window[window_index].append(row)

    issues: list[dict[str, Any]] = []
    window_limit = min(len(reference_windows), len(target_windows))

    for window_index in range(window_limit):
        reference_window = reference_windows[window_index]
        target_window = target_windows[window_index]
        local_events = events_by_window.get(window_index, [])

        mixed_accidental_events = [row for row in local_events if bool(row["has_sharp"]) and bool(row["has_flat"])]
        dense_events = [row for row in local_events if int(row["note_count"]) >= DENSE_CHORD_NOTE_THRESHOLD]
        duplicate_pitch_events = [row for row in local_events if bool(row["duplicate_pitch"])]
        weak_dense_events = [
            row
            for row in local_events
            if int(row["note_count"]) >= WEAK_BEAT_DENSE_NOTE_THRESHOLD
            and round(float(row["start_beat"]) % TWO_BEAT_WINDOW, 6) not in {0.0, 1.0}
        ]

        reference_pitch_min = reference_window["pitch_min"]
        target_pitch_min = target_window["pitch_min"]
        if reference_pitch_min is not None and target_pitch_min is not None:
            if reference_pitch_min <= LOW_ANCHOR_REFERENCE_PITCH_MAX and target_pitch_min >= TARGET_LOW_ANCHOR_MISSING_PITCH_MIN:
                issues.append(
                    build_issue(
                        issue_type="低音锚点疑似缺失",
                        severity="Major",
                        confidence="medium",
                        window_index=window_index,
                        start_beat=reference_window["start_beat"],
                        evidence=(
                            f"参考窗口最低音 {reference_pitch_min}，目标窗口最低音 {target_pitch_min}，"
                            "低音功能疑似被抬到中音区。"
                        ),
                        events=local_events,
                    )
                )
            if (
                reference_window["pitch_span"] >= WIDE_REFERENCE_SPAN_THRESHOLD
                and target_window["pitch_span"] <= NARROW_TARGET_SPAN_THRESHOLD
                and target_window["note_count"] >= 8
            ):
                issues.append(
                    build_issue(
                        issue_type="音区压缩过窄",
                        severity="Major",
                        confidence="medium",
                        window_index=window_index,
                        start_beat=reference_window["start_beat"],
                        evidence=(
                            f"参考窗口跨度 {reference_window['pitch_span']} 半音，目标窗口跨度 "
                            f"{target_window['pitch_span']} 半音且含 {target_window['note_count']} 个音，疑似声部挤压。"
                        ),
                        events=local_events,
                    )
                )

        if reference_window["onset_count"] >= BLOCKY_REFERENCE_ONSET_MIN and 0 < target_window["onset_count"] <= BLOCKY_TARGET_ONSET_MAX:
            issues.append(
                build_issue(
                    issue_type="节奏织体块状化",
                    severity="Major",
                    confidence="medium",
                    window_index=window_index,
                    start_beat=reference_window["start_beat"],
                    evidence=(
                        f"2拍窗口内参考起音 {reference_window['onset_count']} 个，目标起音 "
                        f"{target_window['onset_count']} 个，快速织体可能被压成块。"
                    ),
                    events=local_events,
                )
            )

        if target_window["onset_count"] >= FRAGMENTED_TARGET_ONSET_MIN and reference_window["onset_count"] <= FRAGMENTED_REFERENCE_ONSET_MAX:
            issues.append(
                build_issue(
                    issue_type="节奏碎片化",
                    severity="Major",
                    confidence="medium",
                    window_index=window_index,
                    start_beat=reference_window["start_beat"],
                    evidence=(
                        f"2拍窗口内参考起音 {reference_window['onset_count']} 个，目标起音 "
                        f"{target_window['onset_count']} 个，目标可能产生过多重复击键。"
                    ),
                    events=local_events,
                )
            )

        if dense_events:
            issues.append(
                build_issue(
                    issue_type="和弦密度过高",
                    severity="Major" if len(dense_events) >= 2 else "Minor",
                    confidence="high",
                    window_index=window_index,
                    start_beat=reference_window["start_beat"],
                    evidence=(
                        f"2拍窗口内存在 {len(dense_events)} 个同按数 >= {DENSE_CHORD_NOTE_THRESHOLD} 的 YAML 事件，"
                        f"最大同按 {max(int(row['note_count']) for row in dense_events)}。"
                    ),
                    events=dense_events,
                )
            )

        if mixed_accidental_events:
            issues.append(
                build_issue(
                    issue_type="输入稳定性风险：同事件混合升降半音",
                    severity="Major",
                    confidence="high",
                    window_index=window_index,
                    start_beat=reference_window["start_beat"],
                    evidence=(
                        f"2拍窗口内存在 {len(mixed_accidental_events)} 个同时包含 # 与 b 的事件，"
                        "播放器需要 Ctrl/Shift 分组发送，可能造成和弦不同步。"
                    ),
                    events=mixed_accidental_events,
                )
            )

        if duplicate_pitch_events:
            issues.append(
                build_issue(
                    issue_type="同拍重复目标音高",
                    severity="Minor",
                    confidence="high",
                    window_index=window_index,
                    start_beat=reference_window["start_beat"],
                    evidence=(
                        f"2拍窗口内存在 {len(duplicate_pitch_events)} 个事件含重复 MIDI pitch，"
                        "说明等音异名或 token 去重不足。"
                    ),
                    events=duplicate_pitch_events,
                )
            )

        if weak_dense_events:
            issues.append(
                build_issue(
                    issue_type="异常重音位置：弱拍高密度和弦",
                    severity="Minor",
                    confidence="medium",
                    window_index=window_index,
                    start_beat=reference_window["start_beat"],
                    evidence=(
                        f"2拍窗口内存在 {len(weak_dense_events)} 个高密度和弦落在非 0/1 拍位置，"
                        "疑似 2/4 网格下的异常重音或错位感来源。"
                    ),
                    events=weak_dense_events,
                )
            )

    severity_order = {"Critical": 0, "Major": 1, "Minor": 2, "Info": 3}
    issues.sort(key=lambda issue: (severity_order[issue["severity"]], issue["start_beat"], issue["type"]))
    return issues


def build_issue(
    issue_type: str,
    severity: str,
    confidence: str,
    window_index: int,
    start_beat: float,
    evidence: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构造统一 issue 记录。

    :param issue_type: 问题类型
    :param severity: 严重等级
    :param confidence: 置信度
    :param window_index: 2 拍窗口索引
    :param start_beat: 窗口起始拍点
    :param evidence: 证据说明
    :param events: 相关 YAML 事件
    :return: issue 字典
    """
    return {
        "type": issue_type,
        "severity": severity,
        "confidence": confidence,
        "bar_2_beat_index": window_index,
        "start_beat": round(float(start_beat), 6),
        "estimated_2_4_bar": window_index + 1,
        "estimated_4_4_bar": int(float(start_beat) // 4.0) + 1,
        "evidence": evidence,
        "events": [int(row["event_index"]) for row in events[:12]],
        "event_details": [
            {
                "event_index": int(row["event_index"]),
                "start_beat": float(row["start_beat"]),
                "raw": str(row["raw"]),
                "note_count": int(row["note_count"]),
                "pitches": list(row["pitches"]),
            }
            for row in events[:5]
        ],
    }


def build_alignment(reference_notes: tuple[AuditNote, ...], target_notes: tuple[AuditNote, ...], total_beats: float) -> list[dict[str, Any]]:
    """
    构建 2 拍窗口对齐摘要。

    :param reference_notes: 参考 MIDI 音符
    :param target_notes: YAML 目标音符
    :param total_beats: 覆盖总拍数
    :return: 对齐摘要列表
    """
    reference_windows = build_window_stats(reference_notes, TWO_BEAT_WINDOW, total_beats)
    target_windows = build_window_stats(target_notes, TWO_BEAT_WINDOW, total_beats)
    alignment: list[dict[str, Any]] = []

    for window_index in range(min(len(reference_windows), len(target_windows))):
        reference_window = reference_windows[window_index]
        target_window = target_windows[window_index]
        alignment.append(
            {
                "bar_2_beat_index": window_index,
                "estimated_2_4_bar": window_index + 1,
                "estimated_4_4_bar": int(reference_window["start_beat"] // 4.0) + 1,
                "start_beat": reference_window["start_beat"],
                "reference_note_count": reference_window["note_count"],
                "target_note_count": target_window["note_count"],
                "reference_onset_count": reference_window["onset_count"],
                "target_onset_count": target_window["onset_count"],
                "reference_pitch_min": reference_window["pitch_min"],
                "target_pitch_min": target_window["pitch_min"],
                "reference_pitch_max": reference_window["pitch_max"],
                "target_pitch_max": target_window["pitch_max"],
                "reference_pitch_span": reference_window["pitch_span"],
                "target_pitch_span": target_window["pitch_span"],
                "target_max_simultaneous": target_window["max_simultaneous"],
            }
        )
    return alignment


def build_meter_diagnostics(event_rows: tuple[EventRow, ...]) -> dict[str, Any]:
    """
    生成 2/4 与 4/4 候选网格下的起音位置统计。

    :param event_rows: YAML 事件定位表
    :return: 拍号候选诊断统计
    """
    result: dict[str, Any] = {}
    note_events = [row for row in event_rows if row.note_count > 0]

    for meter_beats in (2.0, 4.0):
        onset_position_counter: Counter[str] = Counter()
        dense_position_counter: Counter[str] = Counter()
        mixed_accidental_position_counter: Counter[str] = Counter()
        for row in note_events:
            position = round(row.start_beat % meter_beats, 3)
            position_key = f"{position:g}"
            onset_position_counter[position_key] += 1
            if row.note_count >= WEAK_BEAT_DENSE_NOTE_THRESHOLD:
                dense_position_counter[position_key] += 1
            if row.has_sharp and row.has_flat:
                mixed_accidental_position_counter[position_key] += 1
        result[f"{int(meter_beats)}/4_candidate"] = {
            "onset_positions_top": dict(onset_position_counter.most_common(16)),
            "dense_positions_top": dict(dense_position_counter.most_common(16)),
            "mixed_accidental_positions_top": dict(mixed_accidental_position_counter.most_common(16)),
        }
    return result


def compare_total_beats(reference_summary: dict[str, Any], target_total_beats: float) -> float:
    """
    决定对齐统计覆盖的总拍数。

    :param reference_summary: 参考 MIDI 统计
    :param target_total_beats: YAML 目标总拍数
    :return: 对齐覆盖拍数
    """
    reference_total = float(reference_summary.get("total_beats") or 0.0)
    if reference_total <= 0:
        return target_total_beats
    # 使用较短主干附近的覆盖范围，避免参考 MIDI 尾部长残响把审查窗口拉得过长。
    return max(target_total_beats, min(reference_total, target_total_beats * 1.2))


def build_default_targets(project_root: Path) -> tuple[AuditTarget, ...]:
    """
    构建本次用户指定三首曲目的默认审查清单。

    :param project_root: 项目根目录
    :return: 审查目标元组
    """
    return (
        AuditTarget(
            song_id="one_last_kiss",
            display_name="One Last Kiss",
            yaml_path=project_root / "config" / "one_last_kiss_svsep_mpdr_v3.yaml",
            reference_midi_path=(
                project_root
                / "work"
                / "one_last_kiss_svsep_mpdr_v3"
                / "midi"
                / "【Animenz】One Last Kiss - 新·福音战士剧场版：终 钢琴.mid"
            ),
        ),
        AuditTarget(
            song_id="tada_koe_hitotsu",
            display_name="ただ声一つ",
            yaml_path=project_root / "config" / "tada_koe_hitotsu_svsep_mpdr_v3.yaml",
            reference_midi_path=(
                project_root
                / "work"
                / "tada_koe_hitotsu_svsep_mpdr_v3"
                / "midi"
                / "ただ声一つ (只想说一声) - ロクデナシ.mid"
            ),
        ),
        AuditTarget(
            song_id="cruel_angel",
            display_name="残酷天使的行动纲领",
            yaml_path=project_root / "config" / "cruel_angel_svsep_mpdr_v3.yaml",
            reference_midi_path=(
                project_root
                / "work"
                / "cruel_angel_svsep_mpdr_v3"
                / "midi"
                / "【Animenz】残酷天使的行动纲领 – 新世纪福音战士 OP1 钢琴版.mid"
            ),
        ),
    )


def audit_one_target(target: AuditTarget, output_root: Path) -> dict[str, Any]:
    """
    审查单首曲目并生成产物。

    :param target: 审查目标
    :param output_root: 总输出目录
    :return: 单曲摘要
    """
    if not target.yaml_path.is_file():
        raise FileNotFoundError(f"目标 YAML 不存在: {target.yaml_path}")
    if not target.reference_midi_path.is_file():
        raise FileNotFoundError(f"参考 MIDI 不存在: {target.reference_midi_path}")

    output_dir = output_root / target.song_id
    output_dir.mkdir(parents=True, exist_ok=True)

    decoded = decode_yaml_score(target.yaml_path)
    existing_reference_output = output_dir / "existing_reference.mid"
    target_reduced_output = output_dir / "target_reduced.mid"

    shutil.copy2(target.reference_midi_path, existing_reference_output)
    write_notes_to_midi(decoded.notes, bpm=decoded.bpm, output_path=target_reduced_output)

    reference_score = read_midi_score(existing_reference_output, fallback_bpm=decoded.bpm)
    target_score = read_midi_score(target_reduced_output, fallback_bpm=decoded.bpm)

    alignment_total_beats = compare_total_beats(reference_score.summary, decoded.stats["total_beats"])
    alignment = build_alignment(reference_score.notes, target_score.notes, alignment_total_beats)
    issues = detect_layout_issues(
        reference_notes=reference_score.notes,
        target_notes=target_score.notes,
        target_events=decoded.event_rows,
        total_beats=alignment_total_beats,
    )

    summary = {
        "song_id": target.song_id,
        "song_name": target.display_name,
        "yaml_path": str(target.yaml_path),
        "reference_midi_path": str(target.reference_midi_path),
        "existing_reference_output": str(existing_reference_output),
        "target_reduced_output": str(target_reduced_output),
        "yaml_bpm": decoded.bpm,
        "yaml_stats": decoded.stats,
        "reference_summary": reference_score.summary,
        "target_summary": target_score.summary,
        "meter_diagnostics": build_meter_diagnostics(decoded.event_rows),
        "issue_count": len(issues),
    }

    write_json(output_dir / "target_events.json", event_rows_to_dicts(decoded.event_rows))
    write_json(output_dir / "alignment_2beat.json", alignment)
    write_json(output_dir / "issues.json", issues)
    write_json(output_dir / "compare_summary.json", summary)
    write_song_markdown(target=target, output_dir=output_dir, summary=summary, issues=issues)
    return summary


def write_json(path: Path, payload: Any) -> None:
    """
    写出 UTF-8 JSON 文件。

    :param path: 输出路径
    :param payload: 可 JSON 序列化对象
    """
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_song_markdown(target: AuditTarget, output_dir: Path, summary: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    """
    写出单曲 Markdown 初步审查报告。

    :param target: 审查目标
    :param output_dir: 单曲输出目录
    :param summary: 单曲摘要统计
    :param issues: 问题列表
    """
    yaml_stats = summary["yaml_stats"]
    reference_summary = summary["reference_summary"]
    target_summary = summary["target_summary"]

    lines: list[str] = []
    lines.append(f"# {target.display_name} 初步谱面对照审查")
    lines.append("")
    lines.append("> 本报告由目标 YAML 与项目现有 MIDI 只读统计生成；尚未执行 fresh-reference 重新转录，因此涉及原曲绝对一致性的判断仍需二次验证。")
    lines.append("")
    lines.append("## 输入与产物")
    lines.append("")
    lines.append(f"- 目标 YAML: `{target.yaml_path}`")
    lines.append(f"- 现有 MIDI: `{target.reference_midi_path}`")
    lines.append(f"- 参考 MIDI 副本: `{output_dir / 'existing_reference.mid'}`")
    lines.append(f"- YAML 反解 MIDI: `{output_dir / 'target_reduced.mid'}`")
    lines.append("")
    lines.append("## 核心统计")
    lines.append("")
    lines.append(f"- YAML BPM: {summary['yaml_bpm']}")
    lines.append(
        f"- YAML 原始事件数: {yaml_stats['event_count']}，展开事件数: {yaml_stats['expanded_event_count']}，"
        f"音符事件数: {yaml_stats['note_event_count']}，休止事件数: {yaml_stats['rest_event_count']}"
    )
    lines.append(
        f"- YAML 总拍数: {yaml_stats['total_beats']}，目标 MIDI 音符数: {target_summary['note_count']}，"
        f"参考 MIDI 音符数: {reference_summary['note_count']}"
    )
    lines.append(
        f"- 目标最大同按: {target_summary['max_simultaneous_notes']}，"
        f"平均每起音音符数: {target_summary['avg_notes_per_onset']}"
    )
    lines.append(
        f"- 混合升降半音事件数: {yaml_stats['mixed_accidental_event_count']}，"
        f"重复 token 事件数: {yaml_stats['duplicate_token_event_count']}，"
        f"重复 pitch 事件数: {yaml_stats['duplicate_pitch_event_count']}"
    )
    lines.append(f"- 目标音区分布: `{json.dumps(target_summary['register_distribution'], ensure_ascii=False)}`")
    lines.append(f"- 参考音区分布: `{json.dumps(reference_summary['register_distribution'], ensure_ascii=False)}`")
    lines.append("")
    lines.append("## 2/4 与 4/4 网格诊断")
    lines.append("")
    lines.append("当前 YAML 没有拍号字段。这里按候选 2/4 与 4/4 网格观察重音位置：若高密度和弦大量落在 2/4 非 0/1 拍位置，容易造成异常重音或错拍感。")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(summary["meter_diagnostics"], ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 优先检查问题片段")
    lines.append("")

    if not issues:
        lines.append("未发现基于当前规则的高风险片段。")
    else:
        lines.append("| 序号 | 等级 | 2/4小节估计 | 4/4小节估计 | 起始拍 | 类型 | 证据 | YAML事件 |")
        lines.append("|---|---|---:|---:|---:|---|---|---|")
        for index, issue in enumerate(issues[:40], start=1):
            events = ", ".join(str(event_index) for event_index in issue.get("events", []))
            evidence = str(issue["evidence"]).replace("|", "｜")
            lines.append(
                f"| {index} | {issue['severity']} | {issue['estimated_2_4_bar']} | "
                f"{issue['estimated_4_4_bar']} | {issue['start_beat']} | {issue['type']} | {evidence} | {events} |"
            )

    (output_dir / "preliminary_findings.md").write_text("\n".join(lines), encoding="utf-8")


def write_index_markdown(output_root: Path, summaries: list[dict[str, Any]]) -> None:
    """
    写出总索引 Markdown。

    :param output_root: 总输出目录
    :param summaries: 单曲摘要列表
    """
    lines = ["# svsep_mpdr_v3 初步审查索引", ""]
    lines.append("| 曲目 | 输出目录 | YAML事件数 | 目标音符数 | 参考音符数 | 目标最大同按 | 混合升降事件 | 问题数 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for summary in summaries:
        output_dir = Path(summary["existing_reference_output"]).parent
        lines.append(
            f"| {summary['song_name']} | `{output_dir}` | {summary['yaml_stats']['event_count']} | "
            f"{summary['target_summary']['note_count']} | {summary['reference_summary']['note_count']} | "
            f"{summary['target_summary']['max_simultaneous_notes']} | "
            f"{summary['yaml_stats']['mixed_accidental_event_count']} | {summary['issue_count']} |"
        )
    (output_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def run_audit(project_root: Path, output_root: Path) -> list[dict[str, Any]]:
    """
    执行三首曲目的初步审查。

    :param project_root: 项目根目录
    :param output_root: 审查产物输出目录
    :return: 单曲摘要列表
    """
    output_root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for target in build_default_targets(project_root=project_root):
        summaries.append(audit_one_target(target=target, output_root=output_root))
    write_index_markdown(output_root=output_root, summaries=summaries)
    write_json(output_root / "summary_index.json", summaries)
    return summaries


def parse_arguments() -> argparse.Namespace:
    """
    解析命令行参数。

    :return: argparse 命名空间
    """
    parser = argparse.ArgumentParser(description="svsep_mpdr_v3 YAML 与现有 MIDI 初步谱面对照审查工具")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="项目根目录，默认从脚本位置推导为 F:/NTEZMusic",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="审查产物输出目录，默认是脚本所在目录",
    )
    return parser.parse_args()


def main() -> None:
    """
    命令行入口函数。
    """
    arguments = parse_arguments()
    summaries = run_audit(project_root=arguments.project_root, output_root=arguments.output_root)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"审查产物已写入: {arguments.output_root}")


if __name__ == "__main__":
    main()
