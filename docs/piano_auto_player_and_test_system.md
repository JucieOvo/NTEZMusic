"""
模块名称：piano_auto_player_and_test_system 代码说明书
功能描述：
    面向后续开发者的中文代码说明书，完整分析 NTEZMusic 项目中自动弹奏器与测试体系
    的架构设计、事件流、YAML 曲谱格式、SendInput 使用方式、测试覆盖与审计工具。

作者：JucieOvo
创建日期：2026-07-09
"""

# NTEZMusic 自动弹奏器与测试体系 -- 代码说明书

---

## 目录

1. [自动弹奏器架构总览](#1-自动弹奏器架构总览)
2. [YAML 曲谱格式定义](#2-yaml-曲谱格式定义)
3. [核心类职责与交互](#3-核心类职责与交互)
4. [完整事件流](#4-完整事件流)
5. [键盘映射方案](#5-键盘映射方案)
6. [SendInput 的使用方式与风险](#6-sendinput-的使用方式与风险)
7. [测试体系分析](#7-测试体系分析)
8. [审计工具工作方式](#8-审计工具工作方式)
9. [潜在问题与改进方向](#9-潜在问题与改进方向)

---

## 1. 自动弹奏器架构总览

自动弹奏器的核心模块位于 `src/piano_auto_player.py`（约 862 行），采用分层设计：

- **数据层**：`SongConfig`、`PlaybackConfig`、`RawScoreEvent`、`ScoreEvent`、`PianoConfig` 五个 frozen dataclass，承载曲谱配置与解析后的可执行事件。
- **加载层**：`PianoConfigLoader` 读取 YAML 文件，经过校验转换为强类型 `PianoConfig`。
- **解析层**：`NotationParser` 将人类可读的简谱字符串（token）解析为具体的键盘按键表达式。
- **映射层**：`VirtualKeyMapper` 将按键名称（如 `q`、`shift`）转换为 Windows 虚拟键码。
- **输入层**：`WinApiInputBackend` 通过 Windows `SendInput` API 发送真实键盘事件。
- **调度层**：`PianoAutoPlayer` 根据 BPM 计算节拍时间线，协调等待、按键和节奏控制。

关系示意图：

```
YAML 文件
    |
    v
PianoConfigLoader  --加载+校验--> PianoConfig
                                    |
                                    v
NotationParser  ----------------> tuple[ScoreEvent]  (简谱解析)
                                    |
                                    v
PianoAutoPlayer.play()  ----------> 按节拍遍历事件
    |                                   |
    v                                   v
WinApiInputBackend.press_keys()     time.sleep() 节奏控制
    |
    v
VirtualKeyMapper.to_virtual_key()
    |
    v
SendInput API  --> 游戏窗口接收键盘输入
```

---

## 2. YAML 曲谱格式定义

### 2.1 顶层结构

项目使用 YAML 作为曲谱的通用数据格式，典型文件如 `config/one_last_kiss_svsep_mpdr_v3.yaml`。顶层包含五个区域：

```yaml
song:        # 曲谱基础信息
playback:    # 播放控制参数
keyboard:    # 音区-音符-按键 三层映射
score:       # 曲谱事件列表（核心）
```

### 2.2 字段详细说明

**song 区域：**

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| name | string | 曲谱名称 | 非空字符串 |
| bpm | float | 每分钟节拍数 | 大于 0 |
| beat_unit | int | 节拍单位分母 | 大于 0 的整数 |

**playback 区域：**

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| start_delay_seconds | float | 开始弹奏前等待秒数 | 大于等于 0 |
| key_press_seconds | float | 按键保持时间秒数 | 大于等于 0 |

**keyboard 区域：**

三层嵌套映射：音区（high/middle/low）到音符编号（1-7）到键盘按键（字母键）：

```yaml
keyboard:
  high:      # 高音区 C5-B5，简谱前缀 +
    1: q
    2: w
    3: e
    4: r
    5: t
    6: y
    7: u
  middle:    # 中音区 C4-B4，简谱无前缀
    1: a
    2: s
    3: d
    4: f
    5: g
    6: h
    7: j
  low:       # 低音区 C3-B3，简谱前缀 -
    1: z
    2: x
    3: c
    4: v
    5: b
    6: n
    7: m
```

**score 区域：**

事件列表，每个事件两个字段：

| 字段 | 类型 | 说明 | 约束 |
|------|------|------|------|
| notes | string | 简谱字符串，含单个或多个 token | 非空字符串 |
| beat | float | 该事件中每个 token 持续的拍数 | 大于 0 |

示例 score 片段（选自 `one_last_kiss_svsep_mpdr_v3.yaml`）：

```yaml
score:
  - notes: '[-#1 #5 7 +3 +#5]'    # 和弦：同时按下 5 个音
    beat: 0.25
  - notes: '0'                      # 休止符：不发送按键
    beat: 0.5
  - notes: '#5'                     # 单音：升半音的第 5 级
    beat: 0.25
```

### 2.3 简谱记谱规则

该简谱不是标准数字简谱（numbered musical notation），而是项目自定义的 36 键游戏缩编记谱：

- **单音**：1 表示自然音级（1-7），对应 C 大调 do-re-mi-fa-sol-la-si
- **音区前缀**：`+` 表示高音区（C5-B5），`-` 表示低音区（C3-B3），无前缀为中音区（C4-B4）
- **变音记号**：`#` 表示升半音，`b`/`B`/`♭` 表示降半音
- **休止符**：`0` 或 `rest`
- **和弦**：用方括号包裹，内部空格分隔，如 `[#1 b3 5]`

变音记号与音区前缀的位置关系：`[音区前缀][变音记号][音级编号]`，例如：

| token | 含义 |
|-------|------|
| `1` | 中音区 do，MIDI 60 |
| `+5` | 高音区 sol，MIDI 79 |
| `-#1` | 低音区升 do，MIDI 49 |
| `+b3` | 高音区降 mi，MIDI 75 |
| `[#1 b3 5]` | 中音区升 do + 降 mi + sol 同时按下 |

### 2.4 变音记号与修饰键的对应关系

播放器层面：`#` 音符通过按 Shift + 基础键实现升半音，`b` 音符通过按 Ctrl + 基础键实现降半音。

这是因为《异环》游戏钢琴的 36 键系统中，"升半音"和"降半音"是通过 Shift/Ctrl 修饰键叠加基础键位实现的，而非单独的物理黑键。

---

## 3. 核心类职责与交互

### 3.1 数据类（frozen dataclass）

所有数据类均为 `frozen=True`，不可变类型，保证线程安全。

**SongConfig**（`piano_auto_player.py:49-65`）

保存曲名、BPM 和节拍单位。`beat_unit` 字段目前在播放器中仅用于配置记录和后续扩展，尚未参与节拍换算。节拍换算直接使用 `60.0 / bpm` 计算每拍秒数。

**PlaybackConfig**（`piano_auto_player.py:68-82`）

保存启动延迟和按键持续时间。`key_press_seconds` 为 0.0 时，播放器使用保底 1ms 按键时间，防止游戏吞音。

**RawScoreEvent**（`piano_auto_player.py:85-99`）

原始曲谱事件，包含 `notes`（原始简谱字符串）和 `beat`（每个 token 持续拍数）。这是一个中间表示，将 YAML 读取与简谱解析解耦。

**ScoreEvent**（`piano_auto_player.py:103-117`）

解析后的曲谱事件。`keys` 是字符串元组，表示需要同时按下的键盘按键表达式（如 `"shift+q"`、`"a"`）；`beat` 是该事件持续拍数。空 `keys` 表示休止符。

**PianoConfig**（`piano_auto_player.py:120-137`）

完整钢琴配置的聚合对象，包含上述四个子对象的组合。

### 3.2 PianoConfigLoader（`piano_auto_player.py:140-288`）

YAML 配置加载与校验器：

- `load(config_path: Path) -> PianoConfig`：主入口，读取文件、校验、解析各子区域
- `_parse_song()`：校验 `song` 区域的 name、bpm、beat_unit，类型和范围检查严格
- `_parse_playback()`：校验 `playback` 区域的延迟和按键时间
- `_parse_keyboard()`：校验键盘映射的三层嵌套结构，将键名统一 lowercase
- `_parse_score()`：校验 `score` 列表的每个事件，确保 notes 和 beat 存在

**设计特点**：
- 校验失败直接抛出异常（`FileNotFoundError` / `ValueError`），不使用默认值
- 使用 `yaml.safe_load()` 避免 YAML 对象构造注入风险
- 使用 `RawScoreEvent` 作为中间表示，将"读取"与"解析"分离

### 3.3 NotationParser（`piano_auto_player.py:291-407`）

简谱解析器，将人类可读的 YAML notes 字符串转换为可执行的 ScoreEvent：

```
RawScoreEvent("[-#1 #5 7 +3 +#5]", beat=0.25)
    |
    v  _split_tokens() 使用正则 CHORD_PATTERN = r"\[[^\]]+\]|\S+"
    |
    tuple: ("[-#1 #5 7 +3 +#5]",)   # 整个和弦被保留为一个 token
    |
    v  _parse_token()
    |
    ScoreEvent(keys=("shift+z", "shift+t", "u", "shift+e", "shift+t"), beat=0.25)
```

关键方法：

- `_split_tokens(notes)`：使用正则 `r"\[[^\]]+\]|\S+"` 拆分简谱字符串，将 `[...]` 和弦保持为一个 token
- `_parse_token(token)`：识别休止符、和弦、单音三种场景
- `_parse_single_note(note_token)`：解析单个音符 token，提取音区前缀（+/-/无）、变音记号（#/b）、音级编号（1-7），查询 keyboard_mapping 得到按键表达式
- `zone_prefix_mapping`：固定字典 `{"+": "high", "": "middle", "-": "low"}`

**变音记号的表达式格式**：
- `#` → 输出 `shift+{key}`（如 `#5` → `shift+t`）
- `b` → 输出 `ctrl+{key}`（如 `b3` → `ctrl+d`）

### 3.4 VirtualKeyMapper（`piano_auto_player.py:503-542`）

将按键名称转换为 Windows 虚拟键码（Virtual Key Code）：

- 字母键（a-z）：使用 `0x41 + 偏移量` 映射（`0x41` = VK_A）
- 数字键（0-9）：使用 `0x30 + 偏移量` 映射（`0x30` = VK_0）
- 修饰键：`shift` = `0x10`（VK_SHIFT），`ctrl`/`control` = `0x11`（VK_CONTROL）

**局限性**：仅支持字母键、数字键和两个修饰键。不支持功能键（F1-F12）、方向键等，但这对游戏 36 键钢琴场景足够。

### 3.5 WinApiInputBackend（`piano_auto_player.py:545-774`）

Windows SendInput API 的真实输入后端：

- 加载 `user32.dll` 并绑定 `SendInput`
- `press_keys(keys, hold_seconds)`：同时按下并释放一组按键
- `_press_keys_unified()`：无冲突修饰键时的统一发送
- `_press_keys_split_modifiers()`：处理 Ctrl+Shift 冲突时的交替发送策略
- `_send_key_event(virtual_key, is_key_up)`：单次 SendInput 调用

**Ctrl/Shift 冲突处理**（关键逻辑）：

《异环》游戏钢琴的修饰键机制特殊：后按入的修饰键会顶掉先前的，同一时刻只能有一个修饰键生效。因此当同一个和弦同时包含 `#` 音符（需要 Shift）和 `b` 音符（需要 Ctrl）时，无法同时按下所有按键，需要交替切换。

解决方案（`_press_keys_split_modifiers` 方法）：

1. 将音符分为三组：Ctrl 组、Shift 组、无修饰自然键组
2. 第 1 拍：按下 Ctrl，发送升半音组，持续 1ms
3. 释放 Ctrl 组
4. 第 2 拍：按下 Shift，发送降半音组，持续 `remaining_hold` 秒
5. 同时保持无修饰自然键始终按下，保证延音连贯
6. 总交替时间 30ms 内完成，低于人耳融合阈值

**注意**：CTRL_AFFECTED_KEYS = {"q", "r", "t"} 和 SHIFT_AFFECTED_KEYS = {"e", "u"} 是硬编码的游戏特定逻辑，在切换修饰键时用于判断自然键分配方向。

### 3.6 PianoAutoPlayer（`piano_auto_player.py:777-823`）

调度器核心：

```
play(config, events):
    1. 计算 seconds_per_beat = 60.0 / bpm
    2. 等待 start_delay_seconds
    3. 遍历每个事件：
       a. 计算事件持续时间 event_seconds = seconds_per_beat * beat
       b. 校验 key_press_seconds 不超过 event_seconds
       c. 记录 start_time
       d. 调用 input_backend.press_keys()
       e. 计算已消耗时间
       f. sleep 剩余时间
```

**风险校验**：`key_press_seconds > event_seconds` 时抛出 ValueError，防止按键保持时间超过事件持续时间导致节奏错位。

---

## 4. 完整事件流

以 `one_last_kiss_svsep_mpdr_v3.yaml` 中一个事件为例，展示完整的事件流：

### 步骤 1：YAML 读取

```yaml
- notes: '[-#1 #5 7 +3 +#5]'
  beat: 0.25
```

### 步骤 2：PianoConfigLoader 加载

```python
RawScoreEvent(notes='[-#1 #5 7 +3 +#5]', beat=0.25)
```

### 步骤 3：NotationParser 解析

正则拆分 `[-#1 #5 7 +3 +#5]` → 一个和弦 token（保留中括号）

_parse_single_note 逐个解析和弦内音符：

| 简谱 token | 音区前缀 | 变音记号 | 音级 | 音区名称 | 基础键 | 最终表达式 |
|-----------|---------|---------|-----|---------|-------|-----------|
| -#1 | - | # | 1 | low | z | shift+z |
| #5 | (无) | # | 5 | middle | t | shift+t |
| 7 | (无) | (无) | 7 | middle | u | u |
| +3 | + | (无) | 3 | high | e | e |
| +#5 | + | # | 5 | high | t | shift+t |

最终 ScoreEvent：

```python
ScoreEvent(
    keys=("shift+z", "shift+t", "u", "e", "shift+t"),
    beat=0.25
)
```

### 步骤 4：PianoAutoPlayer 计算时间

- BPM = 112.3 → seconds_per_beat = 60.0 / 112.3 ≈ 0.534s
- event_seconds = 0.534 * 0.25 ≈ 0.134s
- key_press_seconds = 0.0 → 使用保底 0.001s

### 步骤 5：WinApiInputBackend 发送

检测同时存在 Ctrl（0）和 Shift（1）？否。只有 Shift 修饰键。走统一发送路径（`_press_keys_unified`）：

1. 按下 Shift（VK_SHIFT = 0x10）
2. 依次按下 z, t, u, e, t（注意 t 重复按下，但 SendInput 对同一键重复发 key-down 无害）
3. sleep(0.001s)
4. 依次释放 t, e, u, t, z（逆序释放）
5. 释放 Shift

### 步骤 6：SendInput API 调用

每个按键事件构造一个 INPUT 结构体：

```c
INPUT {
    type = 1 (INPUT_KEYBOARD)
    union.ki = KEYBDINPUT {
        wVk = 0x10 (VK_SHIFT)  // 或 VK_A, VK_Z 等
        wScan = 0
        dwFlags = 0            // 按下; 释放时为 KEYEVENTF_KEYUP(0x0002)
        time = 0
        dwExtraInfo = NULL
    }
}
```

调用 `user32.SendInput(1, &input, sizeof(INPUT))`。

---

## 5. 键盘映射方案

### 5.1 物理键盘布局

游戏《异环》钢琴界面的 36 键映射到 PC 键盘的三行字母键：

```
高音区 (+)：  q  w  e  r  t  y  u        (7 键，对应 do re mi fa sol la si)
中音区 (无)： a  s  d  f  g  h  j        (7 键)
低音区 (-)：  z  x  c  v  b  n  m        (7 键)
```

总共 21 个白键音位，乘以 3 个八度 = 21 个白键。加上 Shift/Ctrl 修饰键产生的升降半音，覆盖完整 36 键（12 半音 * 3 八度）。

### 5.2 音区分配表

| 音区名称 | 简谱前缀 | 八度范围 | MIDI 范围 | 键盘行 |
|---------|---------|---------|----------|-------|
| low | - | C3-B3 | 48-59 | 底行 z-m |
| middle | (无) | C4-B4 | 60-71 | 中行 a-j |
| high | + | C5-B5 | 72-83 | 顶行 q-u |

### 5.3 自然音级到键位

| 简谱音级 | 唱名 | 低音区键 | 中音区键 | 高音区键 |
|---------|------|---------|---------|---------|
| 1 | do | z | a | q |
| 2 | re | x | s | w |
| 3 | mi | c | d | e |
| 4 | fa | v | f | r |
| 5 | sol | b | g | t |
| 6 | la | n | h | y |
| 7 | si | m | j | u |

### 5.4 变音记号的键位组合

- `#1`（升 do，MIDI 61）= Shift + a（中音区 a + Shift 同时按下）
- `b3`（降 mi，MIDI 63）= Ctrl + d（中音区 d + Ctrl 同时按下）
- `+#5`（高音区升 sol，MIDI 80）= Shift + t（高音区 t + Shift 同时按下）

---

## 6. SendInput 的使用方式与风险

### 6.1 SendInput API 说明

`SendInput` 是 Windows 原生 API（user32.dll），功能是将模拟的键盘/鼠标事件注入到系统输入流中。调用方式：

```python
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
```

事件构造使用了 ctypes 联合体（Union），对应 Windows 原生的 `INPUT`、`KEYBDINPUT`、`MOUSEINPUT`、`HARDWAREINPUT` 结构体。

### 6.2 结构体对应关系

| Python 类 | Windows 原生结构 | 用途 |
|-----------|----------------|------|
| Input | INPUT | 顶层事件结构，包含 type + union |
| InputUnion | <union> | 联合体，包含键盘/鼠标/硬件 |
| KeyboardInput | KEYBDINPUT | wVk(虚拟键码), wScan(硬件扫描码, 未使用), dwFlags(按下/释放), time, dwExtraInfo |
| MouseInput | MOUSEINPUT | 保留结构对齐，未使用 |
| HardwareInput | HARDWAREINPUT | 保留结构对齐，未使用 |

### 6.3 风险分析

#### 6.3.1 游戏反作弊检测

代码注释中已明确警告该风险。`SendInput` 属于 R3 级（Ring 3，用户态）输入注入，行为特征：

- 输入事件源自 `SendInput` API，而非物理键盘硬件中断
- 游戏反作弊系统（如 Easy Anti-Cheat、BattlEye、nProtect 等）可通过检测事件源（`GetMessageExtraInfo` 返回值）区分物理按键与模拟注入
- 部分游戏在检测到非物理输入时会触发账号封禁

目前项目使用的是 1ms 保底机制（`hold_seconds <= 0` 时使用 0.001s 保底），但并未采取任何反检测措施（如随机化延迟、人类化按键模式等）。

#### 6.3.2 管理员权限

`SendInput` 在 Windows UIPI（User Interface Privilege Isolation）下，默认只允许相同或更高 integrity level 的进程向目标窗口发送输入。普通用户模式下可能无法向以管理员身份运行的游戏窗口发送按键，因此代码注释要求"以管理员身份运行"。

#### 6.3.3 修饰键全局冲突

`Shift` 和 `Ctrl` 在 Windows 中是全局修饰键。当快捷键组合（如 `Ctrl+Z` = 撤销）被触发时，可能影响其他程序。代码通过在按键前后精确管理修饰键的按下/释放来最小化影响，但无法完全避免。

#### 6.3.4 焦点依赖

`SendInput` 将事件注入到系统输入队列，而非直接发送到指定窗口。这意味着必须在弹奏启动前手动将焦点切换到游戏窗口（通过 `time.sleep(start_delay_seconds)` 提供切换时间）。如果弹奏过程中焦点切换，键盘事件将发送到错误的窗口。

---

## 7. 测试体系分析

### 7.1 测试文件一览

```
tests/
  test_svsep_hand_separator_audit.py   -- SvsepSeparationAudit 纯逻辑校验（4 个测试）
  test_audio_to_yaml_svsep_mpdr.py     -- MIDI 起音聚类、主旋律追踪、候选评分（10 个测试）
  test_score_audit_tool.py             -- 谱面反解与问题检测逻辑（3 个测试）
```

### 7.2 test_svsep_hand_separator_audit.py

**被测试源**：`src/svsep_hand_separator.py` 中的 `SvsepSeparationAudit` 类

**测试用例**（4 个）：

| 测试名 | 场景 | 验证内容 |
|--------|------|---------|
| test_accepts_valid_distribution | 有效声部分离统计 | 不抛异常，match_ratio = 0.98 |
| test_rejects_low_match_ratio | 匹配比例过低 | 抛出 ValueError，匹配中文"匹配比例" |
| test_rejects_empty_hand_result | 左右手全空 | 抛出 ValueError，匹配"左右手音符" |
| test_rejects_unknown_staff | 存在未知 staff 标签 | 抛出 ValueError，匹配"未知 staff" |

**特点**：
- 使用 `_audit(**overrides)` 辅助构造器提供合理默认值
- 仅测试纯逻辑校验（没有模型推理依赖）
- 测试数量偏少，边界覆盖不足

### 7.3 test_audio_to_yaml_svsep_mpdr.py

**被测试源**：`src/audio_to_yaml_converter.py` 中的 `MidiToYamlConverter`

**测试用例**（10 个）：

| 测试名 | 类别 | 验证内容 |
|--------|------|---------|
| TestClusterMidiNoteOnsets.test_cluster_merges_close_chord_notes | 起音聚类 | 三枚近起音和弦被合并为同一 start_beat，velocity 加权平均正确 |
| test_cluster_keeps_arpeggio_when_span_exceeds_limit | 起音聚类 | 起音跨度超出 max_span 的琶音不被合并 |
| test_cluster_rejects_invalid_config | 起音聚类 | window_beats 负数、max_span < window 时抛出 ValueError |
| test_read_midi_notes_applies_clustering_to_real_pretty_midi | 起音聚类 | 真实 pretty_midi 全链路文件读取 |
| TestExtractPrimaryMelodyPath.test_prefers_longer_louder_continuous_path | 主旋律追踪 | DP 在连续旋律与高装饰音间正确选择 |
| test_penalizes_isolated_high_jump | 主旋律追踪 | 大跳惩罚让 DP 避开孤立高跳装饰音 |
| test_score_svsep_mpdr_stats_reports_bass_anchor_integrity | 评分 | 低音锚点完整性写入 stats 且归一化到 0-1 |
| test_build_svsep_mpdr_candidate_accumulates_event_counters | 候选评分 | register/onset/low_mud 事件计数器正确累加 |
| test_candidates_to_modifier_safe_tokens_prefers_primary_melody | token 冲突 | 主旋律优先于高 keep_score 填充音 |
| test_prefers_bass_anchor_over_left_harmony | token 冲突 | 低音锚点优先于左手和声 |
| test_design_does_not_import_video_fusion_modules | 模块隔离 | 纯音频主链路不导入视频融合模块 |
| test_score_report_contains_required_metric_names | 评分 | 所有必需质量指标名称写入 stats |

**特点**：
- 使用 `_base_config(tmp_path, **overrides)` 提供合理默认配置
- 包含真实 pretty_midi 文件写入和读取的全链路测试
- 验证 velocity 加权平均计算结果精度到 1e-9
- 验证统计计数器（clustered_notes、clustered_groups）的正确累加
- 模块隔离测试通过 `read_text` + `assert not in` 验证 import 不泄露

### 7.4 test_score_audit_tool.py

**被测试源**：`work/score_audit/score_audit_tool.py`

**测试用例**（3 个）：

| 测试名 | 验证内容 |
|--------|---------|
| test_parse_note_token_maps_game_notation_to_midi_pitch | 6 种 token 到 MIDI pitch 的映射正确性 |
| test_decode_yaml_score_accumulates_beats_and_detects_duplicate_pitch | YAML 反解积累拍点、识别等音异名重复音高 |
| test_detect_layout_issues_flags_mixed_accidentals_and_weak_dense_chords | 混合升降半音和弱拍高密度和弦的问题检测 |

**特点**：
- 使用真实 `yaml.safe_dump` 构造 YAML 文件写入临时目录
- 验证 YAML 反解后的 `start_beat` 累积正确性
- 验证 `duplicate_pitch_event_count` 等关键统计字段
- 覆盖两个核心 issue 类型

### 7.5 测试覆盖率评估

**已覆盖的场景**：
- 声部分离审计的 4 个边界条件（有效数据、匹配比例低、全空、未知标签）
- MIDI 起音聚类的 3 种场景（近起音合并、琶音不合并、非法参数）
- 主旋律 DP 路径提取的 2 种场景（连续旋律优先、大跳惩罚）
- 评分统计中全部 5 个质量指标的存在性验证
- 候选评分 token 冲突的 2 种优先级决策
- 谱面反解的 token 映射、拍点累积、重复检测
- 审计工具的 2 类问题检测

**遗漏的场景**（按严重程度排列）：

1. **PianoAutoPlayer 完全无测试**：调度器核心的节拍计算逻辑、`play` 方法中校验 `key_press_seconds <= event_seconds` 的边界条件、无效 BPM 的处理均无测试覆盖
2. **NotationParser 无测试**：和弦解析的各种边缘情况（多重嵌套、空和弦、非法字符、超大和弦）、休止符的多种写法
3. **PianoConfigLoader 无测试**：YAML 加载的各种异常情况（缺失字段、类型错误、边界值）、键盘映射的校验
4. **VirtualKeyMapper 无测试**：字母/数字键的虚拟键码计算、非法键名的错误处理
5. **WinApiInputBackend 无测试**：修饰键冲突拆分的所有分支、SendInput 失败处理
6. **未模拟实际游戏环境**：没有任何测试在真实游戏环境中运行，`SendInput` 的调用无法通过单元测试验证
7. **音频流水线未被直接测试**：`DemucsSeparator`、`PianoTranscriber`、`AudioToYamlPipeline` 等需要通过外部命令的组件未被测试覆盖（依赖真实环境和 GPU）
8. **审计工具测试数量偏少**：7 种 issue 类型只覆盖了 2 种，`build_window_stats`、`build_alignment`、`build_meter_diagnostics` 等辅助函数无独立测试

---

## 8. 审计工具工作方式

### 8.1 工具位置

`work/score_audit/score_audit_tool.py`（约 1274 行），是一个独立的谱面对照审查工具。

### 8.2 核心目的

对 `svsep_mpdr_v3` 生成的 YAML 曲谱进行只读谱面对照审查：将 YAML 反解为 36 键限制的 MIDI 文件，与项目现有的参考 MIDI（原始钢琴谱）进行逐窗口统计对比，识别潜在的编曲问题。

### 8.3 审查流程

```
run_audit() 对三首默认曲目执行:

  对每首曲目:
    1. 读取目标 YAML
    2. decode_yaml_score(): 反解 YAML 为 MIDI 音符事件
        - 按 token 拆分 notes
        - 累积拍点 cursor_beat
        - 将游戏 token 映射为 MIDI pitch
        - 统计事件计数、混合升降、重复音高等
    3. 导出反解 MIDI (target_reduced.mid)
    4. 复制参考 MIDI (existing_reference.mid)
    5. read_midi_score(): 将参考 MIDI 和目标 MIDI 统一为 AuditNote 格式
    6. build_alignment(): 2 拍窗口对齐参考与目标
    7. detect_layout_issues(): 检测 7 类问题
    8. 写出产物: target_events.json, alignment_2beat.json, issues.json,
       compare_summary.json, preliminary_findings.md
```

### 8.4 7 类问题检测

| 问题类型 | 严重级 | 置信度 | 触发条件 |
|---------|-------|--------|---------|
| 输入稳定性风险：同事件混合升降半音 | Major | high | 同一事件同时包含 # 和 b 音符 |
| 和弦密度过高 | Major/Minor | high | 同按数 >= 5 |
| 异常重音位置：弱拍高密度和弦 | Minor | medium | 2/4 网格非 0/1 拍位置出现 >= 4 音和弦 |
| 同拍重复目标音高 | Minor | high | 事件内存在重复 MIDI pitch |
| 低音锚点疑似缺失 | Major | medium | 参考窗口最低音 <= 55 但目标窗口最低音 >= 60 |
| 音区压缩过窄 | Major | medium | 参考跨度 >= 36 半音，目标 <= 18 半音且音符数 >= 8 |
| 节奏织体块状化 | Major | medium | 参考起音 >= 6，目标起音 <= 2（快速织体被压缩） |
| 节奏碎片化 | Major | medium | 目标起音 >= 6，参考起音 <= 2（过多重复击键） |

### 8.5 三首曲目的审查结果

`work/score_audit/summary_index.json`（见该文件完整内容），关键指标概览：

| 曲目 | YAML 事件 | 目标/参考音符数 | 音区落差 | 混合升降 | 问题数 |
|------|----------|---------------|---------|---------|-------|
| One Last Kiss | 1683 | 2106/2805 | -699 | 249 | 235 |
| ただ声一つ | 1066 | 1280/1750 | -470 | 90 | 121 |
| 残酷天使的行动纲领 | 2097 | 2703/3393 | -690 | 76 | 200 |

**关键发现**：
- 三首曲目均存在大量"低于/高于游戏音域"的音符（参考 MIDI 超出 36 键范围），缩编导致目标 MIDI 音区被压缩到 C3-C5（48-83）区间
- One Last Kiss 的混合升降半音事件最多（249 个），意味着大量和弦需要 Ctrl/Shift 分组发送
- 部署的目标最大同按为 5（残酷天使为 7），高于参考 MIDI 最大值（4-5），说明缩编中存在音符叠加

### 8.6 审计产物文件结构

```
work/score_audit/
  README.md                   -- 总索引
  summary_index.json          -- 三首曲目摘要
  score_audit_tool.py         -- 审查工具
  one_last_kiss/
    existing_reference.mid    -- 参考 MIDI 副本
    target_reduced.mid        -- YAML 反解 MIDI
    target_events.json        -- YAML 事件定位表
    alignment_2beat.json      -- 2 拍窗口对齐数据
    issues.json               -- 检测到的问题列表
    compare_summary.json      -- 完整摘要统计
    preliminary_findings.md   -- 初步审查报告 Markdown
  tada_koe_hitotsu/           -- 同上
  cruel_angel/                -- 同上
```

---

## 9. 潜在问题与改进方向

### 9.1 自动弹奏器问题

#### 9.1.1 硬编码的音区与变音映射（Critical）

`NotationParser.zone_prefix_mapping`、`WinApiInputBackend.CTRL_AFFECTED_KEYS` 和 `SHIFT_AFFECTED_KEYS` 均为硬编码，假设键盘映射配置始终使用 `low/middle/high` 音区名，且 Ctrl/Shift 受影响的键集合固定。如果 YAML `keyboard` 区域使用不同的音区名或按键映射，这些硬编码将失效。

改进方案：从 `PianoConfig.keyboard_mapping` 解析这些依赖关系，或者要求在 YAML 中显式声明修饰键受影响的键集合。

#### 9.1.2 SendInput 反检测能力为零（Major）

当前实现没有任何随机化或人类化输入特征，被游戏反作弊系统检测的风险高。可添加：
- 按键按下与释放之间的随机微小延迟（符合人类弹奏的 micro-timing 分布）
- 同样的和弦使用可变的修饰键交替间隔（目前固定 1ms）
- 在休止符处添加随机化的微小延迟偏移
- 窗口焦点到游戏窗口的自动切换（通过 `win32gui.FindWindow` 或 `pygetwindow`）

#### 9.1.3 无日志系统（Major）

代码使用 `print()` 输出运行状态，无法记录错误、调试或性能信息。应替换为 Python `logging` 模块，至少提供 INFO（进度）、WARNING（键盘映射缺失）、ERROR（SendInput 失败）三个级别。

#### 9.1.4 节拍精度受限（Minor）

`time.sleep()` 在 Windows 上的精度约为 1-15ms，对于高速曲谱（BPM > 120，拍值 0.125 时每拍约 500ms，误差 1-3%）可能引发节奏偏移。更精确的方案：
- 使用 `time.perf_counter()` 进行忙等待循环
- 或使用 Windows high-resolution timer (`timeBeginPeriod`)

#### 9.1.5 缺少提前终止机制（Minor）

自动弹奏无法被用户中途终止（除非强制关闭进程）。应添加键盘监听（如 `Ctrl+C` 或指定终止键），在按下时立即释放所有按键并退出。

#### 9.1.6 YAML 未定义拍号字段（Info）

YAML 中没有拍号字段（如 `time_signature: 4/4` 或 `2/4`）。审计工具只能通过候选 2/4/4/4 网格试探，无法确定正确的拍号。拍号缺失也影响重音位置分析。

### 9.2 测试体系问题

#### 9.2.1 自动弹奏器零测试覆盖（Critical）

`piano_auto_player.py` 的核心类（PianoConfigLoader、NotationParser、VirtualKeyMapper、WinApiInputBackend、PianoAutoPlayer）完全没有测试覆盖。建议的测试优先级：

1. **PianoConfigLoader**：YAML 加载的正反边界（缺失字段、类型错误、空曲谱、超大曲谱）
2. **NotationParser**：所有和弦语法、休止符变体、非法 token、混合升降场景
3. **VirtualKeyMapper**：字母/数字键映射、修饰键映射、非法键名
4. **PianoAutoPlayer.play()**：BPM 计算、节拍等待、校验逻辑

#### 9.2.2 无集成测试（Major）

整个流水线（加载→解析→播放调度）没有任何端到端集成测试。多个组件间的联合工作无法通过单元测试保证。即使每个组件单独正确，数据流转中的类型不匹配或接口变化可能被遗漏。

#### 9.2.3 现实环境测试缺失（Major）

由于第 6 章所述的风险，SendInput 在真实游戏环境中的行为无法被模拟测试覆盖。至少在开发环境中应有一个"干运行"模式（dry-run mode），记录预期的按键事件但不实际发送。

#### 9.2.4 审计工具测试覆盖不足（Minor）

审计工具的 7 类 issue 目前仅覆盖 2 类（混合升降半音和弱拍高密度和弦）。低音锚点缺失、音区压缩过窄、节奏块状化/碎片化这 4 类 issue 无直接测试。

### 9.3 代码质量与维护性

#### 9.3.1 类型系统使用不完整（Minor）

`InputBackend` 使用 `Protocol` 定义接口，好做法。但 `ctypes` 结构体的类型标注不一致（`wVk` 使用 `ctypes.c_ushort` 等是 ctypes 类型而非 Python 原生类型，无法被静态类型检查器理解）。

#### 9.3.2 错误信息未国际化（Info）

错误信息混杂中文和英文。`PianoConfigLoader` 使用中文错误信息，但 `VirtualKeyMapper` 使用英文。建议统一为中文以匹配项目的注释规范。

### 9.4 改进优先级建议

| 优先级 | 改进项 | 影响评估 |
|--------|-------|---------|
| P0 | 为自动弹奏器添加单元测试 | 缺少测试导致回归风险极高 |
| P0 | 为 SendInput 添加干运行模式 | 开发调试无法进行 |
| P1 | 硬编码修饰键冲突集合改为配置驱动 | 键盘映射灵活性受限制 |
| P1 | 添加 Python logging | 无法诊断运行故障 |
| P1 | 添加提前终止机制 | 弹奏中途无法退出 |
| P2 | 审计工具补充未覆盖的 4 类 issue 测试 | 审计工具回归风险 |
| P2 | YAML 添加 time_signature 字段 | 拍号分析不准确 |
| P2 | WinApiInputBackend 添加反检测特征 | 封号风险 |
| P3 | 集成测试覆盖端到端流程 | 组件间交互未被验证 |
| P3 | 使用高精度定时器替代 time.sleep | 高速曲谱节奏偏移 |
