# UpAgain 基于 win32gui 的输入后端切换方案

## 1. 目标
替换现有针对 UpAgain 使用的 `win32api` 虚拟输入机制。因为某些游戏（尤其是带有强力鼠标/键盘拦截钩子的游戏引擎）可能通过底层直接过滤了 `SendInput`，导致按键命令被吞。通过切换成 `win32gui` 和 `win32con` 结合 `win32api` 的 `SendMessage` 或 `PostMessage` 进行窗口定向句柄（Handle）发包，可以更直接地“告诉”游戏窗口发生了按键操作，也同时支持后台弹奏！

## 2. 意图
1. **摆脱前台键盘焦点限制**：不再使用 `SendInput`（其完全模拟物理键盘，必须游戏保持最前台）。
2. **克服按键吞音**：使用 Win32 消息机制可以精准注入 `WM_KEYDOWN` 和 `WM_KEYUP` 到主窗口句柄中，能够穿透部分常规检测，也是游戏自动化常见的降级方式。
3. **保持高度模块化**：我们要遵循原有 `InputBackend` 协议，完全不修改其它读取与过滤层面的代码，仅在 `UpAgain` 的播放器中替换注入的后端为我们新写的 `Win32MsgInputBackend`。

## 3. 技术要点
- **依赖库**：利用 `pywin32` 库（即 `import win32api, win32gui, win32con`）进行窗口识别与消息发送。
- **句柄查找 (FindWindow)**：先通过窗口名或类名查找到 `UpAgain` 的游戏窗口句柄 `$hwnd`。
- **消息发送 (PostMessage)**：
  对该 `$hwnd` 分别投递消息：
  1. `win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk_code, 0)`
  2. 加入少量的 `time.sleep` 防止过快。
  3. `win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk_code, 0)`
- **自动升降级策略拦截**：UpAgain 不使用组合键（没半音黑键），故不需要考虑 Shift 和 Ctrl 同步发射的冲突问题，我们可以将这一层逻辑精简至极简单发。

## 4. 模块划分与数据流
- 在 `F:\NTEZMusic\UpAgain\upagain_auto_player.py` 中引入 `win32gui`, `win32con`, `win32api`。
- 新增 `Win32MsgInputBackend` 实现 `InputBackend` 的 `press_keys` 协议。
- 在 `main()` 中组装，寻找窗口名，通过新后端执行弹奏。

## 5. 风险点
1. 某些游戏框架（如 Unity 的虚幻输入系统或者部分 UWP 游戏框架）不吃系统标准的消息队列（也就是不鸟 `PostMessage`），它们从 DirectInput 读取硬件流。此时必须测试明确是否生效。
2. 需要安装第三方依赖 `pip install pypiwin32` 并使用指定的 Python 3.12 安装。

## 6. 执行顺序
1. 编写该 Markdown 以作审查。您回复“开始”即表明知悉。
2. 我将在 `F:\NTEZMusic\UpAgain\upagain_auto_player.py` 定义新的后端类。
3. 如果本机环境缺少库，您可以通过 `pip install pywin32` 进行补全。