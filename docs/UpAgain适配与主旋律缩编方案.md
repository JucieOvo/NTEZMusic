# UpAgain 游戏适配与主旋律缩编转换方案

## 1. 目标与意图
我们已经在前一阶段测试中，成功通过音视频双重补差生成了高保真的双声部带有力度标记的 MIDI 文件。
本方案的目标是：
1. 从中提取出**右手主旋律音轨**。
2. 将其在时序与音高维度进行**缩编**，转化为主旋律单音轨输入。
3. 适配并直接连接至《UpAgain》游戏自动弹奏系统，生成游戏可执行的 YAML 键盘简谱，并进行总装测试。

## 2. 游戏逻辑分析
根据对 `UpAgain/upagain_yaml_converter.py` 与 `UpAgain/upagain_auto_player.py` 的代码逻辑审计，判定适配的核心要求如下：
- **物理按键限制**：游戏只支持单音或和弦通过键盘 A-J（中音 `1-7`）与 Q-I（高音 `+1-+7`, `++1`）发出，并且不支持任何半音（即黑键 `#` 或 `b`）。
- **投递限制**：自动弹奏器 `upagain_auto_player.py` 内部的 `UpAgainNotationParser` 会将和弦强制剥离，只保留权重最高的单个最高音。
- **YAML 结构要求**：转换器接收以拍数（Beat）为步长的简谱序列，将每个音符或休止符时值进行网格化（Quantization），随后自动移调至 C 大调以最大程度消灭黑键，最后剪裁至范围 `[60, 84]`（C4 到 C6）内。

## 3. 实现与适配链路
为打通这一闭环，我们需要开发核心桥接模块 `src/midi_to_upagain_yaml.py`，执行如下流水线：

1. **MIDI 核心解析**：使用 `pretty_midi` 读取前一步生成的 `final_hand_split_score.mid`。
2. **提取右手/主旋律**：指定只提取右手音轨（Track 1），或者进一步根据融合引擎的 `is_melody` 属性提纯（右手音轨已凝聚了大部分主旋律信息）。
3. **节拍与相对时值求取**：
   - 提取 MIDI 中的主 BPM（缺省设为 120），将音符的起始绝对秒数 $T$ 映射到以“拍”为单位的分数式时间轴：$Beat = T \times (BPM / 60.0)$。
   - 维持单音的先后发声顺序，对无发声音符的空白时间填充休止符 `"0"` 或 `"rest"`。
4. **逆向音高翻译器（Pitch to Text Mapping）**：
   - 创建字典，将绝对 MIDI 音高反写为简谱符号：
     - $60 \rightarrow "1"$
     - $62 \rightarrow "2"$
     - $72 \rightarrow "+1"$
     - $61 \rightarrow "#1"$ (半音保留，由后续转换器执行自动调性平移以消灭非白键说明)
5. **串联转换链**：
   - 将转换后的原始简谱 YAML 串接到 `upagain_yaml_converter.py` 的 C大调网格化转换逻辑，生成最终的 `upagain_score.yaml`。

## 4. 模块划分
- **`src/midi_to_upagain_yaml.py`** (本次待开发桥接器)：
  - `MidiMelodyExtractor`: 从 MIDI 文件中解码右手音符及相对时值律动。
  - `PitchToNotationTokenConverter`: 音符音高与简谱 token 的强互换。
  - `write_to_raw_yaml`: 输出未量化的原始 YAML 谱。
- **`UpAgain/upagain_yaml_converter.py`** (项目既有模块)：
  - 用于对原始谱进行 C 大调最大白键率移调、88 键高精度过滤折叠。
- **运行总控测试脚本**：
  - 启动转换并在工作区物理进行缩编测试。

## 5. 风险点与防范
1. **多音并发残留**：若 MIDI 提取中右手依然存在和弦，提取时必须执行“同拍取最高音”物理剥离，避免传入非法和弦格式引爆转换器配置。
2. **非自然拍切分**：遇到非四分、八分、十六分音符等不规则延音，网格采样可能产生误差。开发中须设定自适应量化门槛，剔除超细微时空抖动。

## 6. 执行顺序
1. 编写 `src/midi_to_upagain_yaml.py` 核心桥接器代码。
2. 编写全装配测试脚本，串联：
   - 输入 `test.mp4` -> 生成 `final_hand_split_score.mid`。
   - 读取该 MIDI -> 生成 `raw_score.yaml`。
   - 调起 `upagain_yaml_converter.py` -> 生成游戏最终谱 `upagain_playable.yaml`。
3. 执行并输出转换完毕的 YAML 文件进行验证，确保音高都在合法物理映射 `[60, 84]` 之内。
