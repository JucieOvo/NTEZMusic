"""
模块名称：test_evaluate_transcription
功能描述：
    针对 work/transcription_benchmark/evaluate_transcription.py 评测工具的
    真实验收测试。全部测试仅使用两份真实 MAESTRO performance MIDI
    （巴赫 BWV 846 + 肖邦 Op.10 No.12），不生成或写出任何派生/临时/人工 MIDI，
    不记录或写出 MIDI 到 pytest 临时目录，禁止 Mock/Stub/假数据。

    测试覆盖：
        - 同一 MIDI 自比对（巴赫、肖邦各一次）：验证满分指标与零误差
        - 两份不同真实 MIDI 交叉评测：验证合理非满分指标与非空清单
        - 音高转换函数单元测试（MIDI→Hz）
        - 音符提取函数输出维度与排序验证
        - 指标字典字段完整性验证
        - CLI 参数解析与 JSON/Markdown 输出文件生成
        - 容差参数传递与默认值记录

主要组件：
    - TestSelfComparison: 同一真实 MIDI 自比对的满分断言
    - TestCrossComparison: 两份不同真实 MIDI 的交叉评测断言
    - TestMetrics: 辅助函数单元测试（音高、提取、字段、Pearson）
    - TestCLI: 命令行参数与输出文件生成
    - TestParameters: 容差参数传递

依赖说明：
    - pytest: 测试框架
    - numpy: 数值计算
    - mir_eval: 转录评测（评测器内依赖，测试不直接调用）
    - scipy: Pearson 相关系数（评测器内依赖）
    - pretty_midi: MIDI 解析（评测器内依赖）

作者：JucieOvo
创建日期：2026-07-10
修改记录：
    - 2026-07-10 JucieOvo: 重构为仅使用真实 MIDI 的纯真实验收测试
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# 固定路径：两份真实 MAESTRO performance MIDI
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARK_DIR = _PROJECT_ROOT / "work" / "transcription_benchmark"
BACH_REF_MIDI = _BENCHMARK_DIR / "bach_bwv846" / "reference_performance.midi"
CHOPIN_REF_MIDI = _BENCHMARK_DIR / "chopin_op10_no12" / "reference_performance.midi"

# ---------------------------------------------------------------------------
# 动态加载 evaluate_transcription 模块（避免 sys.path 污染与类型抑制注解）
# ---------------------------------------------------------------------------
_EVAL_MODULE_PATH = str(_BENCHMARK_DIR / "evaluate_transcription.py")
_spec = importlib.util.spec_from_file_location("evaluate_transcription", _EVAL_MODULE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载评测模块: {_EVAL_MODULE_PATH}")
_eval_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eval_module)

# 提取测试依赖的函数引用
compute_metrics = _eval_module.compute_metrics
extract_notes_from_midi = _eval_module.extract_notes_from_midi
main = _eval_module.main
midi_pitch_to_hz = _eval_module.midi_pitch_to_hz

# 评测脚本的绝对路径（CLI 测试用）
_EVAL_SCRIPT_PATH = _BENCHMARK_DIR / "evaluate_transcription.py"


# ============================================================================
# 测试类 1: 同一真实 MIDI 自比对 — 期望满分
# ============================================================================

class TestSelfComparison:
    """
    验证金标准 MIDI 与自身比对时，所有指标为满分（1.0）、误差为零、
    无漏检无额外音符。涵盖巴赫 BWV 846（1925 音符）和肖邦 Op.10 No.12（2134 音符）。
    """

    def test_bach_self_comparison_onset_pitch_f1(self) -> None:
        """
        场景：巴赫 BWV 846 参考 MIDI 与自身逐音比对。
        期望：onset+pitch precision/recall/F1 均为 1.0。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )
        assert result["onset_pitch_precision"] == pytest.approx(1.0, abs=1e-9)
        assert result["onset_pitch_recall"] == pytest.approx(1.0, abs=1e-9)
        assert result["onset_pitch_f1"] == pytest.approx(1.0, abs=1e-9)

    def test_bach_self_comparison_offset_included_f1(self) -> None:
        """
        场景：巴赫自比对，启用 offset 约束。
        期望：onset+pitch+offset precision/recall/F1 均为 1.0。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )
        assert result["onset_pitch_offset_precision"] == pytest.approx(1.0, abs=1e-9)
        assert result["onset_pitch_offset_recall"] == pytest.approx(1.0, abs=1e-9)
        assert result["onset_pitch_offset_f1"] == pytest.approx(1.0, abs=1e-9)

    def test_bach_self_comparison_zero_error(self) -> None:
        """
        场景：巴赫自比对，onset/offset 误差应为零。
        期望：onset_error_mae、onset_error_rmse、offset_error_mae、offset_error_rmse 均为 0.0。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )
        assert result["onset_error_mae"] == pytest.approx(0.0, abs=1e-9)
        assert result["onset_error_rmse"] == pytest.approx(0.0, abs=1e-9)
        assert result["offset_error_mae"] == pytest.approx(0.0, abs=1e-9)
        assert result["offset_error_rmse"] == pytest.approx(0.0, abs=1e-9)

    def test_bach_self_comparison_no_missed_no_extra(self) -> None:
        """
        场景：巴赫自比对，不应有漏检或额外音符。
        期望：missed_notes 和 extra_notes 列表均为空，匹配对数等于音符总数。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )
        assert len(result["missed_notes"]) == 0
        assert len(result["extra_notes"]) == 0
        assert result["num_matched_notes"] == result["num_reference_notes"]
        assert result["num_matched_notes"] == result["num_estimated_notes"]

    def test_chopin_self_comparison_f1(self) -> None:
        """
        场景：肖邦 Op.10 No.12 参考 MIDI 与自身逐音比对。
        期望：onset_pitch_f1 为 1.0，参考音符数为 2134。
        """
        result = compute_metrics(
            reference_midi_path=CHOPIN_REF_MIDI,
            estimated_midi_path=CHOPIN_REF_MIDI,
        )
        assert result["onset_pitch_f1"] == pytest.approx(1.0, abs=1e-9)
        assert result["num_reference_notes"] == 2134
        assert result["num_matched_notes"] == 2134


# ============================================================================
# 测试类 2: 两份不同真实 MIDI 交叉评测
# ============================================================================

class TestCrossComparison:
    """
    验证两份不同真实演奏 MIDI 交叉比对时，产生合理非满分指标和非空清单。
    巴赫与肖邦是完全不同的曲目，交叉比对必然产生大量不匹配。
    """

    def test_cross_comparison_f1_not_perfect(self) -> None:
        """
        场景：巴赫 BWV 846 作为参考、肖邦 Op.10 No.12 作为估计。
        期望：onset_pitch_f1 远小于 1.0（实际趋近于 0）。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=CHOPIN_REF_MIDI,
        )
        assert result["onset_pitch_f1"] < 0.1

    def test_cross_comparison_generates_missed_notes(self) -> None:
        """
        场景：交叉比对时，参考中绝大多数音符无法匹配。
        期望：missed_notes 列表非空，且条目包含 onset_seconds/pitch_midi/offset_seconds。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=CHOPIN_REF_MIDI,
        )
        assert len(result["missed_notes"]) > 0
        first_missed = result["missed_notes"][0]
        assert "onset_seconds" in first_missed
        assert "pitch_midi" in first_missed
        assert "offset_seconds" in first_missed

    def test_cross_comparison_generates_extra_notes(self) -> None:
        """
        场景：交叉比对时，估计中绝大多数音符无法匹配。
        期望：extra_notes 列表非空，且条目包含 onset_seconds/pitch_midi/offset_seconds/velocity。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=CHOPIN_REF_MIDI,
        )
        assert len(result["extra_notes"]) > 0
        first_extra = result["extra_notes"][0]
        assert "onset_seconds" in first_extra
        assert "pitch_midi" in first_extra
        assert "offset_seconds" in first_extra
        assert "velocity" in first_extra


# ============================================================================
# 测试类 3: 辅助函数单元测试
# ============================================================================

class TestMetrics:
    """
    验证评测器辅助函数的正确性：MIDI→Hz 转换、音符提取、指标字典完整性、
    Pearson 相关系数在完全匹配时的表现。
    """

    def test_midi_pitch_to_hz_a4(self) -> None:
        """
        场景：MIDI 音高号 69 (A4) 应转换为 440 Hz。
        期望：midi_pitch_to_hz(69) == 440.0。
        """
        assert midi_pitch_to_hz(69) == pytest.approx(440.0, rel=1e-6)

    def test_midi_pitch_to_hz_c4(self) -> None:
        """
        场景：MIDI 音高号 60 (C4, 中央 C) 约为 261.63 Hz。
        期望：midi_pitch_to_hz(60) 约 261.6256。
        """
        hz = midi_pitch_to_hz(60)
        expected = 440.0 * (2.0 ** ((60 - 69) / 12.0))
        assert hz == pytest.approx(expected, rel=1e-6)

    def test_midi_pitch_to_hz_octave_up(self) -> None:
        """
        场景：MIDI 音高号 81（比 A4 高一个八度）应为 880 Hz。
        期望：midi_pitch_to_hz(81) == 880.0。
        """
        assert midi_pitch_to_hz(81) == pytest.approx(880.0, rel=1e-6)

    def test_extract_notes_returns_sorted_intervals_and_hz(self) -> None:
        """
        场景：从真实巴赫 MIDI 提取音符，应返回排序后的四元组。
        期望：intervals 二维数组、pitches_hz 等长、onset 单调递增、频率有效。
        """
        intervals, pitches_hz, midi_pitches, velocities = extract_notes_from_midi(BACH_REF_MIDI)

        # 维度验证
        assert intervals.ndim == 2
        assert intervals.shape[1] == 2
        assert intervals.shape[0] > 0
        assert pitches_hz.shape[0] == intervals.shape[0]
        assert midi_pitches.shape[0] == intervals.shape[0]
        assert velocities.shape[0] == intervals.shape[0]

        # onset 必须单调不降
        onsets = intervals[:, 0]
        assert np.all(np.diff(onsets) >= 0), "onset 必须单调不降"

        # 频率必须为正且有限
        assert np.all(pitches_hz > 0), "所有频率必须为正"
        assert np.all(np.isfinite(pitches_hz)), "所有频率必须为有限值"

    def test_compute_metrics_dict_has_required_fields(self) -> None:
        """
        场景：巴赫自比对的返回字典必须包含所有必要字段。
        期望：字段齐全，precision/recall/F1 在 [0, 1] 范围内。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )

        required_fields = [
            "num_reference_notes",
            "num_estimated_notes",
            "num_matched_notes",
            "onset_pitch_precision",
            "onset_pitch_recall",
            "onset_pitch_f1",
            "onset_pitch_offset_precision",
            "onset_pitch_offset_recall",
            "onset_pitch_offset_f1",
            "onset_error_mae",
            "onset_error_rmse",
            "onset_error_pearson",
            "offset_error_mae",
            "offset_error_rmse",
            "offset_error_pearson",
            "missed_notes",
            "extra_notes",
            "tool_version",
            "parameters",
        ]
        for field in required_fields:
            assert field in result, f"缺少必要字段: {field}"

        assert 0.0 <= result["onset_pitch_precision"] <= 1.0
        assert 0.0 <= result["onset_pitch_recall"] <= 1.0
        assert 0.0 <= result["onset_pitch_f1"] <= 1.0

    def test_pearson_correlation_perfect_match(self) -> None:
        """
        场景：巴赫自比对（完全匹配），onset/offset 误差均为零。
        期望：Pearson 相关系数在方差为零时可返回 None 或 NaN，不可报错。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )
        onset_pearson = result["onset_error_pearson"]
        offset_pearson = result["offset_error_pearson"]

        # 完全匹配时方差为零，Pearson 为 None 或 NaN 或 1.0 均可接受
        acceptable_onset = (
            onset_pearson is None
            or (isinstance(onset_pearson, float) and math.isnan(onset_pearson))
            or onset_pearson == 1.0
        )
        acceptable_offset = (
            offset_pearson is None
            or (isinstance(offset_pearson, float) and math.isnan(offset_pearson))
            or offset_pearson == 1.0
        )
        assert acceptable_onset, f"onset_pearson 异常值: {onset_pearson}"
        assert acceptable_offset, f"offset_pearson 异常值: {offset_pearson}"


# ============================================================================
# 测试类 4: 命令行接口（CLI）
# ============================================================================

class TestCLI:
    """
    验证 CLI 参数解析与输出文件生成。全部使用真实 MIDI 路径，
    不在临时目录写入 MIDI，仅写入 JSON 和 Markdown 报告。
    """

    def test_cli_generates_json_output(self, tmp_path: Path) -> None:
        """
        场景：运行 CLI 对巴赫自比对并指定 --output-json。
        期望：生成合法 JSON 文件，包含所有必要字段。
        """
        json_out = tmp_path / "result.json"
        result = subprocess.run(
            [
                sys.executable,
                str(_EVAL_SCRIPT_PATH),
                "--reference", str(BACH_REF_MIDI),
                "--estimated", str(BACH_REF_MIDI),
                "--output-json", str(json_out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"
        assert json_out.exists()

        with open(json_out, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "onset_pitch_f1" in data
        assert isinstance(data["parameters"], dict)

    def test_cli_generates_markdown_output(self, tmp_path: Path) -> None:
        """
        场景：运行 CLI 对巴赫自比对并指定 --output-markdown。
        期望：生成合法 Markdown 报告，包含标题。
        """
        md_out = tmp_path / "report.md"
        result = subprocess.run(
            [
                sys.executable,
                str(_EVAL_SCRIPT_PATH),
                "--reference", str(BACH_REF_MIDI),
                "--estimated", str(BACH_REF_MIDI),
                "--output-markdown", str(md_out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"CLI 失败: {result.stderr}"
        assert md_out.exists()

        md_content = md_out.read_text(encoding="utf-8")
        assert "# " in md_content, "Markdown 应包含标题"

    def test_cli_both_outputs_together(self, tmp_path: Path) -> None:
        """
        场景：同时生成 JSON 和 Markdown 输出。
        期望：两个文件均生成成功。
        """
        json_out = tmp_path / "result.json"
        md_out = tmp_path / "report.md"
        result = subprocess.run(
            [
                sys.executable,
                str(_EVAL_SCRIPT_PATH),
                "--reference", str(BACH_REF_MIDI),
                "--estimated", str(BACH_REF_MIDI),
                "--output-json", str(json_out),
                "--output-markdown", str(md_out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert json_out.exists()
        assert md_out.exists()

    def test_cli_cross_comparison_output(self, tmp_path: Path) -> None:
        """
        场景：巴赫为参考、肖邦为估计，生成 JSON 报告。
        期望：JSON 中 F1 远小于 1.0。
        """
        json_out = tmp_path / "cross_result.json"
        result = subprocess.run(
            [
                sys.executable,
                str(_EVAL_SCRIPT_PATH),
                "--reference", str(BACH_REF_MIDI),
                "--estimated", str(CHOPIN_REF_MIDI),
                "--output-json", str(json_out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

        with open(json_out, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["onset_pitch_f1"] < 0.1

    def test_cli_missing_reference_shows_error(self, tmp_path: Path) -> None:
        """
        场景：缺少 --reference 参数。
        期望：CLI 返回非零退出码。
        """
        result = subprocess.run(
            [
                sys.executable,
                str(_EVAL_SCRIPT_PATH),
                "--estimated", str(BACH_REF_MIDI),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_cli_file_not_found_error(self, tmp_path: Path) -> None:
        """
        场景：引用不存在的 MIDI 文件路径。
        期望：CLI 返回非零退出码。
        """
        result = subprocess.run(
            [
                sys.executable,
                str(_EVAL_SCRIPT_PATH),
                "--reference", str(tmp_path / "nonexistent.mid"),
                "--estimated", str(tmp_path / "also_nonexistent.mid"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0


# ============================================================================
# 测试类 5: 容差参数传递与记录
# ============================================================================

class TestParameters:
    """
    验证自定义容差值能正确传递给评测函数并记录在输出字典中。
    """

    def test_custom_tolerances_are_recorded(self) -> None:
        """
        场景：传入自定义容差值。
        期望：result["parameters"] 中记录自定义值。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
            onset_tolerance=0.03,
            pitch_tolerance=25.0,
            offset_ratio=0.15,
            offset_min_tolerance=0.08,
        )
        params = result["parameters"]
        assert params["onset_tolerance"] == 0.03
        assert params["pitch_tolerance"] == 25.0
        assert params["offset_ratio"] == 0.15
        assert params["offset_min_tolerance"] == 0.08

    def test_default_tolerances_match_spec(self) -> None:
        """
        场景：不传容差参数，使用默认值。
        期望：默认值为 onset 50ms、pitch 50cents、offset_ratio 0.2、offset_min 0.05。
        """
        result = compute_metrics(
            reference_midi_path=BACH_REF_MIDI,
            estimated_midi_path=BACH_REF_MIDI,
        )
        params = result["parameters"]
        assert params["onset_tolerance"] == 0.05
        assert params["pitch_tolerance"] == 50.0
        assert params["offset_ratio"] == 0.2
        assert params["offset_min_tolerance"] == 0.05
