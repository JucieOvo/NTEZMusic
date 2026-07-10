"""
模块名称：test_svsep_hand_separator_inference
功能描述：
    使用真实模型、真实 CUDA 和真实 MIDI 验证 piano_svsep 左右手分离推理。

主要组件：
    - test_real_cuda_inference_keeps_graph_and_model_on_same_device: 验证设备一致性

依赖说明：
    - svsep_hand_separator: 被测真实声部分离封装
    - piano_svsep/pretrained_models/model.ckpt: 真实模型权重

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from pathlib import Path

from svsep_hand_separator import SvsepHandSeparator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / "piano_svsep" / "pretrained_models" / "model.ckpt"
REAL_MIDI_PATH = (
    PROJECT_ROOT
    / "work"
    / "arrangement_benchmark_20260711"
    / "cruel_angel"
    / "reference"
    / "base_midi"
    / "song.mid"
)


def test_real_cuda_inference_keeps_graph_and_model_on_same_device(tmp_path: Path) -> None:
    """
    验证真实 CUDA 推理不会出现模型权重与图张量分属 CUDA/CPU 的错误。

    :param tmp_path: pytest 提供的真实临时目录
    :return: 无返回值
    :raises AssertionError: 真实推理、回填或审计结果不合格时触发
    """
    separator = SvsepHandSeparator(model_path=MODEL_PATH, device="cuda")
    result = separator.separate(
        midi_path=REAL_MIDI_PATH,
        work_dir=tmp_path,
        bpm=129.2,
    )

    result.audit.validate()
    assert result.left_notes
    assert result.right_notes
    assert result.audit.match_ratio >= 0.95
