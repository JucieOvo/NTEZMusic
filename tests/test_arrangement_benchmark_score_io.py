"""
模块名称：test_arrangement_benchmark_score_io
功能描述：
    使用项目已有真实 YAML 验证36键 MIDI 的严格反解和回读一致性。

主要组件：
    - test_real_yaml_converts_to_constrained_midi: 验证真实 YAML 反解结果

依赖说明：
    - pretty_midi: 回读真实 MIDI 文件
    - arrangement_benchmark.score_io: 被测严格反解模块

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path

import pretty_midi

from arrangement_benchmark.score_io import convert_yaml_to_reduced_midi


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_YAML_PATH = PROJECT_ROOT / "config" / "latest_cruel_angel_thesis.yaml"


def test_real_yaml_converts_to_constrained_midi(tmp_path: Path) -> None:
    """
    验证真实缩编 YAML 可反解为统一力度、无踏板、36键范围内的 MIDI。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 当任一异环硬约束或回读一致性不满足时触发
    """
    output_midi_path = tmp_path / "cruel_angel_reduced.mid"
    result = convert_yaml_to_reduced_midi(
        yaml_path=REAL_YAML_PATH,
        output_midi_path=output_midi_path,
        velocity=80,
    )

    assert output_midi_path.is_file()
    assert result.note_count > 0
    assert result.max_simultaneous_notes <= 6
    assert result.min_pitch >= 48
    assert result.max_pitch <= 83

    rendered_midi = pretty_midi.PrettyMIDI(str(output_midi_path))
    rendered_notes = tuple(note for instrument in rendered_midi.instruments for note in instrument.notes)
    rendered_controls = tuple(
        control
        for instrument in rendered_midi.instruments
        for control in instrument.control_changes
    )

    assert len(rendered_notes) == result.note_count
    assert {note.velocity for note in rendered_notes} == {80}
    assert all(48 <= note.pitch <= 83 for note in rendered_notes)
    assert all(note.end > note.start for note in rendered_notes)
    assert rendered_controls == ()
