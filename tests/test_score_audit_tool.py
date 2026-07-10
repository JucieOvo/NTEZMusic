"""
模块名称：test_score_audit_tool
功能描述：
    验证 score_audit_tool 审查脚本的核心谱面反解与问题检测逻辑。

主要组件：
    - test_parse_note_token_maps_game_notation_to_midi_pitch: 验证 36 键 token 到 MIDI 音高的映射
    - test_decode_yaml_score_accumulates_beats_and_detects_duplicate_pitch: 验证 YAML 事件反解、拍点累积与重复音高检测
    - test_detect_layout_issues_flags_mixed_accidentals_and_weak_dense_chords: 验证混合升降半音与弱拍高密度和弦问题检测

依赖说明：
    - pytest: 测试框架
    - PyYAML: 构造真实 YAML 文件
    - pretty_midi: 审查脚本依赖的真实 MIDI 库

作者：JucieOvo
创建日期：2026-07-01
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCORE_AUDIT_DIR = PROJECT_ROOT / "work" / "score_audit"
if str(SCORE_AUDIT_DIR) not in sys.path:
    sys.path.insert(0, str(SCORE_AUDIT_DIR))

from score_audit_tool import (  # noqa: E402
    AuditNote,
    decode_yaml_score,
    detect_layout_issues,
    parse_note_token,
)


def test_parse_note_token_maps_game_notation_to_midi_pitch() -> None:
    """
    验证游戏 36 键 token 能映射到预期 MIDI 音高。

    该测试覆盖低音、中音、高音和升降半音，确保后续 YAML 反解 MIDI 的音高基础正确。
    """
    assert parse_note_token("1") == 60
    assert parse_note_token("-1") == 48
    assert parse_note_token("+1") == 72
    assert parse_note_token("-#1") == 49
    assert parse_note_token("+#5") == 80
    assert parse_note_token("+b3") == 75


def test_decode_yaml_score_accumulates_beats_and_detects_duplicate_pitch(tmp_path: Path) -> None:
    """
    验证 YAML 反解会累积拍点，并识别同一和弦中等音异名造成的重复目标音高。
    """
    yaml_path = tmp_path / "sample.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "song": {"name": "测试曲", "bpm": 120.0, "beat_unit": 4},
                "playback": {"start_delay_seconds": 0.0, "key_press_seconds": 0.0},
                "keyboard": {
                    "high": {str(i): key for i, key in enumerate("qwertyu", start=1)},
                    "middle": {str(i): key for i, key in enumerate("asdfghj", start=1)},
                    "low": {str(i): key for i, key in enumerate("zxcvbnm", start=1)},
                },
                "score": [
                    {"notes": "[#1 b2]", "beat": 0.25},
                    {"notes": "0", "beat": 0.5},
                    {"notes": "+5", "beat": 0.25},
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    decoded = decode_yaml_score(yaml_path=yaml_path)

    assert decoded.bpm == 120.0
    assert decoded.stats["total_beats"] == 1.0
    assert decoded.stats["note_count"] == 3
    assert decoded.stats["duplicate_pitch_event_count"] == 1
    assert decoded.event_rows[0].start_beat == 0.0
    assert decoded.event_rows[1].start_beat == 0.25
    assert decoded.event_rows[2].start_beat == 0.75
    assert [note.pitch for note in decoded.notes] == [61, 61, 79]


def test_detect_layout_issues_flags_mixed_accidentals_and_weak_dense_chords() -> None:
    """
    验证审查器能识别两类高风险现象：
    1. 同一事件混合升半音和降半音，可能触发 Ctrl/Shift 分组输入风险。
    2. 2/4 网格下非强拍位置出现高密度和弦，可能形成异常重音。
    """
    reference_notes = [
        AuditNote(pitch=48, start_beat=0.0, end_beat=0.5, velocity=80, source_index=None, token=None),
        AuditNote(pitch=72, start_beat=1.0, end_beat=1.5, velocity=80, source_index=None, token=None),
    ]
    target_notes = [
        AuditNote(pitch=61, start_beat=0.5, end_beat=0.75, velocity=80, source_index=0, token="#1"),
        AuditNote(pitch=63, start_beat=0.5, end_beat=0.75, velocity=80, source_index=0, token="b3"),
        AuditNote(pitch=67, start_beat=0.5, end_beat=0.75, velocity=80, source_index=0, token="5"),
        AuditNote(pitch=71, start_beat=0.5, end_beat=0.75, velocity=80, source_index=0, token="7"),
    ]
    event_rows = [
        {
            "event_index": 0,
            "start_beat": 0.5,
            "end_beat": 0.75,
            "beat": 0.25,
            "raw": "[#1 b3 5 7]",
            "tokens": ["#1", "b3", "5", "7"],
            "pitches": [61, 63, 67, 71],
            "note_count": 4,
            "has_sharp": True,
            "has_flat": True,
            "duplicate_token": False,
            "duplicate_pitch": False,
        }
    ]

    issues = detect_layout_issues(
        reference_notes=reference_notes,
        target_notes=target_notes,
        target_events=event_rows,
        total_beats=2.0,
    )

    issue_types = {issue["type"] for issue in issues}
    assert "输入稳定性风险：同事件混合升降半音" in issue_types
    assert "异常重音位置：弱拍高密度和弦" in issue_types
