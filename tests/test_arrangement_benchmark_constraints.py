"""
模块名称：test_arrangement_benchmark_constraints
功能描述：
    使用真实缩编 YAML 与其真实反解 MIDI 验证异环硬约束报告。

主要组件：
    - test_real_reduced_score_passes_hard_constraints: 验证真实产物约束报告

依赖说明：
    - arrangement_benchmark.constraints: 被测硬约束校验器
    - arrangement_benchmark.score_io: 生成真实反解 MIDI

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path

from arrangement_benchmark.constraints import validate_reduced_artifacts
from arrangement_benchmark.score_io import convert_yaml_to_reduced_midi


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_YAML_PATH = PROJECT_ROOT / "config" / "latest_cruel_angel_thesis.yaml"


def test_real_reduced_score_passes_hard_constraints(tmp_path: Path) -> None:
    """
    验证真实残酷天使缩编谱满足基准实验全部 MIDI/YAML 硬约束。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 当真实产物违反任一硬约束时触发
    """
    output_midi_path = tmp_path / "cruel_angel_reduced.mid"
    convert_yaml_to_reduced_midi(
        yaml_path=REAL_YAML_PATH,
        output_midi_path=output_midi_path,
        velocity=80,
    )

    report = validate_reduced_artifacts(
        yaml_path=REAL_YAML_PATH,
        midi_path=output_midi_path,
        expected_velocity=80,
        max_chord_notes=6,
    )

    assert report.passed
    assert report.violations == ()
    assert report.score_event_count > 0
    assert report.midi_note_count > 0
