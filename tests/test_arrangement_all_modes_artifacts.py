"""
模块名称：test_arrangement_all_modes_artifacts
功能描述：
    回读五歌曲四模式正式批次，验证完整产物网格、硬约束、MIDI 音域与 manifest 哈希。

主要组件：
    - test_all_twenty_candidate_artifacts_are_complete_and_valid: 验证20组候选闭环
    - test_manifest_artifact_hashes_match_real_files: 验证清单记录与真实文件一致

依赖说明：
    - pretty_midi: 回读真实缩编 MIDI 音符
    - hashlib: 复算正式产物 SHA-256

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pretty_midi


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "work" / "arrangement_all_modes_20260711"
SONG_SLUGS = (
    "one_last_kiss_animenz",
    "mousou_aika_remix",
    "mihang_familiar_world",
    "shui",
    "beautiful",
)
CANDIDATE_MODES = (
    "adaptive_octave_fold",
    "hands_decoupled",
    "attention_weighted",
    "svsep_mpdr",
)


def _sha256_file(file_path: Path) -> str:
    """
    分块计算真实产物 SHA-256，避免一次性将大型 WAV 读入内存。

    :param file_path: 待校验的真实文件路径
    :return: 小写十六进制 SHA-256
    """
    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_manifest_artifacts(node: Any) -> list[tuple[Path, str]]:
    """
    递归收集 manifest 中同时具备 path 与 sha256 的真实产物记录。

    :param node: 当前 manifest JSON 节点
    :return: 真实路径与预期哈希列表
    """
    if isinstance(node, dict):
        if set(("path", "sha256")).issubset(node):
            return [(Path(str(node["path"])), str(node["sha256"]))]
        artifacts: list[tuple[Path, str]] = []
        for value in node.values():
            artifacts.extend(_collect_manifest_artifacts(value))
        return artifacts
    if isinstance(node, list):
        artifacts = []
        for value in node:
            artifacts.extend(_collect_manifest_artifacts(value))
        return artifacts
    return []


def test_all_twenty_candidate_artifacts_are_complete_and_valid() -> None:
    """
    验证5首参考音频与20组候选的 YAML、MIDI、WAV、MP3、指标和硬约束报告。

    :return: 无返回值
    :raises AssertionError: 任一产物缺失、为空、超音域或违反硬约束时触发
    """
    candidate_count = 0
    for song_slug in SONG_SLUGS:
        reference_root = OUTPUT_ROOT / song_slug / "reference"
        reference_paths = (
            reference_root / "base_midi" / "song.mid",
            reference_root / "audio" / "original_grand_piano.wav",
            reference_root / "audio" / "original_grand_piano.mp3",
        )
        assert all(path.is_file() and path.stat().st_size > 0 for path in reference_paths)

        for mode in CANDIDATE_MODES:
            candidate_count += 1
            candidate_root = OUTPUT_ROOT / song_slug / "algorithms" / mode
            required_paths = (
                candidate_root / "reduced.yaml",
                candidate_root / "reduced.mid",
                candidate_root / "reduced_piano.wav",
                candidate_root / "reduced_piano.mp3",
                candidate_root / "metrics.json",
                candidate_root / "constraint_report.json",
            )
            assert all(path.is_file() and path.stat().st_size > 0 for path in required_paths)

            constraint_payload = json.loads((candidate_root / "constraint_report.json").read_text(encoding="utf-8"))
            assert constraint_payload["passed"] is True
            assert constraint_payload["violations"] == []

            reduced_midi = pretty_midi.PrettyMIDI(str(candidate_root / "reduced.mid"))
            pitches = [note.pitch for instrument in reduced_midi.instruments for note in instrument.notes]
            assert pitches
            assert min(pitches) >= 48
            assert max(pitches) <= 83

    assert candidate_count == 20


def test_manifest_artifact_hashes_match_real_files() -> None:
    """
    验证 manifest 无失败记录，且其全部产物哈希与磁盘真实内容一致。

    :return: 无返回值
    :raises AssertionError: 清单缺项、存在失败或文件内容漂移时触发
    """
    manifest_path = OUTPUT_ROOT / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_payload["failures"] == {}
    assert tuple(manifest_payload["candidate_modes"]) == CANDIDATE_MODES
    assert manifest_payload["score_aware_mode"] is None
    assert set(manifest_payload["source_songs"]) == set(SONG_SLUGS)

    artifact_records = _collect_manifest_artifacts(manifest_payload["artifacts"])
    assert len(artifact_records) == 115
    for artifact_path, expected_sha256 in artifact_records:
        assert artifact_path.is_file()
        assert _sha256_file(artifact_path) == expected_sha256
