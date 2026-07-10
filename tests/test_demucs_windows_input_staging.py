"""
模块名称：test_demucs_windows_input_staging
功能描述：
    验证 Windows 尾随句点音频文件在进入 Demucs 前获得安全且真实的暂存副本。

主要组件：
    - test_stage_windows_unsafe_demucs_input: 验证真实问题文件的安全暂存

依赖说明：
    - audio_to_yaml_converter: 被测 Demucs 输入路径处理逻辑

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.audio_to_yaml_converter import _stage_demucs_input_if_needed


UNSAFE_AUDIO_PATH = PROJECT_ROOT / "audio_水....mp3"


def test_stage_windows_unsafe_demucs_input(tmp_path: Path) -> None:
    """
    验证尾随句点 stem 被清理，且暂存文件与真实源文件字节数完全一致。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 暂存路径仍非法或文件复制不完整时触发
    """
    staged_path = _stage_demucs_input_if_needed(UNSAFE_AUDIO_PATH, tmp_path)

    assert staged_path.is_file()
    assert staged_path != UNSAFE_AUDIO_PATH
    assert not staged_path.stem.endswith((".", " "))
    assert staged_path.suffix == UNSAFE_AUDIO_PATH.suffix
    assert staged_path.stat().st_size == UNSAFE_AUDIO_PATH.stat().st_size
