"""
模块名称：yaml_render_compare
功能描述：
    将两首 YAML 曲谱反向渲染为 MIDI 与合成音频，对比分析其相似性。
    渲染管线：YAML token → MIDI pitch → pretty_midi → 正弦波合成 → WAV 音频
    对比维度：音高分布、节奏密度、频谱特征（MFCC/色度/频谱质心）

主要组件：
    - reverse_token_to_pitch: YAML token → MIDI 音高反向映射
    - yaml_to_midi: 完整 YAML → pretty_midi 对象
    - synthesize_sine: 正弦波合成（替代 fluidsynth）
    - compare_audio: 双音频 librosa 特征对比

依赖说明：
    - librosa: 音频特征提取
    - pretty_midi: MIDI 对象构造
    - scipy / numpy: 信号生成
    - soundfile: WAV 写入

作者：JucieOvo
创建日期：2026-04-28
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import librosa
import numpy as np
import pretty_midi
import soundfile
import yaml

# ---- Token 反向映射表 ----

# NATURAL_PITCH_CLASSES = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
_PC_FROM_NATURAL: dict[str, int] = {"1": 0, "2": 2, "3": 4, "4": 5, "5": 7, "6": 9, "7": 11}

# SHARP_PITCH_CLASSES = {1: "#1", 3: "b3", 6: "#4", 8: "#5", 10: "b7"}
_PC_FROM_ACCIDENTAL: dict[str, int] = {"#1": 1, "b3": 3, "#4": 6, "#5": 8, "b7": 10}

# 合并: token_stem (无变音前缀) → pitch_class
_TOKEN_TO_PC: dict[str, int] = {**_PC_FROM_NATURAL, **_PC_FROM_ACCIDENTAL}

# 音区 → 基础 MIDI 音高（pitch = base + pitch_class）
_ZONE_BASE: dict[str, int] = {"-": 48, "": 60, "+": 72}  # low/middle/high


def reverse_token_to_pitch(token: str) -> int | None:
    """
    将单个 YAML token 反向映射为 MIDI 音高。

    规则：
        - 前缀 '+' → C5-B5 (base 72), 无前缀 → C4-B4 (base 60), '-' → C3-B3 (base 48)
        - 去除前缀后查表获取 pitch_class
        - pitch = base + pitch_class

    :param token: YAML token，如 "+1", "#1", "-3", "b7", "5", "0"
    :return: MIDI 音高(21-108)，休止符(0)返回 None
    """
    token = token.strip()
    if token == "0":
        return None

    # 提取前缀和词干
    if token.startswith("+") or token.startswith("-"):
        prefix = token[0]
        stem = token[1:]
    else:
        prefix = ""
        stem = token

    # 查表 pitch_class
    pitch_class = _TOKEN_TO_PC.get(stem)
    if pitch_class is None:
        return None

    base = _ZONE_BASE.get(prefix)
    if base is None:
        return None

    return base + pitch_class


def yaml_to_midi(yaml_path: Path, bpm: float) -> pretty_midi.PrettyMIDI:
    """
    将 YAML 曲谱反向渲染为 pretty_midi 对象。

    处理流程：
        1. 读取 YAML，提取 score 事件列表
        2. 逐事件解析 notes 字符串（单音/和弦/休止符）
        3. 对非休止符 token 反向映射为 MIDI pitch
        4. 按 beat 值累加时间，构造 MIDI note_on/note_off 事件

    :param yaml_path: YAML 曲谱文件路径
    :param bpm: 曲谱 BPM（拍/分钟）
    :return: 填充完毕的 pretty_midi.PrettyMIDI 对象
    """
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    score_events: list[dict] = data["score"]
    bpm = float(data["song"]["bpm"])
    seconds_per_beat = 60.0 / bpm

    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    piano = pretty_midi.Instrument(program=0, is_drum=False, name="piano")

    current_time = 0.0
    for event in score_events:
        notes_str: str = str(event["notes"])
        beat_val: float = float(event["beat"])

        # 解析和弦/单音/休止符
        if notes_str.startswith("[") and notes_str.endswith("]"):
            # 和弦：按空格拆分内部 token
            inner = notes_str[1:-1]
            tokens = inner.split()
        else:
            tokens = [notes_str]

        # 过滤休止符
        pitches: list[int] = []
        for t in tokens:
            pitch = reverse_token_to_pitch(t)
            if pitch is not None:
                pitches.append(pitch)

        # 写入 MIDI 音符事件
        if pitches:
            note_duration = beat_val * seconds_per_beat
            for pitch in pitches:
                note = pretty_midi.Note(
                    velocity=80,
                    pitch=pitch,
                    start=current_time,
                    end=current_time + note_duration,
                )
                piano.notes.append(note)

        current_time += beat_val * seconds_per_beat

    midi.instruments.append(piano)
    return midi


def synthesize_sine(midi: pretty_midi.PrettyMIDI, sample_rate: int = 22050) -> np.ndarray:
    """
    使用正弦波合成 MIDI 音频（无需 fluidsynth）。

    对每个活跃音符叠加衰减正弦波：
        wave(t) = Σ_note sin(2π * freq(pitch) * t) * envelope(t)

    包络使用线性衰减：attack 10ms, decay 到 silence 在音符持续时间内。

    :param midi: 填充完毕的 pretty_midi 对象
    :param sample_rate: 采样率（默认 22050 Hz）
    :return: 合成音频采样数组，shape=(samples,) 或 (2, samples) 立体声
    """
    total_duration = midi.get_end_time() + 0.5  # 额外 0.5s 尾音
    num_samples = int(total_duration * sample_rate)
    audio = np.zeros(num_samples, dtype=np.float32)

    for instrument in midi.instruments:
        for note in instrument.notes:
            start_sample = int(note.start * sample_rate)
            end_sample = int(note.end * sample_rate)

            if start_sample >= num_samples:
                continue
            end_sample = min(end_sample, num_samples)

            duration_samples = end_sample - start_sample
            if duration_samples <= 0:
                continue

            # 频率计算: freq = 440 * 2^((pitch - 69) / 12)
            freq = 440.0 * (2.0 ** ((note.pitch - 69) / 12.0))

            # 时间轴（相对于音符起始）
            t = np.arange(duration_samples, dtype=np.float32) / sample_rate

            # 正弦波
            wave = np.sin(2.0 * math.pi * freq * t, dtype=np.float32)

            # 包络：快攻慢衰减
            attack_samples = int(0.01 * sample_rate)  # 10ms attack
            envelope = np.ones(duration_samples, dtype=np.float32)
            if duration_samples > attack_samples:
                decay = np.linspace(1.0, 0.0, duration_samples - attack_samples, dtype=np.float32)
                envelope[attack_samples:] = decay
            if attack_samples > 0 and duration_samples > 0:
                attack_env = np.linspace(0.0, 1.0, min(attack_samples, duration_samples), dtype=np.float32)
                envelope[: len(attack_env)] = attack_env

            # 力度缩放（velocity / 127）
            velocity_scale = note.velocity / 127.0
            audio[start_sample:end_sample] += wave * envelope * velocity_scale * 0.3

    # 归一化防削波
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.95

    return audio


def compare_audio_features(audio_a: np.ndarray, audio_b: np.ndarray, sample_rate: int = 22050) -> dict[str, float]:
    """
    计算两段音频的多维特征相似度。

    对比维度：
        1. RMS 能量相关性: 逐帧 RMS 的皮尔逊相关系数
        2. 频谱质心相关性: 逐帧频谱质心的皮尔逊相关系数
        3. MFCC 余弦相似度: 13 维 MFCC 均值的余弦相似度
        4. 色度图相关性: 12 维色度均值的余弦相似度
        5. DTW 距离: 对 RMS 包络的归一化动态时间规整距离

    :param audio_a: 音频 A 采样数组
    :param audio_b: 音频 B 采样数组
    :param sample_rate: 采样率
    :return: 各项相似度指标字典
    """
    # 对齐长度
    min_len = min(len(audio_a), len(audio_b))
    a = audio_a[:min_len]
    b = audio_b[:min_len]

    # 若都是静音则返回满分
    if np.max(np.abs(a)) < 1e-8 and np.max(np.abs(b)) < 1e-8:
        return {"rms_corr": 1.0, "centroid_corr": 1.0, "mfcc_cos": 1.0, "chroma_cos": 1.0, "dtw_norm": 1.0}

    # 1. RMS 能量包络 --- 帧长 2048, 帧移 512
    hop_length = 512
    frame_length = 2048
    rms_a = librosa.feature.rms(y=a, frame_length=frame_length, hop_length=hop_length)[0]
    rms_b = librosa.feature.rms(y=b, frame_length=frame_length, hop_length=hop_length)[0]
    rms_corr = float(np.corrcoef(rms_a, rms_b)[0, 1])
    # 处理 nan（静音导致的常数序列）
    if np.isnan(rms_corr):
        rms_corr = 0.0

    # 2. 频谱质心
    cent_a = librosa.feature.spectral_centroid(y=a, sr=sample_rate, hop_length=hop_length)[0]
    cent_b = librosa.feature.spectral_centroid(y=b, sr=sample_rate, hop_length=hop_length)[0]
    cent_corr = float(np.corrcoef(cent_a, cent_b)[0, 1])
    if np.isnan(cent_corr):
        cent_corr = 0.0

    # 3. MFCC (13维)
    mfcc_a = librosa.feature.mfcc(y=a, sr=sample_rate, n_mfcc=13, hop_length=hop_length)
    mfcc_b = librosa.feature.mfcc(y=b, sr=sample_rate, n_mfcc=13, hop_length=hop_length)
    mfcc_mean_a = np.mean(mfcc_a, axis=1)
    mfcc_mean_b = np.mean(mfcc_b, axis=1)
    mfcc_cos = float(np.dot(mfcc_mean_a, mfcc_mean_b) /
                     (np.linalg.norm(mfcc_mean_a) * np.linalg.norm(mfcc_mean_b) + 1e-10))

    # 4. 色度图 (12维)
    chroma_a = librosa.feature.chroma_stft(y=a, sr=sample_rate, hop_length=hop_length)
    chroma_b = librosa.feature.chroma_stft(y=b, sr=sample_rate, hop_length=hop_length)
    chroma_mean_a = np.mean(chroma_a, axis=1)
    chroma_mean_b = np.mean(chroma_b, axis=1)
    chroma_cos = float(np.dot(chroma_mean_a, chroma_mean_b) /
                        (np.linalg.norm(chroma_mean_a) * np.linalg.norm(chroma_mean_b) + 1e-10))

    # 5. DTW 距离（在 RMS 包络上）
    rms_a_norm = rms_a / (np.max(rms_a) + 1e-10)
    rms_b_norm = rms_b / (np.max(rms_b) + 1e-10)
    rms_a_2d = rms_a_norm.reshape(1, -1)
    rms_b_2d = rms_b_norm.reshape(1, -1)
    # librosa DTW 在长序列上可能很慢，截短到 500 帧
    max_frames = 500
    if rms_a_2d.shape[1] > max_frames:
        rms_a_2d = rms_a_2d[:, :max_frames]
    if rms_b_2d.shape[1] > max_frames:
        rms_b_2d = rms_b_2d[:, :max_frames]
    dtw_dist, _ = librosa.sequence.dtw(X=rms_a_2d, Y=rms_b_2d, metric="euclidean")
    dtw_path = dtw_dist[-1, -1]
    max_possible = max(rms_a_2d.shape[1], rms_b_2d.shape[1])
    dtw_norm = float(1.0 - min(dtw_path / (max_possible + 1e-10), 1.0))

    return {
        "rms_corr": round(rms_corr, 4),
        "centroid_corr": round(cent_corr, 4),
        "mfcc_cos": round(mfcc_cos, 4),
        "chroma_cos": round(chroma_cos, 4),
        "dtw_norm": round(dtw_norm, 4),
    }


def compare_note_statistics(
    midi_a: pretty_midi.PrettyMIDI,
    midi_b: pretty_midi.PrettyMIDI,
) -> dict[str, float]:
    """
    对比两个 MIDI 对象的音符级统计指标。

    :param midi_a: MIDI A
    :param midi_b: MIDI B
    :return: 统计对比指标字典
    """
    notes_a = [n for inst in midi_a.instruments for n in inst.notes if not inst.is_drum]
    notes_b = [n for inst in midi_b.instruments for n in inst.notes if not inst.is_drum]

    pitches_a = np.array([n.pitch for n in notes_a])
    pitches_b = np.array([n.pitch for n in notes_b])

    # 音符数量比
    note_count_ratio = min(len(notes_a), len(notes_b)) / max(len(notes_a), len(notes_b), 1)

    # 音高分布 KL 散度（直方图 21-108，88 bins）
    hist_a, _ = np.histogram(pitches_a, bins=88, range=(21, 109), density=True)
    hist_b, _ = np.histogram(pitches_b, bins=88, range=(21, 109), density=True)
    # 平滑防零
    hist_a = hist_a + 1e-8
    hist_b = hist_b + 1e-8
    hist_a = hist_a / hist_a.sum()
    hist_b = hist_b / hist_b.sum()
    kl_div = float(np.sum(hist_a * np.log(hist_a / hist_b)))

    # 音高分布相关系数
    pitch_corr = float(np.corrcoef(hist_a, hist_b)[0, 1])
    if np.isnan(pitch_corr):
        pitch_corr = 0.0

    # 时长统计（秒）
    durations_a = np.array([n.end - n.start for n in notes_a])
    durations_b = np.array([n.end - n.start for n in notes_b])
    mean_dur_a = float(np.mean(durations_a))
    mean_dur_b = float(np.mean(durations_b))

    # 总时长
    total_dur_a = midi_a.get_end_time()
    total_dur_b = midi_b.get_end_time()

    # 和弦密度（平均每秒音符数）
    density_a = len(notes_a) / total_dur_a if total_dur_a > 0 else 0
    density_b = len(notes_b) / total_dur_b if total_dur_b > 0 else 0

    return {
        "note_count_a": len(notes_a),
        "note_count_b": len(notes_b),
        "note_count_ratio": round(note_count_ratio, 4),
        "pitch_hist_corr": round(pitch_corr, 4),
        "pitch_kl_divergence": round(kl_div, 4),
        "mean_duration_a_s": round(mean_dur_a, 4),
        "mean_duration_b_s": round(mean_dur_b, 4),
        "total_duration_a_s": round(total_dur_a, 2),
        "total_duration_b_s": round(total_dur_b, 2),
        "density_notes_per_sec_a": round(density_a, 4),
        "density_notes_per_sec_b": round(density_b, 4),
        "mean_pitch_a": round(float(np.mean(pitches_a)), 2),
        "mean_pitch_b": round(float(np.mean(pitches_b)), 2),
        "pitch_std_a": round(float(np.std(pitches_a)), 2),
        "pitch_std_b": round(float(np.std(pitches_b)), 2),
    }


def main() -> None:
    """
    主入口：渲染两首 YAML 为音频，逐项对比并输出报告。
    """
    # 路径配置
    yaml_a = Path("config/test_attention.yaml")
    yaml_b = Path("config/test_baseline.yaml")
    work_dir = Path("work/test_render_compare")
    work_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("YAML 渲染对比测试")
    print("=" * 60)

    # 加载 YAML 获取 BPM
    with yaml_a.open("r", encoding="utf-8") as f:
        data_a = yaml.safe_load(f)
    with yaml_b.open("r", encoding="utf-8") as f:
        data_b = yaml.safe_load(f)

    bpm_a = float(data_a["song"]["bpm"])
    bpm_b = float(data_b["song"]["bpm"])
    name_a = data_a["song"]["name"]
    name_b = data_b["song"]["name"]

    print(f"\n曲谱 A (attention_weighted): {name_a}  BPM={bpm_a}")
    print(f"曲谱 B (hands_decoupled):   {name_b}  BPM={bpm_b}")

    # Step 1: YAML → MIDI
    print("\n[1/4] YAML → MIDI 反向渲染...")
    midi_a = yaml_to_midi(yaml_a, bpm_a)
    midi_b = yaml_to_midi(yaml_b, bpm_b)

    midi_a_path = work_dir / "test_attention.mid"
    midi_b_path = work_dir / "test_baseline.mid"
    midi_a.write(str(midi_a_path))
    midi_b.write(str(midi_b_path))
    print(f"  MIDI A: {midi_a_path}  ({len([n for inst in midi_a.instruments for n in inst.notes])} 音符)")
    print(f"  MIDI B: {midi_b_path}  ({len([n for inst in midi_b.instruments for n in inst.notes])} 音符)")

    # Step 2: MIDI → 音频合成（正弦波）
    print("\n[2/4] 正弦波音频合成...")
    sr = 22050
    audio_a = synthesize_sine(midi_a, sample_rate=sr)
    audio_b = synthesize_sine(midi_b, sample_rate=sr)

    wav_a_path = work_dir / "test_attention.wav"
    wav_b_path = work_dir / "test_baseline.wav"
    soundfile.write(str(wav_a_path), audio_a, sr)
    soundfile.write(str(wav_b_path), audio_b, sr)
    print(f"  WAV A: {wav_a_path}  ({len(audio_a)/sr:.1f}s)")
    print(f"  WAV B: {wav_b_path}  ({len(audio_b)/sr:.1f}s)")

    # Step 3: 音符级统计对比
    print("\n[3/4] 音符级统计对比...")
    note_stats = compare_note_statistics(midi_a, midi_b)

    print(f"  音符数量:  {note_stats['note_count_a']:>5}  vs  {note_stats['note_count_b']:<5}  (比率 {note_stats['note_count_ratio']})")
    print(f"  平均音高:  {note_stats['mean_pitch_a']:>6.2f} vs  {note_stats['mean_pitch_b']:<6.2f}")
    print(f"  音高标准差: {note_stats['pitch_std_a']:>6.2f} vs  {note_stats['pitch_std_b']:<6.2f}")
    print(f"  平均时长:  {note_stats['mean_duration_a_s']:.4f}s vs {note_stats['mean_duration_b_s']:.4f}s")
    print(f"  总时长:    {note_stats['total_duration_a_s']:.1f}s vs {note_stats['total_duration_b_s']:.1f}s")
    print(f"  密度:      {note_stats['density_notes_per_sec_a']:.2f}/s vs {note_stats['density_notes_per_sec_b']:.2f}/s")
    print(f"  音高直方图相关系数: {note_stats['pitch_hist_corr']}")
    print(f"  音高直方图 KL 散度: {note_stats['pitch_kl_divergence']}")

    # Step 4: 音频特征相似度
    print("\n[4/4] 音频特征相似度...")
    audio_sim = compare_audio_features(audio_a, audio_b, sample_rate=sr)

    print(f"  RMS 能量包络相关系数:   {audio_sim['rms_corr']}")
    print(f"  频谱质心相关系数:       {audio_sim['centroid_corr']}")
    print(f"  MFCC 余弦相似度:        {audio_sim['mfcc_cos']}")
    print(f"  色度图余弦相似度:       {audio_sim['chroma_cos']}")
    print(f"  RMS 包络 DTW 归一化:    {audio_sim['dtw_norm']}")

    # 综合得分
    overall = (
        0.15 * audio_sim["rms_corr"]
        + 0.15 * audio_sim["centroid_corr"]
        + 0.25 * audio_sim["mfcc_cos"]
        + 0.25 * audio_sim["chroma_cos"]
        + 0.20 * note_stats["note_count_ratio"]
    )
    print(f"\n  综合相似度得分: {overall:.4f}  (越接近 1.0 越相似)")

    # 写入报告
    report = {
        "yaml_a": str(yaml_a),
        "yaml_b": str(yaml_b),
        "mode_a": "attention_weighted",
        "mode_b": "hands_decoupled",
        "song_name": name_a,
        "bpm": bpm_a,
        "note_statistics": note_stats,
        "audio_similarity": audio_sim,
        "overall_score": round(overall, 4),
    }
    import json
    report_path = work_dir / "comparison_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {report_path}")

    print("\n" + "=" * 60)
    print("渲染对比完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
