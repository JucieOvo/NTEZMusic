"""
模块名称：evaluate_transcription.py
功能描述：
    对 MAESTRO 参考 performance MIDI 与管线生成的转录 MIDI 进行逐音评测。
    使用 mir_eval 计算：
        - onset+pitch Precision / Recall / F1（不含 offset 约束）
        - onset+pitch+offset Precision / Recall / F1（含 offset 约束）
        - 匹配对 onset / offset 误差的 MAE、RMSE、Pearson 相关系数
        - 漏检音符与额外音符的结构化清单
    输出 JSON（结构化）和 Markdown（人类可读）报告。
    真实只读，不修改任何 MIDI、WAV、MP3 或项目文件。

主要组件：
    - midi_pitch_to_hz(): MIDI 音高号转 Hz
    - extract_notes_from_midi(): 从 pretty_midi 提取 (intervals, pitches_hz, midi_pitches, velocities)
    - compute_metrics(): 核心评测函数，返回结构化指标字典
    - format_report_markdown(): 将结果渲染为 Markdown 表格
    - main(): CLI 入口

依赖说明：
    - pretty_midi (>=0.2.10): MIDI 解析
    - mir_eval (>=0.7): 转录评测标准库
    - numpy (>=1.20): 数组运算
    - scipy (>=1.7): Pearson 相关系数
    - json, argparse: 内置

作者：JucieOvo
创建日期：2026-07-10
修改记录：
    - 2026-07-10 JucieOvo: TDD GREEN 实现，对齐测试期望 API
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import mir_eval
import numpy as np
import pretty_midi
from scipy import stats as scipy_stats

# ---------------------------------------------------------------------------
# 工具版本与元信息常量（不得硬编码到业务逻辑中）
# ---------------------------------------------------------------------------
TOOL_VERSION = "1.0.0"
TOOL_NAME = "evaluate_transcription"

# 默认容差值（MIREX 标准）
DEFAULT_ONSET_TOLERANCE = 0.05       # 50 ms
DEFAULT_PITCH_TOLERANCE = 50.0       # 50 cents
DEFAULT_OFFSET_RATIO = 0.2           # 20%
DEFAULT_OFFSET_MIN_TOLERANCE = 0.05  # 50 ms


# ============================================================================
# 核心音高转换
# ============================================================================

def midi_pitch_to_hz(pitch: int) -> float:
    """
    将 MIDI 音高号转换为 Hz 频率（A4 = 440 Hz）。

    mir_eval.transcription 的所有音符匹配函数要求音高以 Hz 为单位输入，
    因此从 pretty_midi 提取的 MIDI 音高号必须先经本函数转换。

    :param pitch: MIDI 音高号，范围 0-127（pretty_midi 保证此约束）
    :return: 对应频率，单位 Hz
    """
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _hz_to_midi_pitch(hz: float) -> int:
    """
    将 Hz 频率逆转换回最接近的 MIDI 音高号。

    用于在漏检/额外音清单中将 mir_eval 返回的 Hz 值转回 MIDI 音高。

    :param hz: 频率值，单位 Hz
    :return: 最接近的 MIDI 音高号（四舍五入取整）
    """
    if hz <= 0:
        return 0
    return int(round(69 + 12 * np.log2(hz / 440.0)))


# ============================================================================
# MIDI 音符提取
# ============================================================================

def extract_notes_from_midi(
        midi_path: Union[str, Path],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    从 MIDI 文件中提取所有音符的 onset、offset、pitch（Hz）和 velocity。

    遍历所有非鼓乐器轨道，合并全部音符；按 onset 升序排序后返回。
    这保证 mir_eval 输入的顺序一致性与可复现性。

    :param midi_path: MIDI 文件路径（str 或 pathlib.Path）
    :return: 四元组 (intervals, pitches_hz, midi_pitches, velocities)
        - intervals: 形状 (N, 2) 的 float64 数组，列为 [onset, offset]，单位秒
        - pitches_hz: 形状 (N,) 的 float64 数组，单位 Hz
        - midi_pitches: 形状 (N,) 的 int64 数组，MIDI 音高号
        - velocities: 形状 (N,) 的 int64 数组，力度值 0-127
    :raises FileNotFoundError: 文件不存在时抛出
    :raises ValueError: MIDI 文件中无音符事件时抛出
    """
    midi_path = Path(midi_path)

    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI 文件不存在: {midi_path}")

    # 使用 pretty_midi 加载 MIDI 文件
    pm = pretty_midi.PrettyMIDI(str(midi_path))

    # 收集所有非鼓轨道的音符（MAESTRO performance MIDI 通常 1-2 轨）
    raw_notes: List[Tuple[float, float, int, int]] = []  # (onset, offset, pitch, velocity)
    for instrument in pm.instruments:
        if instrument.is_drum:
            continue
        for note in instrument.notes:
            raw_notes.append((note.start, note.end, note.pitch, note.velocity))

    # 空音符检查：必须在排序/数组构造之前执行
    if len(raw_notes) == 0:
        raise ValueError(f"MIDI 文件中无有效音符事件: {midi_path}")

    # 按 onset 升序排序，onset 相同则按 pitch 升序（保证确定性排序）
    raw_notes.sort(key=lambda x: (x[0], x[2]))

    # 构建 numpy 数组
    intervals = np.array([[n[0], n[1]] for n in raw_notes], dtype=np.float64)
    midi_pitches = np.array([n[2] for n in raw_notes], dtype=np.int64)
    velocities = np.array([n[3] for n in raw_notes], dtype=np.int64)

    # 批量转换 MIDI 音高为 Hz（向量化操作，比逐音调用快 10x+）
    pitches_hz = 440.0 * (2.0 ** ((midi_pitches.astype(np.float64) - 69.0) / 12.0))

    return intervals, pitches_hz, midi_pitches, velocities


# ============================================================================
# 核心评测函数
# ============================================================================

def compute_metrics(
        reference_midi_path: Union[str, Path],
        estimated_midi_path: Union[str, Path],
        *,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
        offset_ratio: float = DEFAULT_OFFSET_RATIO,
        offset_min_tolerance: float = DEFAULT_OFFSET_MIN_TOLERANCE,
) -> Dict[str, Any]:
    """
    对两个 MIDI 文件执行完整的逐音转录评测。

    核心流程：
        1. 从两个 MIDI 分别提取音符（按 onset 排序）
        2. 使用 mir_eval.transcription.precision_recall_f1_overlap() 计算：
           - onset+pitch 的 P/R/F1（不含 offset 约束）
           - onset+pitch+offset 的 P/R/F1（含 offset 约束）
        3. 使用 mir_eval.transcription.match_notes() 获取匹配对
        4. 对匹配对计算 onset/offset 误差的 MAE/RMSE/Pearson
        5. 生成漏检音符与额外音符的结构化清单

    :param reference_midi_path: 金标准参考 MIDI 文件路径
    :param estimated_midi_path: 待评测的估计/转录 MIDI 文件路径
    :param onset_tolerance: onset 容差，单位秒（默认 0.05 = 50ms）
    :param pitch_tolerance: 音高容差，单位 cents（默认 50.0）
    :param offset_ratio: offset 容差比例，相对参考音符时长的百分比（默认 0.2 = 20%）
    :param offset_min_tolerance: offset 最小容差，单位秒（默认 0.05 = 50ms）
    :return: 包含全部评测指标的结构化字典，字段见 test_metrics_dict_has_required_fields
    """
    # ----------------------------------------------------------------
    # 步骤 1: 提取音符
    # ----------------------------------------------------------------
    ref_intervals, ref_pitches_hz, ref_midi, ref_vel = extract_notes_from_midi(reference_midi_path)
    est_intervals, est_pitches_hz, est_midi, est_vel = extract_notes_from_midi(estimated_midi_path)

    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    # ----------------------------------------------------------------
    # 步骤 2: onset+pitch P/R/F1（不检查 offset）
    # ----------------------------------------------------------------
    # 使用 precision_recall_f1_overlap 但 offset_ratio=None 来忽略 offset 约束
    # 注意：该函数返回 tuple (precision, recall, f_measure, avg_overlap_ratio)
    # NOTE: offset_ratio=None 在运行时合法（mir_eval 会忽略 offset），
    # 但类型桩文件声明为 float，此处通过类型忽略消除静态检查误报。
    onset_pitch_precision, onset_pitch_recall, onset_pitch_f1, _ = \
        mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_pitches_hz,
            est_intervals, est_pitches_hz,
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=None,  # type: ignore[arg-type]  # 不检查 offset
        )

    # ----------------------------------------------------------------
    # 步骤 3: onset+pitch+offset P/R/F1（含 offset 约束）
    # ----------------------------------------------------------------
    full_precision, full_recall, full_f1, _ = \
        mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_pitches_hz,
            est_intervals, est_pitches_hz,
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=offset_ratio,
            offset_min_tolerance=offset_min_tolerance,
        )

    # ----------------------------------------------------------------
    # 步骤 4: 获取匹配对并计算误差分布
    # ----------------------------------------------------------------
    matches = mir_eval.transcription.match_notes(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=offset_ratio,
        offset_min_tolerance=offset_min_tolerance,
    )

    num_matched = len(matches)

    # 提取匹配对的 onset/offset 误差
    onset_errors: List[float] = []
    offset_errors: List[float] = []
    for ref_idx, est_idx in matches:
        onset_err = float(est_intervals[est_idx, 0] - ref_intervals[ref_idx, 0])
        offset_err = float(est_intervals[est_idx, 1] - ref_intervals[ref_idx, 1])
        onset_errors.append(onset_err)
        offset_errors.append(offset_err)

    onset_errors_arr = np.array(onset_errors, dtype=np.float64) if onset_errors else np.array([], dtype=np.float64)
    offset_errors_arr = np.array(offset_errors, dtype=np.float64) if offset_errors else np.array([], dtype=np.float64)

    # 计算 MAE 和 RMSE（单位：秒）
    if len(onset_errors_arr) > 0:
        onset_mae = float(np.mean(np.abs(onset_errors_arr)))
        onset_rmse = float(np.sqrt(np.mean(onset_errors_arr ** 2)))
    else:
        onset_mae = 0.0
        onset_rmse = 0.0

    if len(offset_errors_arr) > 0:
        offset_mae = float(np.mean(np.abs(offset_errors_arr)))
        offset_rmse = float(np.sqrt(np.mean(offset_errors_arr ** 2)))
    else:
        offset_mae = 0.0
        offset_rmse = 0.0

    # 计算 Pearson 相关系数（需要至少 2 个匹配对，且双方方差不能为零）
    onset_pearson: Optional[float] = None
    offset_pearson: Optional[float] = None
    if num_matched >= 2:
        # onset Pearson: 参考 onset vs 估计 onset
        ref_onsets = np.array([float(ref_intervals[m[0], 0]) for m in matches], dtype=np.float64)
        est_onsets = np.array([float(est_intervals[m[1], 0]) for m in matches], dtype=np.float64)
        if np.std(ref_onsets) > 1e-12 and np.std(est_onsets) > 1e-12:
            # scipy >= 1.9 返回 PearsonRResult（支持元组解包为 (r, p)），
            # 类型桩在此处存在误报，显式忽略。
            r_val, _ = scipy_stats.pearsonr(ref_onsets, est_onsets)  # type: ignore[assignment]
            onset_pearson = float(r_val)  # type: ignore[arg-type]

        # offset Pearson: 参考 offset vs 估计 offset
        ref_offsets = np.array([float(ref_intervals[m[0], 1]) for m in matches], dtype=np.float64)
        est_offsets = np.array([float(est_intervals[m[1], 1]) for m in matches], dtype=np.float64)
        if np.std(ref_offsets) > 1e-12 and np.std(est_offsets) > 1e-12:
            r_val, _ = scipy_stats.pearsonr(ref_offsets, est_offsets)  # type: ignore[assignment]
            offset_pearson = float(r_val)  # type: ignore[arg-type]

    # ----------------------------------------------------------------
    # 步骤 5: 构建漏检音符与额外音符清单
    # ----------------------------------------------------------------
    ref_matched_indices: set = {m[0] for m in matches}
    est_matched_indices: set = {m[1] for m in matches}

    # 漏检：参考中有但未匹配到的音符（TP = 参考存在 且 估计匹配上，FN = 参考存在 但未匹配）
    missed_notes: List[Dict[str, Any]] = []
    for i in range(num_ref):
        if i not in ref_matched_indices:
            missed_notes.append({
                "onset_seconds": float(ref_intervals[i, 0]),
                "offset_seconds": float(ref_intervals[i, 1]),
                "pitch_midi": int(ref_midi[i]),
                "pitch_hz": float(ref_pitches_hz[i]),
                "velocity": int(ref_vel[i]),
            })

    # 额外音：估计中有但未匹配到的音符（FP = 估计存在 但无参考匹配）
    extra_notes: List[Dict[str, Any]] = []
    for i in range(num_est):
        if i not in est_matched_indices:
            extra_notes.append({
                "onset_seconds": float(est_intervals[i, 0]),
                "offset_seconds": float(est_intervals[i, 1]),
                "pitch_midi": int(est_midi[i]),
                "pitch_hz": float(est_pitches_hz[i]),
                "velocity": int(est_vel[i]),
            })

    # ----------------------------------------------------------------
    # 步骤 6: 组装结果字典
    # ----------------------------------------------------------------
    result: Dict[str, Any] = {
        # --- 基本信息 ---
        "num_reference_notes": num_ref,
        "num_estimated_notes": num_est,
        "num_matched_notes": num_matched,

        # --- onset+pitch 指标（不含 offset 约束） ---
        "onset_pitch_precision": float(onset_pitch_precision),
        "onset_pitch_recall": float(onset_pitch_recall),
        "onset_pitch_f1": float(onset_pitch_f1),

        # --- onset+pitch+offset 指标（含 offset 约束） ---
        "onset_pitch_offset_precision": float(full_precision),
        "onset_pitch_offset_recall": float(full_recall),
        "onset_pitch_offset_f1": float(full_f1),

        # --- 误差统计（单位：秒） ---
        "onset_error_mae": onset_mae,
        "onset_error_rmse": onset_rmse,
        "onset_error_pearson": onset_pearson,
        "offset_error_mae": offset_mae,
        "offset_error_rmse": offset_rmse,
        "offset_error_pearson": offset_pearson,

        # --- 结构化清单 ---
        "missed_notes": missed_notes,
        "extra_notes": extra_notes,

        # --- 元信息 ---
        "tool_version": TOOL_VERSION,
        "parameters": {
            "onset_tolerance": onset_tolerance,
            "pitch_tolerance": pitch_tolerance,
            "offset_ratio": offset_ratio,
            "offset_min_tolerance": offset_min_tolerance,
            "tool_name": TOOL_NAME,
        },
    }

    return result


# ============================================================================
# Markdown 报告生成
# ============================================================================

def format_report_markdown(result: Dict[str, Any]) -> str:
    """
    将 compute_metrics() 返回的评测结果渲染为 Markdown 报告。

    报告包含：
        - 基本信息概要
        - 转录指标表格（onset+pitch / onset+pitch+offset）
        - 误差分布统计
        - 漏检/额外音符清样（最多 20 个）

    :param result: compute_metrics 返回的字典
    :return: Markdown 格式的报告字符串
    """
    lines: List[str] = []

    # --- 标题 ---
    lines.append("# 转录逐音评测报告")
    lines.append("")

    # --- 基本信息 ---
    lines.append("## 基本信息")
    lines.append("")
    lines.append(f"- 参考音符数: {result['num_reference_notes']}")
    lines.append(f"- 估计音符数: {result['num_estimated_notes']}")
    lines.append(f"- 匹配对数: {result['num_matched_notes']}")
    lines.append(f"- 漏检音符数: {len(result['missed_notes'])}")
    lines.append(f"- 额外音符数: {len(result['extra_notes'])}")
    lines.append("")

    # --- 转录指标 ---
    lines.append("## 转录指标")
    lines.append("")
    lines.append("| 指标 | Onset+Pitch | Onset+Pitch+Offset |")
    lines.append("|------|------------|-------------------|")
    lines.append(
        f"| Precision | {result['onset_pitch_precision']:.4f} | "
        f"{result['onset_pitch_offset_precision']:.4f} |"
    )
    lines.append(
        f"| Recall | {result['onset_pitch_recall']:.4f} | "
        f"{result['onset_pitch_offset_recall']:.4f} |"
    )
    lines.append(
        f"| F1 | {result['onset_pitch_f1']:.4f} | "
        f"{result['onset_pitch_offset_f1']:.4f} |"
    )
    lines.append("")

    # --- 误差分布 ---
    lines.append("## 误差分布（匹配对）")
    lines.append("")
    lines.append("| 指标 | Onset | Offset |")
    lines.append("|------|-------|--------|")
    lines.append(f"| MAE (秒) | {result['onset_error_mae']:.6f} | {result['offset_error_mae']:.6f} |")
    lines.append(f"| RMSE (秒) | {result['onset_error_rmse']:.6f} | {result['offset_error_rmse']:.6f} |")
    onset_pearson_str = f"{result['onset_error_pearson']:.4f}" if result['onset_error_pearson'] is not None else "N/A"
    offset_pearson_str = f"{result['offset_error_pearson']:.4f}" if result['offset_error_pearson'] is not None else "N/A"
    lines.append(f"| Pearson r | {onset_pearson_str} | {offset_pearson_str} |")
    lines.append("")

    # --- 参数 ---
    params = result["parameters"]
    lines.append("## 评测参数")
    lines.append("")
    lines.append(f"- onset 容差: {params['onset_tolerance']} 秒 ({params['onset_tolerance'] * 1000:.0f} ms)")
    lines.append(f"- pitch 容差: {params['pitch_tolerance']} cents")
    lines.append(f"- offset_ratio: {params['offset_ratio']} ({params['offset_ratio'] * 100:.0f}%)")
    lines.append(f"- offset_min_tolerance: {params['offset_min_tolerance']} 秒 ({params['offset_min_tolerance'] * 1000:.0f} ms)")
    lines.append(f"- 工具版本: {result['tool_version']}")
    lines.append("")

    # --- 漏检音符清单 ---
    missed = result["missed_notes"]
    if missed:
        lines.append("## 漏检音符（前 20 个）")
        lines.append("")
        lines.append("| # | Onset (秒) | Offset (秒) | MIDI Pitch | Velocity |")
        lines.append("|---|-----------|------------|-----------|---------|")
        for idx, note in enumerate(missed[:20], start=1):
            lines.append(
                f"| {idx} | {note['onset_seconds']:.3f} | {note['offset_seconds']:.3f} | "
                f"{note['pitch_midi']} | {note['velocity']} |"
            )
        if len(missed) > 20:
            lines.append(f"| ... | 共 {len(missed)} 个漏检音符 |")
        lines.append("")

    # --- 额外音符清单 ---
    extra = result["extra_notes"]
    if extra:
        lines.append("## 额外音符（前 20 个）")
        lines.append("")
        lines.append("| # | Onset (秒) | Offset (秒) | MIDI Pitch | Velocity |")
        lines.append("|---|-----------|------------|-----------|---------|")
        for idx, note in enumerate(extra[:20], start=1):
            lines.append(
                f"| {idx} | {note['onset_seconds']:.3f} | {note['offset_seconds']:.3f} | "
                f"{note['pitch_midi']} | {note['velocity']} |"
            )
        if len(extra) > 20:
            lines.append(f"| ... | 共 {len(extra)} 个额外音符 |")
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# CLI 入口
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI 入口：解析命令行参数，执行评测，输出 JSON 和/或 Markdown 报告。

    用法示例：
        python evaluate_transcription.py \
            --reference ref.midi \
            --estimated est.midi \
            --output-json result.json \
            --output-markdown report.md

    :param argv: 命令行参数列表（用于测试注入），None 则使用 sys.argv
    :return: 退出码，0 表示成功，非 0 表示失败
    """
    parser = argparse.ArgumentParser(
        description="MIDI 逐音转录评测工具 - 基于 mir_eval 的严格比对",
    )

    # 必选参数
    parser.add_argument(
        "--reference", "-r",
        required=True,
        type=str,
        help="金标准参考 MIDI 文件路径（如 MAESTRO performance MIDI）",
    )
    parser.add_argument(
        "--estimated", "-e",
        required=True,
        type=str,
        help="待评测的估计/转录 MIDI 文件路径",
    )

    # 可选输出
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="JSON 报告输出路径",
    )
    parser.add_argument(
        "--output-markdown",
        type=str,
        default=None,
        help="Markdown 报告输出路径",
    )

    # 容差参数
    parser.add_argument(
        "--onset-tolerance",
        type=float,
        default=DEFAULT_ONSET_TOLERANCE,
        help=f"onset 容差，单位秒（默认 {DEFAULT_ONSET_TOLERANCE}）",
    )
    parser.add_argument(
        "--pitch-tolerance",
        type=float,
        default=DEFAULT_PITCH_TOLERANCE,
        help=f"音高容差，单位 cents（默认 {DEFAULT_PITCH_TOLERANCE}）",
    )
    parser.add_argument(
        "--offset-ratio",
        type=float,
        default=DEFAULT_OFFSET_RATIO,
        help=f"offset 容差比例（默认 {DEFAULT_OFFSET_RATIO}）",
    )
    parser.add_argument(
        "--offset-min-tolerance",
        type=float,
        default=DEFAULT_OFFSET_MIN_TOLERANCE,
        help=f"offset 最小容差，单位秒（默认 {DEFAULT_OFFSET_MIN_TOLERANCE}）",
    )

    args = parser.parse_args(argv)

    # 验证输入文件存在（先于核心逻辑，快速失败）
    ref_path = Path(args.reference)
    est_path = Path(args.estimated)
    if not ref_path.exists():
        print(f"错误: 参考 MIDI 文件不存在: {ref_path}", file=sys.stderr)
        return 1
    if not est_path.exists():
        print(f"错误: 估计 MIDI 文件不存在: {est_path}", file=sys.stderr)
        return 1

    # 如果两个输出都没指定也没有任何意义
    if args.output_json is None and args.output_markdown is None:
        print("警告: 未指定任何输出文件，仅将指标写入 stdout", file=sys.stderr)

    # 执行核心评测
    try:
        result = compute_metrics(
            reference_midi_path=ref_path,
            estimated_midi_path=est_path,
            onset_tolerance=args.onset_tolerance,
            pitch_tolerance=args.pitch_tolerance,
            offset_ratio=args.offset_ratio,
            offset_min_tolerance=args.offset_min_tolerance,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"未预期的错误: {exc}", file=sys.stderr)
        return 1

    # 输出 JSON
    if args.output_json:
        json_path = Path(args.output_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        # 自定义 JSON 序列化：处理 numpy 类型和 None/NaN
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=_json_serializer)
        print(f"JSON 报告已写入: {json_path}")

    # 输出 Markdown
    if args.output_markdown:
        md_path = Path(args.output_markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_content = format_report_markdown(result)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"Markdown 报告已写入: {md_path}")

    # 如果无输出文件，打印核心指标到 stdout
    if args.output_json is None and args.output_markdown is None:
        print(f"onset_pitch F1: {result['onset_pitch_f1']:.4f}")
        print(f"onset_pitch_offset F1: {result['onset_pitch_offset_f1']:.4f}")
        print(f"onset MAE: {result['onset_error_mae']:.6f}s")

    return 0


def _json_serializer(obj: Any) -> Any:
    """
    JSON 自定义序列化器：处理 numpy 数值类型和 NaN/Inf 值。

    确保 JSON 输出合法且可被 standard json.load 解析。
    """
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        if np.isnan(val):
            return None
        if np.isinf(val):
            return None
        return val
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, float):
        if np.isnan(obj):
            return None
        if np.isinf(obj):
            return None
    raise TypeError(f"无法序列化的类型: {type(obj)}")


# ============================================================================
# 脚本入口
# ============================================================================

if __name__ == "__main__":
    sys.exit(main())
