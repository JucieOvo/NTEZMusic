"""
模块名称：test_diagnostic_evaluator
功能描述：
    针对 work/transcription_benchmark/diagnostic_evaluator.py v2 真实验收测试。
    全部测试使用真实 MAESTRO performance MIDI（A）与管线转录 MIDI（C），无 Mock/Stub。

    关键回归断言：
        global_scale 必须恢复 tempo 覆盖因子：
            巴赫 ≈ 117.5/120 = 0.979（容差 ±0.03）
            肖邦 ≈ 112.3/120 = 0.936（容差 ±0.03）

    测试覆盖：
        - 顶层字段完整性
        - 源文件 SHA-256 验证
        - raw_metrics 全部子字段存在且值域 [0,1]
        - diagnostic_metrics 全部子字段存在（含 local_alignment）
        - error_buckets 计数自洽（nearly_matched + co_wp + cp_wo + pure = total_unmatched）
        - 全局缩放校准恢复已知 tempo 因子
        - 偏移校准 F1 >= raw onset_pitch F1
        - 局部对齐字段存在且合理
        - CLI JSON 输出解析与字段验证

作者：JucieOvo
创建日期：2026-07-10
修改记录：
    - 2026-07-10 JucieOvo: v2 更新，新增 scale-recovery 回归、local_alignment 字段、修正错误桶名称
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# 固定路径
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARK_DIR = _PROJECT_ROOT / "work" / "transcription_benchmark"

BACH_REF_MIDI = _BENCHMARK_DIR / "bach_bwv846" / "reference_performance.midi"
BACH_C_MIDI = _BENCHMARK_DIR / "bach_bwv846" / "pipeline" / "midi" / "input.mid"
BACH_B_MIDI = _BENCHMARK_DIR / "bach_bwv846" / "evaluation" / "raw_transkun" / "transkun_raw_120bpm.mid"
CHOPIN_REF_MIDI = _BENCHMARK_DIR / "chopin_op10_no12" / "reference_performance.midi"
CHOPIN_C_MIDI = _BENCHMARK_DIR / "chopin_op10_no12" / "pipeline" / "midi" / "input.mid"
CHOPIN_B_MIDI = _BENCHMARK_DIR / "chopin_op10_no12" / "evaluation" / "raw_transkun" / "transkun_raw_120bpm.mid"

# 已知 tempo 覆盖因子
BACH_EXPECTED_ALPHA = 117.5 / 120.0   # ≈ 0.9792
CHOPIN_EXPECTED_ALPHA = 112.3 / 120.0  # ≈ 0.9358
ALPHA_TOLERANCE = 0.0005

# ---------------------------------------------------------------------------
# 动态加载模块
# ---------------------------------------------------------------------------
_DIAG_MODULE_PATH = str(_BENCHMARK_DIR / "diagnostic_evaluator.py")
_spec = importlib.util.spec_from_file_location("diagnostic_evaluator", _DIAG_MODULE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载诊断评测模块: {_DIAG_MODULE_PATH}")
_diag_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_diag_module)

_compute_all_diagnostics_uncached = _diag_module.compute_all_diagnostics
hash_file_sha256 = _diag_module.hash_file_sha256
_DIAG_SCRIPT_PATH = _BENCHMARK_DIR / "diagnostic_evaluator.py"


@lru_cache(maxsize=None)
def _compute_all_diagnostics_cached(reference_path: str, estimated_path: str) -> dict[str, Any]:
    """
    缓存真实 MIDI 评测结果，避免同一 A/C 或 A/B 文件在单次测试会话中重复计算。

    :param reference_path: 参考 MIDI 绝对或相对路径
    :param estimated_path: 估计 MIDI 绝对或相对路径
    :return: 完整诊断结果
    """
    return _compute_all_diagnostics_uncached(
        reference_midi_path=Path(reference_path),
        estimated_midi_path=Path(estimated_path),
    )


def compute_all_diagnostics(
    reference_midi_path: Path,
    estimated_midi_path: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    测试侧评测入口；默认缓存相同真实文件组合，显式参数场景保持原始调用。

    :param reference_midi_path: 参考 MIDI 路径
    :param estimated_midi_path: 估计 MIDI 路径
    :param kwargs: 非默认评测参数
    :return: 完整诊断结果
    """
    if kwargs:
        return _compute_all_diagnostics_uncached(
            reference_midi_path=reference_midi_path,
            estimated_midi_path=estimated_midi_path,
            **kwargs,
        )
    return _compute_all_diagnostics_cached(
        str(reference_midi_path.resolve()),
        str(estimated_midi_path.resolve()),
    )

# ---------------------------------------------------------------------------
# 字段规范
# ---------------------------------------------------------------------------

REQUIRED_TOP_FIELDS = [
    "source_files", "parameters", "tool_info",
    "raw_metrics", "diagnostic_metrics", "error_buckets",
]

REQUIRED_SOURCE_FIELDS = [
    "reference_path", "reference_sha256", "estimated_path", "estimated_sha256",
]

REQUIRED_RAW_METRIC_FIELDS = [
    "onset_only_precision", "onset_only_recall", "onset_only_f1", "onset_only_num_matched",
    "onset_pitch_precision", "onset_pitch_recall", "onset_pitch_f1",
    "onset_pitch_offset_precision", "onset_pitch_offset_recall", "onset_pitch_offset_f1",
    "velocity_aware_precision", "velocity_aware_recall", "velocity_aware_f1", "velocity_aware_num_matched",
    "frame_piano_roll_precision", "frame_piano_roll_recall", "frame_piano_roll_accuracy",
    "frame_piano_roll_e_sub", "frame_piano_roll_e_miss", "frame_piano_roll_e_fa", "frame_piano_roll_e_tot",
    "frame_piano_roll_precision_chroma", "frame_piano_roll_recall_chroma", "frame_piano_roll_accuracy_chroma",
    "num_reference_notes", "num_estimated_notes",
    "num_matched_onset_pitch", "num_matched_onset_pitch_offset",
    "onset_residual_median", "onset_residual_mae", "onset_residual_rmse", "onset_residual_p95",
    "onset_residual_mean", "onset_residual_std",
    "offset_residual_median", "offset_residual_mae", "offset_residual_rmse", "offset_residual_p95",
    "offset_residual_mean", "offset_residual_std",
]

REQUIRED_DIAGNOSTIC_FIELDS = [
    "global_offset_best_shift_seconds", "global_offset_best_f1",
    "global_scale_best_alpha", "global_scale_best_f1",
    "scale_then_offset_best_alpha", "scale_then_offset_residual_seconds", "scale_then_offset_combined_f1",
    "local_alignment_f1", "local_alignment_num_windows",
    "local_alignment_offset_std", "local_alignment_offset_mean", "local_alignment_f1_improvement",
    "local_alignment_baseline", "local_alignment_baseline_f1",
]

REQUIRED_ERROR_BUCKET_FIELDS = [
    "nearly_matched_ref_offset_only",
    "correct_onset_wrong_pitch_ref", "correct_pitch_wrong_onset_ref", "pure_miss_ref",
    "nearly_matched_est_offset_only",
    "correct_onset_wrong_pitch_est", "correct_pitch_wrong_onset_est", "pure_insertion_est",
    "total_unmatched_ref", "total_unmatched_est",
    "segment_boundary_error_concentration",
]


# ============================================================================
# 测试类 1: 巴赫 A vs C 完整字段验证
# ============================================================================

class TestDiagnosticBachC:
    """巴赫 BWV 846 完整字段验证。"""

    @pytest.fixture(scope="class")
    def result(self) -> dict:
        return compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )

    def test_top_level_fields_exist(self, result: dict) -> None:
        for field in REQUIRED_TOP_FIELDS:
            assert field in result, f"缺少顶层字段: {field}"

    def test_source_files_info(self, result: dict) -> None:
        src = result["source_files"]
        for field in REQUIRED_SOURCE_FIELDS:
            assert field in src, f"缺少源文件字段: {field}"
        assert len(src["reference_sha256"]) == 64
        assert len(src["estimated_sha256"]) == 64
        assert Path(src["reference_path"]).exists()
        assert Path(src["estimated_path"]).exists()

    def test_raw_metrics_fields_exist_and_range(self, result: dict) -> None:
        rm = result["raw_metrics"]
        for field in REQUIRED_RAW_METRIC_FIELDS:
            assert field in rm, f"缺少 raw_metrics 字段: {field}"
        prf_fields = [f for f in rm if any(k in f for k in ["precision", "recall", "f1", "accuracy"])]
        for field in prf_fields:
            val = rm[field]
            assert 0.0 <= val <= 1.0, f"{field} = {val} 超出 [0,1]"

    def test_diagnostic_metrics_fields_exist(self, result: dict) -> None:
        dm = result["diagnostic_metrics"]
        for field in REQUIRED_DIAGNOSTIC_FIELDS:
            assert field in dm, f"缺少 diagnostic_metrics 字段: {field}"

    def test_error_buckets_fields_exist_and_non_negative(self, result: dict) -> None:
        eb = result["error_buckets"]
        for field in REQUIRED_ERROR_BUCKET_FIELDS:
            assert field in eb, f"缺少 error_buckets 字段: {field}"
            val = eb[field]
            assert val >= 0, f"{field} = {val} 不应为负数"

    def test_parameters_recorded(self, result: dict) -> None:
        params = result["parameters"]
        assert "onset_tolerance" in params
        assert params["onset_tolerance"] > 0
        assert "scale_search_range" in params
        assert "scale_search_step" in params
        assert "local_window_size_seconds" in params

    def test_tool_info_present(self, result: dict) -> None:
        ti = result["tool_info"]
        assert ti["tool_name"] == "diagnostic_evaluator"
        assert ti["tool_version"] == _diag_module.TOOL_VERSION


# ============================================================================
# 测试类 2: 肖邦 A vs C 完整字段验证
# ============================================================================

class TestDiagnosticChopinC:
    """肖邦 Op.10 No.12 完整字段验证。"""

    @pytest.fixture(scope="class")
    def result(self) -> dict:
        return compute_all_diagnostics(
            reference_midi_path=CHOPIN_REF_MIDI,
            estimated_midi_path=CHOPIN_C_MIDI,
        )

    def test_top_fields_exist(self, result: dict) -> None:
        for field in REQUIRED_TOP_FIELDS:
            assert field in result

    def test_source_files_paths_match(self, result: dict) -> None:
        src = result["source_files"]
        assert Path(src["reference_path"]).exists()
        assert Path(src["estimated_path"]).exists()

    def test_raw_metrics_all_in_range(self, result: dict) -> None:
        rm = result["raw_metrics"]
        prf_fields = [f for f in rm if any(k in f for k in ["precision", "recall", "f1", "accuracy"])]
        for field in prf_fields:
            assert 0.0 <= rm[field] <= 1.0

    def test_diagnostic_fields_present(self, result: dict) -> None:
        dm = result["diagnostic_metrics"]
        for field in REQUIRED_DIAGNOSTIC_FIELDS:
            assert field in dm

    def test_error_buckets_non_negative(self, result: dict) -> None:
        eb = result["error_buckets"]
        for field in REQUIRED_ERROR_BUCKET_FIELDS:
            assert eb[field] >= 0


# ============================================================================
# 测试类 3: 全局缩放校准 — 恢复已知 tempo 覆盖因子（回归断言）
# ============================================================================

class TestScaleRecovery:
    """验证 global_scale_* 恢复 C MIDI 的 tempo 覆盖因子。"""

    def test_bach_scale_recovery(self) -> None:
        """巴赫 C 由 120→117.5 BPM 覆盖，scale α 应 ≈ 117.5/120 = 0.979。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        alpha = result["diagnostic_metrics"]["global_scale_best_alpha"]
        assert alpha is not None, "global_scale_best_alpha 不应为 None"
        assert abs(alpha - BACH_EXPECTED_ALPHA) <= ALPHA_TOLERANCE, \
            f"巴赫 scale alpha = {alpha:.4f}, 期望 ≈ {BACH_EXPECTED_ALPHA:.4f} (容差 ±{ALPHA_TOLERANCE})"

    def test_chopin_scale_recovery(self) -> None:
        """肖邦 C 由 120→112.3 BPM 覆盖，scale α 应 ≈ 112.3/120 = 0.936。"""
        result = compute_all_diagnostics(
            reference_midi_path=CHOPIN_REF_MIDI,
            estimated_midi_path=CHOPIN_C_MIDI,
        )
        alpha = result["diagnostic_metrics"]["global_scale_best_alpha"]
        assert alpha is not None, "global_scale_best_alpha 不应为 None"
        assert abs(alpha - CHOPIN_EXPECTED_ALPHA) <= ALPHA_TOLERANCE, \
            f"肖邦 scale alpha = {alpha:.4f}, 期望 ≈ {CHOPIN_EXPECTED_ALPHA:.4f} (容差 ±{ALPHA_TOLERANCE})"

    def test_bach_scale_improves_f1(self) -> None:
        """缩放校准后 F1 应 >= 原始 onset_pitch F1。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        raw_f1 = result["raw_metrics"]["onset_pitch_f1"]
        scale_f1 = result["diagnostic_metrics"]["global_scale_best_f1"]
        assert scale_f1 is not None
        assert scale_f1 >= raw_f1 - 0.001, \
            f"scale F1 ({scale_f1:.6f}) < raw onset_pitch F1 ({raw_f1:.6f})"
        assert scale_f1 > 0.99, \
            f"巴赫精细缩放后 F1={scale_f1:.6f}，未恢复 tempo 覆盖前的时间轴"


class TestVelocityMetric:
    """验证 velocity 指标使用 mir_eval 的标准归一化与全局缩放协议。"""

    def test_chopin_velocity_metric_is_stricter_than_offset_metric(self) -> None:
        """肖邦原始 B 中存在力度偏差，velocity-aware F1 应低于仅 offset F1。"""
        result = compute_all_diagnostics(
            reference_midi_path=CHOPIN_REF_MIDI,
            estimated_midi_path=CHOPIN_B_MIDI,
        )
        raw = result["raw_metrics"]
        assert raw["velocity_aware_f1"] < raw["onset_pitch_offset_f1"]


# ============================================================================
# 测试类 4: 偏移校准一致性
# ============================================================================

class TestOffsetCalibration:
    """验证全局偏移校准的合理性。"""

    def test_bach_offset_improves_over_raw(self) -> None:
        """偏移校准后 F1 >= 原始 onset_pitch F1（使用同类型匹配）。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        raw_f1 = result["raw_metrics"]["onset_pitch_f1"]
        offset_f1 = result["diagnostic_metrics"]["global_offset_best_f1"]
        assert offset_f1 is not None
        assert offset_f1 >= raw_f1 - 0.001

    def test_bach_offset_plausible(self) -> None:
        """巴赫全局偏移量在 [-60, 60] 秒内。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        shift = result["diagnostic_metrics"]["global_offset_best_shift_seconds"]
        assert shift is not None
        assert abs(shift) < 60.0, f"偏移量过大: {shift}s"

    def test_bach_scale_then_offset_exists(self) -> None:
        """scale_then_offset 组合校准结果存在。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        dm = result["diagnostic_metrics"]
        assert dm["scale_then_offset_best_alpha"] is not None
        assert dm["scale_then_offset_combined_f1"] is not None


# ============================================================================
# 测试类 5: 局部对齐
# ============================================================================

class TestLocalAlignment:
    """验证局部对齐诊断字段存在且合理。"""

    def test_bach_local_alignment_fields(self) -> None:
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        la = result["diagnostic_metrics"]
        assert la["local_alignment_num_windows"] > 0, "局部对齐应有非零窗口数"
        assert la["local_alignment_offset_std"] is not None
        assert la["local_alignment_f1"] is not None
        assert la["local_alignment_baseline"] == "global_scale_offset"
        assert la["local_alignment_baseline_f1"] is not None
        assert la["local_alignment_f1"] >= la["local_alignment_baseline_f1"] - 1e-9

    def test_chopin_local_alignment_fields(self) -> None:
        result = compute_all_diagnostics(
            reference_midi_path=CHOPIN_REF_MIDI,
            estimated_midi_path=CHOPIN_C_MIDI,
        )
        la = result["diagnostic_metrics"]
        assert la["local_alignment_num_windows"] > 0
        assert la["local_alignment_offset_std"] is not None
        assert la["local_alignment_baseline"] == "global_scale_offset"
        assert la["local_alignment_baseline_f1"] is not None
        assert la["local_alignment_f1"] >= la["local_alignment_baseline_f1"] - 1e-9


# ============================================================================
# 测试类 6: 错误分桶计数一致性
# ============================================================================

class TestErrorBuckets:
    """验证错误分桶计数自洽。"""

    def test_bach_bucket_counts_sum_to_ref_unmatched(self) -> None:
        """nearly_matched + co_wp + cp_wo + pure = total_unmatched_ref。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        eb = result["error_buckets"]
        actual = (eb["nearly_matched_ref_offset_only"] +
                  eb["correct_onset_wrong_pitch_ref"] +
                  eb["correct_pitch_wrong_onset_ref"] +
                  eb["pure_miss_ref"])
        expected = eb["total_unmatched_ref"]
        assert actual == expected, \
            f"分桶和 ({actual}) != total_unmatched_ref ({expected})"

    def test_bach_bucket_counts_sum_to_est_unmatched(self) -> None:
        """估计侧分桶和 = total_unmatched_est。"""
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        eb = result["error_buckets"]
        actual = (eb["nearly_matched_est_offset_only"] +
                  eb["correct_onset_wrong_pitch_est"] +
                  eb["correct_pitch_wrong_onset_est"] +
                  eb["pure_insertion_est"])
        expected = eb["total_unmatched_est"]
        assert actual == expected, \
            f"分桶和 ({actual}) != total_unmatched_est ({expected})"

    def test_segment_concentration_in_range(self) -> None:
        result = compute_all_diagnostics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_C_MIDI,
        )
        conc = result["error_buckets"]["segment_boundary_error_concentration"]
        assert 0.0 <= conc <= 1.0


# ============================================================================
# 测试类 7: CLI JSON 输出
# ============================================================================

class TestCLIJSON:
    """验证 CLI 生成合法 JSON 文件。"""

    def test_bach_cli_produces_json(self, tmp_path: Path) -> None:
        json_path = tmp_path / "bach_v2.json"
        cmd = [
            sys.executable, str(_DIAG_SCRIPT_PATH),
            "--reference", str(BACH_REF_MIDI),
            "--estimated", str(BACH_C_MIDI),
            "--output-json", str(json_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for field in REQUIRED_TOP_FIELDS:
            assert field in data
        assert len(data["source_files"]["reference_sha256"]) == 64

    def test_chopin_cli_produces_json(self, tmp_path: Path) -> None:
        json_path = tmp_path / "chopin_v2.json"
        cmd = [
            sys.executable, str(_DIAG_SCRIPT_PATH),
            "--reference", str(CHOPIN_REF_MIDI),
            "--estimated", str(CHOPIN_C_MIDI),
            "--output-json", str(json_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "diagnostic_metrics" in data
        assert data["diagnostic_metrics"]["global_scale_best_alpha"] is not None


# ============================================================================
# 测试类 8: SHA-256 哈希
# ============================================================================

class TestSHA256Consistency:
    """SHA-256 哈希验证。"""

    def test_hash_length(self) -> None:
        h = hash_file_sha256(BACH_REF_MIDI)
        assert len(h) == 64
        assert all(c in "0123456789ABCDEF" for c in h)

    def test_hash_stable(self) -> None:
        assert hash_file_sha256(BACH_REF_MIDI) == hash_file_sha256(BACH_REF_MIDI)
