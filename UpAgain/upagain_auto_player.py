# -*- coding: utf-8 -*-
"""
模块名称：upagain_auto_player
功能描述：
    读取 YAML 简谱配置，并将曲谱事件转换为《UpAgain》游戏内乐器的键盘输入。
    本模块对原始多音轨和弦进行单音过滤（保留最高音），并适配特定的键位映射。
    特化：为了解决 MuMu 模拟器吞音/无响应问题，使用了注入底层硬件扫描码
    (Hardware Scan Code) 的 DirectInput 穿透解决方案。

主要组件：
    - UpAgainNotationParser: 针对单音限制与特定键位的简谱解析器
    - EmulatorDirectInputBackend: 针对模拟器底层按键拉取的硬件扫描码专版输入引擎
    - main: 自动弹奏主函数入口

依赖说明：
    - ../src/piano_auto_player: 复用基础的加载、解析、键盘映射与输入逻辑

作者：JucieOvo
创建日期：2026-06-30
"""

import sys
import ctypes
from pathlib import Path

# 将 src 目录加入环境变量以复用基础模块
sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))

from piano_auto_player import (
    PianoConfigLoader,
    NotationParser,
    VirtualKeyMapper,
    WinApiInputBackend,
    PianoAutoPlayer,
    parse_arguments,
    Input,
    InputUnion,
    KeyboardInput,
    INPUT_KEYBOARD,
    KEYEVENTF_KEYUP
)

# 强制将按键检测模式切换为依赖底层的 Scan Code，这是绕过常见客户端模拟器防护的基石
KEYEVENTF_SCANCODE = 0x0008
# MapVK 指令映射类型，虚拟键 -> 扫描码
MAPVK_VK_TO_VSC = 0


class EmulatorDirectInputBackend(WinApiInputBackend):
    """
    针对底层扫描式游戏/模拟器强化的输入后端。

    职责：
        重载源类的单发事件构造行为，彻底摒弃纯高级 Vk(VirtualKey)，
        将其转换为系统电信号意义上的硬件 Scancode(扫描码)。
        能够有效应对 MuMu 等安卓模拟器无视常规软键盘模拟的特征。
    """
    
    def _send_key_event(self, virtual_key: int, is_key_up: bool) -> None:
        """
        发送携带 SCANCODE flag 的特化 Windows 硬件级键盘事件。

        :param virtual_key: Windows 高级虚拟键码，将被转译
        :param is_key_up: True 表示释放弹起，False 表示按下
        :raises RuntimeError: 当到底层调用返回无生效记录时报警
        """
        # 第一拍步：动态换算！借助 User32 API 把 VK 转成该机器真实的底层扫描电信号编码
        scan_code = self.user32.MapVirtualKeyW(virtual_key, MAPVK_VK_TO_VSC)
        
        flags = KEYEVENTF_SCANCODE
        if is_key_up:
            flags |= KEYEVENTF_KEYUP
            
        input_event = Input(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=0,                # 完全置空软键盘标识
                    wScan=scan_code,      # 灌入底层硬件标志信息
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=None,
                )
            ),
        )

        sent_count = self.user32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(input_event))
        if sent_count != 1:
            error_code = ctypes.get_last_error()
            raise RuntimeError(f"底层 ScanCode 投递阻断，SendInput 调用失败，错误码: {error_code}")


class UpAgainNotationParser(NotationParser):
    """
    单音轨简谱解析器。
    
    职责：
        重载源简谱解析逻辑，在遇到和弦[多音]时，执行单音过滤策略，仅保留最高音。
        通过预设的音高权重表判定最高音，对不支持的音符严格抛出异常。
    """
    
    def __init__(self) -> None:
        """
        初始化解析器，并硬编码 UpAgain 专属键位映射。
        
        UpAgain 的合法音符：
        低中音: 1(a), 2(s), 3(d), 4(f), 5(g), 6(h), 7(j)
        高音/超高音: +1(q), +2(w), +3(e), +4(r), +5(t), +6(y), +7(u), ++1(i)
        """
        # 定义专属映射字典
        self.keyboard_mapping = {
            "mid": {
                "1": "a", "2": "s", "3": "d", "4": "f", "5": "g", "6": "h", "7": "j"
            },
            "high": {
                "+1": "q", "+2": "w", "+3": "e", "+4": "r", "+5": "t", "+6": "y", "+7": "u", "++1": "i"
            }
        }
        super().__init__(self.keyboard_mapping)
        
        # 音高权重字典 (dict[str, int])
        # 用途：记录所有受到完全支持音符权重的对比参照基准。
        # 依赖关系：
        #     主：硬编码在解析器内。
        #     从：被 _get_pitch_weight 作为检查及和弦中提取最高音的方法源读取。
        # 影响：直接影响被抛弃音符和被选中音符的具体逻辑，数值错误会导致抛弃高音保留低音。
        # 其他：数字越大代表映射键位的物理泛音越高。
        self._pitch_weights = {
            "1": 11, "2": 12, "3": 13, "4": 14, "5": 15, "6": 16, "7": 17,
            "+1": 21, "+2": 22, "+3": 23, "+4": 24, "+5": 25, "+6": 26, "+7": 27,
            "++1": 31
        }

    def _parse_token(self, token: str) -> tuple[str, ...]:
        """
        核心函数：解析单个简谱 token，执行保留最高音的限制策略。
        
        :param token: 单音、和弦或休止符 token
        :return: 最多包含一个按键元素的元组，休止符对应的为空元组
        :raises ValueError: 当音符或和弦内包含非法音符时触发
        """
        normalized_token = token.strip()

        # 1. 场景一：休止符处理逻辑
        if normalized_token.lower() in {"0", "rest"}:
            return tuple()

        # 2. 场景二：和弦处理逻辑，在此执行过滤策略以保留最高音
        if normalized_token.startswith("[") and normalized_token.endswith("]"):
            chord_body = normalized_token[1:-1].strip()
            if not chord_body:
                raise ValueError("和弦不能为空")
            notes = chord_body.split()
            # 通过权重表比对，提取拥有最大权重的音符作为主流旋律
            highest_note = max(notes, key=lambda n: self._get_pitch_weight(n))
            return (self._parse_single_note(highest_note),)

        # 3. 场景三：常规单音处理逻辑
        return (self._parse_single_note(normalized_token),)

    def _get_pitch_weight(self, note_token: str) -> int:
        """
        获取音符的绝对音高权重，用于最高音判定。
        
        由于严格禁止降级和伪造数据，且游戏内不支持半音和越界音符，遇到不可识别的音符时直接抛错。
        
        :param note_token: 简谱音符
        :return: 音高权重整数
        :raises ValueError: 当遇到不支持的音高或带有修饰符的音符时触发
        """
        if note_token not in self._pitch_weights:
            raise ValueError(f"无法确定该音符的权重，在 UpAgain 音域限制外或包含不支持修饰符: {note_token}")
        return self._pitch_weights[note_token]
        
    def _parse_single_note(self, note_token: str) -> str:
        """
        解析单个音符并返回对应游戏的键盘按键字母。
        
        严格根据已知的 UpAgain 的 a-j 与 q-i 映射表执行判断，不支持降级。
        
        :param note_token: 解析后的纯音符记号
        :return: 对应的键盘按键标识
        :raises ValueError: 找不到该音符在 UpAgain 中的配置时触发
        """
        for zone, notes in self.keyboard_mapping.items():
            if note_token in notes:
                return notes[note_token]
                    
        raise ValueError(f"UpAgain 的游戏内乐器不支持此音符，且禁止降级处理: {note_token}")


def main() -> None:
    """
    主核心函数：装配并启动基于底层硬件扫描码的，可穿透模拟器的自动弹奏模块程序。
    """
    args = parse_arguments()
    config_path = Path(args.config)
    
    # 1. 步骤一：复用 loader 解析指定路径的 yaml 曲谱内容
    loader = PianoConfigLoader()
    piano_config = loader.load(config_path)
    
    # 2. 步骤二：利用专属 UpAgain 解析器二次映射及过滤和弦
    parser = UpAgainNotationParser()
    events = parser.parse_score(piano_config.raw_score)
    
    # 3. 步骤三：注入可穿透 MuMu 等安卓模拟器的 DirectInput 底层后门，要求必须保持目标游戏最前台使用
    virtual_key_mapper = VirtualKeyMapper()
    input_backend = EmulatorDirectInputBackend(virtual_key_mapper)
    player = PianoAutoPlayer(input_backend)
    
    print("=" * 50)
    print(f"目标环境：MuMu 模拟器 / UpAgain 原生硬件模拟输入")
    print(f"弹奏曲目：{piano_config.song.name}")
    print(f"发音限制：单音轨 (和弦过滤：保留最高音)")
    print(f"请在 {piano_config.playback.start_delay_seconds} 秒内火速将【模拟器】切回聚焦至第一前台焦点...")
    print("=" * 50)
    
    # 4. 步骤四：核心调度器阻塞执行
    player.play(piano_config, events)


if __name__ == "__main__":
    main()
