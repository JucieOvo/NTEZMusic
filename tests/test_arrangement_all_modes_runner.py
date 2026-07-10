"""
模块名称：test_arrangement_all_modes_runner
功能描述：
    使用正式五歌曲配置验证真实环境预检与候选模式边界。

主要组件：
    - test_all_modes_environment_preflight_passes: 验证正式批处理环境
    - test_removed_modes_cannot_enter_candidate_pipeline: 验证淘汰模式不可执行

依赖说明：
    - arrangement_benchmark.config_loader: 加载正式五歌曲配置
    - arrangement_benchmark.runner: 执行预检和候选入口校验

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.arrangement_benchmark.config_loader import load_benchmark_config
from src.arrangement_benchmark.runner import BenchmarkRunner


CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_all_modes_20260711.yaml"
REAL_REFERENCE_MIDI = PROJECT_ROOT / "work" / "score_audit" / "cruel_angel" / "existing_reference.mid"


def test_all_modes_environment_preflight_passes() -> None:
    """
    验证五首真实音频、CUDA、模型、SoundFont 与外部工具全部可用。

    :return: 无返回值
    :raises AssertionError: 任一正式运行依赖不可用时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    report = BenchmarkRunner(config).preflight()

    assert report.passed
    assert report.failures == ()
    assert report.tool_versions_path.is_file()
    assert report.requirements_frozen_path.is_file()


@pytest.mark.parametrize("removed_mode", ("score_aware_theory", "octave_fold"))
def test_removed_modes_cannot_enter_candidate_pipeline(tmp_path: Path, removed_mode: str) -> None:
    """
    验证用户淘汰的两种模式在候选入口处直接报错，不产生伪成功产物。

    :param tmp_path: pytest 提供的真实临时目录
    :param removed_mode: 必须被正式配置拒绝的模式名
    :return: 无返回值
    :raises AssertionError: 淘汰模式仍可执行或产生文件时触发
    """
    config = load_benchmark_config(CONFIG_PATH)
    candidate_root = tmp_path / removed_mode

    with pytest.raises(ValueError, match="不在基准候选集合中的算法"):
        BenchmarkRunner(config).run_candidate_from_midi(
            song=config.songs[0],
            bpm=120.0,
            reference_midi_path=REAL_REFERENCE_MIDI,
            mode=removed_mode,
            candidate_root=candidate_root,
        )

    assert not candidate_root.exists()


def test_generated_report_uses_five_song_conclusion_scope() -> None:
    """
    验证多歌曲报告的结论范围来自正式配置，不残留双歌曲硬编码文案。

    :return: 无返回值
    :raises AssertionError: 报告仍错误描述为两首歌曲时触发
    """
    report_path = PROJECT_ROOT / "work" / "arrangement_all_modes_20260711" / "reports" / "final_report.md"
    report_text = report_path.read_text(encoding="utf-8")

    assert "结论范围：仅限本基准5首歌曲" in report_text
    assert "两首歌曲" not in report_text
