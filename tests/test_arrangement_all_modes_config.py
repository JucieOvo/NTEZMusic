"""
模块名称：test_arrangement_all_modes_config
功能描述：
    使用正式五歌曲配置验证任意歌曲数与禁用 score-aware 的批处理能力。

主要组件：
    - test_load_five_song_four_mode_config: 验证正式批处理配置

依赖说明：
    - arrangement_benchmark.config_loader: 被测严格配置加载器

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.arrangement_benchmark.config_loader import load_benchmark_config


CONFIG_PATH = PROJECT_ROOT / "config" / "arrangement_all_modes_20260711.yaml"


def test_load_five_song_four_mode_config() -> None:
    """
    验证5首真实 MP3 与4种保留模式可加载，且两个淘汰模式完全禁用。

    :return: 无返回值
    :raises AssertionError: 正式批处理范围与用户要求不一致时触发
    """
    config = load_benchmark_config(CONFIG_PATH)

    assert config.experiment_id == "arrangement_all_modes_20260711"
    assert config.output_root == PROJECT_ROOT / "work" / "arrangement_all_modes_20260711"
    assert len(config.songs) == 5
    assert all(song.source_mp3.is_file() for song in config.songs)
    assert tuple(candidate.mode for candidate in config.candidates) == (
        "adaptive_octave_fold",
        "hands_decoupled",
        "attention_weighted",
        "svsep_mpdr",
    )
    assert config.score_aware_mode is None
    assert "score_aware_theory" not in {candidate.mode for candidate in config.candidates}
    assert "octave_fold" not in {candidate.mode for candidate in config.candidates}
