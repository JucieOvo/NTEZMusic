"""
模块名称：arrangement_benchmark.constraints
功能描述：
    校验真实缩编 YAML 与反解 MIDI 是否满足异环36键硬约束。

主要组件：
    - ConstraintReport: 结构化硬约束报告
    - validate_reduced_artifacts: 校验 YAML/MIDI 真实产物

依赖说明：
    - pretty_midi: 回读 MIDI 音符和控制事件
    - arrangement_benchmark.score_io: 复用严格 YAML 解析结果

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pretty_midi

from arrangement_benchmark.score_io import parse_yaml_score


@dataclass(frozen=True)
class ConstraintReport:
    """缩编产物硬约束校验报告。"""

    passed: bool
    violations: tuple[str, ...]
    score_event_count: int
    midi_note_count: int


def validate_reduced_artifacts(
    yaml_path: Path,
    midi_path: Path,
    expected_velocity: int,
    max_chord_notes: int,
) -> ConstraintReport:
    """
    校验 YAML 和缩编 MIDI 的全部可程序化硬约束。

    :param yaml_path: 候选算法输出 YAML
    :param midi_path: YAML 真实反解 MIDI
    :param expected_velocity: 集中配置规定的统一力度
    :param max_chord_notes: 异环单事件最大音数
    :return: 包含所有真实违规证据的报告
    :raises FileNotFoundError: 任一产物不存在时触发
    """
    if not yaml_path.is_file():
        raise FileNotFoundError(f"YAML 产物不存在: {yaml_path}")
    if not midi_path.is_file():
        raise FileNotFoundError(f"MIDI 产物不存在: {midi_path}")

    parsed_score = parse_yaml_score(yaml_path)
    midi_data = pretty_midi.PrettyMIDI(str(midi_path))
    midi_notes = tuple(note for instrument in midi_data.instruments for note in instrument.notes)
    violations: list[str] = []

    if not midi_notes:
        violations.append("缩编 MIDI 不包含任何音符")
    if any(len(event.pitches) > max_chord_notes for event in parsed_score.events):
        violations.append(f"YAML 存在超过 {max_chord_notes} 音的同时事件")
    if any(len(set(event.pitches)) != len(event.pitches) for event in parsed_score.events):
        violations.append("YAML 存在重复 pitch/token")
    if any(not 48 <= note.pitch <= 83 for note in midi_notes):
        violations.append("缩编 MIDI 存在 C3-B5 之外的音高")
    if any(note.velocity != expected_velocity for note in midi_notes):
        violations.append("缩编 MIDI velocity 未统一为实验配置值")
    if any(control.number == 64 for instrument in midi_data.instruments for control in instrument.control_changes):
        violations.append("缩编 MIDI 包含 pedal/sustain 控制事件")
    if any(note.end <= note.start for note in midi_notes):
        violations.append("缩编 MIDI 存在非正时值音符")

    return ConstraintReport(
        passed=not violations,
        violations=tuple(violations),
        score_event_count=len(parsed_score.events),
        midi_note_count=len(midi_notes),
    )
