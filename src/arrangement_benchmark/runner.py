"""
模块名称：arrangement_benchmark.runner
功能描述：
    编排多歌曲、多算法真实基准实验的各个可验证阶段。

主要组件：
    - CandidateRunResult: 单候选真实执行产物
    - BenchmarkRunner: 构造固定参数配置并执行候选闭环

依赖说明：
    - audio_to_yaml_converter: 复用现有缩编算法，不修改其业务逻辑
    - arrangement_benchmark.score_io: 严格反解36键 MIDI
    - arrangement_benchmark.constraints: 执行异环硬约束校验

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import traceback
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from arrangement_benchmark.constraints import ConstraintReport, validate_reduced_artifacts
from arrangement_benchmark.metrics import (
    calculate_audio_fidelity,
    calculate_midi_fidelity,
    calculate_weighted_score,
)
from arrangement_benchmark.models import BenchmarkConfig, SongSpec
from arrangement_benchmark.rendering import render_midi_to_audio
from arrangement_benchmark.score_io import convert_yaml_to_reduced_midi
from audio_to_yaml_converter import (
    AudioPipelineConfig,
    DemucsSeparator,
    MidiToYamlConverter,
    _estimate_audio_bpm,
)
from piano_auto_player import PianoConfigLoader


@dataclass(frozen=True)
class CandidateRunResult:
    """单个候选算法在单首歌曲上的真实执行结果。"""

    mode: str
    yaml_path: Path
    reduced_midi_path: Path
    conversion_report_path: Path
    constraint_report_path: Path
    conversion_stats: Mapping[str, int | str | float]
    constraint_report: ConstraintReport


@dataclass(frozen=True)
class PreflightReport:
    """真实环境预检结果。"""

    passed: bool
    failures: tuple[str, ...]
    tool_versions_path: Path
    requirements_frozen_path: Path


@dataclass(frozen=True)
class SourceArtifacts:
    """单首歌曲的不可变参考产物。"""

    song: SongSpec
    bpm: float
    reference_midi_path: Path
    reference_wav_path: Path
    reference_mp3_path: Path
    reference_midi_sha256: str


@dataclass(frozen=True)
class CandidateEvaluation:
    """单首歌曲单候选的完整真实评测结果。"""

    song_slug: str
    mode: str
    score: float
    metrics: Mapping[str, float]
    candidate_result: CandidateRunResult
    wav_path: Path
    mp3_path: Path
    metrics_path: Path


@dataclass(frozen=True)
class FullBenchmarkResult:
    """多歌曲完整基准实验最终产物。"""

    manifest_path: Path
    ranking_path: Path
    final_report_path: Path
    blind_mapping_path: Path
    ranked_modes: tuple[str, ...]


def _write_json(output_path: Path, payload: Mapping[str, Any]) -> None:
    """以 UTF-8 写出可审计 JSON。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _serialize_supported_value(value: Any) -> str:
        """仅将 pathlib.Path 转为字符串，其他未知类型继续失败。"""
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"JSON 报告包含不支持的类型: {type(value).__name__}")

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
            default=_serialize_supported_value,
        )


def _sha256_file(file_path: Path) -> str:
    """流式计算真实文件 SHA-256。"""
    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_process(command: list[str], log_path: Path, stage_name: str, working_directory: Path) -> None:
    """执行长时真实命令并将完整输出写入阶段日志。"""
    completed = subprocess.run(
        command,
        cwd=str(working_directory),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"command: {command}\nreturncode: {completed.returncode}\n\nstdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{stage_name} 失败，详见 {log_path}")


class BenchmarkRunner:
    """
    多歌曲缩编算法基准实验编排器。

    当前实现首先提供单候选真实闭环；完整状态机在该稳定接口上继续构建。
    """

    def __init__(self, config: BenchmarkConfig) -> None:
        """保存经过严格加载的集中实验配置。"""
        self.config = config

    def preflight(self) -> PreflightReport:
        """
        校验正式实验所需的全部真实输入、Python 包、GPU、模型和外部工具。

        :return: 结构化预检报告；任一失败都会出现在 failures 中
        """
        failures: list[str] = []
        version_records: dict[str, Any] = {}
        output_root = self.config.output_root
        output_root.mkdir(parents=True, exist_ok=True)
        tool_versions_path = output_root / "tool_versions.json"
        requirements_frozen_path = output_root / "requirements_frozen.txt"

        # 1. 不可替代的真实文件：输入、模型、音源和执行脚本。
        required_files = {
            "python": self.config.runtime.python_path,
            "raw_midi_generator": self.config.runtime.raw_midi_generator_path,
            "svsep_model": self.config.runtime.svsep_model_path,
            "soundfont": self.config.soundfont.path,
            **{f"song:{song.slug}": song.source_mp3 for song in self.config.songs},
        }
        for file_name, file_path in required_files.items():
            if not file_path.is_file():
                failures.append(f"必需文件不存在 [{file_name}]: {file_path}")
            else:
                version_records[file_name] = {
                    "path": str(file_path),
                    "sha256": _sha256_file(file_path),
                }
        if self.config.soundfont.path.is_file():
            actual_soundfont_sha256 = _sha256_file(self.config.soundfont.path)
            if actual_soundfont_sha256 != self.config.soundfont.expected_sha256:
                failures.append(
                    "SoundFont SHA-256 不匹配: "
                    f"expected={self.config.soundfont.expected_sha256}, actual={actual_soundfont_sha256}"
                )

        # 2. 当前 Python 进程必须真实拥有所有运行依赖。
        required_modules = (
            "yaml",
            "pretty_midi",
            "librosa",
            "soundfile",
            "scipy",
            "numpy",
            "music21",
            "demucs",
            "transkun",
            "mido",
            "soxr",
            "moduleconf",
            "torch",
            "partitura",
            "torch_geometric",
            "pytorch_lightning",
        )
        missing_modules = tuple(module_name for module_name in required_modules if importlib.util.find_spec(module_name) is None)
        if missing_modules:
            failures.append("缺少 Python 依赖: " + ", ".join(missing_modules))
        version_records["python_modules"] = {
            module_name: "available" if module_name not in missing_modules else "missing"
            for module_name in required_modules
        }

        # 3. CUDA 配置不得自动回退 CPU。
        if "cuda" in {self.config.runtime.transcription_device, self.config.runtime.svsep_device}:
            try:
                import torch

                cuda_available = bool(torch.cuda.is_available())
                version_records["cuda"] = {
                    "available": cuda_available,
                    "device_count": int(torch.cuda.device_count()),
                    "torch_version": str(torch.__version__),
                }
                if not cuda_available:
                    failures.append("配置要求 CUDA，但 torch.cuda.is_available() 为 False")
            except Exception as exc:
                failures.append(f"CUDA 预检失败: {exc}")

        # 4. 外部工具必须真实执行版本命令。
        tool_commands = {
            "fluidsynth": [self.config.tools.fluidsynth, "--version"],
            "ffmpeg": [self.config.tools.ffmpeg, "-version"],
            "python": [str(self.config.runtime.python_path), "--version"],
        }
        for tool_name, command in tool_commands.items():
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if completed.returncode != 0:
                    failures.append(f"工具版本命令失败 [{tool_name}]: {completed.stderr.strip()}")
                else:
                    version_records[f"tool:{tool_name}"] = (completed.stdout or completed.stderr).strip()
            except OSError as exc:
                failures.append(f"外部工具不可执行 [{tool_name}]: {exc}")

        # 5. 冻结当前真实 Python 环境，失败同样进入预检报告。
        freeze_process = subprocess.run(
            [str(self.config.runtime.python_path), "-m", "pip", "freeze"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if freeze_process.returncode != 0:
            failures.append(f"pip freeze 失败: {freeze_process.stderr.strip()}")
        else:
            requirements_frozen_path.write_text(freeze_process.stdout, encoding="utf-8")

        version_records["failures"] = failures
        _write_json(tool_versions_path, version_records)
        return PreflightReport(
            passed=not failures,
            failures=tuple(failures),
            tool_versions_path=tool_versions_path,
            requirements_frozen_path=requirements_frozen_path,
        )

    def _build_converter_config(
        self,
        song: SongSpec,
        bpm: float,
        reference_midi_path: Path,
        mode: str,
        yaml_path: Path,
        work_dir: Path,
    ) -> AudioPipelineConfig:
        """将集中基准配置映射为现有转换器的固定参数配置。"""
        parameters = self.config.common_parameters
        return AudioPipelineConfig(
            audio_path=song.source_mp3,
            input_midi_path=reference_midi_path,
            output_yaml_path=yaml_path,
            work_dir=work_dir,
            song_name=song.name,
            bpm=bpm,
            beat_unit=parameters.beat_unit,
            start_delay_seconds=parameters.start_delay_seconds,
            key_press_seconds=parameters.key_press_seconds,
            demucs_model=self.config.runtime.demucs_model,
            demucs_stem=self.config.runtime.demucs_stem,
            transcription_checkpoint=self.config.runtime.transcription_checkpoint,
            transcription_device=self.config.runtime.transcription_device,
            transcription_segment_hop_size=None,
            transcription_segment_size=None,
            quantize_beat=parameters.quantize_beat,
            max_chord_notes=parameters.max_chord_notes,
            max_score_events=parameters.max_score_events,
            allow_accidentals=parameters.allow_accidentals,
            out_of_range_policy=parameters.out_of_range_policy,
            pitch_compression_mode=mode,
            ref_smoothing=parameters.ref_smoothing,
            left_max_chord_notes=parameters.left_max_chord_notes,
            phrase_gap_beats=parameters.phrase_gap_beats,
            global_trend_alpha=parameters.global_trend_alpha,
            global_trend_window_beats=parameters.global_trend_window_beats,
            svsep_model_path=self.config.runtime.svsep_model_path,
            svsep_device=self.config.runtime.svsep_device,
        )

    def run_candidate_from_midi(
        self,
        song: SongSpec,
        bpm: float,
        reference_midi_path: Path,
        mode: str,
        candidate_root: Path,
    ) -> CandidateRunResult:
        """
        使用不可变参考 MIDI 执行单个候选算法的 YAML/MIDI/约束闭环。

        :param song: 当前真实歌曲定义
        :param bpm: sidecar 检测 BPM，不改写参考 MIDI
        :param reference_midi_path: 不可变原始 B MIDI 或真实回归参考 MIDI
        :param mode: 当前压缩模式
        :param candidate_root: 候选产物目录
        :return: 真实候选产物与报告
        :raises ValueError: 模式、BPM 或硬约束非法时触发
        :raises RuntimeError: 转换后无法重新读取或违反硬约束时触发
        """
        allowed_modes = {candidate.mode for candidate in self.config.candidates}
        if self.config.score_aware_mode is not None:
            allowed_modes.add(self.config.score_aware_mode)
        if mode not in allowed_modes:
            raise ValueError(f"不在基准候选集合中的算法: {mode}")
        if bpm <= 0:
            raise ValueError("候选算法 BPM 必须大于 0")
        if not reference_midi_path.is_file():
            raise FileNotFoundError(f"参考 MIDI 不存在: {reference_midi_path}")

        candidate_root.mkdir(parents=True, exist_ok=True)
        yaml_path = candidate_root / "reduced.yaml"
        reduced_midi_path = candidate_root / "reduced.mid"
        conversion_report_path = candidate_root / "conversion_report.json"
        constraint_report_path = candidate_root / "constraint_report.json"
        converter_config = self._build_converter_config(
            song=song,
            bpm=bpm,
            reference_midi_path=reference_midi_path,
            mode=mode,
            yaml_path=yaml_path,
            work_dir=candidate_root / "work",
        )

        converter = MidiToYamlConverter()
        yaml_data = converter.convert(midi_path=reference_midi_path, config=converter_config)
        with yaml_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(yaml_data, file, allow_unicode=True, sort_keys=False)
        PianoConfigLoader().load(yaml_path)

        midi_result = convert_yaml_to_reduced_midi(
            yaml_path=yaml_path,
            output_midi_path=reduced_midi_path,
            velocity=self.config.reduced_velocity,
        )
        constraint_report = validate_reduced_artifacts(
            yaml_path=yaml_path,
            midi_path=reduced_midi_path,
            expected_velocity=self.config.reduced_velocity,
            max_chord_notes=self.config.common_parameters.max_chord_notes,
        )

        conversion_stats = dict(converter.conversion_stats)
        _write_json(
            conversion_report_path,
            {
                "song_slug": song.slug,
                "mode": mode,
                "reference_midi_path": str(reference_midi_path),
                "bpm_sidecar": bpm,
                "midi_build": asdict(midi_result),
                "conversion_stats": conversion_stats,
            },
        )
        _write_json(constraint_report_path, asdict(constraint_report))
        if not constraint_report.passed:
            raise RuntimeError(
                f"候选 {mode} 违反硬约束: " + "; ".join(constraint_report.violations)
            )

        return CandidateRunResult(
            mode=mode,
            yaml_path=yaml_path,
            reduced_midi_path=reduced_midi_path,
            conversion_report_path=conversion_report_path,
            constraint_report_path=constraint_report_path,
            conversion_stats=MappingProxyType(conversion_stats),
            constraint_report=constraint_report,
        )

    def _prepare_song_source(self, song: SongSpec) -> SourceArtifacts:
        """生成或严格复用单首歌曲的 Demucs stem、原始 B MIDI 和参考钢琴音频。"""
        song_root = self.config.output_root / song.slug
        reference_root = song_root / "reference"
        base_midi_path = reference_root / "base_midi" / "song.mid"
        bpm_path = reference_root / "bpm.json"
        reference_wav_path = reference_root / "audio" / "original_grand_piano.wav"
        reference_mp3_path = reference_root / "audio" / "original_grand_piano.mp3"

        # 已完整生成时只复用可回读真实产物；任何缺项都会重新进入真实生成流程。
        if all(path.is_file() and path.stat().st_size > 0 for path in (base_midi_path, bpm_path, reference_wav_path, reference_mp3_path)):
            with bpm_path.open("r", encoding="utf-8") as file:
                bpm_payload = json.load(file)
            bpm = float(bpm_payload["detected_bpm"])
            return SourceArtifacts(
                song=song,
                bpm=bpm,
                reference_midi_path=base_midi_path,
                reference_wav_path=reference_wav_path,
                reference_mp3_path=reference_mp3_path,
                reference_midi_sha256=_sha256_file(base_midi_path),
            )

        bpm = _estimate_audio_bpm(song.source_mp3)
        source_work_dir = reference_root / "source_work"
        source_config = self._build_converter_config(
            song=song,
            bpm=bpm,
            reference_midi_path=base_midi_path,
            mode=self.config.candidates[0].mode,
            yaml_path=reference_root / "unused.yaml",
            work_dir=source_work_dir,
        )
        piano_stem_path = DemucsSeparator().separate(source_config)

        transcription_root = reference_root / "transcription"
        raw_midi_path = transcription_root / "transkun_raw_120bpm.mid"
        raw_manifest_path = transcription_root / "run_manifest.json"
        if not raw_midi_path.is_file() or not raw_manifest_path.is_file():
            raw_command = [
                str(self.config.runtime.python_path),
                str(self.config.runtime.raw_midi_generator_path),
                "--audio",
                str(piano_stem_path),
                "--output-dir",
                str(transcription_root),
                "--device",
                self.config.runtime.transcription_device,
            ]
            if self.config.runtime.transcription_checkpoint is not None:
                raw_command.extend(["--checkpoint", str(self.config.runtime.transcription_checkpoint)])
            _run_process(
                raw_command,
                reference_root / "logs" / "transkun_raw.log",
                f"{song.slug} Transkun 原始 B MIDI 生成",
                self.config.project_root,
            )
        if not raw_midi_path.is_file():
            raise FileNotFoundError(f"原始 B MIDI 未生成: {raw_midi_path}")

        base_midi_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(raw_midi_path, base_midi_path)
        if _sha256_file(raw_midi_path) != _sha256_file(base_midi_path):
            raise RuntimeError(f"原始 B MIDI 发布前后 SHA-256 不一致: {song.slug}")
        _write_json(
            bpm_path,
            {
                "song_slug": song.slug,
                "detected_bpm": bpm,
                "source_mp3": str(song.source_mp3),
                "note": "BPM 只作为 sidecar 使用，不改写原始 B MIDI",
            },
        )
        render_midi_to_audio(
            midi_path=base_midi_path,
            output_wav_path=reference_wav_path,
            output_mp3_path=reference_mp3_path,
            soundfont_path=self.config.soundfont.path,
            fluidsynth_command=self.config.tools.fluidsynth,
            ffmpeg_command=self.config.tools.ffmpeg,
            sample_rate=self.config.rendering.sample_rate,
            mp3_bitrate=self.config.rendering.mp3_bitrate,
        )
        return SourceArtifacts(
            song=song,
            bpm=bpm,
            reference_midi_path=base_midi_path,
            reference_wav_path=reference_wav_path,
            reference_mp3_path=reference_mp3_path,
            reference_midi_sha256=_sha256_file(base_midi_path),
        )

    def _evaluate_candidate(
        self,
        source: SourceArtifacts,
        mode: str,
    ) -> CandidateEvaluation:
        """完成单首歌曲单候选的转换、硬约束、渲染和六维评分。"""
        candidate_root = self.config.output_root / source.song.slug / "algorithms" / mode
        candidate_result = self.run_candidate_from_midi(
            song=source.song,
            bpm=source.bpm,
            reference_midi_path=source.reference_midi_path,
            mode=mode,
            candidate_root=candidate_root,
        )
        wav_path = candidate_root / "reduced_piano.wav"
        mp3_path = candidate_root / "reduced_piano.mp3"
        render_midi_to_audio(
            midi_path=candidate_result.reduced_midi_path,
            output_wav_path=wav_path,
            output_mp3_path=mp3_path,
            soundfont_path=self.config.soundfont.path,
            fluidsynth_command=self.config.tools.fluidsynth,
            ffmpeg_command=self.config.tools.ffmpeg,
            sample_rate=self.config.rendering.sample_rate,
            mp3_bitrate=self.config.rendering.mp3_bitrate,
        )
        metric_config = self._build_converter_config(
            song=source.song,
            bpm=source.bpm,
            reference_midi_path=source.reference_midi_path,
            mode=mode,
            yaml_path=candidate_result.yaml_path,
            work_dir=candidate_root / "metric_work",
        )
        midi_metrics = calculate_midi_fidelity(
            reference_midi_path=source.reference_midi_path,
            reduced_midi_path=candidate_result.reduced_midi_path,
            config=metric_config,
        )
        audio_metrics = calculate_audio_fidelity(
            reference_wav_path=source.reference_wav_path,
            reduced_wav_path=wav_path,
            frame_seconds=self.config.audio_metrics.frame_seconds,
            n_mfcc=self.config.audio_metrics.n_mfcc,
        )
        all_metrics = {**midi_metrics.values, **audio_metrics.values}
        score = calculate_weighted_score(all_metrics, self.config.metric_weights)
        metrics_path = candidate_root / "metrics.json"
        _write_json(
            metrics_path,
            {
                "song_slug": source.song.slug,
                "mode": mode,
                "score": score,
                "metrics": all_metrics,
                "weights": dict(self.config.metric_weights),
                "reference_midi_sha256": source.reference_midi_sha256,
                "reduced_midi_sha256": _sha256_file(candidate_result.reduced_midi_path),
                "wav_sha256": _sha256_file(wav_path),
                "mp3_sha256": _sha256_file(mp3_path),
            },
        )
        return CandidateEvaluation(
            song_slug=source.song.slug,
            mode=mode,
            score=score,
            metrics=MappingProxyType(all_metrics),
            candidate_result=candidate_result,
            wav_path=wav_path,
            mp3_path=mp3_path,
            metrics_path=metrics_path,
        )

    def _load_completed_result(self) -> FullBenchmarkResult | None:
        """仅在最终产物完整可读时复用已完成实验。"""
        manifest_path = self.config.output_root / "manifest.json"
        ranking_path = self.config.output_root / "reports" / "ranking.json"
        final_report_path = self.config.output_root / "reports" / "final_report.md"
        blind_mapping_path = self.config.output_root / "blind_listening" / "mapping_sealed.json"
        if not all(path.is_file() and path.stat().st_size > 0 for path in (manifest_path, ranking_path, final_report_path, blind_mapping_path)):
            return None
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest_payload = json.load(file)
        if "artifacts" not in manifest_payload or "parameters" not in manifest_payload:
            return None
        expected_candidate_modes = [candidate.mode for candidate in self.config.candidates]
        if manifest_payload.get("candidate_modes") != expected_candidate_modes:
            return None
        if manifest_payload.get("score_aware_mode") != self.config.score_aware_mode:
            return None
        expected_parameters = {
            "reduced_velocity": self.config.reduced_velocity,
            "common_parameters": asdict(self.config.common_parameters),
            "rendering": asdict(self.config.rendering),
            "audio_metrics": asdict(self.config.audio_metrics),
            "metric_weights": dict(self.config.metric_weights),
        }
        if manifest_payload.get("parameters") != expected_parameters:
            return None
        with ranking_path.open("r", encoding="utf-8") as file:
            ranking_payload = json.load(file)
        cached_failures = ranking_payload.get("failures", {})
        if isinstance(cached_failures, dict):
            for song_failures in cached_failures.values():
                if not isinstance(song_failures, dict):
                    return None
                if any("违反硬约束" not in str(message) for message in song_failures.values()):
                    return None
        ranked_modes = tuple(item["mode"] for item in ranking_payload.get("ranking", []))
        if len(ranked_modes) < 2:
            return None
        for song in self.config.songs:
            required_paths = [
                self.config.output_root / song.slug / "reference" / "base_midi" / "song.mid",
                self.config.output_root / song.slug / "reference" / "audio" / "original_grand_piano.mp3",
            ]
            required_paths.extend(
                self.config.output_root / song.slug / "algorithms" / mode / file_name
                for mode in ranked_modes
                for file_name in ("reduced.mid", "reduced_piano.mp3", "metrics.json")
            )
            if not all(path.is_file() and path.stat().st_size > 0 for path in required_paths):
                return None
        return FullBenchmarkResult(
            manifest_path=manifest_path,
            ranking_path=ranking_path,
            final_report_path=final_report_path,
            blind_mapping_path=blind_mapping_path,
            ranked_modes=ranked_modes,
        )

    def run_full_benchmark(self) -> FullBenchmarkResult:
        """执行或严格复用完整多歌曲算法基准实验。"""
        completed_result = self._load_completed_result()
        if completed_result is not None:
            return completed_result
        preflight_report = self.preflight()
        if not preflight_report.passed:
            raise RuntimeError("实验预检失败: " + "; ".join(preflight_report.failures))

        sources = tuple(self._prepare_song_source(song) for song in self.config.songs)
        formal_modes = tuple(candidate.mode for candidate in self.config.candidates)
        evaluations: dict[str, dict[str, CandidateEvaluation]] = {mode: {} for mode in formal_modes}
        failures: dict[str, dict[str, str]] = {}

        for mode in formal_modes:
            for source in sources:
                try:
                    evaluations[mode][source.song.slug] = self._evaluate_candidate(source, mode)
                except Exception as exc:
                    failures.setdefault(mode, {})[source.song.slug] = str(exc)
                    failure_path = self.config.output_root / source.song.slug / "algorithms" / mode / "failure.json"
                    _write_json(
                        failure_path,
                        {
                            "mode": mode,
                            "song_slug": source.song.slug,
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    )

        # score_aware_theory 必须两首均成功才通过就绪门槛并进入正式排名。
        if self.config.score_aware_mode is not None:
            score_aware_evaluations: dict[str, CandidateEvaluation] = {}
            score_aware_failures: dict[str, str] = {}
            for source in sources:
                try:
                    score_aware_evaluations[source.song.slug] = self._evaluate_candidate(
                        source,
                        self.config.score_aware_mode,
                    )
                except Exception as exc:
                    score_aware_failures[source.song.slug] = str(exc)
            score_aware_ready = len(score_aware_evaluations) == len(sources) and not score_aware_failures
            _write_json(
                self.config.output_root / "score_aware_readiness.json",
                {
                    "mode": self.config.score_aware_mode,
                    "ready": score_aware_ready,
                    "success_songs": sorted(score_aware_evaluations),
                    "failures": score_aware_failures,
                },
            )
            if score_aware_ready:
                evaluations[self.config.score_aware_mode] = score_aware_evaluations
            else:
                failures[self.config.score_aware_mode] = score_aware_failures

        ranking_rows: list[dict[str, Any]] = []
        for mode, song_evaluations in evaluations.items():
            if len(song_evaluations) != len(sources):
                continue
            per_song_scores = {slug: evaluation.score for slug, evaluation in song_evaluations.items()}
            average_score = sum(per_song_scores.values()) / len(sources)
            ranking_rows.append(
                {
                    "mode": mode,
                    "average_score": average_score,
                    "per_song_scores": per_song_scores,
                }
            )
        ranking_rows.sort(key=lambda item: (-item["average_score"], item["mode"]))
        if len(ranking_rows) < 2:
            raise RuntimeError(f"完成全部歌曲的合格候选少于两个: {ranking_rows}; failures={failures}")

        reports_root = self.config.output_root / "reports"
        ranking_path = reports_root / "ranking.json"
        _write_json(
            ranking_path,
            {
            "conclusion_scope": f"仅限本基准{len(sources)}首歌曲",
                "ranking": ranking_rows,
                "failures": failures,
            },
        )

        top_modes = tuple(row["mode"] for row in ranking_rows[:2])
        blind_root = self.config.output_root / "blind_listening"
        blind_root.mkdir(parents=True, exist_ok=True)
        blind_mapping: dict[str, dict[str, str]] = {}
        labels = ("A", "B")
        for source in sources:
            song_mapping: dict[str, str] = {}
            for label, mode in zip(labels, top_modes):
                source_mp3 = evaluations[mode][source.song.slug].mp3_path
                target_mp3 = blind_root / f"{source.song.slug}_{label}.mp3"
                shutil.copy2(source_mp3, target_mp3)
                song_mapping[label] = mode
            blind_mapping[source.song.slug] = song_mapping
        blind_mapping_path = blind_root / "mapping_sealed.json"
        _write_json(blind_mapping_path, blind_mapping)

        manifest_path = self.config.output_root / "manifest.json"
        artifact_manifest: dict[str, Any] = {}
        for source in sources:
            song_artifacts: dict[str, Any] = {
                "reference": {
                    "midi": {
                        "path": str(source.reference_midi_path),
                        "sha256": _sha256_file(source.reference_midi_path),
                    },
                    "wav": {
                        "path": str(source.reference_wav_path),
                        "sha256": _sha256_file(source.reference_wav_path),
                    },
                    "mp3": {
                        "path": str(source.reference_mp3_path),
                        "sha256": _sha256_file(source.reference_mp3_path),
                    },
                },
                "candidates": {},
            }
            for mode, song_evaluations in evaluations.items():
                evaluation = song_evaluations.get(source.song.slug)
                if evaluation is None:
                    continue
                candidate_result = evaluation.candidate_result
                song_artifacts["candidates"][mode] = {
                    "yaml": {
                        "path": str(candidate_result.yaml_path),
                        "sha256": _sha256_file(candidate_result.yaml_path),
                    },
                    "midi": {
                        "path": str(candidate_result.reduced_midi_path),
                        "sha256": _sha256_file(candidate_result.reduced_midi_path),
                    },
                    "wav": {
                        "path": str(evaluation.wav_path),
                        "sha256": _sha256_file(evaluation.wav_path),
                    },
                    "mp3": {
                        "path": str(evaluation.mp3_path),
                        "sha256": _sha256_file(evaluation.mp3_path),
                    },
                    "metrics": {
                        "path": str(evaluation.metrics_path),
                        "sha256": _sha256_file(evaluation.metrics_path),
                    },
                }
            artifact_manifest[source.song.slug] = song_artifacts
        manifest_payload = {
            "experiment_id": self.config.experiment_id,
            "generated_at": datetime.now().astimezone().isoformat(),
            "source_songs": {
                source.song.slug: {
                    "source_mp3": str(source.song.source_mp3),
                    "source_mp3_sha256": _sha256_file(source.song.source_mp3),
                    "bpm_sidecar": source.bpm,
                    "reference_midi": str(source.reference_midi_path),
                    "reference_midi_sha256": source.reference_midi_sha256,
                }
                for source in sources
            },
            "soundfont": {
                "path": str(self.config.soundfont.path),
                "sha256": _sha256_file(self.config.soundfont.path),
                "source_url": self.config.soundfont.source_url,
                "license_url": self.config.soundfont.license_url,
                "expected_sha256": self.config.soundfont.expected_sha256,
            },
            "ranking_path": str(ranking_path),
            "top_blind_modes": top_modes,
            "failures": failures,
            "candidate_modes": [candidate.mode for candidate in self.config.candidates],
            "score_aware_mode": self.config.score_aware_mode,
            "parameters": {
                "reduced_velocity": self.config.reduced_velocity,
                "common_parameters": asdict(self.config.common_parameters),
                "rendering": asdict(self.config.rendering),
                "audio_metrics": asdict(self.config.audio_metrics),
                "metric_weights": dict(self.config.metric_weights),
            },
            "artifacts": artifact_manifest,
            "tools": {
                "fluidsynth": {
                    "path": self.config.tools.fluidsynth,
                    "sha256": _sha256_file(Path(self.config.tools.fluidsynth)),
                },
                "ffmpeg": {
                    "path": self.config.tools.ffmpeg,
                    "sha256": _sha256_file(Path(self.config.tools.ffmpeg)),
                },
            },
            "blind_mapping": {
                "path": str(blind_mapping_path),
                "sha256": _sha256_file(blind_mapping_path),
            },
        }
        _write_json(manifest_path, manifest_payload)

        final_report_path = reports_root / "final_report.md"
        reports_root.mkdir(parents=True, exist_ok=True)
        report_lines = [
            f"# {len(sources)}歌曲缩编算法客观评测报告",
            "",
            "> 作者：JucieOvo",
            f"> 结论范围：仅限本基准{len(sources)}首歌曲",
            "",
            "## 客观排名",
            "",
            f"| 排名 | 算法 | {len(sources)}歌曲等权得分 |",
            "|------|------|----------------:|",
        ]
        for index, row in enumerate(ranking_rows, start=1):
            report_lines.append(f"| {index} | `{row['mode']}` | {row['average_score']:.6f} |")
        report_lines.extend(
            [
                "",
                "## 盲听阶段",
                "",
                f"客观前两名已匿名化为 A/B：`{top_modes[0]}` 与 `{top_modes[1]}`。映射文件已封存，等待用户完成盲听后再公开。",
                "",
            f"本报告不把{len(sources)}首歌曲结果外推为跨曲风全局最优结论。",
            ]
        )
        final_report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

        return FullBenchmarkResult(
            manifest_path=manifest_path,
            ranking_path=ranking_path,
            final_report_path=final_report_path,
            blind_mapping_path=blind_mapping_path,
            ranked_modes=tuple(row["mode"] for row in ranking_rows),
        )
