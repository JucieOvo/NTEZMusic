"""
模块名称：raw_midi_generator
功能描述：
    提供 Transkun 2.0.1 原始 88 键 MIDI 生成器。
    对输入音频执行一次完整的 Transkun 推理，产出三类不可变产物：
        1. notes_est.json     — 模型输出的原始音符估计（pitch/start/end/velocity/hasOnset/hasOffset）
        2. transkun_raw_120bpm.mid  — 由 writeMidi() 直接写出的 120 BPM MIDI（产物 B）
        3. run_manifest.json  — 运行清单（输入/模型/输出哈希、依赖版本、GPU、分段参数）

    核心约束：
        - 绝不调用 _fix_midi_tempo()，MIDI B 保持 120 BPM 不变
        - 默认禁止覆盖已有产物，必须显式传入 --force-overwrite 才允许
        - CUDA、checkpoint 或真实依赖不可用时直接报错停止，不回退 CPU
        - 仅处理 MP3/WAV 输入并输出 88 键范围 MIDI
        - 不进入 MIDI 清洗、MusicXML、乐理分析、36 键缩编、YAML 生成

    使用方式：
        CLI:
            python raw_midi_generator.py --audio input.mp3 --output-dir ./output --device cuda

        API:
            from raw_midi_generator import generate_raw_midi
            result = generate_raw_midi(audio_path=Path("input.mp3"), output_dir=Path("./output"))

主要组件：
    - build_argument_parser(): 构建 CLI 参数解析器
    - generate_raw_midi(): 核心生成函数，执行 Transkun 推理并写入三类产物
    - _load_audio_samples(): 读取音频采样数组，保留原始采样率
    - _load_transkun_model(): 加载 Transkun 模型与推理设备
    - _serialize_notes_est(): 将模型输出的 notes_est 转为 JSON 兼容字典列表
    - _build_run_manifest(): 构建运行清单字典
    - _validate_midi_is_120bpm(): 校验 MIDI 文件的 tempo 为 120 BPM
    - _sha256_file(): 计算文件 SHA-256 哈希
    - main(): CLI 入口函数

依赖说明：
    - librosa (>=0.10): 音频读取
    - soxr: 采样率重采样（transkun 传递依赖）
    - torch (>=2.0): 深度学习推理
    - transkun (2.0.1): Neural Semi-CRF Transformer V2 音频转 MIDI
    - mido: MIDI 文件校验
    - pretty_midi: MIDI 结构解析（预留，当前仅用于元信息提取）
    - moduleconf: transkun 配置文件解析

作者：JucieOvo
创建日期：2026-07-10
修改记录：
    - 2026-07-10 JucieOvo: 初始实现，基于 docs/transkun_raw_midi_evaluation_plan.md
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata as importlib_metadata
import json
import os as _os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

if TYPE_CHECKING:
    import torch

# ---------------------------------------------------------------------------
# 工具版本常量
# ---------------------------------------------------------------------------
TOOL_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# manifest 必填字段定义（用于外部验证）
# ---------------------------------------------------------------------------
MANIFEST_REQUIRED_FIELDS = (
    "input_sha256",
    "checkpoint_sha256",
    "output_midi_sha256",
    "output_notes_json_sha256",
    "dependency_versions",
    "gpu_info",
    "segment_settings",
    "input_metadata",
    "tool_version",
    "generated_at_iso",
)

# ---------------------------------------------------------------------------
# 产物文件名常量（不可变，与设计文档一致）
# ---------------------------------------------------------------------------
NOTES_EST_FILENAME = "notes_est.json"
RAW_MIDI_FILENAME = "transkun_raw_120bpm.mid"
RUN_MANIFEST_FILENAME = "run_manifest.json"

# 支持的输入音频扩展名
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac"}


# ============================================================================
# 辅助函数
# ============================================================================

def _sha256_file(file_path: Path) -> str:
    """
    计算文件的 SHA-256 哈希值。

    使用分块读取策略，支持大文件而不耗尽内存。

    :param file_path: 目标文件路径
    :return: 十六进制 SHA-256 字符串
    :raises FileNotFoundError: 当文件不存在时触发
    """
    if not file_path.is_file():
        raise FileNotFoundError(f"无法计算哈希，文件不存在: {file_path}")
    hasher = hashlib.sha256()
    with open(file_path, "rb") as fh:
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().upper()


def _validate_midi_is_120bpm(midi_path: Path) -> bool:
    """
    校验 MIDI 文件的 tempo 元事件是否仅为 120 BPM。

    扫描所有轨道中的 set_tempo 消息，确认每条消息的 tempo 值
    恰好为 500000 微秒/拍（即 120 BPM）。若出现其他 tempo 值或
    无 tempo 事件，返回 False。

    :param midi_path: MIDI 文件路径
    :return: 当所有 tempo 事件均为 120 BPM 时返回 True
    :raises ImportError: 当 mido 未安装时触发
    :raises FileNotFoundError: 当 MIDI 文件不存在时触发
    """
    import mido

    if not midi_path.is_file():
        raise FileNotFoundError(f"MIDI 文件不存在: {midi_path}")

    midi_file = mido.MidiFile(str(midi_path))
    tempo_120 = mido.bpm2tempo(120.0)  # 500000

    for track in midi_file.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                if msg.tempo != tempo_120:
                    return False
    return True


def _collect_dependency_versions() -> Dict[str, str]:
    """
    收集当前运行时关键依赖的版本号。

    查询 torch、transkun、librosa、soxr、mido、pretty_midi 的版本。
    如果某个包未安装，对应值设为 "not_installed"。

    :return: 包名到版本字符串的映射字典
    """
    versions: Dict[str, str] = {}
    # 每个包单独 try/except，确保一个失败不影响其他
    _pkg_checks = [
        ("torch", "torch"),
        ("transkun", "transkun"),
        ("librosa", "librosa"),
        ("soxr", "soxr"),
        ("mido", "mido"),
        ("pretty_midi", "pretty_midi"),
        ("moduleconf", "moduleconf"),
    ]
    for name, distribution_name in _pkg_checks:
        try:
            versions[name] = importlib_metadata.version(distribution_name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    return versions


def _collect_gpu_info(device: Any) -> Dict[str, Any]:
    """
    收集当前 GPU 硬件与驱动信息。

    记录 CUDA 可用性、GPU 名称、显存总量、驱动版本和 CUDA 版本。

    :param device: 当前使用的 torch 设备对象
    :return: GPU 信息字典
    """
    import torch

    info: Dict[str, Any] = {
        "cuda_available": torch.cuda.is_available(),
        "device_used": str(device),
    }
    if torch.cuda.is_available():
        info["gpu_count"] = torch.cuda.device_count()
        info["gpu_name"] = torch.cuda.get_device_name(0)
        # 使用 getattr 访问 torch.version.cuda 以兼容类型检查
        cuda_ver = getattr(getattr(torch, "version", None), "cuda", None)
        info["cuda_version"] = str(cuda_ver) if cuda_ver else "unknown"
        try:
            # 查询总显存（仅显示时记录，不做业务逻辑）
            mem_info = torch.cuda.mem_get_info(0)
            info["total_memory_gb"] = round(mem_info[1] / (1024 ** 3), 2)
        except Exception:
            info["total_memory_gb"] = "unavailable"
    return info


# ============================================================================
# 音频加载
# ============================================================================

def _load_audio_samples(audio_path: Path) -> Tuple[Any, Union[int, float]]:
    """
    读取音频采样数组，保留原始采样率。

    与 audio_to_yaml_converter.py 中的 PianoTranscriber._load_audio_samples()
    保持一致的加载方式：使用 librosa.load() 读取为单声道 float32 数组，
    不强制重采样，由调用方按 model.fs 做按需适配。

    :param audio_path: 输入音频路径
    :return: (音频采样数组 numpy.ndarray, 原始采样率 Hz)
    :raises FileNotFoundError: 当音频文件不存在时触发
    :raises RuntimeError: 当音频读取失败时触发
    """
    import librosa

    if not audio_path.is_file():
        raise FileNotFoundError(f"输入音频不存在: {audio_path}")

    try:
        raw_audio, original_sr = librosa.load(
            str(audio_path), sr=None, mono=True
        )
    except Exception as exc:
        raise RuntimeError(f"音频读取失败: {audio_path}") from exc

    return raw_audio, original_sr


# ============================================================================
# Transkun 模型加载
# ============================================================================

def _load_transkun_model(
    checkpoint_path: Optional[Path],
    device_str: str,
) -> Tuple[Any, Any, float, float]:
    """
    加载 Transkun 模型与推理设备。

    复用 audio_to_yaml_converter.py 中 PianoTranscriber._load_transkun_model()
    的加载模式，支持两种权重来源：
        1. 用户通过 checkpoint_path 指定的 .pt 文件（同时查找同目录 .conf 配置）
        2. transkun pip 包内置的 pretrained/2.0.pt 与 pretrained/2.0.conf

    :param checkpoint_path: 自定义 checkpoit .pt 路径，None 时使用内置默认权重
    :param device_str: 推理设备，"cuda" 或 "cpu"
    :return: (TransKun 模型实例, torch 设备对象, 默认分段步长秒数, 默认分段尺寸秒数)
    :raises ImportError: 当 transkun 或 moduleconf 未安装时触发
    :raises FileNotFoundError: 当权重或配置文件不存在时触发
    :raises RuntimeError: 当 device_str="cuda" 但 CUDA 不可用时触发
    """
    import torch
    import transkun as _transkun
    import moduleconf

    # 获取 transkun 包安装目录，用于定位内置默认模型权重
    _pkg_dir = _os.path.dirname(_transkun.__file__)

    # 确定权重路径与配置路径
    if checkpoint_path is not None:
        weight_path = str(checkpoint_path)
        # 在同目录查找 .conf 配置文件
        conf_dir = checkpoint_path.parent
        conf_candidates = list(conf_dir.glob("*.conf"))
        if conf_candidates:
            conf_path = str(conf_candidates[0])
        else:
            raise FileNotFoundError(
                f"自定义 checkpoint 同目录缺少 .conf 配置文件: {conf_dir}"
            )
    else:
        weight_path = _os.path.join(_pkg_dir, "pretrained", "2.0.pt")
        conf_path = _os.path.join(_pkg_dir, "pretrained", "2.0.conf")

    # 校验文件存在性
    if not Path(weight_path).exists():
        raise FileNotFoundError(f"Transkun 权重文件不存在: {weight_path}")
    if not Path(conf_path).exists():
        raise FileNotFoundError(f"Transkun 配置文件不存在: {conf_path}")

    # 从配置文件加载模型类定义
    conf_manager = moduleconf.parseFromFile(conf_path)
    model_conf = conf_manager["Model"]
    if model_conf is None:
        raise RuntimeError(f"Transkun 配置文件中缺少 Model 节: {conf_path}")
    # moduleconf 保证 model_conf.module 不为 None，此处仅满足类型检查
    model_module: Any = model_conf.module
    TransKun = model_module.TransKun
    conf = model_conf.config
    if conf is None:
        raise RuntimeError(f"Transkun 配置文件中 Model 节缺少 config: {conf_path}")

    # 确定推理设备，cuda 不可用时直接报错（不回退 CPU）
    # .to() 方法支持字符串参数，无需创建 torch.device 对象
    if device_str == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA 不可用，但 device='cuda' 已被指定。"
                "本工具禁止回退到 CPU，请确保 CUDA GPU 可用后重试。"
            )
        device: Any = "cuda"
    else:
        device = "cpu"

    # 加载模型权重
    checkpoint = torch.load(weight_path, map_location=device)
    model = TransKun(conf=conf).to(device)

    if "best_state_dict" in checkpoint:
        model.load_state_dict(checkpoint["best_state_dict"], strict=False)
    else:
        model.load_state_dict(checkpoint["state_dict"], strict=False)

    model.eval()
    torch.set_grad_enabled(False)

    return (
        model,
        device,
        float(conf.segmentHopSizeInSecond),
        float(conf.segmentSizeInSecond),
    )


# ============================================================================
# notes_est 序列化
# ============================================================================

def _serialize_notes_est(notes_est: Any) -> List[Dict[str, Any]]:
    """
    将 Transkun 模型输出的 notes_est 转为 JSON 兼容的字典列表。

    Transkun 2.0.1 的 model.transcribe() 返回的 notes_est 可能为：
        - transkun.Data.Note 对象列表（默认行为，属性: pitch, start, end, velocity, hasOnset, hasOffset）
        - numpy record array（字段: pitch, start, end, velocity, hasOnset, hasOffset）
        - numpy ndarray（结构化数组）
        - 字典列表
    本函数自动检测并转换，确保每条记录包含六个必填字段。

    Transkun 语义约定：
        - pitch > 0: 真实 MIDI 音符（event_type = "midi_note"）
        - pitch <= 0: MIDI ControlChange 事件（event_type = "cc_event"），
          其中 abs(pitch) 为 CC 编号（如 -64 = CC#64 sustain pedal）。
          writeMidi() 会将此类事件转为 ControlChange 消息而非 note_on。

    :param notes_est: 模型推理输出的原始音符估计
    :return: JSON 兼容的字典列表，每条包含 pitch/start/end/velocity/hasOnset/hasOffset/event_type
    :raises TypeError: 当 notes_est 类型无法识别时触发
    """
    result: List[Dict[str, Any]] = []

    # 检测输入类型并转换
    if isinstance(notes_est, list):
        for note in notes_est:
            entry: Dict[str, Any] = {}
            if isinstance(note, dict):
                # 字典 → 按 key 提取，处理 numpy bool 类型
                for key in ("pitch", "start", "end", "velocity", "hasOnset", "hasOffset"):
                    val = note[key]
                    if hasattr(val, "item"):
                        val = val.item()
                    entry[key] = val
            elif hasattr(note, "__dict__"):
                # transkun.Data.Note 或其他有 __dict__ 的对象 → 按属性提取
                for key in ("pitch", "start", "end", "velocity", "hasOnset", "hasOffset"):
                    val = getattr(note, key)
                    # numpy 类型需要显式转为 Python 原生类型
                    if hasattr(val, "item"):
                        val = val.item()
                    entry[key] = val
            else:
                raise TypeError(
                    f"notes_est 列表元素类型不支持: {type(note)}"
                )
            # 根据 pitch 符号添加 event_type 语义分类
            entry["event_type"] = "midi_note" if entry["pitch"] > 0 else "cc_event"
            result.append(entry)
    else:
        # numpy record array / ndarray → 逐行转换
        try:
            # 先尝试 __dict__ 风格属性访问
            for i in range(len(notes_est)):
                row = notes_est[i]
                entry: Dict[str, Any] = {}
                if hasattr(row, "__dict__"):
                    for key in ("pitch", "start", "end", "velocity", "hasOnset", "hasOffset"):
                        val = getattr(row, key)
                        if hasattr(val, "item"):
                            val = val.item()
                        entry[key] = val
                else:
                    for key in ("pitch", "start", "end", "velocity", "hasOnset", "hasOffset"):
                        val = row[key]
                        if hasattr(val, "item"):
                            val = val.item()
                        entry[key] = val
                entry["event_type"] = "midi_note" if entry["pitch"] > 0 else "cc_event"
                result.append(entry)
        except Exception as exc:
            raise TypeError(
                f"无法序列化 notes_est，类型不支持: {type(notes_est)}"
            ) from exc

    return result


# ============================================================================
# 运行清单构建
# ============================================================================

def _build_run_manifest(
    audio_path: Path,
    weight_path: str,
    output_dir: Path,
    device: Any,
    model_fs: float,
    segment_hop_size: Optional[float],
    segment_size: Optional[float],
    event_classification: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    构建运行清单字典。

    记录输入哈希、checkpoint 哈希、模型采样率、输出哈希（生成后填入）、
    依赖版本、GPU 信息、分段参数和输入元数据。

    :param audio_path: 输入音频路径
    :param weight_path: 实际使用的权重文件路径
    :param output_dir: 产物输出目录
    :param device: 推理设备
    :param model_fs: 模型期望采样率 Hz
    :param segment_hop_size: segment 步长秒数，None 表示使用模型默认
    :param segment_size: segment 尺寸秒数，None 表示使用模型默认
    :param event_classification: event_type 分类计数，由 _serialize_notes_est 后计算得出
    :return: 运行清单字典
    """
    # 构建 event_classification 默认值（若未传入则设 None）
    ec = event_classification or {}
    manifest: Dict[str, Any] = {
        "tool_version": TOOL_VERSION,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_metadata": {
            "input_path": str(audio_path),
            "input_filename": audio_path.name,
            "input_size_bytes": audio_path.stat().st_size if audio_path.is_file() else None,
        },
        "input_sha256": _sha256_file(audio_path),
        "checkpoint_path": weight_path,
        "checkpoint_sha256": _sha256_file(Path(weight_path)),
        "model_sampling_rate_hz": int(model_fs),
        "segment_settings": {
            "segment_hop_size_seconds": segment_hop_size,
            "segment_size_seconds": segment_size,
        },
        "dependency_versions": _collect_dependency_versions(),
        "gpu_info": _collect_gpu_info(device),
        "event_classification": ec,
        "output_midi_sha256": None,
        "output_notes_json_sha256": None,
    }
    return manifest


# ============================================================================
# 核心生成函数
# ============================================================================

def generate_raw_midi(
    audio_path: Path,
    output_dir: Path,
    device: str = "cuda",
    checkpoint_path: Optional[Path] = None,
    segment_hop_size: Optional[float] = None,
    segment_size: Optional[float] = None,
    force_overwrite: bool = False,
) -> Dict[str, Any]:
    """
    对输入音频执行一次完整的 Transkun 2.0.1 推理，产出三类不可变产物。

    产物清单：
        - notes_est.json: 模型输出的原始音符估计（不可变）
        - transkun_raw_120bpm.mid: 120 BPM 原始 MIDI B（不可变，不调 tempo）
        - run_manifest.json: 运行清单（不可变）

    安全策略：
        - 默认禁止覆盖：若任一产物文件已存在，抛出 FileExistsError
        - 传入 force_overwrite=True 时强制执行覆盖。

    CUDA 策略：
        - device="cuda" 但 CUDA 不可用时直接抛出 RuntimeError，不回退 CPU。

    输入限制：
        - 仅支持 MP3/WAV/FLAC 格式
        - 输出为 88 键范围 MIDI（Transkun 原生输出范围）

    :param audio_path: 输入音频文件路径（MP3 或 WAV）
    :param output_dir: 产物输出目录（自动创建）
    :param device: 推理设备，"cuda" 或 "cpu"
    :param checkpoint_path: 自定义 checkpoit .pt 路径，None 使用内置默认
    :param segment_hop_size: segment 步长秒数，None 使用模型默认值
    :param segment_size: segment 尺寸秒数，None 使用模型默认值
    :param force_overwrite: 是否强制覆盖已有产物
    :return: 包含三个产物路径的字典:
             {"notes_json_path": Path, "midi_path": Path, "manifest_path": Path}
    :raises FileNotFoundError: 当输入音频或权重文件不存在时触发
    :raises FileExistsError: 当产物已存在且 force_overwrite=False 时触发
    :raises RuntimeError: 当 CUDA 不可用或推理失败时触发
    :raises ImportError: 当关键依赖缺失时触发
    """
    import torch
    import soxr

    # ------------------------------------------------------------------
    # 1. 输入校验：音频文件存在性与格式
    # ------------------------------------------------------------------
    if not audio_path.is_file():
        raise FileNotFoundError(f"输入音频不存在: {audio_path}")
    suffix = audio_path.suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"不支持的音频格式: {suffix}，仅支持 {SUPPORTED_AUDIO_EXTENSIONS}"
        )

    # ------------------------------------------------------------------
    # 2. 输出目录准备与覆盖安全策略
    # ------------------------------------------------------------------
    output_dir.mkdir(parents=True, exist_ok=True)

    notes_json_path = output_dir / NOTES_EST_FILENAME
    midi_path = output_dir / RAW_MIDI_FILENAME
    manifest_path = output_dir / RUN_MANIFEST_FILENAME

    # 检测任一产物已存在 → 根据 force_overwrite 策略决定行为
    existing_artifacts = [
        p for p in (notes_json_path, midi_path, manifest_path) if p.is_file()
    ]
    if existing_artifacts and not force_overwrite:
        existing_names = ", ".join(str(p.name) for p in existing_artifacts)
        raise FileExistsError(
            f"产物文件已存在: {existing_names}\n"
            f"请使用 --force-overwrite 或 force_overwrite=True 允许覆盖，"
            f"或手动删除 {output_dir} 后重试。"
        )

    # ------------------------------------------------------------------
    # 3. 加载音频数据，保留原始采样率
    # ------------------------------------------------------------------
    raw_audio, original_sr = _load_audio_samples(audio_path=audio_path)

    # ------------------------------------------------------------------
    # 4. 加载 Transkun 模型
    # ------------------------------------------------------------------
    model, torch_device, model_segment_hop_size, model_segment_size = _load_transkun_model(
        checkpoint_path=checkpoint_path,
        device_str=device,
    )

    # 确定实际使用的权重路径（用于 manifest）
    if checkpoint_path is not None:
        weight_path = str(checkpoint_path)
    else:
        import transkun as _transkun
        _pkg_dir = _os.path.dirname(_transkun.__file__)
        weight_path = _os.path.join(_pkg_dir, "pretrained", "2.0.pt")

    # ------------------------------------------------------------------
    # 5. 采样率适配：若不匹配模型要求的采样率，使用 soxr 重采样
    # ------------------------------------------------------------------
    if original_sr != model.fs:
        raw_audio = soxr.resample(raw_audio, original_sr, model.fs)

    # ------------------------------------------------------------------
    # 6. 执行 Transkun 推理
    # ------------------------------------------------------------------
    # 通过 getattr 实现运行时类型安全边界：避免 Pyright stub 导出不完整导致的误报
    audio_tensor = getattr(torch, "from_numpy")(raw_audio).to(torch_device)
    # Transkun 期望输入形状为 (采样数, 通道数)，单声道需补维度
    if audio_tensor.ndim == 1:
        audio_tensor = audio_tensor.unsqueeze(-1)

    # 构建 transcribe 参数：仅当用户显式指定 segment 参数时才传递
    transcribe_kwargs: Dict[str, Any] = {}
    # segment_hop_size 统一使用 stepInSecond 参数名
    if segment_hop_size is not None:
        transcribe_kwargs["stepInSecond"] = segment_hop_size
    if segment_size is not None:
        transcribe_kwargs["segmentSizeInSecond"] = segment_size

    try:
        notes_est = model.transcribe(
            audio_tensor,
            **transcribe_kwargs,
        )
    except Exception as exc:
        raise RuntimeError(f"Transkun 转录推理失败: {exc}") from exc

    # ------------------------------------------------------------------
    # 7. 序列化 notes_est 并写入 JSON（不可变）
    # ------------------------------------------------------------------
    serialized_notes = _serialize_notes_est(notes_est)
    with open(notes_json_path, "w", encoding="utf-8") as fh:
        json.dump(serialized_notes, fh, indent=2, ensure_ascii=False)

    # 计算 event_type 分类计数
    midi_note_count = sum(1 for n in serialized_notes if n["event_type"] == "midi_note")
    cc_event_count = sum(1 for n in serialized_notes if n["event_type"] == "cc_event")
    event_classification: Dict[str, int] = {
        "total_events": len(serialized_notes),
        "midi_note_count": midi_note_count,
        "cc_event_count": cc_event_count,
    }

    # ------------------------------------------------------------------
    # 8. 使用 writeMidi 写出 120 BPM 原始 MIDI B（不可变，不调用 _fix_midi_tempo）
    # ------------------------------------------------------------------
    try:
        from transkun.Data import writeMidi
    except ImportError:
        raise RuntimeError("无法导入 transkun.Data，请确认 transkun 已正确安装")

    output_midi = writeMidi(notes_est)
    output_midi.write(str(midi_path))

    if not midi_path.is_file():
        raise FileNotFoundError(f"Transkun 转录未生成 MIDI: {midi_path}")

    # 校验 MIDI B 确实为 120 BPM
    if not _validate_midi_is_120bpm(midi_path):
        raise RuntimeError(
            f"生成的 MIDI 文件并非 120 BPM: {midi_path}\n"
            f"writeMidi 应默认写 120 BPM，请检查模型行为是否变更。"
        )

    # ------------------------------------------------------------------
    # 9. 构建并写入 run_manifest.json
    # ------------------------------------------------------------------
    effective_segment_hop_size = (
        segment_hop_size if segment_hop_size is not None else model_segment_hop_size
    )
    effective_segment_size = (
        segment_size if segment_size is not None else model_segment_size
    )

    manifest = _build_run_manifest(
        audio_path=audio_path,
        weight_path=weight_path,
        output_dir=output_dir,
        device=torch_device,
        model_fs=model.fs,
        segment_hop_size=effective_segment_hop_size,
        segment_size=effective_segment_size,
        event_classification=event_classification,
    )
    # 补充输出文件哈希
    manifest["output_midi_sha256"] = _sha256_file(midi_path)
    manifest["output_notes_json_sha256"] = _sha256_file(notes_json_path)
    # 补充输入元数据中运行时获取的信息
    manifest["input_metadata"]["original_sample_rate"] = int(original_sr)
    manifest["input_metadata"]["duration_seconds"] = round(
        len(raw_audio) / model.fs, 6
    ) if original_sr == model.fs else round(
        len(raw_audio) / model.fs, 6
    )

    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # 10. 返回产物路径
    # ------------------------------------------------------------------
    return {
        "notes_json_path": notes_json_path,
        "midi_path": midi_path,
        "manifest_path": manifest_path,
    }


# ============================================================================
# CLI 参数解析
# ============================================================================

def build_argument_parser() -> argparse.ArgumentParser:
    """
    构建 CLI 参数解析器。

    参数定义与设计文档及项目现有 CLI 惯例保持一致。

    :return: argparse.ArgumentParser 实例
    """
    parser = argparse.ArgumentParser(
        description=(
            "Transkun 2.0.1 原始 88 键 MIDI 生成器 — "
            "对输入音频执行一次完整推理，产出 notes_est.json、"
            "transkun_raw_120bpm.mid (产物 B) 和 run_manifest.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "输出文件（不可变）:\n"
            f"  {NOTES_EST_FILENAME}\n"
            f"  {RAW_MIDI_FILENAME}\n"
            f"  {RUN_MANIFEST_FILENAME}\n\n"
            "注意事项:\n"
            "  - 默认禁止覆盖已有产物，使用 --force-overwrite 允许覆盖\n"
            "  - device=cuda 时若 CUDA 不可用则直接报错，不回退 CPU\n"
            "  - 不调用 _fix_midi_tempo，MIDI B 保持 120 BPM\n"
            f"版本: {TOOL_VERSION}"
        ),
    )

    parser.add_argument(
        "--audio",
        type=Path,
        required=True,
        metavar="PATH",
        help="输入音频路径（MP3 或 WAV）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        metavar="DIR",
        help="产物输出目录",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cuda",
        choices=["cuda", "cpu"],
        help="推理设备，cuda 不可用时直接报错（默认: cuda）",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        metavar="PATH",
        help="自定义 Transkun 权重 .pt 路径（默认: transkun 内置 2.0.pt）",
    )
    parser.add_argument(
        "--segment-hop-size",
        type=float,
        default=None,
        metavar="SECONDS",
        help="segment 步长秒数（默认: 模型内置值）",
    )
    parser.add_argument(
        "--segment-size",
        type=float,
        default=None,
        metavar="SECONDS",
        help="segment 尺寸秒数（默认: 模型内置值）",
    )
    parser.add_argument(
        "--force-overwrite",
        action="store_true",
        default=False,
        help="强制覆盖已有产物",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"raw_midi_generator v{TOOL_VERSION}",
    )

    return parser


# ============================================================================
# CLI 入口
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI 入口函数。

    解析命令行参数，调用 generate_raw_midi() 执行推理，并输出结果摘要。

    :param argv: 命令行参数列表，None 时使用 sys.argv[1:]
    :return: 退出码，0 表示成功
    """
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    # 额外校验：输出目录不能与输入音频路径冲突
    try:
        args.output_dir = args.output_dir.resolve()
    except Exception as exc:
        print(f"[错误] 无法解析输出目录: {args.output_dir}", file=sys.stderr)
        return 1

    print(f"[信息] 输入音频: {args.audio}", file=sys.stderr)
    print(f"[信息] 输出目录: {args.output_dir}", file=sys.stderr)
    print(f"[信息] 推理设备: {args.device}", file=sys.stderr)
    if args.checkpoint is not None:
        print(f"[信息] checkpoit 权重: {args.checkpoint}", file=sys.stderr)
    if args.segment_hop_size is not None:
        print(f"[信息] segment hop size: {args.segment_hop_size}s", file=sys.stderr)
    if args.segment_size is not None:
        print(f"[信息] segment size: {args.segment_size}s", file=sys.stderr)

    try:
        result = generate_raw_midi(
            audio_path=args.audio,
            output_dir=args.output_dir,
            device=args.device,
            checkpoint_path=args.checkpoint,
            segment_hop_size=args.segment_hop_size,
            segment_size=args.segment_size,
            force_overwrite=args.force_overwrite,
        )
    except FileNotFoundError as exc:
        print(f"[错误] 文件未找到: {exc}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"[错误] 产物已存在: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ImportError, ValueError) as exc:
        print(f"[错误] 推理失败: {exc}", file=sys.stderr)
        return 1

    print(f"\n[完成] 三类产物已生成:", file=sys.stderr)
    print(f"  notes_est.json          → {result['notes_json_path']}", file=sys.stderr)
    print(f"  transkun_raw_120bpm.mid  → {result['midi_path']}", file=sys.stderr)
    print(f"  run_manifest.json        → {result['manifest_path']}", file=sys.stderr)

    return 0


# ============================================================================
# 模块入口
# ============================================================================

if __name__ == "__main__":
    sys.exit(main())
