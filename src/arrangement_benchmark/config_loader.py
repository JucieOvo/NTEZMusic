"""
模块名称：arrangement_benchmark.config_loader
功能描述：
    严格加载多歌曲缩编算法基准实验配置，配置缺失或非法时直接报错。

主要组件：
    - load_benchmark_config: 读取并校验正式实验 YAML

依赖说明：
    - PyYAML: 安全读取 YAML
    - arrangement_benchmark.models: 构建冻结配置模型

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import yaml

from arrangement_benchmark.models import (
    AudioMetricParameters,
    BenchmarkConfig,
    CandidateSpec,
    CommonParameters,
    RenderingParameters,
    RuntimeParameters,
    SongSpec,
    SoundFontSpec,
    ToolSpec,
)


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    """校验字段为对象并返回该对象。"""
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return value


def _require_non_empty_string(value: Any, field_name: str) -> str:
    """校验字段为非空字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _resolve_project_path(project_root: Path, value: Any, field_name: str) -> Path:
    """将配置路径解析为项目内绝对路径。"""
    raw_path = Path(_require_non_empty_string(value, field_name))
    resolved_path = raw_path if raw_path.is_absolute() else project_root / raw_path
    return resolved_path.resolve()


def _require_number(value: Any, field_name: str, *, positive: bool = True) -> float:
    """校验字段为有限数字。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是数字")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{field_name} 必须是有限正数")
    return number


def load_benchmark_config(config_path: Path) -> BenchmarkConfig:
    """
    加载并严格校验正式多歌曲基准配置。

    :param config_path: 实验 YAML 配置路径
    :return: 冻结的完整实验配置
    :raises FileNotFoundError: 配置或真实歌曲输入不存在时触发
    :raises ValueError: 任一配置字段缺失、类型错误或违反设计时触发
    """
    if not config_path.is_file():
        raise FileNotFoundError(f"实验配置不存在: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file)
    root = _require_mapping(raw_config, "配置顶层")
    project_root = config_path.resolve().parents[1]

    # 1. 实验基础字段：目录与统一力度必须由集中配置提供。
    experiment_id = _require_non_empty_string(root.get("experiment_id"), "experiment_id")
    output_root = _resolve_project_path(project_root, root.get("output_root"), "output_root")
    reduced_velocity_value = root.get("reduced_velocity")
    if not isinstance(reduced_velocity_value, int) or isinstance(reduced_velocity_value, bool):
        raise ValueError("reduced_velocity 必须是整数")
    if not 1 <= reduced_velocity_value <= 127:
        raise ValueError("reduced_velocity 必须位于 1-127")

    # 2. 真实歌曲输入：正式配置要求至少一首且每个文件真实存在。
    raw_songs = root.get("songs")
    if not isinstance(raw_songs, list) or not raw_songs:
        raise ValueError("songs 必须是至少包含一首歌曲的列表")
    songs: list[SongSpec] = []
    seen_slugs: set[str] = set()
    for index, raw_song in enumerate(raw_songs):
        song_mapping = _require_mapping(raw_song, f"songs[{index}]")
        slug = _require_non_empty_string(song_mapping.get("slug"), f"songs[{index}].slug")
        if slug in seen_slugs:
            raise ValueError(f"歌曲 slug 重复: {slug}")
        seen_slugs.add(slug)
        source_mp3 = _resolve_project_path(project_root, song_mapping.get("source_mp3"), f"songs[{index}].source_mp3")
        if not source_mp3.is_file():
            raise FileNotFoundError(f"真实输入 MP3 不存在: {source_mp3}")
        songs.append(
            SongSpec(
                slug=slug,
                name=_require_non_empty_string(song_mapping.get("name"), f"songs[{index}].name"),
                source_mp3=source_mp3,
            )
        )

    # 3. 候选算法：配置只保存实际运行模式，不引入 none 伪候选。
    raw_candidates = root.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("candidates 必须是非空列表")
    candidates = tuple(CandidateSpec(mode=_require_non_empty_string(mode, "candidate.mode")) for mode in raw_candidates)
    if len({candidate.mode for candidate in candidates}) != len(candidates):
        raise ValueError("候选算法名称不能重复")

    # 4. 六维权重：所有维度必须显式提供且权重和严格为 1。
    raw_weights = _require_mapping(root.get("metric_weights"), "metric_weights")
    expected_dimensions = {"melody", "harmony", "rhythm", "bass", "voice_register", "audio_features"}
    if set(raw_weights) != expected_dimensions:
        raise ValueError(f"metric_weights 必须且只能包含: {sorted(expected_dimensions)}")
    metric_weights = {name: _require_number(raw_weights[name], f"metric_weights.{name}") for name in expected_dimensions}
    if not math.isclose(sum(metric_weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("metric_weights 权重和必须等于 1.0")

    # 5. 公共算法参数：字段缺失直接失败，不在代码中补默认值。
    raw_parameters = _require_mapping(root.get("common_parameters"), "common_parameters")
    allow_accidentals = raw_parameters.get("allow_accidentals")
    if not isinstance(allow_accidentals, bool):
        raise ValueError("common_parameters.allow_accidentals 必须是布尔值")
    common_parameters = CommonParameters(
        beat_unit=int(_require_number(raw_parameters.get("beat_unit"), "common_parameters.beat_unit")),
        start_delay_seconds=_require_number(raw_parameters.get("start_delay_seconds"), "common_parameters.start_delay_seconds", positive=False),
        key_press_seconds=_require_number(raw_parameters.get("key_press_seconds"), "common_parameters.key_press_seconds", positive=False),
        quantize_beat=_require_number(raw_parameters.get("quantize_beat"), "common_parameters.quantize_beat"),
        max_chord_notes=int(_require_number(raw_parameters.get("max_chord_notes"), "common_parameters.max_chord_notes")),
        max_score_events=int(_require_number(raw_parameters.get("max_score_events"), "common_parameters.max_score_events")),
        allow_accidentals=allow_accidentals,
        out_of_range_policy=_require_non_empty_string(raw_parameters.get("out_of_range_policy"), "common_parameters.out_of_range_policy"),
        ref_smoothing=_require_number(raw_parameters.get("ref_smoothing"), "common_parameters.ref_smoothing"),
        left_max_chord_notes=int(_require_number(raw_parameters.get("left_max_chord_notes"), "common_parameters.left_max_chord_notes")),
        phrase_gap_beats=_require_number(raw_parameters.get("phrase_gap_beats"), "common_parameters.phrase_gap_beats"),
        global_trend_alpha=_require_number(raw_parameters.get("global_trend_alpha"), "common_parameters.global_trend_alpha"),
        global_trend_window_beats=_require_number(raw_parameters.get("global_trend_window_beats"), "common_parameters.global_trend_window_beats"),
    )
    # 6. 外部工具和音源：此阶段校验声明完整性，真实存在性由执行预检负责。
    raw_soundfont = _require_mapping(root.get("soundfont"), "soundfont")
    soundfont = SoundFontSpec(
        path=_resolve_project_path(project_root, raw_soundfont.get("path"), "soundfont.path"),
        source_url=_require_non_empty_string(raw_soundfont.get("source_url"), "soundfont.source_url"),
        license_url=_require_non_empty_string(raw_soundfont.get("license_url"), "soundfont.license_url"),
        expected_sha256=_require_non_empty_string(
            raw_soundfont.get("expected_sha256"),
            "soundfont.expected_sha256",
        ).lower(),
    )
    if not soundfont.source_url.startswith("https://") or not soundfont.license_url.startswith("https://"):
        raise ValueError("SoundFont 来源与许可证必须使用 HTTPS URL")
    if re.fullmatch(r"[0-9a-f]{64}", soundfont.expected_sha256) is None:
        raise ValueError("soundfont.expected_sha256 必须是64位十六进制 SHA-256")

    raw_tools = _require_mapping(root.get("tools"), "tools")
    tools = ToolSpec(
        fluidsynth=_require_non_empty_string(raw_tools.get("fluidsynth"), "tools.fluidsynth"),
        ffmpeg=_require_non_empty_string(raw_tools.get("ffmpeg"), "tools.ffmpeg"),
    )

    raw_rendering = _require_mapping(root.get("rendering"), "rendering")
    sample_rate = int(_require_number(raw_rendering.get("sample_rate"), "rendering.sample_rate"))
    mp3_bitrate = _require_non_empty_string(raw_rendering.get("mp3_bitrate"), "rendering.mp3_bitrate")
    if not mp3_bitrate[:-1].isdigit() or not mp3_bitrate.endswith("k"):
        raise ValueError("rendering.mp3_bitrate 必须使用正整数加 k 的格式，例如 320k")
    rendering = RenderingParameters(sample_rate=sample_rate, mp3_bitrate=mp3_bitrate)

    raw_audio_metrics = _require_mapping(root.get("audio_metrics"), "audio_metrics")
    frame_seconds = _require_number(raw_audio_metrics.get("frame_seconds"), "audio_metrics.frame_seconds")
    n_mfcc = int(_require_number(raw_audio_metrics.get("n_mfcc"), "audio_metrics.n_mfcc"))
    audio_metrics = AudioMetricParameters(frame_seconds=frame_seconds, n_mfcc=n_mfcc)

    raw_runtime = _require_mapping(root.get("runtime"), "runtime")
    transcription_checkpoint_value = raw_runtime.get("transcription_checkpoint")
    transcription_checkpoint = None
    if transcription_checkpoint_value is not None:
        transcription_checkpoint = _resolve_project_path(
            project_root,
            transcription_checkpoint_value,
            "runtime.transcription_checkpoint",
        )
        if not transcription_checkpoint.is_file():
            raise FileNotFoundError(f"Transkun 权重不存在: {transcription_checkpoint}")
    runtime = RuntimeParameters(
        python_path=_resolve_project_path(project_root, raw_runtime.get("python_path"), "runtime.python_path"),
        raw_midi_generator_path=_resolve_project_path(
            project_root,
            raw_runtime.get("raw_midi_generator_path"),
            "runtime.raw_midi_generator_path",
        ),
        svsep_model_path=_resolve_project_path(
            project_root,
            raw_runtime.get("svsep_model_path"),
            "runtime.svsep_model_path",
        ),
        transcription_checkpoint=transcription_checkpoint,
        transcription_device=_require_non_empty_string(
            raw_runtime.get("transcription_device"),
            "runtime.transcription_device",
        ),
        svsep_device=_require_non_empty_string(raw_runtime.get("svsep_device"), "runtime.svsep_device"),
        demucs_model=_require_non_empty_string(raw_runtime.get("demucs_model"), "runtime.demucs_model"),
        demucs_stem=_require_non_empty_string(raw_runtime.get("demucs_stem"), "runtime.demucs_stem"),
    )
    for runtime_path, field_name in (
        (runtime.python_path, "runtime.python_path"),
        (runtime.raw_midi_generator_path, "runtime.raw_midi_generator_path"),
        (runtime.svsep_model_path, "runtime.svsep_model_path"),
    ):
        if not runtime_path.is_file():
            raise FileNotFoundError(f"{field_name} 不存在: {runtime_path}")
    if runtime.transcription_device not in {"cpu", "cuda"}:
        raise ValueError("runtime.transcription_device 只允许 cpu 或 cuda")
    if runtime.svsep_device not in {"cpu", "cuda"}:
        raise ValueError("runtime.svsep_device 只允许 cpu 或 cuda")

    score_aware_mode_value = root.get("score_aware_mode")
    score_aware_mode = None
    if score_aware_mode_value is not None:
        score_aware_mode = _require_non_empty_string(score_aware_mode_value, "score_aware_mode")

    return BenchmarkConfig(
        experiment_id=experiment_id,
        project_root=project_root,
        output_root=output_root,
        reduced_velocity=reduced_velocity_value,
        songs=tuple(songs),
        candidates=candidates,
        score_aware_mode=score_aware_mode,
        metric_weights=MappingProxyType(metric_weights),
        common_parameters=common_parameters,
        soundfont=soundfont,
        tools=tools,
        rendering=rendering,
        audio_metrics=audio_metrics,
        runtime=runtime,
    )
