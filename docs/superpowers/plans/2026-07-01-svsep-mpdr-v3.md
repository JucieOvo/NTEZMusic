# SVSEP-MPDR v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure-audio SVSEP-MPDR v3 pipeline so audio-derived MIDI can be normalized, separated by piano_svsep, tracked for primary melody, compressed by MPDR, folded to C3-B5, and audited with real conversion metrics.

**Architecture:** Keep `src/audio_to_yaml_converter.py` as the main orchestration file because the current project already centralizes MIDI-to-YAML conversion there. Add focused helper dataclasses/functions inside that module only where they serve `svsep_mpdr`, and enhance `src/svsep_hand_separator.py` to return separation audit data. Do not wire `src/fusion_engine.py` or `src/dual_stream_extractor.py` into this implementation; this version must analyze directly from audio/MIDI to reduce video-tracking barriers.

**Tech Stack:** Python 3.10, pretty_midi, PyYAML, librosa, Demucs, Transkun, music21, partitura, piano_svsep, torch, torch_geometric, pytest.

## Global Constraints

- All code comments and docstrings must be detailed Chinese; every Python module must keep author name `JucieOvo`.
- No Mock, Stub, fake data, fake success responses, or simulated external dependency results.
- No silent fallback or degradation: Demucs, Transkun, piano_svsep, note matching, and conversion failures must raise real errors.
- Do not use performer video, MediaPipe hand tracking, `src/fusion_engine.py`, or `src/dual_stream_extractor.py` in this implementation.
- Keep YAML output schema compatible with the existing player: `song`, `playback`, `keyboard`, `score`.
- Preserve game pitch range C3-B5: `MIN_GAME_PITCH = 48`, `MAX_GAME_PITCH = 83`.
- Use Windows-compatible paths and commands.
- Tests must use real in-memory/file MIDI objects via `pretty_midi`; do not mock model output or fake command success.
- The project is not a git repository in this environment, so skip commit steps unless the repository is initialized later.

---

## File Structure

### Modify: `src/audio_to_yaml_converter.py`

Responsibilities after implementation:

- Own `AudioPipelineConfig`, `MidiNoteEvent`, `MpdrNoteCandidate`, and `MpdrBuildResult` data structures.
- Normalize MIDI note timing before quantization.
- Orchestrate `svsep_mpdr` without video inputs.
- Track primary melody from right-hand MIDI notes.
- Build and rerank MPDR candidates.
- Fold selected candidates to C3-B5 and emit modifier-safe YAML tokens.
- Populate real conversion statistics.

### Modify: `src/svsep_hand_separator.py`

Responsibilities after implementation:

- Keep piano_svsep MIDI → MusicXML → graph → staff prediction flow.
- Return left/right note events plus a real audit object with match counts and staff distribution.
- Raise errors when model output cannot be aligned to original MIDI sufficiently.

### Create: `tests/test_audio_to_yaml_svsep_mpdr.py`

Responsibilities:

- Unit-test pure algorithm helpers using real `pretty_midi` MIDI files written to temporary paths.
- Validate onset clustering, melody DP scoring, MPDR score aggregation, token conflict priority, and pure-audio mode constraints.
- Do not import or call video fusion modules.

### Create: `tests/test_svsep_hand_separator_audit.py`

Responsibilities:

- Unit-test audit dataclass/statistics that do not require running the neural model.
- Validate that invalid audit ratios raise errors through pure functions.
- Do not mock piano_svsep inference.

---

## Subagent Dispatch Policy

The user requested top-tier subagents for code quality. Dispatch one fresh subagent per task with `model: "opus"` or the highest available top-tier model in the current harness. Use `subagent_type: "claude"` unless a more specific implementation agent is available. Each subagent must receive only its task section plus the global constraints and must not proceed beyond its task.

Use this shared instruction in every subagent prompt:

```text
你是顶级模型代码子代理，负责在 F:\NTEZMusic 中完成一个明确实现任务。必须遵守：中文注释、作者 JucieOvo、禁止 Mock/Stub/伪数据、禁止静默降级、失败直接报错、纯音频主链路、不接入 src/fusion_engine.py 或 src/dual_stream_extractor.py。实现前先读取本任务列出的文件；只改本任务允许的文件；每个测试必须使用真实 pretty_midi 构造或真实本地文件，不伪造外部模型成功。完成后报告：修改文件、测试命令、测试结果、未完成原因。
```

---

### Task 1: Add MIDI onset normalization and configuration

**Files:**
- Modify: `src/audio_to_yaml_converter.py:149-291`
- Modify: `src/audio_to_yaml_converter.py:703-737`
- Create: `tests/test_audio_to_yaml_svsep_mpdr.py`

**Interfaces:**
- Consumes: existing `AudioPipelineConfig`, `MidiNoteEvent`, `_read_midi_notes`.
- Produces:
  - `AudioPipelineConfig.onset_cluster_enabled: bool`
  - `AudioPipelineConfig.onset_cluster_window_beats: float`
  - `AudioPipelineConfig.onset_cluster_max_span_beats: float`
  - `MidiToYamlConverter._cluster_midi_note_onsets(midi_notes: tuple[MidiNoteEvent, ...], config: AudioPipelineConfig) -> tuple[MidiNoteEvent, ...]`

#### Subagent prompt

```text
任务：实现 Task 1: Add MIDI onset normalization and configuration。

请在 F:\NTEZMusic 中工作。先读取：
- src/audio_to_yaml_converter.py:149-291
- src/audio_to_yaml_converter.py:703-760
- docs/DESIGN.md 中 @DESIGN:FR-002 与 @DESIGN:change-2

目标：为 MIDI note 读取增加纯音频/MIDI 的 onset clustering 配置与实现，解决同一物理和弦因毫秒级起音误差被拆到不同量化格的问题。

必须修改：
1. 在 AudioPipelineConfig docstring 和 dataclass 字段中增加：
   - onset_cluster_enabled: bool = True
   - onset_cluster_window_beats: float = 0.08
   - onset_cluster_max_span_beats: float = 0.12
2. 在 _read_midi_notes 读取并合并 sustained notes 后、最终按 start_beat 排序返回前，调用 _cluster_midi_note_onsets。
3. 新增方法：
   def _cluster_midi_note_onsets(self, midi_notes: tuple[MidiNoteEvent, ...], config: AudioPipelineConfig) -> tuple[MidiNoteEvent, ...]

实现要求：
- 若 onset_cluster_enabled 为 False，原样返回 midi_notes。
- onset_cluster_window_beats 必须 >= 0，否则 ValueError。
- onset_cluster_max_span_beats 必须 >= onset_cluster_window_beats，否则 ValueError。
- 按 start_beat 排序扫描；如果当前 note.start_beat 与当前 group 第一枚 note.start_beat 的差值 <= max_span，并且与 group 最近一枚 note.start_beat 的差值 <= window，则加入同组。
- 每组若只有 1 个 note，原样保留。
- 每组若多于 1 个 note，cluster_start_beat 使用组内 velocity 加权平均；若 velocity 总和 <= 0，使用算术平均。
- 每个 note 的 end_beat 按同样 delta 平移：new_end = max(cluster_start_beat + config.quantize_beat * 0.25, note.end_beat + delta)。duration_beats = new_end - cluster_start_beat。
- 不改变 pitch 和 velocity。
- 返回按 (start_beat, pitch) 排序的 tuple。
- 在 conversion_stats 中新增 onset_clustered_notes 与 onset_clustered_groups；如果 _read_midi_notes 在 conversion_stats 初始化前调用，则方法内部必须安全判断 key 是否存在，不得报 KeyError。

测试：创建 tests/test_audio_to_yaml_svsep_mpdr.py。测试必须使用真实 pretty_midi 生成临时 MIDI 文件或直接构造 MidiNoteEvent，不使用 Mock。

至少写这些测试：
1. test_cluster_midi_note_onsets_merges_close_chord_notes：构造三枚 start_beat 1.000、1.030、1.050 的 MidiNoteEvent，window=0.08、span=0.12，断言输出三枚 start_beat 相同，pitch/velocity 不变，duration > 0。
2. test_cluster_midi_note_onsets_keeps_arpeggio_when_span_exceeds_limit：构造 start_beat 1.000、1.030、1.200，断言第三枚不与前两枚合并。
3. test_cluster_midi_note_onsets_rejects_invalid_config：window=-0.1 抛 ValueError；span < window 抛 ValueError。
4. test_read_midi_notes_applies_onset_clustering_to_real_pretty_midi：用 pretty_midi 写临时 MIDI 文件，读取后调用 _read_midi_notes，断言近 onset 和弦被聚类。

运行命令：
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v

只改允许文件，不接入视频模块。
```

- [ ] **Step 1: Write failing tests**

Create `tests/test_audio_to_yaml_svsep_mpdr.py` with these tests:

```python
"""
模块名称：test_audio_to_yaml_svsep_mpdr
功能描述：
    验证 SVSEP-MPDR v3 纯音频算法中的 MIDI 起音聚类、主旋律追踪与候选评分基础能力。

主要组件：
    - test_cluster_midi_note_onsets_merges_close_chord_notes: 验证近起音和弦聚类
    - test_cluster_midi_note_onsets_keeps_arpeggio_when_span_exceeds_limit: 验证分解和弦不被误合并
    - test_cluster_midi_note_onsets_rejects_invalid_config: 验证非法聚类配置直接报错
    - test_read_midi_notes_applies_onset_clustering_to_real_pretty_midi: 验证真实 MIDI 读取链路应用聚类

依赖说明：
    - pretty_midi: 构造真实 MIDI 文件
    - pytest: 执行测试断言

作者：JucieOvo
创建日期：2026-07-01
"""

from pathlib import Path

import pretty_midi
import pytest

from audio_to_yaml_converter import AudioPipelineConfig, MidiNoteEvent, MidiToYamlConverter


def _base_config(tmp_path: Path, **overrides) -> AudioPipelineConfig:
    values = {
        "audio_path": None,
        "input_midi_path": None,
        "output_yaml_path": tmp_path / "out.yaml",
        "work_dir": tmp_path,
        "song_name": "测试曲目",
        "bpm": 120.0,
        "beat_unit": 4,
        "start_delay_seconds": 1.0,
        "key_press_seconds": 0.0,
        "demucs_model": "htdemucs_6s",
        "demucs_stem": "piano",
        "transcription_checkpoint": None,
        "transcription_device": "cuda",
        "transcription_segment_hop_size": None,
        "transcription_segment_size": None,
        "quantize_beat": 0.25,
        "max_chord_notes": 4,
        "max_score_events": 10000,
        "allow_accidentals": True,
        "out_of_range_policy": "error",
        "pitch_compression_mode": "octave_fold",
        "ref_smoothing": 0.2,
        "left_max_chord_notes": 4,
        "phrase_gap_beats": 0.5,
        "global_trend_alpha": 0.05,
        "global_trend_window_beats": 4.0,
    }
    values.update(overrides)
    return AudioPipelineConfig(**values)


def _note(pitch: int, start: float, end: float, velocity: int = 80) -> MidiNoteEvent:
    return MidiNoteEvent(
        pitch=pitch,
        start_beat=start,
        end_beat=end,
        velocity=velocity,
        duration_beats=end - start,
    )


def test_cluster_midi_note_onsets_merges_close_chord_notes(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(
        tmp_path,
        onset_cluster_enabled=True,
        onset_cluster_window_beats=0.08,
        onset_cluster_max_span_beats=0.12,
    )
    notes = (_note(60, 1.000, 1.500, 70), _note(64, 1.030, 1.520, 80), _note(67, 1.050, 1.550, 90))

    clustered = converter._cluster_midi_note_onsets(notes, config)

    assert len(clustered) == 3
    assert {note.pitch for note in clustered} == {60, 64, 67}
    assert {note.velocity for note in clustered} == {70, 80, 90}
    assert len({round(note.start_beat, 9) for note in clustered}) == 1
    assert all(note.duration_beats > 0 for note in clustered)


def test_cluster_midi_note_onsets_keeps_arpeggio_when_span_exceeds_limit(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(
        tmp_path,
        onset_cluster_enabled=True,
        onset_cluster_window_beats=0.08,
        onset_cluster_max_span_beats=0.12,
    )
    notes = (_note(60, 1.000, 1.500), _note(64, 1.030, 1.520), _note(67, 1.200, 1.700))

    clustered = converter._cluster_midi_note_onsets(notes, config)

    first_group_start = clustered[0].start_beat
    assert clustered[1].start_beat == pytest.approx(first_group_start)
    assert clustered[2].start_beat != pytest.approx(first_group_start)


def test_cluster_midi_note_onsets_rejects_invalid_config(tmp_path: Path):
    converter = MidiToYamlConverter()
    notes = (_note(60, 1.000, 1.500),)

    negative_window = _base_config(tmp_path, onset_cluster_window_beats=-0.1)
    with pytest.raises(ValueError, match="onset_cluster_window_beats"):
        converter._cluster_midi_note_onsets(notes, negative_window)

    invalid_span = _base_config(tmp_path, onset_cluster_window_beats=0.1, onset_cluster_max_span_beats=0.05)
    with pytest.raises(ValueError, match="onset_cluster_max_span_beats"):
        converter._cluster_midi_note_onsets(notes, invalid_span)


def test_read_midi_notes_applies_onset_clustering_to_real_pretty_midi(tmp_path: Path):
    midi_path = tmp_path / "cluster.mid"
    midi = pretty_midi.PrettyMIDI(initial_tempo=120)
    instrument = pretty_midi.Instrument(program=0)
    instrument.notes.append(pretty_midi.Note(velocity=80, pitch=60, start=0.500, end=1.000))
    instrument.notes.append(pretty_midi.Note(velocity=90, pitch=64, start=0.515, end=1.015))
    midi.instruments.append(instrument)
    midi.write(str(midi_path))

    config = _base_config(
        tmp_path,
        bpm=120.0,
        onset_cluster_enabled=True,
        onset_cluster_window_beats=0.08,
        onset_cluster_max_span_beats=0.12,
        merge_sustained_notes=False,
    )
    converter = MidiToYamlConverter()
    midi_data = pretty_midi.PrettyMIDI(str(midi_path))

    notes = converter._read_midi_notes(midi_data=midi_data, config=config)

    assert len(notes) == 2
    assert notes[0].start_beat == pytest.approx(notes[1].start_beat)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: FAIL because `AudioPipelineConfig` does not yet accept `onset_cluster_enabled` and `_cluster_midi_note_onsets` does not exist.

- [ ] **Step 3: Implement minimal code**

Modify `AudioPipelineConfig` docstring and fields. Add the three config fields after `merge_sustained_gap_beats`:

```python
    onset_cluster_enabled: bool = True
    onset_cluster_window_beats: float = 0.08
    onset_cluster_max_span_beats: float = 0.12
```

Modify `_read_midi_notes` after sustained merge:

```python
        if config.merge_sustained_notes:
            sorted_notes = self._merge_sustained_midi_notes(midi_notes=sorted_notes, config=config)

        clustered_notes = self._cluster_midi_note_onsets(midi_notes=tuple(sorted_notes), config=config)
        return tuple(sorted(clustered_notes, key=lambda item: (item.start_beat, item.pitch)))
```

Add method after `_merge_sustained_midi_notes`:

```python
    def _cluster_midi_note_onsets(
        self,
        midi_notes: tuple[MidiNoteEvent, ...],
        config: AudioPipelineConfig,
    ) -> tuple[MidiNoteEvent, ...]:
        """
        将毫秒级接近的 MIDI 起音聚合为同一物理发声组。

        :param midi_notes: 已读取并按拍点换算的真实 MIDI 音符事件
        :param config: 音频转换流水线配置
        :return: 起音聚类后的 MIDI 音符事件
        :raises ValueError: 当聚类窗口配置无效时触发
        """
        if not config.onset_cluster_enabled or not midi_notes:
            return midi_notes
        if config.onset_cluster_window_beats < 0:
            raise ValueError("onset_cluster_window_beats 必须大于等于 0")
        if config.onset_cluster_max_span_beats < config.onset_cluster_window_beats:
            raise ValueError("onset_cluster_max_span_beats 必须大于等于 onset_cluster_window_beats")

        sorted_notes = sorted(midi_notes, key=lambda item: (item.start_beat, item.pitch))
        groups: list[list[MidiNoteEvent]] = []
        current_group: list[MidiNoteEvent] = []

        for note in sorted_notes:
            if not current_group:
                current_group.append(note)
                continue
            first_start = current_group[0].start_beat
            previous_start = current_group[-1].start_beat
            within_span = note.start_beat - first_start <= config.onset_cluster_max_span_beats
            within_window = note.start_beat - previous_start <= config.onset_cluster_window_beats
            if within_span and within_window:
                current_group.append(note)
            else:
                groups.append(current_group)
                current_group = [note]
        if current_group:
            groups.append(current_group)

        clustered: list[MidiNoteEvent] = []
        clustered_notes_count = 0
        clustered_groups_count = 0
        min_duration = max(config.quantize_beat * 0.25, 1e-6)

        for group in groups:
            if len(group) == 1:
                clustered.append(group[0])
                continue
            velocity_sum = sum(max(0, note.velocity) for note in group)
            if velocity_sum > 0:
                cluster_start = sum(note.start_beat * max(0, note.velocity) for note in group) / velocity_sum
            else:
                cluster_start = sum(note.start_beat for note in group) / len(group)
            clustered_groups_count += 1
            clustered_notes_count += len(group)
            for note in group:
                delta = cluster_start - note.start_beat
                new_end = max(cluster_start + min_duration, note.end_beat + delta)
                clustered.append(
                    MidiNoteEvent(
                        pitch=note.pitch,
                        start_beat=cluster_start,
                        end_beat=new_end,
                        velocity=note.velocity,
                        duration_beats=new_end - cluster_start,
                    )
                )

        if "onset_clustered_notes" in self.conversion_stats:
            self.conversion_stats["onset_clustered_notes"] = int(self.conversion_stats["onset_clustered_notes"]) + clustered_notes_count
        if "onset_clustered_groups" in self.conversion_stats:
            self.conversion_stats["onset_clustered_groups"] = int(self.conversion_stats["onset_clustered_groups"]) + clustered_groups_count
        return tuple(sorted(clustered, key=lambda item: (item.start_beat, item.pitch)))
```

Also initialize stats in `convert`:

```python
            "onset_clustered_notes": 0,
            "onset_clustered_groups": 0,
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: PASS for Task 1 tests.

---

### Task 2: Add piano_svsep separation audit data

**Files:**
- Modify: `src/svsep_hand_separator.py:24-244`
- Modify: `src/audio_to_yaml_converter.py:886-918`
- Create: `tests/test_svsep_hand_separator_audit.py`

**Interfaces:**
- Consumes: existing `SvsepHandSeparator.separate(midi_path, work_dir=None, bpm=120.0)`.
- Produces:
  - `SvsepSeparationAudit` dataclass in `src/svsep_hand_separator.py`.
  - `SvsepSeparationResult` dataclass in `src/svsep_hand_separator.py`.
  - `SvsepHandSeparator.separate(...) -> SvsepSeparationResult`.
  - `SvsepSeparationAudit.validate() -> None`.

#### Subagent prompt

```text
任务：实现 Task 2: Add piano_svsep separation audit data。

请在 F:\NTEZMusic 中工作。先读取：
- src/svsep_hand_separator.py:1-244
- src/audio_to_yaml_converter.py:886-918
- docs/DESIGN.md 中 @DESIGN:FR-003 与 @DESIGN:change-3

目标：让 piano_svsep 左右手分离返回真实审计信息，而不是只有 left/right list。禁止 Mock 模型成功；测试只覆盖 audit dataclass 纯逻辑，不伪造推理成功。

必须修改：
1. 在 src/svsep_hand_separator.py 中新增 dataclass：
   - SvsepSeparationAudit
   - SvsepSeparationResult
2. 修改 SvsepHandSeparator.separate 返回 SvsepSeparationResult。
3. 在推理成功后填充 audit：
   - total_note_array_count
   - predicted_staff_count
   - matched_original_note_count
   - unmatched_original_note_count
   - left_note_count
   - right_note_count
   - unknown_staff_count
   - match_ratio
4. audit.validate() 必须在 match_ratio < 0.95、left/right 全空、unknown_staff_count > 0 时抛 ValueError。
5. 修改 audio_to_yaml_converter._separate_hands_with_svsep 接收 result，并写入 conversion_stats：
   - svsep_total_note_array_count
   - svsep_predicted_staff_count
   - svsep_matched_original_note_count
   - svsep_unmatched_original_note_count
   - svsep_match_ratio
   - svsep_unknown_staff_count

测试：创建 tests/test_svsep_hand_separator_audit.py。只测试 audit.validate 的真实纯逻辑；不要 mock piano_svsep，不要 fake separate 成功。

运行：
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_svsep_hand_separator_audit.py -v

只改允许文件。
```

- [ ] **Step 1: Write failing tests**

Create `tests/test_svsep_hand_separator_audit.py`:

```python
"""
模块名称：test_svsep_hand_separator_audit
功能描述：
    验证 piano_svsep 声部分离审计结构的真实校验逻辑。

主要组件：
    - test_svsep_audit_accepts_valid_distribution: 验证正常分离统计通过
    - test_svsep_audit_rejects_low_match_ratio: 验证匹配比例过低时报错
    - test_svsep_audit_rejects_empty_hand_result: 验证左右手为空时报错
    - test_svsep_audit_rejects_unknown_staff: 验证未知 staff 时报错

依赖说明：
    - pytest: 执行测试断言

作者：JucieOvo
创建日期：2026-07-01
"""

import pytest

from svsep_hand_separator import SvsepSeparationAudit


def _audit(**overrides) -> SvsepSeparationAudit:
    values = {
        "total_note_array_count": 100,
        "predicted_staff_count": 100,
        "matched_original_note_count": 98,
        "unmatched_original_note_count": 2,
        "left_note_count": 40,
        "right_note_count": 58,
        "unknown_staff_count": 0,
    }
    values.update(overrides)
    return SvsepSeparationAudit(**values)


def test_svsep_audit_accepts_valid_distribution():
    audit = _audit()

    audit.validate()

    assert audit.match_ratio == pytest.approx(0.98)


def test_svsep_audit_rejects_low_match_ratio():
    audit = _audit(matched_original_note_count=80, unmatched_original_note_count=20)

    with pytest.raises(ValueError, match="匹配比例"):
        audit.validate()


def test_svsep_audit_rejects_empty_hand_result():
    audit = _audit(left_note_count=0, right_note_count=0, matched_original_note_count=0, unmatched_original_note_count=100)

    with pytest.raises(ValueError, match="左右手音符"):
        audit.validate()


def test_svsep_audit_rejects_unknown_staff():
    audit = _audit(unknown_staff_count=1)

    with pytest.raises(ValueError, match="未知 staff"):
        audit.validate()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_svsep_hand_separator_audit.py -v
```

Expected: FAIL because `SvsepSeparationAudit` does not exist.

- [ ] **Step 3: Implement audit dataclasses**

Add near the imports in `src/svsep_hand_separator.py`:

```python
@dataclass(frozen=True)
class SvsepSeparationAudit:
    """
    piano_svsep 声部分离审计结果。

    职责：
        保存 MusicXML note_array、模型预测、原始 MIDI 回填之间的真实匹配统计，
        用于阻断低质量或不可对齐的声部分离结果。
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
        """
        获取原始 MIDI 音符回填匹配比例。

        :return: 匹配比例，范围 0 到 1
        """
        total = self.matched_original_note_count + self.unmatched_original_note_count
        if total <= 0:
            return 0.0
        return self.matched_original_note_count / total

    def validate(self) -> None:
        """
        校验声部分离统计是否达到可用门槛。

        :raises ValueError: 当匹配比例、左右手数量或 staff 标签异常时触发
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
    """

    left_notes: list["MidiNoteEvent"]
    right_notes: list["MidiNoteEvent"]
    audit: SvsepSeparationAudit
```

Modify `separate` return annotation and return construction. The implementation must compute the audit from real result counts and call `audit.validate()` before returning.

Modify `src/audio_to_yaml_converter.py`:

```python
        result = separator.separate(
            midi_path=midi_path,
            work_dir=config.work_dir / "svsep",
            bpm=config.bpm,
        )
        left_notes = result.left_notes
        right_notes = result.right_notes
        result.audit.validate()
```

Write audit stats into `self.conversion_stats`.

- [ ] **Step 4: Run tests**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_svsep_hand_separator_audit.py -v
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: PASS for Task 1 and Task 2 tests.

---

### Task 3: Strengthen primary melody DP scoring

**Files:**
- Modify: `src/audio_to_yaml_converter.py:149-291`
- Modify: `src/audio_to_yaml_converter.py:1333-1442`
- Modify: `tests/test_audio_to_yaml_svsep_mpdr.py`

**Interfaces:**
- Consumes: `MidiNoteEvent`, `_extract_primary_melody_path`, `_score_primary_melody_note`.
- Produces new config fields:
  - `mpdr_melody_pitch_weight: float = 1.0`
  - `mpdr_melody_duration_weight: float = 0.35`
  - `mpdr_melody_velocity_weight: float = 0.45`
  - `mpdr_melody_beat_weight: float = 0.20`
  - `mpdr_melody_continuity_weight: float = 0.70`
  - `mpdr_melody_large_jump_penalty: float = 0.35`
  - `mpdr_melody_repetition_penalty: float = 0.20`

#### Subagent prompt

```text
任务：实现 Task 3: Strengthen primary melody DP scoring。

请在 F:\NTEZMusic 中工作。先读取：
- src/audio_to_yaml_converter.py:149-291
- src/audio_to_yaml_converter.py:1333-1442
- tests/test_audio_to_yaml_svsep_mpdr.py
- docs/DESIGN.md 中 @DESIGN:FR-004 与 @DESIGN:change-4

目标：增强右手主旋律 DP，综合 pitch、velocity、duration、强拍、连续性、大跳惩罚、重复噪声惩罚；不要接入视频模块。

实现要求：
1. 在 AudioPipelineConfig 中新增主旋律权重字段。
2. _score_primary_melody_note 使用新字段，不再直接混用 mpdr_voice_leading_weight 作为强拍评分权重。
3. _extract_primary_melody_path 的转移分数使用 mpdr_melody_continuity_weight。
4. 当 interval > mpdr_register_collision_semitones 时，扣除 mpdr_melody_large_jump_penalty。
5. 当当前 pitch 与前一个 melody pitch 相同且 start 间隔 <= mpdr_repeat_suppression_beats 时，扣除 mpdr_melody_repetition_penalty。
6. 保持返回类型 dict[int, MidiNoteEvent] 不变。

测试追加到 tests/test_audio_to_yaml_svsep_mpdr.py：
- test_extract_primary_melody_prefers_longer_louder_continuous_path
- test_extract_primary_melody_penalizes_isolated_high_jump

运行：
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

- [ ] **Step 1: Add failing tests**

Append:

```python

def test_extract_primary_melody_prefers_longer_louder_continuous_path(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    right_groups = {
        0: [_note(60, 0.0, 0.25, 70), _note(72, 0.0, 0.08, 40)],
        1: [_note(62, 0.25, 0.75, 95), _note(76, 0.25, 0.32, 35)],
        2: [_note(64, 0.50, 1.00, 100), _note(79, 0.50, 0.58, 35)],
    }

    melody = converter._extract_primary_melody_path(right_groups=right_groups, config=config)

    assert [melody[index].pitch for index in sorted(melody)] == [60, 62, 64]


def test_extract_primary_melody_penalizes_isolated_high_jump(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(
        tmp_path,
        mpdr_melody_large_jump_penalty=5.0,
        mpdr_melody_pitch_weight=0.5,
        mpdr_melody_duration_weight=1.0,
        mpdr_melody_velocity_weight=1.0,
    )
    right_groups = {
        0: [_note(64, 0.0, 0.5, 90)],
        1: [_note(65, 0.25, 0.75, 90), _note(88, 0.25, 0.30, 30)],
        2: [_note(67, 0.50, 1.00, 90)],
    }

    melody = converter._extract_primary_melody_path(right_groups=right_groups, config=config)

    assert melody[1].pitch == 65
```

- [ ] **Step 2: Run tests to fail**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: at least one new test fails because current scoring over-prefers high pitch.

- [ ] **Step 3: Implement scoring changes**

Add config fields after `mpdr_melody_onset_guard_beats`.

Change `_score_primary_melody_note` to compute:

```python
        return (
            config.mpdr_melody_pitch_weight * pitch_score
            + config.mpdr_melody_duration_weight * duration_score
            + config.mpdr_melody_velocity_weight * velocity_score
            + config.mpdr_melody_beat_weight * beat_score
        )
```

Change transition in `_extract_primary_melody_path` to add continuity and subtract configured penalties when conditions match.

- [ ] **Step 4: Run tests**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: PASS.

---

### Task 4: Improve MPDR candidate scoring and audit counters

**Files:**
- Modify: `src/audio_to_yaml_converter.py:1154-1311`
- Modify: `src/audio_to_yaml_converter.py:1526-1885`
- Modify: `src/audio_to_yaml_converter.py:2130-2190`
- Modify: `tests/test_audio_to_yaml_svsep_mpdr.py`

**Interfaces:**
- Consumes: `MpdrNoteCandidate`, `_select_mpdr_right_notes`, `_select_mpdr_left_notes`, `_score_svsep_mpdr_stats`.
- Produces additional stats keys:
  - `kept_bass_anchor_notes`
  - `total_bass_anchor_notes`
  - `suppressed_repeat_notes`
  - `low_mud_penalty_events`
  - `register_collision_events`
  - `onset_collision_events`
  - `melody_integrity`
  - `bass_anchor_integrity`
  - `masking_avoidance`
  - `harmonic_completeness`
  - `register_clarity`

#### Subagent prompt

```text
任务：实现 Task 4: Improve MPDR candidate scoring and audit counters。

请在 F:\NTEZMusic 中工作。先读取：
- src/audio_to_yaml_converter.py:1154-1311
- src/audio_to_yaml_converter.py:1526-1885
- src/audio_to_yaml_converter.py:2130-2190
- tests/test_audio_to_yaml_svsep_mpdr.py
- docs/DESIGN.md 中 @DESIGN:FR-005、FR-007、change-5、change-7

目标：增强 MPDR 的真实审计指标与候选统计，让主旋律、低音锚点、遮蔽风险、和声完整度和音区清晰度都可量化。不要接入视频模块，不造假统计。

实现要求：
1. _build_svsep_mpdr_candidate 初始化 stats 时增加 total_bass_anchor_notes、kept_bass_anchor_notes、suppressed_repeat_notes、low_mud_penalty_events、register_collision_events、onset_collision_events。
2. 每个 time_index 若 left_notes 非空，bass_pitch = min(left_notes)，total_bass_anchor_notes += 1。
3. token 保留后若包含该 bass_pitch，kept_bass_anchor_notes += 1。
4. _suppress_mpdr_repeated_candidates 返回 suppressed_count 已存在，将其累计到 stats["suppressed_repeat_notes"]。
5. _compute_mpdr_masking_risk 可继续返回 float，但调用处根据 note 与 melody_pitch 的关系累计 register_collision_events/onset_collision_events；低频浑浊条件触发时累计 low_mud_penalty_events。
6. _score_svsep_mpdr_stats 增加 bass_anchor_integrity，并写入 stats。
7. 不改变 YAML schema。

测试追加：
- test_score_svsep_mpdr_stats_reports_bass_anchor_integrity
- test_select_mpdr_left_notes_keeps_bass_anchor_when_budget_tight

运行：
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

- [ ] **Step 1: Add failing tests**

Append:

```python

def test_score_svsep_mpdr_stats_reports_bass_anchor_integrity(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    stats = {
        "total_melody_notes": 4,
        "kept_melody_notes": 4,
        "kept_masking_risk": 0.0,
        "kept_left_utility": 2.0,
        "selected_original_bass": 4,
        "kept_original_bass": 3,
        "token_dropped_notes": 0,
        "total_bass_anchor_notes": 4,
        "kept_bass_anchor_notes": 3,
    }

    score = converter._score_svsep_mpdr_stats(stats=stats, config=config)

    assert 0.0 <= score <= 1.0
    assert stats["bass_anchor_integrity"] == pytest.approx(0.75)


def test_select_mpdr_left_notes_keeps_bass_anchor_when_budget_tight(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path, mpdr_left_opacity_base=0.2, mpdr_left_opacity_min=0.2, mpdr_left_opacity_max=0.2)
    left_notes = [_note(48, 0.0, 0.5, 70), _note(55, 0.0, 0.5, 100), _note(60, 0.0, 0.5, 100)]

    selected = converter._select_mpdr_left_notes(
        time_index=0,
        left_notes=left_notes,
        right_notes=[],
        melody_by_index={},
        previous_left_selected=[],
        config=config,
    )

    assert any(candidate.note.pitch == 48 for candidate in selected)
```

- [ ] **Step 2: Run tests to fail**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: FAIL because `bass_anchor_integrity` is not populated.

- [ ] **Step 3: Implement stats changes**

Modify stats initialization and loop accounting. In `_score_svsep_mpdr_stats`, compute:

```python
        total_bass_anchor = int(stats.get("total_bass_anchor_notes", stats.get("selected_original_bass", 0)))
        kept_bass_anchor = int(stats.get("kept_bass_anchor_notes", stats.get("kept_original_bass", 0)))
        bass_anchor_integrity = 1.0 if total_bass_anchor == 0 else kept_bass_anchor / total_bass_anchor
        stats["bass_anchor_integrity"] = round(bass_anchor_integrity, 6)
```

Use bass_anchor_integrity for bass continuity score or combine it with existing bass metric.

- [ ] **Step 4: Run tests**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: PASS.

---

### Task 5: Strengthen token conflict priority and report fields

**Files:**
- Modify: `src/audio_to_yaml_converter.py:318-346`
- Modify: `src/audio_to_yaml_converter.py:2047-2078`
- Modify: `tests/test_audio_to_yaml_svsep_mpdr.py`

**Interfaces:**
- Consumes: `MpdrNoteCandidate`, `_candidates_to_modifier_safe_tokens`.
- Produces:
  - `MpdrNoteCandidate.priority_rank: int | None = None` if needed, or internal priority function.
  - Deterministic priority: primary melody > bass anchor > right non-melody > left harmony > duplicate/filler.

#### Subagent prompt

```text
任务：实现 Task 5: Strengthen token conflict priority and report fields。

请在 F:\NTEZMusic 中工作。先读取：
- src/audio_to_yaml_converter.py:318-346
- src/audio_to_yaml_converter.py:2047-2078
- tests/test_audio_to_yaml_svsep_mpdr.py
- docs/DESIGN.md 中 @DESIGN:FR-006 与 @DESIGN:change-6

目标：当多个候选映射到同一物理 token 时，使用明确音乐优先级，而不是只比较 keep_score。优先级：主旋律 > 低音锚点 > 右手非旋律 > 左手和声 > 填充。

实现要求：
1. 新增 helper：
   def _mpdr_candidate_priority(self, candidate: MpdrNoteCandidate) -> tuple[int, float, float, int]
2. priority tuple 越大越优先。
3. 主旋律 rank=5。
4. 左手最低音/bass anchor 需要识别：如果 candidate.hand == "left" 且 candidate.utility 中包含 bass anchor 不好直接判断，则用 candidate.keep_score 与 note.pitch 暂不够。为避免破坏结构，可在 MpdrNoteCandidate 增加 is_bass_anchor: bool 字段，默认 False。
5. _select_mpdr_left_notes 中 bass_candidate 构造时设置 is_bass_anchor=True；其他候选 False。
6. _candidates_to_modifier_safe_tokens 发生同 token 冲突时，比较 _mpdr_candidate_priority。
7. conversion_stats 中累计 svsep_mpdr_token_conflict_dropped_notes。

测试追加：
- test_candidates_to_modifier_safe_tokens_prefers_primary_melody_on_same_token
- test_candidates_to_modifier_safe_tokens_prefers_bass_anchor_over_left_harmony

运行：
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

- [ ] **Step 1: Add failing tests**

Append:

```python

def _candidate(note: MidiNoteEvent, hand: str, mapped_pitch: int, keep_score: float, is_primary: bool = False, is_bass: bool = False):
    from audio_to_yaml_converter import MpdrNoteCandidate

    return MpdrNoteCandidate(
        note=note,
        hand=hand,
        mapped_pitch=mapped_pitch,
        start_index=0,
        is_primary_melody=is_primary,
        utility=keep_score,
        masking_risk=0.0,
        perceptual_cost=1.0,
        keep_score=keep_score,
        is_bass_anchor=is_bass,
    )


def test_candidates_to_modifier_safe_tokens_prefers_primary_melody_on_same_token(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    melody = _candidate(_note(60, 0.0, 0.5, 90), "right", 60, 1.0, is_primary=True)
    harmony = _candidate(_note(72, 0.0, 0.5, 80), "left", 60, 10.0, is_primary=False)

    tokens, kept, dropped = converter._candidates_to_modifier_safe_tokens([harmony, melody], config)

    assert tokens == ["1"]
    assert kept == [melody]
    assert dropped == 1


def test_candidates_to_modifier_safe_tokens_prefers_bass_anchor_over_left_harmony(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    bass = _candidate(_note(48, 0.0, 0.5, 70), "left", 48, 1.0, is_bass=True)
    harmony = _candidate(_note(60, 0.0, 0.5, 100), "left", 48, 5.0, is_bass=False)

    tokens, kept, dropped = converter._candidates_to_modifier_safe_tokens([harmony, bass], config)

    assert tokens == ["-1"]
    assert kept == [bass]
    assert dropped == 1
```

- [ ] **Step 2: Run tests to fail**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: FAIL because `is_bass_anchor` does not exist and conflict selection uses keep_score only.

- [ ] **Step 3: Implement priority**

Add field to `MpdrNoteCandidate`:

```python
    is_bass_anchor: bool = False
```

Update every construction of `MpdrNoteCandidate` to pass `is_bass_anchor=False` except bass anchor in `_select_mpdr_left_notes`.

Add helper:

```python
    def _mpdr_candidate_priority(self, candidate: MpdrNoteCandidate) -> tuple[int, float, float, int]:
        """
        计算 MPDR token 冲突消解优先级。

        :param candidate: 待比较候选
        :return: 越大越优先的排序元组
        """
        if candidate.is_primary_melody:
            rank = 5
        elif candidate.is_bass_anchor:
            rank = 4
        elif candidate.hand == "right":
            rank = 3
        elif candidate.hand == "left":
            rank = 2
        else:
            rank = 1
        return (rank, candidate.keep_score, candidate.utility, -candidate.note.pitch)
```

Use it inside `_candidates_to_modifier_safe_tokens`.

- [ ] **Step 4: Run tests**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py -v
```

Expected: PASS.

---

### Task 6: Add CLI/config wiring and end-to-end smoke validation

**Files:**
- Modify: `src/audio_to_yaml_converter.py` CLI parser section near `ArgumentParser` / `add_argument` lines.
- Modify: `docs/audio_to_yaml_converter使用说明.md` if it already documents CLI options.
- Modify: `tests/test_audio_to_yaml_svsep_mpdr.py`

**Interfaces:**
- Consumes: all new `AudioPipelineConfig` fields from Tasks 1-5.
- Produces: CLI arguments for new config fields and a conversion stats completeness test.

#### Subagent prompt

```text
任务：实现 Task 6: Add CLI/config wiring and end-to-end smoke validation。

请在 F:\NTEZMusic 中工作。先读取：
- src/audio_to_yaml_converter.py 中 ArgumentParser/add_argument 相关区域
- src/audio_to_yaml_converter.py 中 AudioPipelineConfig 构造区域
- docs/audio_to_yaml_converter使用说明.md
- tests/test_audio_to_yaml_svsep_mpdr.py
- docs/DESIGN.md 中 @DESIGN:FR-007、NFR-002、change-7

目标：把新增配置接入 CLI 和 AudioPipelineConfig 构造，并增加不依赖视频、不依赖 Mock 的 smoke test。不要运行 Demucs/Transkun/piano_svsep 的伪测试；如果缺少真实模型，只做不需要模型的单元 smoke。

实现要求：
1. 为新增字段添加 CLI 参数：
   - --disable-onset-cluster
   - --onset-cluster-window-beats
   - --onset-cluster-max-span-beats
   - --mpdr-melody-pitch-weight
   - --mpdr-melody-duration-weight
   - --mpdr-melody-velocity-weight
   - --mpdr-melody-beat-weight
   - --mpdr-melody-continuity-weight
   - --mpdr-melody-large-jump-penalty
   - --mpdr-melody-repetition-penalty
2. AudioPipelineConfig 构造时传入这些 args。
3. 更新使用说明文档，只说明真实 CLI 参数，不写假示例结果。
4. 增加测试：test_svsep_mpdr_design_does_not_import_video_fusion_modules，检查 audio_to_yaml_converter 源码中没有 import fusion_engine/dual_stream_extractor。
5. 增加测试：test_score_report_contains_required_svsep_mpdr_metric_names，调用 _score_svsep_mpdr_stats 后断言 stats 包含 melody_integrity、bass_anchor_integrity、masking_avoidance、harmonic_completeness、register_clarity。

运行：
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py tests/test_svsep_hand_separator_audit.py -v
```

- [ ] **Step 1: Add failing tests**

Append:

```python

def test_svsep_mpdr_design_does_not_import_video_fusion_modules():
    source = Path("src/audio_to_yaml_converter.py").read_text(encoding="utf-8")

    assert "import fusion_engine" not in source
    assert "from fusion_engine" not in source
    assert "import dual_stream_extractor" not in source
    assert "from dual_stream_extractor" not in source


def test_score_report_contains_required_svsep_mpdr_metric_names(tmp_path: Path):
    converter = MidiToYamlConverter()
    config = _base_config(tmp_path)
    stats = {
        "total_melody_notes": 1,
        "kept_melody_notes": 1,
        "kept_masking_risk": 0.0,
        "kept_left_utility": 1.0,
        "selected_original_bass": 1,
        "kept_original_bass": 1,
        "token_dropped_notes": 0,
        "total_bass_anchor_notes": 1,
        "kept_bass_anchor_notes": 1,
    }

    converter._score_svsep_mpdr_stats(stats=stats, config=config)

    for key in ("melody_integrity", "bass_anchor_integrity", "masking_avoidance", "harmonic_completeness", "register_clarity"):
        assert key in stats
```

- [ ] **Step 2: Run tests to fail**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py tests/test_svsep_hand_separator_audit.py -v
```

Expected: FAIL until Task 4/CLI wiring is complete.

- [ ] **Step 3: Wire CLI**

Add arguments near existing MPDR arguments. Use `action="store_true"` for disable flag and pass `onset_cluster_enabled=not args.disable_onset_cluster` into config.

- [ ] **Step 4: Update usage docs**

Modify `docs/audio_to_yaml_converter使用说明.md` to list new options under SVSEP-MPDR 参数. Do not include fake conversion metrics.

- [ ] **Step 5: Run tests**

Run:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py tests/test_svsep_hand_separator_audit.py -v
```

Expected: PASS.

---

## Final Verification

After all tasks pass, run syntax and available tests:

```bash
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m py_compile src/audio_to_yaml_converter.py src/svsep_hand_separator.py
"/c/Users/15311/AppData/Local/Programs/Python/Python310/python.exe" -m pytest tests/test_audio_to_yaml_svsep_mpdr.py tests/test_svsep_hand_separator_audit.py -v
```

If the real piano_svsep model path is available, run one real MIDI conversion in `svsep_mpdr` mode using an existing `work/*/midi/*.mid` file and the actual model path. If the model path is not available, report the missing path as an environment blocker; do not fake the run.

## Plan Self-Review

- Spec coverage: FR-001 is covered by Tasks 2-6; FR-002 by Task 1; FR-003 by Task 2; FR-004 by Task 3; FR-005 by Task 4; FR-006 by Task 5; FR-007 by Tasks 4 and 6; pure-audio exclusion by Task 6.
- 占位符扫描：已检查任务步骤中的未完成占位表述，当前没有遗留。
- Type consistency: all produced interfaces are named in their defining task before later tasks use them.
- Scope check: video fusion is deliberately excluded from implementation; no task modifies `src/fusion_engine.py` or `src/dual_stream_extractor.py`.
