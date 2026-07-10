# 模块名称：MIDI主旋律提取与简谱序列化桥接模块
# 功能描述：
#     该模块用于物理读取由音视频联合校验产生的 MIDI 终谱（包含左右手分轨数）。
#     提取指定右手音轨（Track 1 / "Right Hand"），对可能含有的和弦与重叠音符执行单音化裁剪。
#     依据 BPM 等时值信息将秒级别绝对时间轴转化为分数式节拍（Beat），并逆向映射为简谱 Token。
#     最终输出未经过调性缩限的原始 YAML 简谱文件，以供给下游转换器清洗。
#
# 主要组件：
#     - MidiMelodyExtractor: 从指定 MIDI 分离主旋律并提供时间转换的业务类
#     - PitchToNotationTokenConverter: 逆向解析音高的转化封装
#
# 依赖说明：
#     - pretty_midi: 用于解析复杂的 MIDI 事件与时间换算
#     - yaml / codecs: 处理标准的简谱 YAML 写出
#
# 作者：JucieOvo
# 创建日期：2026-07-01
# 修改记录：
#     - 2026-07-01 JucieOvo: 初始编写，打通 MIDI 到 UpAgain YAML 数据桥接

import os
import sys
import yaml
import codecs
import logging
import pretty_midi

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("midi_to_upagain_yaml")

class PitchToNotationTokenConverter:
    """
    负责将 MIDI 绝对音高 (Pitch) 翻译为简谱表示法的 Token (支持升半音 `#` 属性)。
    该映射严格适配 `UpAgain/upagain_yaml_converter.py` 中的解析规则。
    """
    # 对应的 12 半音偏移表 (0-11 对应 C 调下的简谱音阶)
    _SEMITONE_MAP = {
        0: "1",    # C
        1: "#1",   # C#
        2: "2",    # D
        3: "#2",   # D#
        4: "3",    # E
        5: "4",    # F
        6: "#4",   # F#
        7: "5",    # G
        8: "#5",   # G#
        9: "6",    # A
        10: "#6",  # A#
        11: "7"    # B
    }

    @classmethod
    def convert_pitch(cls, pitch: int) -> str:
        """
        核心逆向翻译函数。以 C4 (MIDI: 60) 为 0 偏移基准点。

        :param pitch: 绝对 MIDI 音高 (如 60, 72)
        :return: 含有八度符号与升音符的 Token 字符串 (如 "+1", "+#2")
        """
        # 1. 计算相对 C4 偏移量与所对应的八度段
        relative = pitch - 60
        octave = relative // 12
        semitone_index = relative % 12

        base_token = cls._SEMITONE_MAP[semitone_index]

        # 2. 定位八度上下缀，+代表高八度，-代表低八度
        if octave > 0:
            prefix = "+" * octave
        elif octave < 0:
            prefix = "-" * abs(octave)
        else:
            prefix = ""

        # 3. 按照 YAML 转换器正则要求排列顺序：八度标志 + 变音符 + 基础唱名 (例如: "+#1")
        if base_token.startswith("#"):
            return f"{prefix}#{base_token[1]}"
        return f"{prefix}{base_token}"


class MidiMelodyExtractor:
    """
    提供单轨主旋律提纯、单音化除叠以及时间量化节拍提取功能。
    """
    def __init__(self, midi_path: str):
        if not os.path.exists(midi_path):
            raise FileNotFoundError(f"MIDI文件不存在: {midi_path}")
        self.midi_path = midi_path
        self.midi_data = pretty_midi.PrettyMIDI(self.midi_path)

    def extract_melody_events(self, target_track_name: str = "Right Hand") -> list:
        """
        抓取特定音轨对象并在时间轴排序。由于原曲谱可能会有左右手重叠混响，
        本函数在此执行基于力度的真·主旋律提取 (Velocity-Voiced Monophonic Reduction)：
        1. 钢琴演奏中，主旋律音符（无论音高低）通常会被演奏者施加更大的敲击力度（Velocity）。
        2. 我们将时间差在 80ms 内的音符划分为同一个“和弦/发声组”。
        3. 在每个发声组内，保留力度 (Velocity) 最大的音符作为主旋律，抛弃伴奏辅音。
        4. 随后，对保留的单音序列执行时域截断，确保任一时刻绝对没有重叠发声。

        :param target_track_name: 提取的目标音轨名称标志（默认分配给右手）
        :return: 提取并截断后的单音主旋律音符列表
        """
        melody_notes = []
        # 寻找匹配的乐器轨
        for inst in self.midi_data.instruments:
            if inst.name == target_track_name:
                melody_notes = inst.notes
                break

        if not melody_notes:
            logger.warning(f"MIDI中未找到名为 {target_track_name} 的轨道。默认使用第0轨代替。")
            if self.midi_data.instruments:
                melody_notes = self.midi_data.instruments[0].notes

        if not melody_notes:
            return []

        # 根据绝对时间戳进行正序排列
        melody_notes.sort(key=lambda n: n.start)

        # 核心算法1：将 onset 时间容差由 0.08 缩短为 0.03，防止快速连弹被误当作和弦合并丢弃
        onset_groups = []
        for note in melody_notes:
            placed = False
            for group in onset_groups:
                if abs(note.start - group[0].start) <= 0.03:
                    group.append(note)
                    placed = True
                    break
            if not placed:
                onset_groups.append([note])

        # 核心算法2：通过动态规划 (Viterbi 算法) 寻找全局连续性最优的主旋律路径，防止跳切
        T = len(onset_groups)
        if T == 0:
            return []

        import math

        dp = []
        backptr = []

        # 参数配置：w_vel力度权重，w_trans转移音高差权重，tau时间衰减常数
        w_vel = 0.1
        w_trans = 1.5
        tau = 1.0

        # 初始化 t = 0
        t0_dp = []
        for note in onset_groups[0]:
            emit_score = note.pitch + w_vel * note.velocity
            t0_dp.append(emit_score)
        dp.append(t0_dp)
        backptr.append([-1] * len(onset_groups[0]))

        # 递推递推
        for t in range(1, T):
            prev_group = onset_groups[t - 1]
            curr_group = onset_groups[t]
            t_dp = []
            t_back = []
            for i, curr_note in enumerate(curr_group):
                curr_emit = curr_note.pitch + w_vel * curr_note.velocity
                best_score = -float('inf')
                best_prev_idx = 0
                for j, prev_note in enumerate(prev_group):
                    dt = max(0.0, curr_note.start - prev_note.start)
                    trans_penalty = w_trans * abs(curr_note.pitch - prev_note.pitch) * math.exp(-dt / tau)
                    score = dp[t - 1][j] + curr_emit - trans_penalty
                    if score > best_score:
                        best_score = score
                        best_prev_idx = j
                t_dp.append(best_score)
                t_back.append(best_prev_idx)
            dp.append(t_dp)
            backptr.append(t_back)

        # 寻找最优终点状态
        best_end_val = -float('inf')
        best_end_idx = 0
        for i, val in enumerate(dp[-1]):
            if val > best_end_val:
                best_end_val = val
                best_end_idx = i

        # 反向回溯路径
        best_path_indices = []
        curr_idx = best_end_idx
        for t in range(T - 1, -1, -1):
            best_path_indices.append(curr_idx)
            curr_idx = backptr[t][curr_idx]
        best_path_indices.reverse()

        # 生成最优主旋律音符列表
        voiced_notes = []
        for t, idx in enumerate(best_path_indices):
            selected_note = onset_groups[t][idx]
            copied_note = pretty_midi.Note(
                velocity=selected_note.velocity,
                pitch=selected_note.pitch,
                start=selected_note.start,
                end=selected_note.end
            )
            voiced_notes.append(copied_note)

        # 再次确认时间轴递增排序
        voiced_notes.sort(key=lambda n: n.start)

        # 核心算法3：执行时域物理单音化，剪取重叠延音的结束时间
        monophonic_notes = []
        for note in voiced_notes:
            if not monophonic_notes:
                monophonic_notes.append(note)
                continue

            prev_note = monophonic_notes[-1]
            if note.start < prev_note.end:
                # 冲突！强行把前一个音符缩短，使它在当前音符开始时恰好终结
                prev_note.end = note.start
                # 如果削短后前一个音符时长失效(例如<=0)，则弹出该音符
                if prev_note.end <= prev_note.start:
                    monophonic_notes.pop()

            monophonic_notes.append(note)

        logger.info(f"真·主旋律力度过滤与单音化修剪完成。原音符: {len(melody_notes)} 个 -> 提取后主旋律单音: {len(monophonic_notes)} 个")
        return monophonic_notes

    def serialize_to_yaml(self, clean_notes: list, output_yaml_path: str, bpm: float = 120.0):
        """
        计算各音符与休止符的时长，换算为 Beat，输出未清洗的 YAML。

        :param clean_notes: 裁剪无重叠的音符事件列表
        :param output_yaml_path: 生成的原始 YAML 文件存放物理绝对路径
        :param bpm: 构建曲谱时对应的参考速度，用于时间折算
        """
        logger.info(f"开始简谱节拍序列化，参考BPM设置为: {bpm}")

        # 获取 MIDI 底层定义的精确 BPM (使用第一变拍记号)
        tempo_times, tempo_bpms = self.midi_data.get_tempo_changes()
        if len(tempo_bpms) > 0:
            bpm = float(tempo_bpms[0])
            logger.info(f"成功读取 MIDI 原生头速度，更新BPM为: {bpm}")

        score_list = []
        current_time = 0.0

        for note in clean_notes:
            # 1. 判定休止符：如果音符开始时间与上一个结束点有明显空隙
            if note.start > current_time:
                rest_duration = note.start - current_time
                rest_beat = float(rest_duration * (bpm / 60.0))
                # 过滤掉低于人类按键极限阈值的微小杂碎时间
                if rest_beat > 0.02:
                    score_list.append({
                        "notes": "0",
                        "beat": float(round(rest_beat, 3))
                    })

            # 2. 插入当前弹奏音符
            note_duration = note.end - note.start
            note_beat = float(note_duration * (bpm / 60.0))
            token = PitchToNotationTokenConverter.convert_pitch(note.pitch)

            if note_beat > 0.02:
                score_list.append({
                    "notes": token,
                    "beat": float(round(note_beat, 3))
                })

            current_time = note.end

        # 写出原始简谱格式的 YAML 结构体
        output_data = {
            "song": {
                "name": "Extracted Right Hand Melody",
                "bpm": int(bpm),
                "beat_unit": 4
            },
            "playback": {
                "start_delay_seconds": 2.0,
                "key_press_seconds": 0.05
            },
            "score": score_list
        }

        # 确保输出目录真实存在
        os.makedirs(os.path.dirname(output_yaml_path), exist_ok=True)

        with codecs.open(output_yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(output_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"主旋律原始 YAML 简谱转换成功，落盘于: {output_yaml_path}")


if __name__ == "__main__":
    # 功能验证独立段
    pass