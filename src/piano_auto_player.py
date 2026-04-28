"""
模块名称：piano_auto_player
功能描述：
    读取 YAML 简谱配置，并将曲谱事件转换为《异环》游戏钢琴界面的键盘输入。
    本模块只负责真实本机键盘输入，不包含窗口注入、后台控制或任何绕过逻辑。

风险警告：
    使用 Windows SendInput API（R3 级操作注入）发送键盘事件。
    可能被游戏反作弊系统检测，存在账号封禁风险。
    请以管理员身份运行。开发者不对账号封禁负责。

主要组件：
    - SongConfig: 曲谱基础配置数据结构
    - PlaybackConfig: 播放控制配置数据结构
    - ScoreEvent: 单个曲谱事件数据结构
    - PianoConfigLoader: YAML 配置加载与校验器
    - NotationParser: 简谱解析器
    - VirtualKeyMapper: Windows 虚拟键码映射器
    - WinApiInputBackend: Windows SendInput 输入后端
    - PianoAutoPlayer: 自动弹奏调度器

依赖说明：
    - PyYAML: 用于读取 YAML 曲谱配置
    - ctypes: 用于调用 Windows SendInput 接口

作者：JucieOvo
创建日期：2026-04-27
修改记录：
    - 2026-04-27 JucieOvo: 创建 YAML 驱动的钢琴自动弹奏脚本
"""

from __future__ import annotations

import argparse
import ctypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


@dataclass(frozen=True)
class SongConfig:
    """
    曲谱基础配置。

    职责：
        保存曲名、速度和节拍单位，为后续节拍换算提供稳定数据来源。

    属性：
        name (str): 曲谱名称
        bpm (float): 每分钟节拍数
        beat_unit (int): 节拍单位，目前用于配置记录和后续扩展
    """

    name: str
    bpm: float
    beat_unit: int


@dataclass(frozen=True)
class PlaybackConfig:
    """
    播放控制配置。

    职责：
        保存启动延迟和按键持续时间，避免在播放逻辑中写死执行参数。

    属性：
        start_delay_seconds (float): 开始弹奏前等待的秒数
        key_press_seconds (float): 每次按键保持按下的秒数
    """

    start_delay_seconds: float
    key_press_seconds: float


@dataclass(frozen=True)
class RawScoreEvent:
    """
    原始曲谱事件。

    职责：
        保存 YAML 中未经解析的曲谱片段，便于将配置读取与简谱解析解耦。

    属性：
        notes (str): 原始简谱字符串
        beat (float): 该事件中每个音符或和弦持续的拍数
    """

    notes: str
    beat: float


@dataclass(frozen=True)
class ScoreEvent:
    """
    已解析曲谱事件。

    职责：
        保存单个待执行事件对应的键盘按键列表和持续拍数。

    属性：
        keys (tuple[str, ...]): 需要同时按下的键盘按键表达式，空元组表示休止符
        beat (float): 当前事件持续的拍数
    """

    keys: tuple[str, ...]
    beat: float


@dataclass(frozen=True)
class PianoConfig:
    """
    完整钢琴配置。

    职责：
        聚合曲谱基础信息、播放参数、键盘映射和原始曲谱事件。

    属性：
        song (SongConfig): 曲谱基础配置
        playback (PlaybackConfig): 播放控制配置
        keyboard_mapping (dict[str, dict[str, str]]): 音区到音符再到键盘按键的映射
        raw_score (tuple[RawScoreEvent, ...]): 原始曲谱事件列表
    """

    song: SongConfig
    playback: PlaybackConfig
    keyboard_mapping: dict[str, dict[str, str]]
    raw_score: tuple[RawScoreEvent, ...]


class PianoConfigLoader:
    """
    YAML 配置加载器。

    职责：
        从磁盘读取 YAML 文件，将其校验并转换为强类型配置对象。
        校验失败时直接抛出异常，避免使用默认值掩盖配置错误。
    """

    def load(self, config_path: Path) -> PianoConfig:
        """
        加载并校验 YAML 配置。

        :param config_path: YAML 配置文件路径
        :return: 完整钢琴配置对象
        :raises FileNotFoundError: 当配置文件不存在时触发
        :raises ValueError: 当配置结构或字段值无效时触发
        """
        # 1. 文件存在性校验：配置文件是曲谱唯一数据来源，不存在时直接失败
        if not config_path.is_file():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        # 2. YAML 读取：使用 safe_load 避免执行任意 YAML 对象构造逻辑
        with config_path.open("r", encoding="utf-8") as file:
            raw_config = yaml.safe_load(file)

        # 3. 顶层结构校验：后续解析依赖字典结构
        if not isinstance(raw_config, dict):
            raise ValueError("YAML 顶层结构必须是对象")

        song = self._parse_song(raw_config.get("song"))
        playback = self._parse_playback(raw_config.get("playback"))
        keyboard_mapping = self._parse_keyboard(raw_config.get("keyboard"))
        raw_score = self._parse_score(raw_config.get("score"))

        return PianoConfig(
            song=song,
            playback=playback,
            keyboard_mapping=keyboard_mapping,
            raw_score=raw_score,
        )

    def _parse_song(self, raw_song: Any) -> SongConfig:
        """
        解析曲谱基础配置。

        :param raw_song: YAML 中的 song 区域
        :return: 曲谱基础配置对象
        :raises ValueError: 当 song 区域缺失或字段无效时触发
        """
        if not isinstance(raw_song, dict):
            raise ValueError("song 必须是对象")

        name = raw_song.get("name")
        bpm = raw_song.get("bpm")
        beat_unit = raw_song.get("beat_unit")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("song.name 必须是非空字符串")
        if not isinstance(bpm, (int, float)) or bpm <= 0:
            raise ValueError("song.bpm 必须是大于 0 的数字")
        if not isinstance(beat_unit, int) or beat_unit <= 0:
            raise ValueError("song.beat_unit 必须是大于 0 的整数")

        return SongConfig(name=name.strip(), bpm=float(bpm), beat_unit=beat_unit)

    def _parse_playback(self, raw_playback: Any) -> PlaybackConfig:
        """
        解析播放控制配置。

        :param raw_playback: YAML 中的 playback 区域
        :return: 播放控制配置对象
        :raises ValueError: 当 playback 区域缺失或字段无效时触发
        """
        if not isinstance(raw_playback, dict):
            raise ValueError("playback 必须是对象")

        start_delay_seconds = raw_playback.get("start_delay_seconds")
        key_press_seconds = raw_playback.get("key_press_seconds")

        if not isinstance(start_delay_seconds, (int, float)) or start_delay_seconds < 0:
            raise ValueError("playback.start_delay_seconds 必须是大于等于 0 的数字")
        if not isinstance(key_press_seconds, (int, float)) or key_press_seconds < 0:
            raise ValueError("playback.key_press_seconds 必须是大于 0 的数字")

        return PlaybackConfig(
            start_delay_seconds=float(start_delay_seconds),
            key_press_seconds=float(key_press_seconds),
        )

    def _parse_keyboard(self, raw_keyboard: Any) -> dict[str, dict[str, str]]:
        """
        解析键盘映射配置。

        :param raw_keyboard: YAML 中的 keyboard 区域
        :return: 音区到音符再到键盘按键的映射
        :raises ValueError: 当 keyboard 区域缺失或字段无效时触发
        """
        if not isinstance(raw_keyboard, dict):
            raise ValueError("keyboard 必须是对象")

        keyboard_mapping: dict[str, dict[str, str]] = {}

        # 逐音区校验：允许未来增加更多音区，但每个音区都必须是字符串到字符串的映射
        for zone_name, zone_mapping in raw_keyboard.items():
            if not isinstance(zone_name, str) or not zone_name.strip():
                raise ValueError("keyboard 的音区名称必须是非空字符串")
            if not isinstance(zone_mapping, dict):
                raise ValueError(f"keyboard.{zone_name} 必须是对象")

            normalized_zone_mapping: dict[str, str] = {}
            for note_name, key_name in zone_mapping.items():
                if not isinstance(note_name, str) or not note_name.strip():
                    raise ValueError(f"keyboard.{zone_name} 的音符名称必须是非空字符串")
                if not isinstance(key_name, str) or not key_name.strip():
                    raise ValueError(f"keyboard.{zone_name}.{note_name} 的键位必须是非空字符串")
                normalized_zone_mapping[note_name.strip()] = key_name.strip().lower()

            keyboard_mapping[zone_name.strip()] = normalized_zone_mapping

        return keyboard_mapping

    def _parse_score(self, raw_score: Any) -> tuple[RawScoreEvent, ...]:
        """
        解析原始曲谱事件列表。

        :param raw_score: YAML 中的 score 区域
        :return: 原始曲谱事件元组
        :raises ValueError: 当 score 区域缺失或字段无效时触发
        """
        if not isinstance(raw_score, list) or not raw_score:
            raise ValueError("score 必须是非空列表")

        events: list[RawScoreEvent] = []
        for index, raw_event in enumerate(raw_score, start=1):
            if not isinstance(raw_event, dict):
                raise ValueError(f"score 第 {index} 项必须是对象")

            notes = raw_event.get("notes")
            beat = raw_event.get("beat")

            if not isinstance(notes, str) or not notes.strip():
                raise ValueError(f"score 第 {index} 项 notes 必须是非空字符串")
            if not isinstance(beat, (int, float)) or beat <= 0:
                raise ValueError(f"score 第 {index} 项 beat 必须是大于 0 的数字")

            events.append(RawScoreEvent(notes=notes.strip(), beat=float(beat)))

        return tuple(events)


class NotationParser:
    """
    简谱解析器。

    职责：
        将 YAML 中的人类可读简谱字符串转换为可执行的键盘事件。
        解析器只关心记谱语法，不直接发送键盘输入。
    """

    CHORD_PATTERN = re.compile(r"\[[^\]]+\]|\S+")

    def __init__(self, keyboard_mapping: dict[str, dict[str, str]]) -> None:
        """
        初始化简谱解析器。

        :param keyboard_mapping: 音区到音符再到键盘按键的映射
        """
        self.keyboard_mapping = keyboard_mapping
        self.zone_prefix_mapping = {
            "+": "high",
            "": "middle",
            "-": "low",
        }

    def parse_score(self, raw_score: tuple[RawScoreEvent, ...]) -> tuple[ScoreEvent, ...]:
        """
        解析完整曲谱事件列表。

        :param raw_score: 原始曲谱事件元组
        :return: 已解析曲谱事件元组
        :raises ValueError: 当简谱 token 无法解析时触发
        """
        parsed_events: list[ScoreEvent] = []

        # 将一条 notes 中的多个 token 拆成多个连续事件，使每个 token 独立占用 beat 时长
        for raw_event in raw_score:
            for token in self._split_tokens(raw_event.notes):
                parsed_events.append(ScoreEvent(keys=self._parse_token(token), beat=raw_event.beat))

        return tuple(parsed_events)

    def _split_tokens(self, notes: str) -> tuple[str, ...]:
        """
        拆分简谱字符串。

        :param notes: 原始简谱字符串
        :return: 简谱 token 元组
        :raises ValueError: 当拆分结果为空时触发
        """
        tokens = tuple(match.group(0) for match in self.CHORD_PATTERN.finditer(notes))
        if not tokens:
            raise ValueError("notes 未解析出任何音符")
        return tokens

    def _parse_token(self, token: str) -> tuple[str, ...]:
        """
        解析单个简谱 token。

        :param token: 单音、和弦或休止符 token
        :return: 需要同时按下的键盘按键元组，休止符返回空元组
        :raises ValueError: 当 token 无法映射到键盘按键时触发
        """
        normalized_token = token.strip()

        # 休止符场景：不发送任何按键，只保持节拍等待
        if normalized_token.lower() in {"0", "rest"}:
            return tuple()

        # 和弦场景：中括号内部的多个音符需要同时按下
        if normalized_token.startswith("[") and normalized_token.endswith("]"):
            chord_body = normalized_token[1:-1].strip()
            if not chord_body:
                raise ValueError("和弦不能为空")
            return tuple(self._parse_single_note(note_token) for note_token in chord_body.split())

        return (self._parse_single_note(normalized_token),)

    def _parse_single_note(self, note_token: str) -> str:
        """
        解析单个音符 token。

        :param note_token: 单个简谱音符，例如 1、+1、-1、#1、b3、+#1、-b7
        :return: 对应的键盘按键表达式，升半音使用 shift+键位，降半音使用 ctrl+键位
        :raises ValueError: 当音区或音符不存在时触发
        """
        prefix = ""
        note_name = note_token
        modifier_name = ""

        # 音区判断：使用前缀表达高音和低音，不带前缀则为中音
        if note_token.startswith(("+", "-")):
            prefix = note_token[0]
            note_name = note_token[1:]

        # 临时变音记号：# 表示按住 Shift 升半音，b 表示按住 Ctrl 降半音
        if note_name.startswith("#"):
            modifier_name = "shift"
            note_name = note_name[1:]
        elif note_name.startswith(("b", "B", "♭")):
            modifier_name = "ctrl"
            note_name = note_name[1:]

        zone_name = self.zone_prefix_mapping.get(prefix)
        if zone_name is None:
            raise ValueError(f"不支持的音区前缀: {prefix}")

        zone_mapping = self.keyboard_mapping.get(zone_name)
        if zone_mapping is None:
            raise ValueError(f"keyboard 缺少音区配置: {zone_name}")

        key_name = zone_mapping.get(note_name)
        if key_name is None:
            raise ValueError(f"音符无法映射到键盘: {note_token}")

        if modifier_name:
            return f"{modifier_name}+{key_name}"
        return key_name


class InputBackend(Protocol):
    """
    输入后端协议。

    职责：
        约束所有输入后端必须实现的按键接口，便于后续扩展不同输入方式。
    """

    def press_keys(self, keys: tuple[str, ...], hold_seconds: float) -> None:
        """
        同时按下并释放一组按键。

        :param keys: 需要同时按下的键盘按键表达式元组
        :param hold_seconds: 按键保持时间，单位为秒
        """


class MouseInput(ctypes.Structure):
    """
    Windows 鼠标输入结构体。

    职责：
        对应 WinAPI INPUT 结构体中的 MOUSEINPUT 部分，用于保证联合体大小与 Windows 原生一致。
    """

    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KeyboardInput(ctypes.Structure):
    """
    Windows 键盘输入结构体。

    职责：
        对应 WinAPI INPUT 结构体中的 KEYBDINPUT 部分，用于描述一次键盘输入事件。
    """

    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HardwareInput(ctypes.Structure):
    """
    Windows 硬件输入结构体。

    职责：
        对应 WinAPI INPUT 结构体中的 HARDWAREINPUT 部分，用于保证联合体大小与 Windows 原生一致。
    """

    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class InputUnion(ctypes.Union):
    """
    Windows 输入联合体。

    职责：
        对应 WinAPI INPUT 结构体中的输入联合区域，包含鼠标、键盘、硬件三种输入类型。
    """

    _fields_ = [
        ("mi", MouseInput),
        ("ki", KeyboardInput),
        ("hi", HardwareInput),
    ]


class Input(ctypes.Structure):
    """
    Windows 输入结构体。

    职责：
        对应 WinAPI INPUT 结构体，作为 SendInput 的事件数组元素。
    """

    _fields_ = [("type", ctypes.c_ulong), ("union", InputUnion)]


class VirtualKeyMapper:
    """
    Windows 虚拟键码映射器。

    职责：
        将 YAML 中配置的键名转换为 WinAPI SendInput 所需的虚拟键码。
    """

    LETTER_BASE_CODE = 0x41
    DIGIT_BASE_CODE = 0x30
    SPECIAL_KEY_CODES = {
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
    }

    def to_virtual_key(self, key_name: str) -> int:
        """
        将键名转换为 Windows 虚拟键码。

        :param key_name: YAML 中配置的键名，例如 a、q、1、shift、ctrl
        :return: Windows 虚拟键码
        :raises ValueError: 当键名暂不支持时触发
        """
        normalized_key_name = key_name.strip().lower()

        # 修饰键场景：升降半音依赖 Shift/Ctrl 与基础音符键位组合发送
        special_key_code = self.SPECIAL_KEY_CODES.get(normalized_key_name)
        if special_key_code is not None:
            return special_key_code

        # 字母键场景：游戏钢琴当前只使用字母键，按 ASCII 偏移计算虚拟键码
        if len(normalized_key_name) == 1 and "a" <= normalized_key_name <= "z":
            return self.LETTER_BASE_CODE + ord(normalized_key_name) - ord("a")

        # 数字键场景：为后续扩展键位映射预留，不影响当前曲谱格式
        if len(normalized_key_name) == 1 and "0" <= normalized_key_name <= "9":
            return self.DIGIT_BASE_CODE + ord(normalized_key_name) - ord("0")

        raise ValueError(f"暂不支持的键名: {key_name}")


class WinApiInputBackend:
    """
    Windows SendInput 输入后端。

    职责：
        使用 Windows 原生 SendInput 接口发送真实键盘事件，使播放调度器不依赖第三方按键库。
        自动检测 Ctrl/Shift 修饰键冲突，将冲突和弦拆分为两个子事件间隔 50ms 依次发送。
    """

    # 受 Ctrl 修饰影响的基础键名（按下 Ctrl 时这些键变为升半音）
    CTRL_AFFECTED_KEYS = {"q", "r", "t"}
    # 受 Shift 修饰影响的基础键名（按下 Shift 时这些键变为降半音）
    SHIFT_AFFECTED_KEYS = {"e", "u"}

    def __init__(self, virtual_key_mapper: VirtualKeyMapper) -> None:
        """
        初始化 Windows 输入后端。

        :param virtual_key_mapper: 键名到 Windows 虚拟键码的映射器
        """
        self.virtual_key_mapper = virtual_key_mapper
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
        self.user32.SendInput.restype = ctypes.c_uint

    def press_keys(self, keys: tuple[str, ...], hold_seconds: float) -> None:
        """
        同时按下并释放一组按键。

        若同时存在 ctrl 和 shift 修饰键，自动拆分为两次发送，
        间隔 1ms 以避免全局修饰键冲突。

        :param keys: 需要同时按下的键盘按键表达式元组
        :param hold_seconds: 按键保持时间，单位为秒。若为 0 则使用保底 1ms。
        :raises RuntimeError: 当 SendInput 执行失败时触发
        """
        if not keys:
            return

        # 播放层保底: 按键保持时间最低 1ms，防止游戏吞音
        if hold_seconds <= 0:
            hold_seconds = 0.001

        modifier_key_names: list[str] = []
        base_key_names: list[str] = []
        for key_expression in keys:
            parsed_modifiers, parsed_base = self._parse_key_expression(key_expression)
            for m in parsed_modifiers:
                if m not in modifier_key_names:
                    modifier_key_names.append(m)
            base_key_names.append(parsed_base)

        # 检测 Ctrl+Shift 冲突
        has_ctrl = "ctrl" in modifier_key_names
        has_shift = "shift" in modifier_key_names

        if has_ctrl and has_shift:
            self._press_keys_split_modifiers(
                keys=keys,
                hold_seconds=hold_seconds,
                modifier_key_names=modifier_key_names,
                base_key_names=base_key_names,
            )
        else:
            self._press_keys_unified(
                modifier_key_names=modifier_key_names,
                base_key_names=base_key_names,
                hold_seconds=hold_seconds,
            )

    def _press_keys_split_modifiers(
        self,
        keys: tuple[str, ...],
        hold_seconds: float,
        modifier_key_names: list[str],
        base_key_names: list[str],
    ) -> None:
        """
        拆分冲突修饰键: Ctrl 组和 Shift 组交替切换，30ms 内完成切换。

        游戏修饰键机制: 后按入的修饰键会顶掉先前的，同一时刻只能有一个修饰键生效。
        因此必须交替发送 Ctrl 和 Shift，不能同时保持。
        30ms 内完成一组交替，低于人耳融合阈值，听感上无法区分。

        自然键分配策略:
        - Ctrl 组: ctrl 基础键 + 不受 Ctrl 影响的自然键
        - Shift 组: shift 基础键 + 不受 Shift 影响的自然键
        - 不受两种修饰影响的自然键始终按住以保证延音连贯。

        :param keys: 原始按键表达式元组
        :param hold_seconds: 总按键保持时间，单位秒
        :param modifier_key_names: 当前事件涉及的所有修饰键名
        :param base_key_names: 当前事件涉及的所有基础键名
        """
        modifier_stagger_seconds = 0.001  # 1ms 交替间隔

        ctrl_base_keys: list[str] = []
        shift_base_keys: list[str] = []
        both_safe_keys: list[str] = []  # 不受两种修饰影响的自然键

        for key_expression in keys:
            parsed_modifiers, parsed_base = self._parse_key_expression(key_expression)
            if "ctrl" in parsed_modifiers:
                ctrl_base_keys.append(parsed_base)
            elif "shift" in parsed_modifiers:
                shift_base_keys.append(parsed_base)
            else:
                is_ctrl_affected = parsed_base in self.CTRL_AFFECTED_KEYS
                is_shift_affected = parsed_base in self.SHIFT_AFFECTED_KEYS
                if not is_ctrl_affected and not is_shift_affected:
                    both_safe_keys.append(parsed_base)
                elif not is_ctrl_affected:
                    ctrl_base_keys.append(parsed_base)
                elif not is_shift_affected:
                    shift_base_keys.append(parsed_base)

        ctrl_vk = self.virtual_key_mapper.to_virtual_key("ctrl")
        shift_vk = self.virtual_key_mapper.to_virtual_key("shift")
        ctrl_base_vks_unique = [self.virtual_key_mapper.to_virtual_key(k) for k in dict.fromkeys(ctrl_base_keys)]
        shift_base_vks_unique = [self.virtual_key_mapper.to_virtual_key(k) for k in dict.fromkeys(shift_base_keys)]
        safe_vks = [self.virtual_key_mapper.to_virtual_key(k) for k in dict.fromkeys(both_safe_keys)]

        remaining_hold = max(0.0, hold_seconds - modifier_stagger_seconds)

        try:
            # 全程保持不受修饰影响的自然键
            for vk in safe_vks:
                self._send_key_event(virtual_key=vk, is_key_up=False)

            # 第一拍: Ctrl 有效，发送升半音组
            self._send_key_event(virtual_key=ctrl_vk, is_key_up=False)
            for vk in ctrl_base_vks_unique:
                self._send_key_event(virtual_key=vk, is_key_up=False)
            time.sleep(modifier_stagger_seconds)

            # Ctrl 组基础键释放，Ctrl 修饰键释放（升半音组结束发声）
            for vk in reversed(ctrl_base_vks_unique):
                self._send_key_event(virtual_key=vk, is_key_up=True)
            self._send_key_event(virtual_key=ctrl_vk, is_key_up=True)

            # 第二拍: Shift 有效，发送降半音组
            self._send_key_event(virtual_key=shift_vk, is_key_up=False)
            for vk in shift_base_vks_unique:
                self._send_key_event(virtual_key=vk, is_key_up=False)
            time.sleep(remaining_hold)
        except Exception as exc:
            raise RuntimeError(f"修饰键交替发送失败: {exc}") from exc
        finally:
            # 释放顺序: Shift 组基础键 → Shift 修饰键 → 安全自然键
            for vk in reversed(shift_base_vks_unique):
                self._send_key_event(virtual_key=vk, is_key_up=True)
            self._send_key_event(virtual_key=shift_vk, is_key_up=True)
            for vk in reversed(safe_vks):
                self._send_key_event(virtual_key=vk, is_key_up=True)

    def _press_keys_unified(
        self,
        modifier_key_names: list[str],
        base_key_names: list[str],
        hold_seconds: float,
    ) -> None:
        """
        无冲突修饰键的统一按键发送。

        :param modifier_key_names: 修饰键名列表
        :param base_key_names: 基础键名列表
        :param hold_seconds: 按键保持时间，单位秒
        """
        modifier_virtual_keys = tuple(
            self.virtual_key_mapper.to_virtual_key(key_name) for key_name in modifier_key_names
        )
        base_virtual_keys = tuple(self.virtual_key_mapper.to_virtual_key(key_name) for key_name in base_key_names)
        try:
            for virtual_key in modifier_virtual_keys:
                self._send_key_event(virtual_key=virtual_key, is_key_up=False)
            for virtual_key in base_virtual_keys:
                self._send_key_event(virtual_key=virtual_key, is_key_up=False)
            time.sleep(hold_seconds)
        except Exception as exc:
            raise RuntimeError(f"键盘输入失败: {exc}") from exc
        finally:
            for virtual_key in reversed(base_virtual_keys):
                self._send_key_event(virtual_key=virtual_key, is_key_up=True)
            for virtual_key in reversed(modifier_virtual_keys):
                self._send_key_event(virtual_key=virtual_key, is_key_up=True)

    def _parse_key_expression(self, key_expression: str) -> tuple[tuple[str, ...], str]:
        """
        解析按键表达式。

        :param key_expression: 单个按键表达式，例如 q、shift+q、ctrl+a
        :return: 修饰键名称元组与基础键名称
        :raises ValueError: 当表达式为空或缺少基础键时触发
        """
        key_parts = tuple(part.strip().lower() for part in key_expression.split("+") if part.strip())
        if not key_parts:
            raise ValueError("按键表达式不能为空")

        base_key_name = key_parts[-1]
        modifier_key_names = key_parts[:-1]
        if not base_key_name:
            raise ValueError(f"按键表达式缺少基础键: {key_expression}")

        return modifier_key_names, base_key_name

    def _send_key_event(self, virtual_key: int, is_key_up: bool) -> None:
        """
        发送单个 Windows 键盘事件。

        :param virtual_key: Windows 虚拟键码
        :param is_key_up: True 表示释放事件，False 表示按下事件
        :raises RuntimeError: 当 SendInput 返回值异常时触发
        """
        input_event = Input(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=virtual_key,
                    wScan=0,
                    dwFlags=KEYEVENTF_KEYUP if is_key_up else 0,
                    time=0,
                    dwExtraInfo=None,
                )
            ),
        )

        sent_count = self.user32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(input_event))
        if sent_count != 1:
            error_code = ctypes.get_last_error()
            raise RuntimeError(f"SendInput 调用失败，错误码: {error_code}")


class PianoAutoPlayer:
    """
    钢琴自动弹奏调度器。

    职责：
        根据曲谱事件、BPM 和播放参数协调等待、按键和节奏控制。
    """

    def __init__(self, input_backend: InputBackend) -> None:
        """
        初始化自动弹奏调度器。

        :param input_backend: 输入后端
        """
        self.input_backend = input_backend

    def play(self, config: PianoConfig, events: tuple[ScoreEvent, ...]) -> None:
        """
        执行自动弹奏。

        :param config: 完整钢琴配置对象
        :param events: 已解析曲谱事件元组
        :raises ValueError: 当按键保持时间大于事件持续时间时触发
        """
        seconds_per_beat = 60.0 / config.song.bpm

        print(f"曲谱: {config.song.name}")
        print(f"BPM: {config.song.bpm}")
        print(f"事件数量: {len(events)}")
        print(f"请在 {config.playback.start_delay_seconds:.2f} 秒内切换到游戏窗口")
        time.sleep(config.playback.start_delay_seconds)

        for event_index, event in enumerate(events, start=1):
            event_seconds = seconds_per_beat * event.beat
            if config.playback.key_press_seconds > event_seconds:
                raise ValueError(
                    "playback.key_press_seconds 不能大于单个事件持续时间，"
                    f"第 {event_index} 个事件持续 {event_seconds:.4f} 秒"
                )

            started_at = time.perf_counter()
            self.input_backend.press_keys(event.keys, config.playback.key_press_seconds)

            elapsed_seconds = time.perf_counter() - started_at
            remaining_seconds = event_seconds - elapsed_seconds
            if remaining_seconds > 0:
                time.sleep(remaining_seconds)


def parse_arguments() -> argparse.Namespace:
    """
    解析命令行参数。

    :return: 命令行参数命名空间
    """
    argument_parser = argparse.ArgumentParser(description="根据 YAML 简谱自动弹奏《异环》钢琴界面")
    argument_parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="YAML 曲谱配置文件路径，例如 config/example_song.yaml",
    )
    return argument_parser.parse_args()


def main() -> None:
    """
    程序入口函数。

    职责：
        串联配置加载、简谱解析和自动弹奏流程。
    """
    arguments = parse_arguments()
    config_loader = PianoConfigLoader()
    config = config_loader.load(arguments.config)

    notation_parser = NotationParser(config.keyboard_mapping)
    events = notation_parser.parse_score(config.raw_score)

    player = PianoAutoPlayer(input_backend=WinApiInputBackend(virtual_key_mapper=VirtualKeyMapper()))
    player.play(config=config, events=events)


if __name__ == "__main__":
    main()
