# 五歌曲四模式全产物生成实施计划

> 作者：JucieOvo
> 日期：2026-07-11
> 状态：用户已明确要求执行

## 一、目标

对以下5个真实 MP3 分别运行4种保留模式，并生成每种模式的 YAML、36键 MIDI、真实采样钢琴 WAV、MP3、硬约束报告和六维指标：

1. `F:\NTEZMusic\【Animenz】One Last Kiss - 新·福音战士剧场版：终 钢琴.mp3`
2. `F:\NTEZMusic\妄想哀歌 (feat_ 初音ミク & 可不) (Dreamydwaddie remix) (Remix) - Dreamydwaddie.mp3`
3. `F:\NTEZMusic\audio_迷航在最熟悉的世界.mp3`
4. `F:\NTEZMusic\audio_水....mp3`
5. `F:\NTEZMusic\audio_Beautiful.mp3`

保留模式：

- `adaptive_octave_fold`
- `hands_decoupled`
- `attention_weighted`
- `svsep_mpdr`

明确排除：

- `score_aware_theory`
- `octave_fold`

## 二、代码改动

1. 新增 `config/arrangement_all_modes_20260711.yaml`，使用独立输出根目录 `work/arrangement_all_modes_20260711`。
2. 扩展 `BenchmarkConfig.score_aware_mode` 为可选值；配置为 `null` 时完全跳过 score-aware 就绪检查、执行和报告。
3. 将配置加载器从“恰好2首”改为“至少1首”，保持 slug 唯一与真实文件校验。
4. runner 的排名、manifest、报告标题与平均分按实际歌曲数量动态生成。
5. 保持原两歌曲基准配置与测试兼容，不改变已有排名和产物。

## 三、TDD 顺序

### 任务1：五歌曲配置 RED/GREEN

新增 `tests/test_arrangement_all_modes_config.py`：

- 真实加载5首 MP3；
- 断言候选恰好为4种保留模式；
- 断言 `score_aware_mode is None`；
- 断言输出目录、SoundFont 哈希、工具和 CUDA 配置有效。

先运行并确认现有加载器因“必须恰好两首”失败，再修改生产代码。

### 任务2：runner 可选 score-aware RED/GREEN

新增真实 runner 测试，使用5歌曲配置和已有真实 MIDI 执行单候选，确认：

- `score_aware_theory` 不在允许模式；
- 正式4模式可执行；
- manifest 不出现 score-aware 字段或产物；
- 报告按5首歌曲动态描述。

### 任务3：预检

执行真实预检：

- 两首 Animenz/混音音频和3个 `audio_*` 文件可读；
- CUDA、Demucs、Transkun、piano_svsep、FluidSynth、ffmpeg 可用；
- Salamander Grand Piano SF2 哈希匹配。

## 四、真实运行

运行：

```powershell
$env:PYTHONPATH = "F:\NTEZMusic\src"
& "C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe" `
  -m arrangement_benchmark.cli `
  --config config/arrangement_all_modes_20260711.yaml run
```

每首歌曲只生成一次 Demucs piano stem 和不可变 Transkun B MIDI，4种模式共享该 MIDI。BPM 仅写 sidecar，不修改 B MIDI tempo/ticks。

## 五、产物结构

```text
work/arrangement_all_modes_20260711/
├─ <song_slug>/
│  ├─ reference/base_midi/song.mid
│  ├─ reference/audio/original_grand_piano.wav
│  ├─ reference/audio/original_grand_piano.mp3
│  └─ algorithms/
│     ├─ adaptive_octave_fold/
│     ├─ hands_decoupled/
│     ├─ attention_weighted/
│     └─ svsep_mpdr/
├─ reports/ranking.json
├─ reports/final_report.md
├─ manifest.json
└─ blind_listening/
```

## 六、验收

1. 5首原始 B MIDI、原始大钢琴 WAV/MP3 均可回读。
2. 5×4=20组模式产物全部包含 YAML、MIDI、WAV、MP3、metrics 和约束报告。
3. 所有缩编 MIDI 位于 C3-B5、最多6音、统一 velocity、无 pedal。
4. manifest 记录全部核心产物 SHA-256、工具、参数和音源证据。
5. 不生成 `score_aware_theory` 与 `octave_fold` 目录或排名记录。
6. 新测试、现有11项基准测试、48项相关回归测试与 LSP 全部通过。
