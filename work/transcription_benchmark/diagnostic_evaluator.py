"""
模块名称：diagnostic_evaluator.py
功能描述：
    面向 MAESTRO 参考 MIDI（A）与 Transkun 估计 MIDI（B/C）的诊断性逐音评测器。
    核心目的：将音高内容错误与起音/时值/速度/时序错误分离，提供独立诊断指标。

    评测维度（严格分离，不混淆）：
        raw_metrics — 端到端质量（原始比对，无对齐）
            onset_only P/R/F1（仅时间匹配，不计音高）
            onset_pitch P/R/F1（时间+音高，不计 offset）
            onset_pitch_offset P/R/F1（时间+音高+offset）
            velocity_aware P/R/F1（匹配对 velocity 容差）
            frame_piano_roll P/R/accuracy（帧级音高内容）

        diagnostic_metrics — 根因诊断（标注 Diagnostic，不作为端点成绩）
            global_offset — 网格搜索最优恒定时间偏移
            global_scale — 网格搜索最优全局线性缩放因子 α
            scale_then_offset — α 缩放后搜索最优 residual offset
            local_alignment — 分段滑动窗口对齐（局部 tempo 漂移诊断）

        error_buckets — 未匹配音符分类（不声称"X% 是音高错误"）
            已匹配但 offset 不满足者（nearly matched）
            正确起音/错误音高
            正确音高/错误起音
            纯漏检/纯插入
            分段边界集中度

    正确校准预期：
        C MIDI 的 tempo 从 120 BPM 被覆盖为检测值（117.5/112.3）但未 retiming ticks，
        因此 C 的绝对时间被拉伸因子 120/detected_BPM。
        global_scale 应恢复因子 detected_BPM/120：
            巴赫 ≈ 117.5/120 = 0.979，肖邦 ≈ 112.3/120 = 0.936

主要组件：
    - hash_file_sha256()
    - extract_notes_from_midi()
    - compute_onset_only_metrics()
    - compute_global_offset_calibration()
    - compute_global_scale_calibration()
    - compute_global_scale_offset_combined()
    - compute_local_alignment()
    - compute_frame_level_piano_roll()
    - compute_velocity_metrics()
    - compute_error_buckets()
    - compute_all_diagnostics()
    - main()

依赖说明：
    - pretty_midi (>=0.2.10): MIDI 解析
    - mir_eval (0.8.2): 转录评测标准库
    - numpy (>=1.20): 数组运算
    - scipy (>=1.7): 线性回归
    - hashlib, json, argparse, pathlib: 内置

作者：JucieOvo
创建日期：2026-07-10
修改记录：
    - 2026-07-10 JucieOvo: 初始版本 TDD GREEN
    - 2026-07-10 JucieOvo: 重写校准逻辑（网格搜索替代回归），新增 local_alignment，
      移除所有 type: ignore，修正错误分桶语义
    - 2026-07-10 JucieOvo: 局部对齐改为全局仿射校准后的残差诊断，
      修正窗口 offset 符号与应用逻辑，并增加 0.0001 精细缩放搜索
    - 2026-07-10 JucieOvo: velocity-aware 指标改用 mir_eval 标准归一化与全局缩放协议
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import mir_eval
import numpy as np
import pretty_midi

# ---------------------------------------------------------------------------
# 工具版本常量
# ---------------------------------------------------------------------------
TOOL_VERSION = "2.2.0"
TOOL_NAME = "diagnostic_evaluator"

# 用于 mir_eval 的哨兵值：当不需要 offset 约束时，使用极大容差使其恒成立
# mir_eval 运行时支持 offset_ratio=None，但类型桩要求 float，故用 1e9 替代
_NO_OFFSET_CONSTRAINT = 1e9

# 默认容差值（MIREX 标准）
DEFAULT_ONSET_TOLERANCE = 0.05       # 50 ms
DEFAULT_PITCH_TOLERANCE = 50.0       # 50 cents
DEFAULT_OFFSET_RATIO = 0.2           # 20%
DEFAULT_OFFSET_MIN_TOLERANCE = 0.05  # 50 ms

# velocity 容差（MIDI velocity 值域 0-127）
VELOCITY_TOLERANCE = 0.1

# frame-level piano-roll 帧率
PIANO_ROLL_FRAME_RATE = 100.0

# ---------------------------------------------------------------------------
# 全局偏移校准搜索范围
# ---------------------------------------------------------------------------
OFFSET_SEARCH_MIN = -10.0
OFFSET_SEARCH_MAX = 10.0
OFFSET_SEARCH_STEP = 0.02  # 20 ms

# ---------------------------------------------------------------------------
# 全局缩放校准搜索范围
# ---------------------------------------------------------------------------
SCALE_ALPHA_MIN = 0.8
SCALE_ALPHA_MAX = 1.2
SCALE_ALPHA_STEP = 0.002
SCALE_ALPHA_FINE_STEP = 0.0001

# ---------------------------------------------------------------------------
# 局部对齐参数
# ---------------------------------------------------------------------------
LOCAL_WINDOW_SIZE_SECONDS = 5.0     # 每个局部窗口尺寸
LOCAL_OFFSET_RANGE = 2.0              # 局部偏移搜索范围 ±2s
LOCAL_OFFSET_STEP = 0.05              # 局部偏移步长 50ms

# 分段边界错误浓度默认分段尺寸
DEFAULT_SEGMENT_SIZE_SECONDS = 10.0
SEGMENT_BOUNDARY_WINDOW = 1.0


# ============================================================================
# 工具函数
# ============================================================================

def hash_file_sha256(file_path: Union[str, Path]) -> str:
    """
    计算文件的 SHA-256 哈希值，返回大写的十六进制字符串。

    :param file_path: 文件路径
    :return: 64 位十六进制 SHA-256 字符串（大写）
    """
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest().upper()


# ============================================================================
# MIDI 音符提取
# ============================================================================

def extract_notes_from_midi(
        midi_path: Union[str, Path],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    从 MIDI 文件中提取所有非鼓轨道音符的 onset、offset、pitch（Hz）和 velocity。

    :param midi_path: MIDI 文件路径
    :return: 四元组 (intervals, pitches_hz, midi_pitches, velocities)
        - intervals: 形状 (N, 2) 的 float64 数组，列为 [onset, offset]，单位秒
        - pitches_hz: 形状 (N,) 的 float64 数组，单位 Hz
        - midi_pitches: 形状 (N,) 的 int64 数组，MIDI 音高号
        - velocities: 形状 (N,) 的 int64 数组，力度值 0-127
    :raises FileNotFoundError: 文件不存在
    :raises ValueError: MIDI 无音符
    """
    midi_path = Path(midi_path)
    if not midi_path.exists():
        raise FileNotFoundError(f"MIDI 文件不存在: {midi_path}")

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    raw_notes: List[Tuple[float, float, int, int]] = []
    for instrument in pm.instruments:
        if instrument.is_drum:
            continue
        for note in instrument.notes:
            raw_notes.append((note.start, note.end, note.pitch, note.velocity))

    if len(raw_notes) == 0:
        raise ValueError(f"MIDI 文件中无有效音符事件: {midi_path}")

    raw_notes.sort(key=lambda x: (x[0], x[2]))

    intervals = np.array([[n[0], n[1]] for n in raw_notes], dtype=np.float64)
    midi_pitches = np.array([n[2] for n in raw_notes], dtype=np.int64)
    velocities = np.array([n[3] for n in raw_notes], dtype=np.int64)
    pitches_hz = 440.0 * (2.0 ** ((midi_pitches.astype(np.float64) - 69.0) / 12.0))

    return intervals, pitches_hz, midi_pitches, velocities


# ============================================================================
# 残差计算
# ============================================================================

def _compute_residual_summary(errors: np.ndarray) -> Dict[str, Optional[float]]:
    """
    对一维误差数组计算汇总统计量：median、MAE、RMSE、P95、mean、std。

    :param errors: 一维误差数组，单位秒
    :return: 包含各统计量的字典，数组为空时所有值为 None
    """
    if len(errors) == 0:
        return {
            "median": None, "mae": None, "rmse": None,
            "p95": None, "mean": None, "std": None,
        }
    abs_errors = np.abs(errors)
    return {
        "median": float(np.median(errors)),
        "mae": float(np.mean(abs_errors)),
        "rmse": float(np.sqrt(np.mean(errors ** 2))),
        "p95": float(np.percentile(abs_errors, 95)),
        "mean": float(np.mean(errors)),
        "std": float(np.std(errors, ddof=1)) if len(errors) > 1 else 0.0,
    }


# ============================================================================
# onset-only 指标
# ============================================================================

def compute_onset_only_metrics(
        ref_intervals: np.ndarray,
        est_intervals: np.ndarray,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
) -> Dict[str, Any]:
    """
    仅基于起音时间匹配的 P/R/F1，使用 mir_eval.transcription.match_note_onsets()。

    注意：此函数使用贪心一键匹配算法，与 onset+pitch 的
    precision_recall_f1_overlap 最优化匹配算法不同。
    两者数字不可直接相减得出"音高错误率"——仅指示相对趋势。

    :param ref_intervals: 参考音符区间 (N, 2)
    :param est_intervals: 估计音符区间 (M, 2)
    :param onset_tolerance: onset 容差，单位秒
    :return: 字典 {"precision", "recall", "f1", "num_matched"}
    """
    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    if num_ref == 0 or num_est == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "num_matched": 0}

    matches: List[Tuple[int, int]] = mir_eval.transcription.match_note_onsets(
        ref_intervals, est_intervals, onset_tolerance=onset_tolerance,
    )

    num_matched = len(matches)
    precision = num_matched / num_est if num_est > 0 else 0.0
    recall = num_matched / num_ref if num_ref > 0 else 0.0
    f1 = mir_eval.util.f_measure(precision, recall)

    return {
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "num_matched": num_matched,
    }


# ============================================================================
# 全局偏移校准（诊断 E4）
# ============================================================================

def compute_global_offset_calibration(
        ref_intervals: np.ndarray,
        ref_pitches_hz: np.ndarray,
        est_intervals: np.ndarray,
        est_pitches_hz: np.ndarray,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
) -> Dict[str, Optional[float]]:
    """
    网格搜索最佳全局恒定时间偏移，最大化 onset+pitch F1（不含 offset 约束）。

    对估计 MIDI 的 onset/offset 统一施加偏移 Δt，在 [OFFSET_SEARCH_MIN, OFFSET_SEARCH_MAX]
    范围内搜索，步长 OFFSET_SEARCH_STEP。
    使用 onset+pitch 匹配（offset_ratio=None），仅衡量时间对齐改善程度。

    Diagnostic: 此结果指示是否存在固定录音链路延迟，不作为端点成绩。

    :return: {"best_shift_seconds": float|None, "best_f1": float|None}
    """
    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    if num_ref == 0 or num_est == 0:
        return {"best_shift_seconds": None, "best_f1": None}

    ref_duration = float(ref_intervals[-1, 1])
    search_min = max(OFFSET_SEARCH_MIN, -ref_duration)
    search_max = min(OFFSET_SEARCH_MAX, ref_duration)

    best_shift: Optional[float] = None
    best_f1: float = -1.0

    for shift in np.arange(search_min, search_max + OFFSET_SEARCH_STEP * 0.5, OFFSET_SEARCH_STEP):
        shift = float(shift)
        shifted = est_intervals.copy()
        shifted[:, 0] += shift
        shifted[:, 1] += shift

        valid = shifted[:, 0] >= 0
        if not np.any(valid):
            continue

        _, _, f1_val, _ = mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_pitches_hz,
            shifted[valid], est_pitches_hz[valid],
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=_NO_OFFSET_CONSTRAINT,
            offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
        )
        if f1_val > best_f1:
            best_f1 = float(f1_val)
            best_shift = shift

    if best_shift is None:
        return {"best_shift_seconds": None, "best_f1": None}

    return {"best_shift_seconds": best_shift, "best_f1": best_f1}


# ============================================================================
# 全局缩放校准（诊断 E5）
# ============================================================================

def compute_global_scale_calibration(
        ref_intervals: np.ndarray,
        ref_pitches_hz: np.ndarray,
        est_intervals: np.ndarray,
        est_pitches_hz: np.ndarray,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
) -> Dict[str, Optional[float]]:
    """
    网格搜索最佳全局线性缩放因子 α，最大化 onset+pitch F1（不含 offset 约束）。

    将估计音符的 onset/offset 缩放为 est_time_scaled = est_time * α，
    在 [SCALE_ALPHA_MIN, SCALE_ALPHA_MAX] 范围内搜索，步长 SCALE_ALPHA_STEP。

    预期恢复 tempo 覆盖导致的时间拉伸：
        巴赫 ≈ 117.5/120 = 0.979，肖邦 ≈ 112.3/120 = 0.936

    Diagnostic: 此结果指示是否存在全局时钟缩放问题，不作为端点成绩。

    :return: {"best_alpha": float|None, "best_f1": float|None}
    """
    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    if num_ref == 0 or num_est == 0:
        return {"best_alpha": None, "best_f1": None}

    best_alpha: Optional[float] = None
    best_f1: float = -1.0

    def evaluate_alpha(alpha_value: float) -> float:
        """
        计算单个缩放候选的 onset+pitch F1。

        :param alpha_value: 应用于估计时间轴的缩放因子
        :return: 对应的 onset+pitch F1
        """
        scaled = est_intervals.copy()
        scaled[:, 0] *= alpha_value
        scaled[:, 1] *= alpha_value

        valid = scaled[:, 0] >= 0
        if not np.any(valid):
            return -1.0

        _, _, f1_value, _ = mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_pitches_hz,
            scaled[valid], est_pitches_hz[valid],
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=_NO_OFFSET_CONSTRAINT,
            offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
        )
        return float(f1_value)

    # 第一阶段：按 0.002 扫描完整范围，定位候选峰值。
    for alpha in np.arange(SCALE_ALPHA_MIN, SCALE_ALPHA_MAX + SCALE_ALPHA_STEP * 0.5, SCALE_ALPHA_STEP):
        alpha = float(alpha)
        f1_val = evaluate_alpha(alpha)
        if f1_val > best_f1:
            best_f1 = f1_val
            best_alpha = alpha

    if best_alpha is None:
        return {"best_alpha": None, "best_f1": None}

    # 第二阶段：围绕粗搜索峰值按 0.0001 精排。长曲目中 0.001 的误差
    # 会在尾部累积为数百毫秒，因此不能用粗网格结果代表最终时间校准。
    fine_min = max(SCALE_ALPHA_MIN, best_alpha - SCALE_ALPHA_STEP)
    fine_max = min(SCALE_ALPHA_MAX, best_alpha + SCALE_ALPHA_STEP)
    for alpha in np.arange(fine_min, fine_max + SCALE_ALPHA_FINE_STEP * 0.5, SCALE_ALPHA_FINE_STEP):
        alpha = float(alpha)
        f1_val = evaluate_alpha(alpha)
        if f1_val > best_f1:
            best_f1 = f1_val
            best_alpha = alpha

    return {"best_alpha": best_alpha, "best_f1": best_f1}


def compute_global_scale_offset_combined(
        ref_intervals: np.ndarray,
        ref_pitches_hz: np.ndarray,
        est_intervals: np.ndarray,
        est_pitches_hz: np.ndarray,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
) -> Dict[str, Optional[float]]:
    """
    组合校准：先找最佳 α（缩放），再在缩放后数据上找最佳 offset（平移）。

    两步策略避免 O(n²) 网格搜索，同时提供更精确的校准：
        step 1: compute_global_scale_calibration() → best_alpha
        step 2: 在 α 缩放后数据上 compute_global_offset_calibration() → residual_shift

    Diagnostic: 此结果同时诊断了时钟缩放和固定延迟，不作为端点成绩。

    :return: {"best_alpha": float|None, "residual_offset_seconds": float|None, "combined_f1": float|None}
    """
    scale_result = compute_global_scale_calibration(
        ref_intervals, ref_pitches_hz, est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance, pitch_tolerance=pitch_tolerance,
    )
    best_alpha = scale_result.get("best_alpha")
    if best_alpha is None:
        return {"best_alpha": None, "residual_offset_seconds": None, "combined_f1": None}

    # 应用缩放
    scaled = est_intervals.copy()
    scaled[:, 0] *= best_alpha
    scaled[:, 1] *= best_alpha

    # 在缩放后数据上搜索最佳偏移
    offset_result = compute_global_offset_calibration(
        ref_intervals, ref_pitches_hz, scaled, est_pitches_hz,
        onset_tolerance=onset_tolerance, pitch_tolerance=pitch_tolerance,
    )

    return {
        "best_alpha": best_alpha,
        "residual_offset_seconds": offset_result.get("best_shift_seconds"),
        "combined_f1": offset_result.get("best_f1"),
    }


# ============================================================================
# 局部对齐诊断（诊断 E6）
# ============================================================================

def compute_local_alignment(
        ref_intervals: np.ndarray,
        ref_pitches_hz: np.ndarray,
        est_intervals: np.ndarray,
        est_pitches_hz: np.ndarray,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
        window_size: float = LOCAL_WINDOW_SIZE_SECONDS,
        offset_range: float = LOCAL_OFFSET_RANGE,
        offset_step: float = LOCAL_OFFSET_STEP,
) -> Dict[str, Any]:
    """
    分段滑动窗口局部对齐诊断。

    将参考时间线划分为固定尺寸窗口，对每个窗口独立搜索最佳局部偏移，
    然后应用局部偏移后计算全局 onset+pitch F1。调用方应先完成全局
    scale+offset 校准，因此本函数只测量剩余的局部时间漂移。

    工作流程：
        1. 将 ref 时间线划分为 window_size 秒窗口
        2. 对每个窗口 w，在当前窗口内收集 ref 音符
        3. 对候选偏移 o ∈ [-offset_range, offset_range]，收集对应偏移后 est 窗口内音符
        4. 选择使窗口内 onset 匹配数最大的偏移
        5. 每窗口的最佳偏移组成局部偏移数组
        6. 应用全部局部偏移后重新计算全局 onset+pitch F1

    Diagnostic: 如果局部偏移方差大，说明存在局部 tempo 漂移；
    如果对齐后 F1 显著提升，说明时间对齐是主要问题源。

    :return: {
        "locally_aligned_f1": float|None,
        "num_windows": int,
        "local_offset_std_seconds": float|None,
        "local_offset_mean_seconds": float|None,
        "f1_improvement_over_raw": float|None,
    }
    """
    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    if num_ref == 0 or num_est == 0:
        return {
            "locally_aligned_f1": None, "num_windows": 0,
            "local_offset_std_seconds": None, "local_offset_mean_seconds": None,
            "f1_improvement_over_raw": None,
        }

    ref_duration = float(ref_intervals[-1, 1])
    # 窗口数量
    num_windows = max(1, int(np.ceil(ref_duration / window_size)))

    # 参考 onset 序列
    ref_onsets = ref_intervals[:, 0]
    est_onsets = est_intervals[:, 0]

    window_offsets: List[float] = []

    for w in range(num_windows):
        win_start = w * window_size
        win_end = win_start + window_size

        # 收集此窗口内的参考音符索引
        ref_in_win = np.where((ref_onsets >= win_start) & (ref_onsets < win_end))[0]
        if len(ref_in_win) == 0:
            window_offsets.append(0.0)
            continue

        best_offset: float = 0.0
        best_matches: int = 0

        # 搜索最佳局部偏移
        for offset in np.arange(-offset_range, offset_range + offset_step * 0.5, offset_step):
            offset = float(offset)
            # 候选 offset 会被加到估计时间上，因此只有满足
            # est_onset + offset ∈ [win_start, win_end) 的音符属于当前窗口。
            est_in_win = np.where(
                (est_onsets >= win_start - offset) & (est_onsets < win_end - offset)
            )[0]
            if len(est_in_win) == 0:
                continue

            # 对候选 offset 实际平移窗口内估计音符，再按 onset+pitch 计数。
            # 仅改变窗口筛选范围而不平移音符会让搜索目标与最终应用脱节。
            ref_win_subset = ref_intervals[ref_in_win]
            est_win_subset = est_intervals[est_in_win].copy()
            est_win_subset[:, 0] += offset
            est_win_subset[:, 1] += offset

            matches_count = len(mir_eval.transcription.match_notes(
                ref_win_subset,
                ref_pitches_hz[ref_in_win],
                est_win_subset,
                est_pitches_hz[est_in_win],
                onset_tolerance=onset_tolerance,
                pitch_tolerance=pitch_tolerance,
                offset_ratio=_NO_OFFSET_CONSTRAINT,
                offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
            ))

            if matches_count > best_matches:
                best_matches = matches_count
                best_offset = offset

        window_offsets.append(best_offset)

    # 应用局部偏移：对每个估计音符，使用其所在窗口的偏移
    offset_array = np.array(window_offsets, dtype=np.float64)
    # 扩展为 per-note 偏移：使用 note onset 对应窗口
    note_offsets = (est_onsets / window_size).astype(np.int64)
    note_offsets = np.clip(note_offsets, 0, num_windows - 1)
    per_note_offset = offset_array[note_offsets]

    aligned_intervals = est_intervals.copy()
    aligned_intervals[:, 0] += per_note_offset
    aligned_intervals[:, 1] += per_note_offset

    valid = aligned_intervals[:, 0] >= 0
    if not np.any(valid):
        return {
            "locally_aligned_f1": None, "num_windows": num_windows,
            "local_offset_std_seconds": float(np.std(offset_array, ddof=1)) if len(offset_array) > 1 else 0.0,
            "local_offset_mean_seconds": float(np.mean(offset_array)),
            "f1_improvement_over_raw": None,
        }

    _, _, aligned_f1, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals, ref_pitches_hz,
        aligned_intervals[valid], est_pitches_hz[valid],
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=_NO_OFFSET_CONSTRAINT,
        offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
    )

    # 计算原始 F1 用于对比
    _, _, raw_f1, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=_NO_OFFSET_CONSTRAINT,
        offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
    )

    improvement = float(aligned_f1) - float(raw_f1) if aligned_f1 is not None else None

    return {
        "locally_aligned_f1": float(aligned_f1),
        "num_windows": num_windows,
        "local_offset_std_seconds": float(np.std(offset_array, ddof=1)) if len(offset_array) > 1 else 0.0,
        "local_offset_mean_seconds": float(np.mean(offset_array)),
        "baseline_f1": float(raw_f1),
        "f1_improvement_over_raw": improvement,
    }


# ============================================================================
# 帧级 piano-roll 指标
# ============================================================================

def _midi_to_piano_roll_frames(
        midi_path: Union[str, Path],
        frame_rate: float = PIANO_ROLL_FRAME_RATE,
) -> Tuple[np.ndarray, List[np.ndarray]]:
    """
    将 MIDI 文件的音符事件转换为帧级活跃音高频率列表。

    :param midi_path: MIDI 文件路径
    :param frame_rate: 帧率 Hz（默认 100）
    :return: (time_array, freq_list)
    """
    intervals, pitches_hz, _, _ = extract_notes_from_midi(midi_path)

    if len(intervals) == 0:
        return np.array([], dtype=np.float64), []

    duration = float(intervals[-1, 1])
    hop = 1.0 / frame_rate
    num_frames = max(1, int(np.ceil(duration / hop)))
    time_array = np.arange(num_frames, dtype=np.float64) * hop + hop / 2.0

    freq_list: List[np.ndarray] = []
    for frame_idx in range(num_frames):
        frame_start = frame_idx * hop
        frame_end = frame_start + hop
        active_mask = (intervals[:, 0] <= frame_end) & (intervals[:, 1] >= frame_start)
        freq_list.append(pitches_hz[active_mask].copy())

    return time_array, freq_list


def compute_frame_level_piano_roll(
        ref_midi_path: Union[str, Path],
        est_midi_path: Union[str, Path],
        frame_rate: float = PIANO_ROLL_FRAME_RATE,
) -> Dict[str, float]:
    """
    帧级 piano-roll 评测，使用 mir_eval.multipitch.metrics()。
    两 MIDI 分别转为帧级 piano-roll 后取最小帧数对齐。

    注意：帧级评测不依赖时序精确对齐，反映音高内容的整体存在性匹配。

    :param ref_midi_path: 参考 MIDI 文件路径
    :param est_midi_path: 估计 MIDI 文件路径
    :param frame_rate: 帧率 Hz
    :return: 帧级指标字典
    """
    ref_time, ref_freqs = _midi_to_piano_roll_frames(ref_midi_path, frame_rate=frame_rate)
    est_time, est_freqs = _midi_to_piano_roll_frames(est_midi_path, frame_rate=frame_rate)

    if len(ref_time) == 0 or len(est_time) == 0:
        return {
            "precision": 0.0, "recall": 0.0, "accuracy": 0.0,
            "e_sub": 0.0, "e_miss": 0.0, "e_fa": 0.0, "e_tot": 0.0,
            "precision_chroma": 0.0, "recall_chroma": 0.0, "accuracy_chroma": 0.0,
        }

    min_frames = min(len(ref_time), len(est_time))
    scores = mir_eval.multipitch.metrics(
        ref_time[:min_frames], ref_freqs[:min_frames],
        est_time[:min_frames], est_freqs[:min_frames],
    )
    return {
        "precision": float(scores[0]),
        "recall": float(scores[1]),
        "accuracy": float(scores[2]),
        "e_sub": float(scores[3]),
        "e_miss": float(scores[4]),
        "e_fa": float(scores[5]),
        "e_tot": float(scores[6]),
        "precision_chroma": float(scores[7]),
        "recall_chroma": float(scores[8]),
        "accuracy_chroma": float(scores[9]),
    }


# ============================================================================
# velocity-aware 指标
# ============================================================================

def compute_velocity_metrics(
        ref_intervals: np.ndarray,
        ref_pitches_hz: np.ndarray,
        ref_vel: np.ndarray,
        est_intervals: np.ndarray,
        est_pitches_hz: np.ndarray,
        est_vel: np.ndarray,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
        offset_ratio: float = DEFAULT_OFFSET_RATIO,
        offset_min_tolerance: float = DEFAULT_OFFSET_MIN_TOLERANCE,
        velocity_tolerance: float = VELOCITY_TOLERANCE,
) -> Dict[str, Any]:
    """
    使用 mir_eval 标准协议计算 velocity-aware 指标。

    mir_eval 会先把参考力度归一化到 [0, 1]，再对估计力度执行全局
    线性缩放，最后按 velocity_tolerance 判定力度是否匹配。该协议不能
    用原始 MIDI velocity 的固定整数差替代。

    :return: {"precision", "recall", "f1", "num_velocity_matched"}
    """
    precision, recall, f1, _ = (
        mir_eval.transcription_velocity.precision_recall_f1_overlap(
            ref_intervals,
            ref_pitches_hz,
            ref_vel,
            est_intervals,
            est_pitches_hz,
            est_vel,
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=offset_ratio,
            offset_min_tolerance=offset_min_tolerance,
            velocity_tolerance=velocity_tolerance,
        )
    )
    velocity_matched = int(round(float(precision) * len(est_intervals)))

    return {
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "num_velocity_matched": velocity_matched,
    }


# ============================================================================
# 错误分桶
# ============================================================================

def compute_error_buckets(
        ref_intervals: np.ndarray,
        ref_pitches_hz: np.ndarray,
        est_intervals: np.ndarray,
        est_pitches_hz: np.ndarray,
        matches_full: List[Tuple[int, int]],
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
        segment_size_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    对未匹配音符按错误类型细分类，不声称两类算法间的直接差值为"音高错误率"。

    分类规则（对每个未匹配的参考音符，独立判断）：
        1. nearly_matched: 存在 onset+pitch 均匹配的估计音符，但因 offset 不满足未入选
        2. correct_onset_wrong_pitch: 存在 onset 匹配但无 pitch 匹配的估计音符
        3. correct_pitch_wrong_onset: 存在 pitch 匹配但无 onset 匹配的估计音符
        4. pure_miss: 以上均不满足

    估计侧未匹配音符同理分类。
    segment_boundary_concentration: 漏检/插入在分段边界（±SEGMENT_BOUNDARY_WINDOW 秒）的占比。

    :return: 分桶计数字典
    """
    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    ref_matched_indices: set = {m[0] for m in matches_full}
    est_matched_indices: set = {m[1] for m in matches_full}

    # cents 转 Hz 比率
    pitch_ratio_tol = 2.0 ** (pitch_tolerance / 1200.0) - 1.0

    # ----------------------------------------------------------------
    # 参考侧分类
    # ----------------------------------------------------------------
    nearly_matched_ref = 0
    correct_onset_wrong_pitch_ref = 0
    correct_pitch_wrong_onset_ref = 0
    pure_miss_ref = 0

    for i in range(num_ref):
        if i in ref_matched_indices:
            continue

        has_onset_match = np.any(np.abs(est_intervals[:, 0] - ref_intervals[i, 0]) <= onset_tolerance)
        pitch_ratio = np.abs(est_pitches_hz - ref_pitches_hz[i]) / max(ref_pitches_hz[i], 1e-6)
        has_pitch_match = np.any(pitch_ratio <= pitch_ratio_tol)
        # 检查是否同时满足 onset+pitch（即 nearly matched，仅 offset 不满足）
        onset_match_mask = np.abs(est_intervals[:, 0] - ref_intervals[i, 0]) <= onset_tolerance
        pitch_match_mask = pitch_ratio <= pitch_ratio_tol
        has_onset_pitch_match = np.any(onset_match_mask & pitch_match_mask)

        if has_onset_pitch_match:
            nearly_matched_ref += 1
        elif has_onset_match:
            correct_onset_wrong_pitch_ref += 1
        elif has_pitch_match:
            correct_pitch_wrong_onset_ref += 1
        else:
            pure_miss_ref += 1

    # ----------------------------------------------------------------
    # 估计侧分类
    # ----------------------------------------------------------------
    nearly_matched_est = 0
    correct_onset_wrong_pitch_est = 0
    correct_pitch_wrong_onset_est = 0
    pure_insertion = 0

    for i in range(num_est):
        if i in est_matched_indices:
            continue

        has_onset_match = np.any(np.abs(ref_intervals[:, 0] - est_intervals[i, 0]) <= onset_tolerance)
        pitch_ratio = np.abs(ref_pitches_hz - est_pitches_hz[i]) / max(est_pitches_hz[i], 1e-6)
        has_pitch_match = np.any(pitch_ratio <= pitch_ratio_tol)

        onset_match_mask = np.abs(ref_intervals[:, 0] - est_intervals[i, 0]) <= onset_tolerance
        pitch_match_mask = pitch_ratio <= pitch_ratio_tol
        has_onset_pitch_match = np.any(onset_match_mask & pitch_match_mask)

        if has_onset_pitch_match:
            nearly_matched_est += 1
        elif has_onset_match:
            correct_onset_wrong_pitch_est += 1
        elif has_pitch_match:
            correct_pitch_wrong_onset_est += 1
        else:
            pure_insertion += 1

    # ----------------------------------------------------------------
    # 分段边界集中度
    # ----------------------------------------------------------------
    seg_size = segment_size_seconds if segment_size_seconds is not None else DEFAULT_SEGMENT_SIZE_SECONDS
    seg_size = max(seg_size, 1.0)

    total_errors = pure_miss_ref + pure_insertion
    near_boundary = 0

    if total_errors > 0:
        error_onsets: List[float] = []
        for i in range(num_ref):
            if i not in ref_matched_indices:
                error_onsets.append(float(ref_intervals[i, 0]))
        for i in range(num_est):
            if i not in est_matched_indices:
                error_onsets.append(float(est_intervals[i, 0]))

        half_window = SEGMENT_BOUNDARY_WINDOW
        for onset in error_onsets:
            dist = min(onset % seg_size, seg_size - (onset % seg_size))
            if dist <= half_window:
                near_boundary += 1

    concentration = near_boundary / total_errors if total_errors > 0 else 0.0

    return {
        "nearly_matched_ref_offset_only": nearly_matched_ref,
        "correct_onset_wrong_pitch_ref": correct_onset_wrong_pitch_ref,
        "correct_pitch_wrong_onset_ref": correct_pitch_wrong_onset_ref,
        "pure_miss_ref": pure_miss_ref,
        "nearly_matched_est_offset_only": nearly_matched_est,
        "correct_onset_wrong_pitch_est": correct_onset_wrong_pitch_est,
        "correct_pitch_wrong_onset_est": correct_pitch_wrong_onset_est,
        "pure_insertion_est": pure_insertion,
        "total_unmatched_ref": num_ref - len(ref_matched_indices),
        "total_unmatched_est": num_est - len(est_matched_indices),
        "segment_boundary_error_concentration": float(concentration),
        "segment_size_used_seconds": seg_size,
    }


# ============================================================================
# 主编排函数
# ============================================================================

def compute_all_diagnostics(
        reference_midi_path: Union[str, Path],
        estimated_midi_path: Union[str, Path],
        *,
        onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
        pitch_tolerance: float = DEFAULT_PITCH_TOLERANCE,
        offset_ratio: float = DEFAULT_OFFSET_RATIO,
        offset_min_tolerance: float = DEFAULT_OFFSET_MIN_TOLERANCE,
        segment_size_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    主编排函数：执行全部原始指标、诊断指标和错误分桶。

    :return: 完整评测字典
    """
    ref_path = Path(reference_midi_path)
    est_path = Path(estimated_midi_path)

    # 步骤 1: 文件存在性与哈希
    if not ref_path.exists():
        raise FileNotFoundError(f"参考 MIDI 文件不存在: {ref_path}")
    if not est_path.exists():
        raise FileNotFoundError(f"估计 MIDI 文件不存在: {est_path}")

    ref_sha256 = hash_file_sha256(ref_path)
    est_sha256 = hash_file_sha256(est_path)

    # 步骤 2: 音符提取
    ref_intervals, ref_pitches_hz, ref_midi_pitches, ref_vel = extract_notes_from_midi(ref_path)
    est_intervals, est_pitches_hz, est_midi_pitches, est_vel = extract_notes_from_midi(est_path)

    num_ref = len(ref_intervals)
    num_est = len(est_intervals)

    # 步骤 3: onset-only 指标
    onset_only = compute_onset_only_metrics(
        ref_intervals, est_intervals, onset_tolerance=onset_tolerance,
    )

    # 步骤 4: onset+pitch 指标
    onset_pitch_p, onset_pitch_r, onset_pitch_f1, _ = \
        mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_pitches_hz,
            est_intervals, est_pitches_hz,
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=_NO_OFFSET_CONSTRAINT,
            offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
        )

    # 步骤 5: onset+pitch+offset 指标
    full_p, full_r, full_f1, _ = \
        mir_eval.transcription.precision_recall_f1_overlap(
            ref_intervals, ref_pitches_hz,
            est_intervals, est_pitches_hz,
            onset_tolerance=onset_tolerance,
            pitch_tolerance=pitch_tolerance,
            offset_ratio=offset_ratio,
            offset_min_tolerance=offset_min_tolerance,
        )

    # 步骤 6: 匹配对 onset/offset 残差
    matches_full: List[Tuple[int, int]] = mir_eval.transcription.match_notes(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=offset_ratio,
        offset_min_tolerance=offset_min_tolerance,
    )

    onset_errors = np.array([
        est_intervals[est_idx, 0] - ref_intervals[ref_idx, 0]
        for ref_idx, est_idx in matches_full
    ], dtype=np.float64)

    offset_errors = np.array([
        est_intervals[est_idx, 1] - ref_intervals[ref_idx, 1]
        for ref_idx, est_idx in matches_full
    ], dtype=np.float64)

    onset_residual = _compute_residual_summary(onset_errors)
    offset_residual = _compute_residual_summary(offset_errors)

    matches_onset_pitch: List[Tuple[int, int]] = mir_eval.transcription.match_notes(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=_NO_OFFSET_CONSTRAINT,
        offset_min_tolerance=_NO_OFFSET_CONSTRAINT,
    )
    num_matched_onset_pitch = len(matches_onset_pitch)
    num_matched_full = len(matches_full)

    # 步骤 7: velocity-aware 指标
    velocity_metrics = compute_velocity_metrics(
        ref_intervals=ref_intervals,
        ref_pitches_hz=ref_pitches_hz,
        ref_vel=ref_vel,
        est_intervals=est_intervals,
        est_pitches_hz=est_pitches_hz,
        est_vel=est_vel,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=offset_ratio,
        offset_min_tolerance=offset_min_tolerance,
    )

    # 步骤 8: 帧级 piano-roll 指标
    frame_pr = compute_frame_level_piano_roll(ref_path, est_path)

    # 步骤 9: 诊断性全局偏移校准
    offset_cal = compute_global_offset_calibration(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
    )

    # 步骤 10: 诊断性全局缩放校准
    scale_cal = compute_global_scale_calibration(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
    )

    # 步骤 11: 诊断性缩放+偏移联合校准
    scale_offset_cal = compute_global_scale_offset_combined(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
    )

    # 步骤 12: 诊断性局部对齐
    # 先应用全局 scale+offset，避免把已知的整体 tempo 缩放误判为局部漂移。
    globally_aligned_intervals = est_intervals.copy()
    global_alpha = scale_offset_cal.get("best_alpha")
    global_offset = scale_offset_cal.get("residual_offset_seconds")
    if global_alpha is not None:
        globally_aligned_intervals[:, 0] *= global_alpha
        globally_aligned_intervals[:, 1] *= global_alpha
    if global_offset is not None:
        globally_aligned_intervals[:, 0] += global_offset
        globally_aligned_intervals[:, 1] += global_offset

    local_alignment = compute_local_alignment(
        ref_intervals, ref_pitches_hz,
        globally_aligned_intervals, est_pitches_hz,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
    )

    # 步骤 13: 错误分桶
    error_buckets = compute_error_buckets(
        ref_intervals, ref_pitches_hz,
        est_intervals, est_pitches_hz,
        matches_full=matches_full,
        onset_tolerance=onset_tolerance,
        pitch_tolerance=pitch_tolerance,
        segment_size_seconds=segment_size_seconds,
    )

    # 步骤 14: 组装结果
    result: Dict[str, Any] = {
        "source_files": {
            "reference_path": str(ref_path.resolve()),
            "reference_sha256": ref_sha256,
            "estimated_path": str(est_path.resolve()),
            "estimated_sha256": est_sha256,
        },
        "parameters": {
            "onset_tolerance": onset_tolerance,
            "pitch_tolerance": pitch_tolerance,
            "offset_ratio": offset_ratio,
            "offset_min_tolerance": offset_min_tolerance,
            "velocity_tolerance": VELOCITY_TOLERANCE,
            "frame_rate_hz": PIANO_ROLL_FRAME_RATE,
            "scale_search_range": [SCALE_ALPHA_MIN, SCALE_ALPHA_MAX],
            "scale_search_step": SCALE_ALPHA_STEP,
            "scale_search_fine_step": SCALE_ALPHA_FINE_STEP,
            "offset_search_range": [OFFSET_SEARCH_MIN, OFFSET_SEARCH_MAX],
            "offset_search_step": OFFSET_SEARCH_STEP,
            "local_window_size_seconds": LOCAL_WINDOW_SIZE_SECONDS,
            "segment_size_seconds": error_buckets["segment_size_used_seconds"],
        },
        "tool_info": {
            "tool_name": TOOL_NAME,
            "tool_version": TOOL_VERSION,
        },
        "raw_metrics": {
            "onset_only_precision": onset_only["precision"],
            "onset_only_recall": onset_only["recall"],
            "onset_only_f1": onset_only["f1"],
            "onset_only_num_matched": onset_only["num_matched"],

            "onset_pitch_precision": float(onset_pitch_p),
            "onset_pitch_recall": float(onset_pitch_r),
            "onset_pitch_f1": float(onset_pitch_f1),

            "onset_pitch_offset_precision": float(full_p),
            "onset_pitch_offset_recall": float(full_r),
            "onset_pitch_offset_f1": float(full_f1),

            "velocity_aware_precision": velocity_metrics["precision"],
            "velocity_aware_recall": velocity_metrics["recall"],
            "velocity_aware_f1": velocity_metrics["f1"],
            "velocity_aware_num_matched": velocity_metrics["num_velocity_matched"],

            "frame_piano_roll_precision": frame_pr["precision"],
            "frame_piano_roll_recall": frame_pr["recall"],
            "frame_piano_roll_accuracy": frame_pr["accuracy"],
            "frame_piano_roll_e_sub": frame_pr["e_sub"],
            "frame_piano_roll_e_miss": frame_pr["e_miss"],
            "frame_piano_roll_e_fa": frame_pr["e_fa"],
            "frame_piano_roll_e_tot": frame_pr["e_tot"],
            "frame_piano_roll_precision_chroma": frame_pr["precision_chroma"],
            "frame_piano_roll_recall_chroma": frame_pr["recall_chroma"],
            "frame_piano_roll_accuracy_chroma": frame_pr["accuracy_chroma"],

            "num_reference_notes": num_ref,
            "num_estimated_notes": num_est,
            "num_matched_onset_pitch": num_matched_onset_pitch,
            "num_matched_onset_pitch_offset": num_matched_full,

            "onset_residual_median": onset_residual["median"],
            "onset_residual_mae": onset_residual["mae"],
            "onset_residual_rmse": onset_residual["rmse"],
            "onset_residual_p95": onset_residual["p95"],
            "onset_residual_mean": onset_residual["mean"],
            "onset_residual_std": onset_residual["std"],

            "offset_residual_median": offset_residual["median"],
            "offset_residual_mae": offset_residual["mae"],
            "offset_residual_rmse": offset_residual["rmse"],
            "offset_residual_p95": offset_residual["p95"],
            "offset_residual_mean": offset_residual["mean"],
            "offset_residual_std": offset_residual["std"],
        },
        "diagnostic_metrics": {
            "global_offset_best_shift_seconds": offset_cal["best_shift_seconds"],
            "global_offset_best_f1": offset_cal["best_f1"],

            "global_scale_best_alpha": scale_cal["best_alpha"],
            "global_scale_best_f1": scale_cal["best_f1"],

            "scale_then_offset_best_alpha": scale_offset_cal["best_alpha"],
            "scale_then_offset_residual_seconds": scale_offset_cal["residual_offset_seconds"],
            "scale_then_offset_combined_f1": scale_offset_cal["combined_f1"],

            "local_alignment_f1": local_alignment["locally_aligned_f1"],
            "local_alignment_num_windows": local_alignment["num_windows"],
            "local_alignment_offset_std": local_alignment["local_offset_std_seconds"],
            "local_alignment_offset_mean": local_alignment["local_offset_mean_seconds"],
            "local_alignment_f1_improvement": local_alignment["f1_improvement_over_raw"],
            "local_alignment_baseline": "global_scale_offset",
            "local_alignment_baseline_f1": local_alignment.get("baseline_f1"),
        },
        "error_buckets": {
            "nearly_matched_ref_offset_only": error_buckets["nearly_matched_ref_offset_only"],
            "correct_onset_wrong_pitch_ref": error_buckets["correct_onset_wrong_pitch_ref"],
            "correct_pitch_wrong_onset_ref": error_buckets["correct_pitch_wrong_onset_ref"],
            "pure_miss_ref": error_buckets["pure_miss_ref"],
            "nearly_matched_est_offset_only": error_buckets["nearly_matched_est_offset_only"],
            "correct_onset_wrong_pitch_est": error_buckets["correct_onset_wrong_pitch_est"],
            "correct_pitch_wrong_onset_est": error_buckets["correct_pitch_wrong_onset_est"],
            "pure_insertion_est": error_buckets["pure_insertion_est"],
            "total_unmatched_ref": error_buckets["total_unmatched_ref"],
            "total_unmatched_est": error_buckets["total_unmatched_est"],
            "segment_boundary_error_concentration": error_buckets["segment_boundary_error_concentration"],
        },
    }

    return result


# ============================================================================
# CLI 入口
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI 入口：解析参数，执行评测，输出 JSON。

    :param argv: 命令行参数列表（用于测试注入）
    :return: 退出码 0=成功
    """
    parser = argparse.ArgumentParser(
        description="MAESTRO 诊断性 MIDI 转录评测器 v2 — 分离时序/音高/offset/velocity 错误",
    )
    parser.add_argument("--reference", "-r", required=True, type=str, help="金标准参考 MIDI 路径")
    parser.add_argument("--estimated", "-e", required=True, type=str, help="估计 MIDI 路径")
    parser.add_argument("--output-json", type=str, default=None, help="JSON 输出路径")
    parser.add_argument("--onset-tolerance", type=float, default=DEFAULT_ONSET_TOLERANCE,
                        help=f"onset 容差秒（默认 {DEFAULT_ONSET_TOLERANCE}s = {int(DEFAULT_ONSET_TOLERANCE*1000)}ms）")
    parser.add_argument("--pitch-tolerance", type=float, default=DEFAULT_PITCH_TOLERANCE,
                        help=f"音高容差 cents（默认 {DEFAULT_PITCH_TOLERANCE}）")
    parser.add_argument("--offset-ratio", type=float, default=DEFAULT_OFFSET_RATIO,
                        help=f"offset 容差比例（默认 {DEFAULT_OFFSET_RATIO}）")
    parser.add_argument("--offset-min-tolerance", type=float, default=DEFAULT_OFFSET_MIN_TOLERANCE,
                        help=f"offset 最小容差秒（默认 {DEFAULT_OFFSET_MIN_TOLERANCE}s）")
    parser.add_argument("--segment-size", type=float, default=None,
                        help=f"分段尺寸秒（默认 {DEFAULT_SEGMENT_SIZE_SECONDS}s）")

    args = parser.parse_args(argv)

    ref_path = Path(args.reference)
    est_path = Path(args.estimated)
    if not ref_path.exists():
        print(f"错误: 参考 MIDI 文件不存在: {ref_path}", file=sys.stderr)
        return 1
    if not est_path.exists():
        print(f"错误: 估计 MIDI 文件不存在: {est_path}", file=sys.stderr)
        return 1

    try:
        result = compute_all_diagnostics(
            reference_midi_path=ref_path,
            estimated_midi_path=est_path,
            onset_tolerance=args.onset_tolerance,
            pitch_tolerance=args.pitch_tolerance,
            offset_ratio=args.offset_ratio,
            offset_min_tolerance=args.offset_min_tolerance,
            segment_size_seconds=args.segment_size,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"未预期的错误: {exc}", file=sys.stderr)
        return 1

    if args.output_json:
        json_path = Path(args.output_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=_json_serializer)
        print(f"JSON 报告已写入: {json_path}")
    else:
        rm = result["raw_metrics"]
        dm = result["diagnostic_metrics"]
        eb = result["error_buckets"]
        print(f"onset_only F1: {rm['onset_only_f1']:.4f}")
        print(f"onset_pitch F1: {rm['onset_pitch_f1']:.4f}")
        print(f"onset_pitch_offset F1: {rm['onset_pitch_offset_f1']:.4f}")
        print(f"velocity_aware F1: {rm['velocity_aware_f1']:.4f}")
        print(f"frame_pr accuracy: {rm['frame_piano_roll_accuracy']:.4f}")
        print(f"[Diagnostic] global_scale_alpha: {dm['global_scale_best_alpha']}")
        print(f"[Diagnostic] global_offset_shift: {dm['global_offset_best_shift_seconds']}")
        print(f"[Diagnostic] local_align_f1: {dm['local_alignment_f1']}")
        print(f"[Diagnostic] local_offset_std: {dm['local_alignment_offset_std']:.4f}s")

    return 0


def _json_serializer(obj: Any) -> Any:
    """JSON 自定义序列化器：处理 numpy 数值和 NaN/Inf。"""
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


if __name__ == "__main__":
    sys.exit(main())
