"""
模块名称：svsep_hand_separator
功能描述：
    使用 piano_svsep (GNN 声部/谱表预测模型) 对 MIDI 钢琴进行左右手声部分离。
    将 MIDI 文件转为 MusicXML，通过 piano_svsep 预测每个音符的 staff 标签
    （1=高音谱表/右手，2=低音谱表/左手），再按 staff 拆分为两个独立的
    MidiNoteEvent 列表。

主要组件：
    - SvsepHandSeparator: piano_svsep 声部分离器

依赖说明：
    - music21: MIDI → MusicXML 转换
    - partitura: MusicXML 乐谱加载与 note_array 操作
    - piano_svsep: GNN 声部预测模型
    - torch / torch_geometric: 深度学习推理

作者：JucieOvo
创建日期：2026-04-30
修改记录：
    - 2026-04-30 JucieOvo: 初始创建，实现 MIDI → MusicXML → piano_svsep → staff 拆分流水线
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

import pretty_midi
import partitura as pt
from partitura.score import Beam, GraceNote, Rest, Score, Tuplet, merge_parts
import numpy as np
import torch
from torch_geometric.data import Batch


class SvsepHandSeparator:
    """
    piano_svsep 声部分离器。

    职责：
        加载 piano_svsep 预训练模型，接收 MIDI 文件路径，经过
        MIDI → MusicXML 转换与 GNN 推理，按 staff 标签将音符拆分为
        右手 (staff=1) 与左手 (staff=2) 两组 MidiNoteEvent 序列。

    属性：
        model_path (Path): 预训练模型权重 (.ckpt) 路径
        _model: 加载后的 PLPianoSVSep 推理模型实例 (延迟加载)
        _device: 推理设备 (cpu 或 cuda)
    """

    def __init__(self, model_path: str | Path, device: str = "cpu"):
        """
        初始化分离器并加载预训练权重。

        :param model_path: piano_svsep 预训练模型 .ckpt 文件路径
        :param device: 推理设备，"cpu" 或 "cuda"
        :raises FileNotFoundError: 当模型权重文件不存在时触发
        :raises ImportError: 当 piano_svsep 依赖缺失时触发
        """
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"piano_svsep 模型权重文件不存在: {self.model_path}")
        if device not in {"cpu", "cuda"}:
            raise ValueError("piano_svsep 推理设备只允许 cpu 或 cuda")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("piano_svsep 配置为 cuda，但当前 CUDA 不可用")
        self._device = device
        self._model: Any | None = None

    def _load_model(self):
        """
        延迟加载预训练模型。

        仅在首次推理调用时加载，避免初始化时的内存开销。

        :raises ImportError: 当 piano_svsep 或 pytorch_lightning 不可用时触发
        :raises RuntimeError: 当模型加载失败时触发
        """
        if self._model is not None:
            return
        try:
            model_module = import_module("piano_svsep.models.pl_models")
            PLPianoSVSep = getattr(model_module, "PLPianoSVSep")
        except ImportError as exc:
            raise ImportError(
                "piano_svsep 未安装或不可导入。"
                "请确保已执行: pip install -e path/to/piano_svsep --no-deps"
            ) from exc

        try:
            loaded_model = PLPianoSVSep.load_from_checkpoint(
                str(self.model_path),
                map_location=self._device,
                strict=False,
                weights_only=False,
            )
            # 模型权重、缓冲区与输入图必须位于同一设备；map_location 只控制
            # checkpoint 反序列化位置，不能替代 LightningModule.to(device)。
            loaded_model = loaded_model.to(self._device)
            loaded_model.eval()
            self._model = loaded_model
        except Exception as exc:
            raise RuntimeError(f"加载 piano_svsep 模型失败: {exc}") from exc

    def separate(
        self,
        midi_path: str | Path,
        work_dir: str | Path | None = None,
        bpm: float = 120.0,
    ) -> "SvsepSeparationResult":
        """
        将 MIDI 文件拆分为左右手两组音符事件，并返回审计统计。

        流程：
            1. MIDI → MusicXML (music21)
            2. partitura 加载 MusicXML → partitura Score
            3. 预处理 (去除符杠/休止符/装饰音，构建异构图)
            4. piano_svsep 推理 → predicted_staff
            5. 按 staff 标签拆分 note_array → 左手/右手
            6. 回填 velocity/duration 到 MidiNoteEvent
            7. 构造审计统计并校验分离质量

        :param midi_path: 输入 MIDI 文件路径
        :param work_dir: 中间文件输出目录（None 使用临时目录）
        :param bpm: MIDI 拍速换算依据（BPM）
        :return: SvsepSeparationResult 包含左右手音符与审计统计
        :raises FileNotFoundError: 当 MIDI 文件不存在时触发
        :raises ValueError: 当 MIDI 不包含可解析的音符或 MusicXML 转换失败时触发
        :raises RuntimeError: 当 piano_svsep 推理失败时触发
        """
        from music21 import converter

        midi_path = Path(midi_path)
        if not midi_path.is_file():
            raise FileNotFoundError(f"输入 MIDI 文件不存在: {midi_path}")

        # 解析 work_dir 或使用临时目录
        if work_dir is not None:
            output_dir = Path(work_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            musicxml_path = output_dir / f"{midi_path.stem}_svsep.musicxml"
        else:
            tmp_dir = tempfile.mkdtemp(prefix="svsep_")
            output_dir = Path(tmp_dir)
            musicxml_path = output_dir / f"{midi_path.stem}_svsep.musicxml"

        # 阶段1: MIDI → MusicXML
        try:
            midi_score = converter.parse(str(midi_path))
            midi_score.write("musicxml", fp=str(musicxml_path))
        except Exception as exc:
            raise ValueError(
                f"MIDI 转 MusicXML 失败: {exc}。"
                f"请确认 music21 已安装且 MIDI 文件格式正确。"
            ) from exc

        if not musicxml_path.is_file():
            raise ValueError(f"MusicXML 转换结果未生成: {musicxml_path}")

        # 阶段2: 加载模型
        self._load_model()

        # 阶段3: 预处理 + 推理
        try:
            utils_module = import_module("piano_svsep.utils")
            hetero_graph_from_note_array = getattr(utils_module, "hetero_graph_from_note_array")
            get_vocsep_features = getattr(utils_module, "get_vocsep_features")
            score_graph_to_pyg = getattr(utils_module, "score_graph_to_pyg")
            HeteroScoreGraph = getattr(utils_module, "HeteroScoreGraph")
            remove_ties_acros_barlines = getattr(utils_module, "remove_ties_acros_barlines")
            get_measurewise_pot_edges = getattr(utils_module, "get_measurewise_pot_edges")
            get_pot_chord_edges = getattr(utils_module, "get_pot_chord_edges")

            # 3a: 加载乐谱
            # Partitura 通过动态导出暴露 load_score，当前类型信息未覆盖其布尔参数。
            load_score = getattr(pt, "load_score")
            score = load_score(str(musicxml_path), force_note_ids=True)
            if len(score) > 1:
                score = Score(merge_parts(score.parts))

            # 3b: 预处理（移除连音线/符杠/休止符/装饰音）
            remove_ties_acros_barlines(score, return_ids=False)
            for part in score:
                for beam in list(part.iter_all(Beam)):
                    for note in beam.notes:
                        note.beam = None
                    part.remove(beam)
                for rest in list(part.iter_all(Rest)):
                    part.remove(rest)
                for tuplet in list(part.iter_all(Tuplet)):
                    if (
                        isinstance(tuplet.start_note, Rest)
                        or isinstance(tuplet.end_note, Rest)
                    ):
                        part.remove(tuplet)
                for grace_note in list(part.iter_all(GraceNote)):
                    part.remove(grace_note)

            # 3c: 构建 note_array 与异构图
            note_array = score[0].note_array(
                include_time_signature=True,
                include_grace_notes=True,
                include_staff=True,
            )
            # 小节号映射 (用于 measurewise pot edges)
            score_index = int(np.array([p._quarter_durations[0] for p in score]).argmax())
            mn_map = score[score_index].measure_number_map
            note_measures = mn_map(note_array["onset_div"])
            nodes, edges = hetero_graph_from_note_array(note_array, pot_edge_dist=0)
            note_features = get_vocsep_features(note_array)
            hg = HeteroScoreGraph(
                note_features,
                edges,
                name="infer_graph",
                labels=None,
                note_array=note_array,
            )
            pot_edges = get_measurewise_pot_edges(note_array, note_measures)
            pot_chord_edges = get_pot_chord_edges(
                note_array, hg.get_edges_of_type("onset").numpy()
            )
            tensor_factory = getattr(torch, "tensor")
            setattr(hg, "pot_edges", tensor_factory(pot_edges))
            setattr(hg, "pot_chord_edges", tensor_factory(pot_chord_edges))
            pg_graph = score_graph_to_pyg(hg)
            pg_graph = Batch.from_data_list([pg_graph])
            # torch_geometric Batch 默认保留在 CPU。显式迁移全部节点、边和属性，
            # 避免 CUDA 模型在 embedding/index_select 时接收 CPU 索引张量。
            move_graph_to_device = getattr(pg_graph, "to")
            pg_graph = move_graph_to_device(self._device)

            # 3d: 模型推理
            model = self._model
            if model is None:
                raise RuntimeError("piano_svsep 模型未完成加载")
            with torch.no_grad():
                pred_voices, pred_staff, _ = model.predict_step(
                    pg_graph, return_graph=True
                )

            # 3e: 提取 staff 预测
            # 钢琴谱表约定: staff=1 高音谱表/右手, staff=2 低音谱表/左手
            predicted_staff_np = pred_staff.detach().cpu().numpy().astype(int) + 1

        except Exception as exc:
            raise RuntimeError(f"piano_svsep 推理失败: {exc}") from exc

        # 阶段4: 构建 staff 标签数组
        # note_array 中的 id 与 prediction 对齐
        staff_labels = np.zeros(len(note_array), dtype=int)
        for i, note_id in enumerate(note_array["id"]):
            match_idx = np.where(note_array["id"] == note_id)[0]
            if len(match_idx) == 1:
                staff_labels[i] = predicted_staff_np[match_idx[0]]

        # 阶段5: 读取原始 MIDI notes 以便回填 velocity/duration
        midi_data = pretty_midi.PrettyMIDI(str(midi_path))
        seconds_per_beat = 60.0 / bpm

        # 收集所有原始 MIDI notes (去除鼓轨)，按 (onset_beat, pitch) 排序
        orig_midi_notes: list[MidiNoteEvent] = []
        for instrument in midi_data.instruments:
            if instrument.is_drum:
                continue
            for note in instrument.notes:
                if note.end <= note.start:
                    continue
                orig_midi_notes.append(
                    MidiNoteEvent(
                        pitch=int(note.pitch),
                        start_beat=note.start / seconds_per_beat,
                        end_beat=note.end / seconds_per_beat,
                        velocity=int(note.velocity),
                        duration_beats=(note.end - note.start) / seconds_per_beat,
                    )
                )
        orig_midi_notes.sort(key=lambda n: (n.start_beat, n.pitch))

        # 使用双指针顺序匹配策略：
        # partitura 与 pretty_midi 的 onset 时间存在 MusicXML 往返偏移
        # (~0.2-0.4拍)，无法用精确时间匹配，但 pitch+顺序是一致的。
        # 对每个 partitura 音符，在原始 MIDI 列表中顺序查找音高匹配。
        # 匹配策略: 从上次匹配位置继续向前搜索，降低复杂度；匹配失败直接报错。
        def _match_note_by_pitch_and_order(
            pt_pitch: int,
            orig_list: list[MidiNoteEvent],
            start_idx: int,
            used_indices: set[int],
            search_window: int = 50,
        ) -> tuple[MidiNoteEvent | None, int, int | None]:
            """
            在原始 MIDI 音符列表中顺序查找指定音高的最佳匹配。

            由于两者排序一致，从上次匹配位置 (start_idx) 开始向前搜索，
            找到第一个音高相同且尚未使用的音符即为匹配（贪心策略）。

            :param pt_pitch: 待匹配的 partitura 音符音高
            :param orig_list: 原始 MIDI 音符列表 (已按 onset_beat 排序)
            :param start_idx: 搜索起始索引
            :param used_indices: 已回填过的原始 MIDI 音符索引集合
            :param search_window: 最大搜索窗口大小
            :return: (匹配到的 MidiNoteEvent 或 None, 下一搜索位置, 匹配索引或 None)
            """
            end_idx = min(start_idx + search_window, len(orig_list))
            for idx in range(start_idx, end_idx):
                if idx not in used_indices and orig_list[idx].pitch == pt_pitch:
                    return orig_list[idx], idx + 1, idx
            # 若窗口内未找到，进行全程搜索以处理局部排序偏移。
            for idx, mn in enumerate(orig_list):
                if idx not in used_indices and mn.pitch == pt_pitch:
                    return mn, idx + 1, idx
            return None, start_idx, None

        # 阶段6: 按 staff 拆分并转换为 MidiNoteEvent
        left_hand_notes: list[MidiNoteEvent] = []
        right_hand_notes: list[MidiNoteEvent] = []
        search_idx = 0  # 双指针搜索起始位置
        used_orig_indices: set[int] = set()
        skipped_split_fragments = 0  # MusicXML 拆分出的额外片段计数，不参与最终 MIDI 事件

        for i, staff_label in enumerate(staff_labels):
            na_pitch = int(note_array["pitch"][i])
            # 将 partitura 的 onset_div 转换为拍数
            onset_div = float(note_array["onset_div"][i])
            duration_div = float(note_array["duration_div"][i])

            # 获取 score 的 divisions (每四分音符的 tick 数)
            divisions = score[0]._quarter_durations[0]

            if divisions > 0:
                onset_beat = onset_div / divisions
                duration_beats = duration_div / divisions
            else:
                onset_beat = onset_div
                duration_beats = duration_div

            # 从原始 MIDI 回填 velocity 和精确的 start_beat/duration_beats
            matched, new_idx, matched_idx = _match_note_by_pitch_and_order(
                pt_pitch=na_pitch,
                orig_list=orig_midi_notes,
                start_idx=search_idx,
                used_indices=used_orig_indices,
            )
            search_idx = new_idx

            if matched is None:
                skipped_split_fragments += 1
                continue
            if matched_idx is not None:
                used_orig_indices.add(matched_idx)

            # 使用原始 MIDI 的精确 start_beat、duration_beats 与 velocity，禁止伪造默认属性。
            note_event = MidiNoteEvent(
                pitch=matched.pitch,
                start_beat=matched.start_beat,
                end_beat=matched.end_beat,
                velocity=matched.velocity,
                duration_beats=matched.duration_beats,
            )

            if staff_label == 1:
                # staff=1 高音谱表 → 右手
                right_hand_notes.append(note_event)
            elif staff_label == 2:
                # staff=2 低音谱表 → 左手
                left_hand_notes.append(note_event)
            # staff_label == 0 表示未分配，跳过（不应出现）

        if len(used_orig_indices) != len(orig_midi_notes):
            raise RuntimeError(
                "piano_svsep 回填未覆盖全部原始 MIDI 音符: "
                f"matched={len(used_orig_indices)}, original={len(orig_midi_notes)}, "
                f"skipped_split_fragments={skipped_split_fragments}"
            )

        # 构造审计统计：记录 note_array、预测与回填之间的真实匹配情况
        total_note_array_count = len(note_array)
        predicted_staff_count = len(predicted_staff_np)
        matched_count = len(used_orig_indices)
        unmatched_count = len(orig_midi_notes) - matched_count
        left_count = len(left_hand_notes)
        right_count = len(right_hand_notes)
        # 统计未知 staff 标签数量 (staff_label == 0)
        unknown_count = sum(1 for sl in staff_labels if sl == 0)

        audit = SvsepSeparationAudit(
            total_note_array_count=total_note_array_count,
            predicted_staff_count=predicted_staff_count,
            matched_original_note_count=matched_count,
            unmatched_original_note_count=unmatched_count,
            left_note_count=left_count,
            right_note_count=right_count,
            unknown_staff_count=unknown_count,
        )
        # 校验审计结果质量
        audit.validate()

        return SvsepSeparationResult(
            left_notes=left_hand_notes,
            right_notes=right_hand_notes,
            audit=audit,
        )


@dataclass(frozen=True)
class SvsepSeparationAudit:
    """
    piano_svsep 声部分离审计结果。

    职责：
        保存 MusicXML note_array、模型预测、原始 MIDI 回填之间的真实匹配统计，
        用于阻断低质量或不可对齐的声部分离结果。

    属性：
        total_note_array_count (int): note_array 中的总音符数
        predicted_staff_count (int): 模型预测的 staff 标签数量
        matched_original_note_count (int): 成功回填的原始 MIDI 音符数
        unmatched_original_note_count (int): 未能回填的原始 MIDI 音符数
        left_note_count (int): 判定为左手的音符数 (staff=2)
        right_note_count (int): 判定为右手的音符数 (staff=1)
        unknown_staff_count (int): 未知 staff 标签的音符数 (staff=0)
    """

    total_note_array_count: int
    predicted_staff_count: int
    matched_original_note_count: int
    unmatched_original_note_count: int
    left_note_count: int
    right_note_count: int
    unknown_staff_count: int

    @property
    def match_ratio(self) -> float:
        """获取原始 MIDI 音符回填匹配比例，范围 0 到 1。"""
        total = self.matched_original_note_count + self.unmatched_original_note_count
        if total <= 0:
            return 0.0
        return self.matched_original_note_count / total

    def validate(self) -> None:
        """
        校验声部分离统计是否达到可用门槛。

        :raises ValueError: 当匹配比例过低、左右手全空或存在未知 staff 标签时触发
        """
        if self.left_note_count + self.right_note_count <= 0:
            raise ValueError("piano_svsep 左右手音符结果为空")
        if self.match_ratio < 0.95:
            raise ValueError(f"piano_svsep 原始 MIDI 回填匹配比例过低: {self.match_ratio:.4f}")
        if self.unknown_staff_count > 0:
            raise ValueError(f"piano_svsep 存在未知 staff 标签数量: {self.unknown_staff_count}")


@dataclass(frozen=True)
class SvsepSeparationResult:
    """
    piano_svsep 声部分离完整结果。

    职责：
        同时携带左右手音符与审计统计，避免调用方只使用不可验证的拆分列表。

    属性：
        left_notes (list[MidiNoteEvent]): 左手音符列表
        right_notes (list[MidiNoteEvent]): 右手音符列表
        audit (SvsepSeparationAudit): 分离审计统计
    """

    left_notes: list["MidiNoteEvent"]
    right_notes: list["MidiNoteEvent"]
    audit: SvsepSeparationAudit


@dataclass(frozen=True)
class MidiNoteEvent:
    """
    MIDI 音符事件。

    职责：
        保存从原始 MIDI 回填后的音符属性。字段定义与 audio_to_yaml_converter.MidiNoteEvent
        保持一致，但在本模块内独立定义，避免命令行执行时产生循环导入。

    属性：
        pitch (int): MIDI 音高编号
        start_beat (float): 音符开始拍点
        end_beat (float): 音符结束拍点
        velocity (int): MIDI 力度值 0-127
        duration_beats (float): 音符持续拍数
    """

    pitch: int
    start_beat: float
    end_beat: float
    velocity: int
    duration_beats: float
