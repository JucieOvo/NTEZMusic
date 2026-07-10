# UpAgain 游戏乐器自动弹奏适配方案

## 1. 目标
依托现有的 `NTEZMusic` 自动弹奏基础架构，为游戏 `UpAgain` 适配专用的乐器自动弹奏工具，并将其部署于 `F:\NTEZMusic\UpAgain` 目录下。

## 2. 意图
UpAgain 游戏内乐器具有特殊性：
1. **音域限制**：仅包含15个音，分别为中低音 `do` 到 `si`（7个音）以及高音 `do·` 到超高音 `do··`（8个音）。
2. **和弦限制**：单次只能触发一个音（单音发声机制）。
3. **键位映射**：完全基于 `a-j` 与 `q-i` 的键盘映射。
我们需要在现有读取 YAML 简谱和模拟输入机制的基础上，定制针对 UpAgain 的音符过滤与键位转换逻辑。

## 3. 技术要点
- **键位映射 (Key Mapping)**：
  重新实现针对 UpAgain 的虚拟键码映射表：
  - `do-si` (音符 1-7) 对应键盘 `a s d f g h j`
  - `do·-do··` (音符 +1 到 +7 及 ++1) 对应键盘 `q w e r t y u i`
- **单音轨策略 (Monophonic Filter)**：
  由于游戏单次只能有一个音触发，原有谱子中若存在和弦（即同一时间点有多个音符的 `ScoreEvent`），必须进行过滤。
  算法策略：当同一时间戳或时间间隔极短内出现多个音符时，优先保留**最高音**（通常是主旋律），剔除伴奏音。
- **模块复用**：
  在 `UpAgain` 目录下，新建 `upagain_auto_player.py`，尽可能调用或参考 `src/piano_auto_player.py` 中的 `PianoConfigLoader` 与 `WinApiInputBackend`，从而保持架构一致性和代码模块性。

## 4. 模块划分与数据流
- **Parser 模块**：读取现有 YAML 配置，新增“和弦降级/高音保留”解析逻辑，将多音符事件平铺或缩减为纯单音序列。
- **Mapper 模块**：将解析后的单音字符（如 `+1`, `3`, `++1`）转换为 Windows 虚拟键码（VK_Q - VK_I, VK_A - VK_J）。
- **Player 模块**：基于当前时间的精确比对，调用 `ctypes.windll.user32.SendInput` 发送按键。

## 5. 风险点
1. 过滤和弦可能会导致原先复杂的钢琴谱表现力下降。
2. 同音连续触发时的按键抬起（KeyUp）间隔需要精心微调，否则会被游戏识别为长按而无法重新触发单音。
3. Windows SendInput 同样存在反作弊检测风险。

## 6. 执行顺序
1. **第一步**：在 `F:\NTEZMusic\UpAgain` 中创建针对单音限制的解析模型与映射表原型。
2. **第二步**：编写核心类 `UpAgainVirtualKeyMapper` 与 `UpAgainNotationParser`。
3. **第三步**：组装 `UpAgainAutoPlayer` 入口并包含真实的 `SendInput` 测试逻辑。
4. **第四步**：以一个简单的 YAML 曲谱为例，进行单音过滤及模拟按键的最终验证。