"""
模块名称：test_arrangement_benchmark_artifacts
功能描述：
    对完整双歌曲基准产物执行真实回读、约束、哈希与排名一致性验收。

主要组件：
    - test_completed_benchmark_artifacts_are_readable_and_consistent: 验证最终产物

依赖说明：
    - pretty_midi / soundfile: 回读真实 MIDI 与 WAV
    - arrangement_benchmark.constraints: 复核缩编硬约束

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pretty_midi
import soundfile

from arrangement_benchmark.config_loader import load_benchmark_config
from arrangement_benchmark.constraints import validate_reduced_artifacts
from piano_auto_player import PianoConfigLoader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_benchmark_20260711.yaml"


def _sha256_file(file_path: Path) -> str:
    """流式计算验收文件 SHA-256。"""
    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_completed_benchmark_artifacts_are_readable_and_consistent() -> None:
    """
    验证最终排名只引用可回读、满足硬约束且指标可追溯的真实产物。

    :return: 无返回值
    :raises AssertionError: 任一最终产物缺失、损坏、违规或排名不一致时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    ranking_path = config.output_root / "reports" / "ranking.json"
    manifest_path = config.output_root / "manifest.json"
    with ranking_path.open("r", encoding="utf-8") as file:
        ranking_payload = json.load(file)
    with manifest_path.open("r", encoding="utf-8") as file:
        manifest_payload = json.load(file)

    ranking_rows = ranking_payload["ranking"]
    ranked_modes = tuple(row["mode"] for row in ranking_rows)
    assert ranked_modes[0] == "svsep_mpdr"
    assert ranking_payload["failures"] == {
        "octave_fold": {
            "cruel_angel": "候选 octave_fold 违反硬约束: YAML 存在超过 6 音的同时事件"
        }
    }
    assert _sha256_file(config.soundfont.path) == manifest_payload["soundfont"]["sha256"]
    assert set(manifest_payload["artifacts"]) == {song.slug for song in config.songs}
    assert manifest_payload["parameters"]["metric_weights"] == dict(config.metric_weights)
    assert manifest_payload["candidate_modes"] == [candidate.mode for candidate in config.candidates]
    assert manifest_payload["score_aware_mode"] == config.score_aware_mode
    assert manifest_payload["blind_mapping"]["sha256"] == _sha256_file(
        config.output_root / "blind_listening" / "mapping_sealed.json"
    )
    assert manifest_payload["tools"]["ffmpeg"]["sha256"] == _sha256_file(Path(config.tools.ffmpeg))

    for song in config.songs:
        reference_midi_path = config.output_root / song.slug / "reference" / "base_midi" / "song.mid"
        reference_wav_path = config.output_root / song.slug / "reference" / "audio" / "original_grand_piano.wav"
        reference_mp3_path = config.output_root / song.slug / "reference" / "audio" / "original_grand_piano.mp3"
        reference_midi = pretty_midi.PrettyMIDI(str(reference_midi_path))
        assert sum(len(instrument.notes) for instrument in reference_midi.instruments) > 0
        assert soundfile.info(str(reference_wav_path)).frames > 0
        assert reference_mp3_path.is_file() and reference_mp3_path.stat().st_size > 0

        for row in ranking_rows:
            mode = row["mode"]
            candidate_root = config.output_root / song.slug / "algorithms" / mode
            yaml_path = candidate_root / "reduced.yaml"
            midi_path = candidate_root / "reduced.mid"
            wav_path = candidate_root / "reduced_piano.wav"
            mp3_path = candidate_root / "reduced_piano.mp3"
            metrics_path = candidate_root / "metrics.json"
            PianoConfigLoader().load(yaml_path)
            report = validate_reduced_artifacts(
                yaml_path=yaml_path,
                midi_path=midi_path,
                expected_velocity=config.reduced_velocity,
                max_chord_notes=config.common_parameters.max_chord_notes,
            )
            assert report.passed
            assert soundfile.info(str(wav_path)).frames > 0
            assert mp3_path.is_file() and mp3_path.stat().st_size > 0
            with metrics_path.open("r", encoding="utf-8") as file:
                metrics_payload = json.load(file)
            assert set(metrics_payload["metrics"]) == set(config.metric_weights)
            assert math.isclose(
                metrics_payload["score"],
                row["per_song_scores"][song.slug],
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            artifact_entry = manifest_payload["artifacts"][song.slug]["candidates"][mode]
            for artifact_name, artifact_path in {
                "yaml": yaml_path,
                "midi": midi_path,
                "wav": wav_path,
                "mp3": mp3_path,
                "metrics": metrics_path,
            }.items():
                assert artifact_entry[artifact_name]["sha256"] == _sha256_file(artifact_path)

    for row in ranking_rows:
        expected_average = sum(row["per_song_scores"].values()) / len(config.songs)
        assert math.isclose(row["average_score"], expected_average, rel_tol=0.0, abs_tol=1e-12)

    blind_root = config.output_root / "blind_listening"
    for song in config.songs:
        assert (blind_root / f"{song.slug}_A.mp3").is_file()
        assert (blind_root / f"{song.slug}_B.mp3").is_file()
