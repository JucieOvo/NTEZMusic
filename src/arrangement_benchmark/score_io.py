"""
模块名称：arrangement_benchmark.score_io
功能描述：
    严格读取项目真实 YAML 曲谱，并反解为符合异环限制的 pretty_midi 文件。

主要组件：
    - ParsedScoreEvent: 单个连续曲谱事件
    - ParsedYamlScore: 完整解析结果
    - ReducedMidiBuildResult: 反解 MIDI 统计结果
    - parse_yaml_score: 严格解析真实 YAML
    - convert_yaml_to_reduced_midi: 写出统一力度、无踏板的36键 MIDI

依赖说明：
    - piano_auto_player.PianoConfigLoader: 复用项目正式 YAML 结构校验
    - pretty_midi: 构建和回读 MIDI

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re

import pretty_midi

from piano_auto_player import PianoConfigLoader


# 自然音级到十二平均律 pitch class 的唯一映射。
NATURAL_PITCH_CLASS_BY_TOKEN = {
    "1": 0,
    "2": 2,
    "3": 4,
    "4": 5,
    "5": 7,
    "6": 9,
    "7": 11,
}

# 游戏三个音区分别对应 C3、C4、C5 起始音高。
ZONE_BASE_PITCH = {"-": 48, "": 60, "+": 72}

# 与播放器相同：中括号和弦视为一个事件，其余空白分隔 token 依次执行。
TOKEN_PATTERN = re.compile(r"\[[^\]]+\]|\S+")


@dataclass(frozen=True)
class ParsedScoreEvent:
    """单个连续曲谱事件。"""

    pitches: tuple[int, ...]
    beat: float
    source_token: str


@dataclass(frozen=True)
class ParsedYamlScore:
    """完整 YAML 曲谱的严格解析结果。"""

    song_name: str
    bpm: float
    beat_unit: int
    events: tuple[ParsedScoreEvent, ...]


@dataclass(frozen=True)
class ReducedMidiBuildResult:
    """36键 MIDI 反解结果与真实统计。"""

    output_midi_path: Path
    note_count: int
    event_count: int
    max_simultaneous_notes: int
    min_pitch: int
    max_pitch: int
    duration_seconds: float


def _parse_single_note(note_token: str) -> int:
    """
    将单个游戏简谱 token 严格转换为 MIDI pitch。

    :param note_token: 例如 `1`、`-b3`、`+#4`
    :return: MIDI pitch
    :raises ValueError: token 结构非法或音高超出 C3-B5 时触发
    """
    normalized_token = note_token.strip()
    if not normalized_token:
        raise ValueError("音符 token 不能为空")

    zone_prefix = ""
    note_body = normalized_token
    if note_body[0] in {"-", "+"}:
        zone_prefix = note_body[0]
        note_body = note_body[1:]
    if not note_body:
        raise ValueError(f"音符 token 缺少音级: {note_token}")

    accidental_offset = 0
    if note_body.startswith("#"):
        accidental_offset = 1
        note_body = note_body[1:]
    elif note_body.startswith(("b", "B", "♭")):
        accidental_offset = -1
        note_body = note_body[1:]

    if note_body not in NATURAL_PITCH_CLASS_BY_TOKEN:
        raise ValueError(f"不支持的音符 token: {note_token}")
    pitch = ZONE_BASE_PITCH[zone_prefix] + NATURAL_PITCH_CLASS_BY_TOKEN[note_body] + accidental_offset
    if not 48 <= pitch <= 83:
        raise ValueError(f"音符 token 超出异环 C3-B5 音域: {note_token} -> {pitch}")
    return pitch


def _parse_event_token(token: str) -> tuple[int, ...]:
    """将休止符、单音或和弦 token 解析为同时发音 pitch 元组。"""
    normalized_token = token.strip()
    if normalized_token.lower() in {"0", "rest"}:
        return tuple()
    if normalized_token.startswith("[") and normalized_token.endswith("]"):
        chord_body = normalized_token[1:-1].strip()
        if not chord_body:
            raise ValueError("和弦 token 不能为空")
        pitches = tuple(_parse_single_note(note_token) for note_token in chord_body.split())
    else:
        pitches = (_parse_single_note(normalized_token),)
    if len(set(pitches)) != len(pitches):
        raise ValueError(f"同一事件包含重复 pitch: {normalized_token}")
    return pitches


def parse_yaml_score(yaml_path: Path) -> ParsedYamlScore:
    """
    使用项目正式加载器和严格音高规则解析 YAML 曲谱。

    :param yaml_path: 真实 YAML 曲谱路径
    :return: 连续事件解析结果
    :raises FileNotFoundError: YAML 不存在时触发
    :raises ValueError: YAML 结构、beat 或 token 非法时触发
    """
    piano_config = PianoConfigLoader().load(yaml_path)
    events: list[ParsedScoreEvent] = []
    for raw_event in piano_config.raw_score:
        if not math.isfinite(raw_event.beat) or raw_event.beat <= 0:
            raise ValueError(f"score 事件 beat 必须是有限正数: {raw_event.beat}")
        tokens = tuple(match.group(0) for match in TOKEN_PATTERN.finditer(raw_event.notes.strip()))
        if not tokens:
            raise ValueError("score.notes 未解析出任何 token")
        for token in tokens:
            events.append(
                ParsedScoreEvent(
                    pitches=_parse_event_token(token),
                    beat=float(raw_event.beat),
                    source_token=token,
                )
            )
    if not events:
        raise ValueError(f"YAML 曲谱没有可执行事件: {yaml_path}")
    return ParsedYamlScore(
        song_name=piano_config.song.name,
        bpm=float(piano_config.song.bpm),
        beat_unit=piano_config.song.beat_unit,
        events=tuple(events),
    )


def convert_yaml_to_reduced_midi(
    yaml_path: Path,
    output_midi_path: Path,
    velocity: int,
) -> ReducedMidiBuildResult:
    """
    将真实 YAML 写出为统一力度、无踏板、36键范围的 MIDI。

    每个 token 事件持续时间由其 `beat` 和歌曲 BPM 换算，休止符只推进时间。

    :param yaml_path: 输入 YAML 路径
    :param output_midi_path: 输出 MIDI 路径
    :param velocity: 全曲统一 MIDI velocity，范围 1-127
    :return: 写出并回读验证后的真实统计
    :raises ValueError: velocity 非法、曲谱为空或回读不一致时触发
    """
    if not isinstance(velocity, int) or isinstance(velocity, bool) or not 1 <= velocity <= 127:
        raise ValueError("velocity 必须是 1-127 的整数")
    parsed_score = parse_yaml_score(yaml_path)
    seconds_per_beat = 60.0 / parsed_score.bpm
    midi_data = pretty_midi.PrettyMIDI(initial_tempo=parsed_score.bpm)
    instrument = pretty_midi.Instrument(program=0, is_drum=False, name="NTEZ 36-Key Piano")

    absolute_seconds = 0.0
    note_count = 0
    max_simultaneous_notes = 0
    all_pitches: list[int] = []
    for event in parsed_score.events:
        duration_seconds = event.beat * seconds_per_beat
        event_end_seconds = absolute_seconds + duration_seconds
        for pitch in event.pitches:
            instrument.notes.append(
                pretty_midi.Note(
                    velocity=velocity,
                    pitch=pitch,
                    start=absolute_seconds,
                    end=event_end_seconds,
                )
            )
            all_pitches.append(pitch)
            note_count += 1
        max_simultaneous_notes = max(max_simultaneous_notes, len(event.pitches))
        absolute_seconds = event_end_seconds

    if note_count == 0 or not all_pitches:
        raise ValueError(f"YAML 曲谱没有可写出的音符: {yaml_path}")
    midi_data.instruments.append(instrument)
    output_midi_path.parent.mkdir(parents=True, exist_ok=True)
    midi_data.write(str(output_midi_path))

    # 回读是正式产物门槛：写出成功但结构变化同样视为失败。
    reloaded_midi = pretty_midi.PrettyMIDI(str(output_midi_path))
    reloaded_notes = tuple(note for item in reloaded_midi.instruments for note in item.notes)
    if len(reloaded_notes) != note_count:
        raise RuntimeError(f"MIDI 回读音符数不一致: 写出 {note_count}, 回读 {len(reloaded_notes)}")
    if any(item.control_changes for item in reloaded_midi.instruments):
        raise RuntimeError("缩编 MIDI 回读后出现未授权控制事件")

    return ReducedMidiBuildResult(
        output_midi_path=output_midi_path,
        note_count=note_count,
        event_count=len(parsed_score.events),
        max_simultaneous_notes=max_simultaneous_notes,
        min_pitch=min(all_pitches),
        max_pitch=max(all_pitches),
        duration_seconds=absolute_seconds,
    )
