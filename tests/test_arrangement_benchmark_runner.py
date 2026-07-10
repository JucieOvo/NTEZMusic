"""
模块名称：test_arrangement_benchmark_runner
功能描述：
    使用真实残酷天使 MIDI 验证单候选编排、YAML、反解 MIDI 与约束报告闭环。

主要组件：
    - test_real_candidate_run_produces_valid_artifacts: 验证真实候选执行

依赖说明：
    - arrangement_benchmark.runner: 被测实验编排器
    - arrangement_benchmark.config_loader: 加载正式配置

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path

from arrangement_benchmark.config_loader import load_benchmark_config
from arrangement_benchmark.runner import BenchmarkRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_benchmark_20260711.yaml"
REAL_REFERENCE_MIDI = PROJECT_ROOT / "work" / "score_audit" / "cruel_angel" / "existing_reference.mid"


def test_real_candidate_run_produces_valid_artifacts(tmp_path: Path) -> None:
    """
    使用同一真实 MIDI 执行自适应八度折叠并验证候选产物闭环。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 候选产物缺失或违反硬约束时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    runner = BenchmarkRunner(config)
    result = runner.run_candidate_from_midi(
        song=config.songs[0],
        bpm=129.2,
        reference_midi_path=REAL_REFERENCE_MIDI,
        mode="adaptive_octave_fold",
        candidate_root=tmp_path / "adaptive_octave_fold",
    )

    assert result.mode == "adaptive_octave_fold"
    assert result.yaml_path.is_file()
    assert result.reduced_midi_path.is_file()
    assert result.conversion_report_path.is_file()
    assert result.constraint_report_path.is_file()
    assert result.constraint_report.passed
    assert result.constraint_report.violations == ()
    assert result.conversion_stats["raw_note_count"] > 0
    assert result.conversion_stats["output_note_count"] > 0


def test_real_environment_preflight_passes() -> None:
    """
    验证正式实验所需的真实文件、GPU、Python 包和外部工具全部可用。

    :return: 无返回值
    :raises AssertionError: 任一真实依赖未通过预检时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    report = BenchmarkRunner(config).preflight()

    assert report.passed
    assert report.failures == ()
    assert report.tool_versions_path.is_file()
    assert report.requirements_frozen_path.is_file()


def test_full_real_benchmark_generates_ranked_audio_artifacts() -> None:
    """
    运行两首真实歌曲完整基准，验证排名、原始/缩编音频和盲听材料全部生成。

    该测试是长时真实集成测试，不使用 Mock、Stub、跳过标记或历史 YAML 代替本轮结果。

    :return: 无返回值
    :raises AssertionError: 完整真实实验缺少任一规定产物时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    result = BenchmarkRunner(config).run_full_benchmark()

    assert result.manifest_path.is_file()
    assert result.ranking_path.is_file()
    assert result.final_report_path.is_file()
    assert result.blind_mapping_path.is_file()
    assert len(result.ranked_modes) >= 2
    assert "svsep_mpdr" in result.ranked_modes
    for song in config.songs:
        song_root = config.output_root / song.slug
        assert (song_root / "reference" / "base_midi" / "song.mid").is_file()
        assert (song_root / "reference" / "audio" / "original_grand_piano.mp3").is_file()
        for mode in result.ranked_modes:
            assert (song_root / "algorithms" / mode / "reduced.mid").is_file()
            assert (song_root / "algorithms" / mode / "reduced_piano.mp3").is_file()
