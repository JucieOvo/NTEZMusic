# YAML 曲谱压缩与多键同按方案

## 一、目标

当前 `config/animenz_a_cruel_angels_thesis.yaml` 由 MIDI 量化生成，事件数量为 `2210`，大多数事件为 `0.25` 拍。该粒度能够保留密集节奏，但对游戏键盘输入和人工检查都偏重。

本方案目标是在尽量保持听感一致的前提下，通过“时间窗口聚合 + 多键同按”的方式压缩 YAML：

1. 减少 score 事件数量。
2. 将相邻短时间内出现的音符合并为同一和弦。
3. 保持整体时长、BPM 和基本节奏骨架。
4. 不生成超出键位映射范围的 token。
5. 不使用伪数据，不对不存在的音符做虚构补全。

## 二、现状分析

当前生成结果：

1. 文件：`config/animenz_a_cruel_angels_thesis.yaml`
2. BPM：`126`
3. 量化单位：`0.25` 拍
4. score 事件数量：`2210`
5. 已启用半音 token。
6. 已启用 `octave_fold`，超范围音符已按八度迁移到 C3 到 B5。

主要问题：

1. 许多连续 0.25 拍音符在听感上属于同一快速琶音或装饰音。
2. 游戏键盘输入对极短连续事件的稳定性有限。
3. 直接播放 2210 个事件会导致持续执行时间长、调试成本高。

## 三、压缩策略

### 3.1 时间窗口聚合

新增一个压缩窗口参数 `compression_window_beats`。

默认建议从 `0.5` 拍开始，即把连续两个 `0.25` 拍窗口内的音符合并为一个事件。

示例：

```yaml
- notes: '1'
  beat: 0.25
- notes: '[3 5]'
  beat: 0.25
```

压缩为：

```yaml
- notes: '[1 3 5]'
  beat: 0.5
```

### 3.2 和弦去重

同一窗口内如果重复出现相同 token，只保留一次。

示例：

```yaml
- notes: '+1'
- notes: '[+1 +5]'
```

合并后输出：

```yaml
- notes: '[+1 +5]'
```

### 3.3 休止符处理

若窗口内只有休止符，则输出休止符。

若窗口内同时存在休止符和音符，则休止符不参与合并，只保留真实音符。

### 3.4 最大同按数量限制

压缩会增加单个和弦的音符数量，因此需要保留 `max_chord_notes` 限制。

建议默认值：

1. 普通压缩：`10`
2. 激进压缩：`12`
3. 超过限制时直接报错，不自动删音。

### 3.5 时长保持

压缩不能改变总拍数。

如果原始事件总时长为 `N` 拍，压缩后所有事件 `beat` 之和必须仍为 `N` 拍。

### 3.6 不做的事情

以下处理会明显改变音乐内容，默认不做：

1. 不自动删除低音或高音。
2. 不自动改变 BPM。
3. 不自动把半音改成自然音。
4. 不自动重配和声。
5. 不把不同窗口的长音延音伪造成持续按键。

## 四、实现方式

建议在 `src/audio_to_yaml_converter.py` 中新增 `YamlScoreCompressor`：

1. 输入 YAML 数据结构。
2. 读取 `score` 事件。
3. 按 `compression_window_beats` 累计事件。
4. 将窗口内 token 展开为单音 token 集合。
5. 去重并合并为单音或和弦。
6. 输出新的 `score`。
7. 写出压缩报告。
8. 使用 `PianoConfigLoader` 回读校验。

也可以新增命令行参数：

```powershell
python src/audio_to_yaml_converter.py `
  --audio "...mp3" `
  --output-yaml "config/animenz_a_cruel_angels_thesis_compressed.yaml" `
  --work-dir "work/animenz_a_cruel_angels_thesis_compressed" `
  --song-name "Animenz 残酷天使的行动纲领 压缩版" `
  --bpm 126 `
  --allow-accidentals `
  --out-of-range-policy "octave_fold" `
  --compress-score `
  --compression-window-beats 0.5 `
  --max-chord-notes 10
```

## 五、预期效果

如果使用 `0.5` 拍窗口，理论上事件数量会接近减半，但实际取决于休止符和窗口边界。

如果使用 `1.0` 拍窗口，事件数量会进一步下降，但快速旋律会更明显地被和弦化，听感会从“快速跑动”变成“密集和声块”。

建议先生成 `0.5` 拍压缩版，保留原始版作为对照。

## 六、风险点

1. 多键同按过多时，游戏端可能漏键。
2. 过大窗口会牺牲旋律走向，听感变得块状。
3. 半音组合键与多音和弦同时出现时，输入后端会按住修饰键，可能影响同一和弦中的其他自然音。
4. 当前自动弹奏器的升降半音是全和弦共享修饰键输入，混合自然音与半音的复杂和弦可能不完全等价于钢琴音高。

## 七、建议执行顺序

1. 新增压缩器和命令行参数。
2. 生成 `config/animenz_a_cruel_angels_thesis_compressed.yaml`，不覆盖原始文件。
3. 输出压缩报告，对比压缩前后事件数量和总拍数。
4. 使用 `PianoConfigLoader` 回读校验。
5. 用户试听后决定是否继续调整窗口为 `0.75` 或 `1.0`。

> 作者：JucieOvo
