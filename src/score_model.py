"""
模块名称：score_model
功能描述：
    定义乐谱语义感知缩编第一阶段 MVP 使用的核心数据模型。

主要组件：
    - ScoreRegularizationConfig: MIDI 到五线谱合规化的运行配置。
    - CanonicalNoteEvent: 清洗后的 MIDI 音符事件。
    - BeatGrid: 推断出的节拍与小节网格。
    - QuantizedNoteEvent: 对齐到乐谱网格后的音符事件。
    - StaffNotationBuildResult: MusicXML 构建与导出结果。
    - NotationValidationReport: 五线谱合规校验报告。

依赖说明：
    - dataclasses: 用于定义不可变或结构化数据对象。
    - pathlib: 用于保存真实文件路径。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from music21.stream.base import Score


@dataclass(frozen=True)
class ScoreRegularizationConfig:
    """
    乐谱合规化 MVP 配置。

    职责：
        集中保存 MIDI 清洗、节拍推断、量化和 MusicXML 导出所需的基础参数，
        避免各模块使用隐藏默认值。

    :param bpm: 输入 MIDI 采用的全局 BPM
    :param beat_unit: 拍号分母，第一阶段主要使用 4
    :param quantize_beat: 量化步长，单位为拍
    :param time_signature: 目标拍号文本，例如 4/4
    :param key_signature_sharps: 调号升降号数量，0 表示 C 大调 / A 小调无升降号
    :param merge_sustained_gap_beats: 同音碎片合并允许的最大间隔拍数
    :param onset_cluster_window_beats: 和弦起音聚类窗口拍数
    :param min_noise_duration_beats: 极短噪声音过滤时长阈值
    :param min_noise_velocity: 极弱噪声音过滤力度阈值
    :param work_dir: 中间产物输出目录
    """

    bpm: float
    beat_unit: int
    quantize_beat: float
    time_signature: str
    key_signature_sharps: int
    merge_sustained_gap_beats: float
    onset_cluster_window_beats: float
    min_noise_duration_beats: float
    min_noise_velocity: int
    work_dir: Path


@dataclass(frozen=True)
class CanonicalNoteEvent:
    """
    清洗后的 MIDI 音符事件。

    职责：
        保存经过同音碎片合并、起音聚类和噪声过滤后的音符事实，作为后续量化与
        五线谱构建的统一输入。

    :param note_id: 清洗后音符唯一编号
    :param pitch: MIDI 音高编号
    :param start_beat: 清洗后的起音拍点
    :param end_beat: 清洗后的结束拍点
    :param velocity: MIDI 力度
    :param duration_beats: 持续拍数
    :param source_note_ids: 来源原始 MIDI 音符编号列表
    :param confidence: 当前音符保留置信度
    """

    note_id: int
    pitch: int
    start_beat: float
    end_beat: float
    velocity: int
    duration_beats: float
    source_note_ids: tuple[int, ...]
    confidence: float = 1.0


@dataclass(frozen=True)
class CanonicalizationReport:
    """
    MIDI 清洗报告。

    :param raw_note_count: 原始非鼓音符数量
    :param merged_note_count: 同音碎片合并次数
    :param filtered_noise_count: 被过滤的极短极弱噪声音数量
    :param onset_clustered_group_count: 发生起音聚类的和弦组数量
    :param output_note_count: 清洗后输出音符数量
    """

    raw_note_count: int
    merged_note_count: int
    filtered_noise_count: int
    onset_clustered_group_count: int
    output_note_count: int


@dataclass(frozen=True)
class CanonicalizationResult:
    """
    MIDI 清洗结果。

    :param notes: 清洗后的音符事件
    :param report: 清洗统计报告
    """

    notes: tuple[CanonicalNoteEvent, ...]
    report: CanonicalizationReport


@dataclass(frozen=True)
class BeatGrid:
    """
    节拍与小节网格。

    :param bpm: 全局 BPM
    :param time_signature: 拍号文本
    :param beats_per_bar: 每小节拍数
    :param total_beats: 覆盖全曲的总拍数
    :param bar_count: 小节数量
    :param beat_positions: 拍点位置列表
    :param confidence: 节拍网格置信度
    """

    bpm: float
    time_signature: str
    beats_per_bar: float
    total_beats: float
    bar_count: int
    beat_positions: tuple[float, ...]
    confidence: float


@dataclass(frozen=True)
class QuantizedNoteEvent:
    """
    对齐到乐谱网格后的音符事件。

    :param note_id: 继承自 CanonicalNoteEvent 的音符编号
    :param pitch: MIDI 音高编号
    :param start_beat: 量化起音拍点
    :param duration_beats: 量化持续拍数
    :param bar_index: 所属小节编号，从 0 开始
    :param staff_id: 谱表编号，1 表示高音谱表，2 表示低音谱表
    :param voice_id: 声部编号
    :param source_note_ids: 来源原始 MIDI 音符编号列表
    """

    note_id: int
    pitch: int
    start_beat: float
    duration_beats: float
    bar_index: int
    staff_id: int
    voice_id: str
    source_note_ids: tuple[int, ...]


@dataclass(frozen=True)
class QuantizedScore:
    """
    量化后的乐谱事件集合。

    :param notes: 量化音符列表
    :param beat_grid: 节拍网格
    :param time_signature: 拍号文本
    :param key_signature_sharps: 调号升降号数量
    """

    notes: tuple[QuantizedNoteEvent, ...]
    beat_grid: BeatGrid
    time_signature: str
    key_signature_sharps: int


@dataclass(frozen=True)
class StaffNotationBuildResult:
    """
    五线谱构建与导出结果。

    :param score: music21 Score 对象。注意：Score 本身是可变对象，frozen 只限制该字段引用不可重新赋值。
    :param reloaded_score: 从 MusicXML 回读得到的 music21 Score 对象
    :param musicxml_path: 导出的 MusicXML 路径
    :param quantized_score: 构建来源量化乐谱
    :param measure_count: 输出小节数量
    :param note_count: 输出音符数量
    """

    score: Score
    reloaded_score: Score
    musicxml_path: Path
    quantized_score: QuantizedScore
    measure_count: int
    note_count: int


@dataclass(frozen=True)
class NotationValidationReport:
    """
    五线谱合规校验报告。

    :param is_valid: 是否通过全部硬校验
    :param musicxml_exported: MusicXML 是否成功写出
    :param musicxml_reloaded: MusicXML 是否成功回读
    :param invalid_measure_count: 时值不完整的小节数量
    :param unassigned_note_count: 缺少 staff 或 voice 的音符数量
    :param messages: 详细校验消息
    """

    is_valid: bool
    musicxml_exported: bool
    musicxml_reloaded: bool
    invalid_measure_count: int
    unassigned_note_count: int
    messages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ScoreAwareTheoryMvpResult:
    """
    乐谱语义感知 MVP 独立管线执行结果。

    :param canonicalization_result: MIDI 清洗结果
    :param beat_grid: 节拍网格
    :param quantized_score: 量化乐谱
    :param build_result: MusicXML 构建结果
    :param validation_report: 五线谱合规校验报告
    :param semantic_model: 可选基础语义分析结果
    :param arrangement_result: 可选 36 键缩编结果
    :param musicxml_path: MusicXML 输出路径
    :param validation_report_path: JSON 合规报告输出路径
    """

    canonicalization_result: CanonicalizationResult
    beat_grid: BeatGrid
    quantized_score: QuantizedScore
    build_result: StaffNotationBuildResult
    validation_report: NotationValidationReport
    semantic_model: ScoreSemanticModel | None
    arrangement_result: TheoryArrangementResult | None
    musicxml_path: Path
    validation_report_path: Path


@dataclass(frozen=True)
class NoteSemanticRole:
    """
    单个量化音符的基础乐理语义。

    :param note_id: 音符编号
    :param pitch: 原始 MIDI 音高
    :param start_beat: 起音拍点
    :param duration_beats: 持续拍数
    :param is_melody: 是否为当前时间片主旋律音
    :param is_bass_anchor: 是否为当前时间片低音骨架音
    :param chord_role: 和弦角色，包含 root、third、fifth、seventh、color、non_chord
    :param keep_score: 面向 36 键缩编的基础保留分
    """

    note_id: int
    pitch: int
    start_beat: float
    duration_beats: float
    is_melody: bool
    is_bass_anchor: bool
    chord_role: str
    keep_score: float


@dataclass(frozen=True)
class BasicChordSemantic:
    """
    时间片基础和声语义。

    :param start_beat: 时间片起音拍点
    :param root_pitch_class: 根音音级
    :param pitch_classes: 当前时间片音级集合
    :param note_ids: 当前时间片包含的音符编号
    """

    start_beat: float
    root_pitch_class: int
    pitch_classes: tuple[int, ...]
    note_ids: tuple[int, ...]


@dataclass(frozen=True)
class BasicPhraseBoundary:
    """
    基础乐句边界。

    :param boundary_beat: 边界拍点
    :param boundary_type: 边界类型
    :param confidence: 置信度
    """

    boundary_beat: float
    boundary_type: str
    confidence: float


@dataclass(frozen=True)
class ScoreSemanticModel:
    """
    第二阶段基础乐谱语义模型。

    :param quantized_score: 来源量化乐谱
    :param key_signature_sharps: 调号升降号数量
    :param note_semantics: 单音符语义列表
    :param chord_semantics: 时间片和声语义列表
    :param phrase_boundaries: 乐句边界列表
    """

    quantized_score: QuantizedScore
    key_signature_sharps: int
    note_semantics: tuple[NoteSemanticRole, ...]
    chord_semantics: tuple[BasicChordSemantic, ...]
    phrase_boundaries: tuple[BasicPhraseBoundary, ...]


@dataclass(frozen=True)
class ArrangedNoteEvent:
    """
    36 键缩编后的音符事件。

    :param note_id: 来源音符编号
    :param original_pitch: 原始 MIDI 音高
    :param mapped_pitch: 映射到 C3-B5 后的 MIDI 音高
    :param token: YAML 简谱 token
    :param start_beat: 起音拍点
    :param duration_beats: 持续拍数
    :param keep_score: 保留分
    """

    note_id: int
    original_pitch: int
    mapped_pitch: int
    token: str
    start_beat: float
    duration_beats: float
    keep_score: float


@dataclass(frozen=True)
class ArrangementReport:
    """
    36 键缩编统计报告。

    :param input_note_count: 输入音符数
    :param output_note_count: 输出音符数
    :param octave_moved_note_count: 发生八度迁移的音符数
    :param clipped_note_count: 因密度限制被裁剪的音符数
    """

    input_note_count: int
    output_note_count: int
    octave_moved_note_count: int
    clipped_note_count: int


@dataclass(frozen=True)
class ArrangementValidationReport:
    """
    36 键缩编硬约束校验报告。

    :param is_valid: 是否通过校验
    :param out_of_range_note_count: 超出 C3-B5 的音符数
    :param max_chord_notes: 输出最大和弦密度
    :param duplicate_token_count: 同一时间片重复 token 数量
    """

    is_valid: bool
    out_of_range_note_count: int
    max_chord_notes: int
    duplicate_token_count: int


@dataclass(frozen=True)
class TheoryArrangementResult:
    """
    36 键乐理缩编结果。

    :param arranged_notes: 缩编后音符列表
    :param arranged_notes_by_start: 按起音拍点分组的缩编音符
    :param score_events: 可直接接入 YAML score 的事件列表
    :param report: 缩编统计报告
    :param validation: 硬约束校验报告
    """

    arranged_notes: tuple[ArrangedNoteEvent, ...]
    arranged_notes_by_start: dict[float, tuple[ArrangedNoteEvent, ...]]
    score_events: tuple[dict[str, int | float | str], ...]
    report: ArrangementReport
    validation: ArrangementValidationReport
