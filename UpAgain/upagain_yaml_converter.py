# -*- coding: utf-8 -*-
"""
模块名称：upagain_yaml_converter
功能描述：
    读取原始曲谱 YAML 文件，将多音符和弦提纯转化为最高权重的单音轨简谱。
    【V4 时域网格化量化与真乐理重构版】：
    1. 引入跳转频率把绝对时间轴网格化。合并所有过度密集的细碎音。
    2. 依托全局最佳移调与重心偏移算法。

主要组件：
    - UpAgainTimeHopMusicalConverter: 处理时频跃迁抽取与乐理转换的核心封装体
    - main: 流程入口

作者：JucieOvo
创建日期：2026-06-30
"""

import yaml
import argparse
import codecs
import re


class UpAgainTimeHopMusicalConverter:
    def __init__(self, quantize_beat: float = 0.25):
        self.quantize_beat = quantize_beat
        self._note_steps = {"1": 0, "2": 2, "3": 4, "4": 5, "5": 7, "6": 9, "7": 11}
        self.white_keys_classes = {0, 2, 4, 5, 7, 9, 11}
        self.upagain_valid_pitches_rev = {
            60: "1", 62: "2", 64: "3", 65: "4", 67: "5", 69: "6", 71: "7",
            72: "+1", 74: "+2", 76: "+3", 77: "+4", 79: "+5", 81: "+6", 83: "+7", 84: "++1"
        }
        self.min_valid_pitch = 60
        self.max_valid_pitch = 84

    def convert(self, input_path: str, output_path: str) -> None:
        with codecs.open(input_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        original_score = data.get("score", [])
        
        timeline_events = []
        current_time = 0.0
        
        for item in original_score:
            raw_notes = str(item.get("notes", ""))
            beat = float(item.get("beat", 0.0))
            pitches_here = []
            if raw_notes not in ("0", "rest", ""):
                if raw_notes.startswith("[") and raw_notes.endswith("]"):
                    raw_notes = raw_notes[1:-1]
                tokens = raw_notes.split()
                for tk in tokens:
                    p = self._parse_to_absolute_pitch(tk)
                    if p is not None:
                        pitches_here.append(p)
                        
            timeline_events.append({"absolute_start": current_time, "pitches": pitches_here, "beat": beat})
            current_time += beat
            
        if not timeline_events:
            self._write_out(data, [], output_path)
            return

        time_grids: dict = {}
        for ev in timeline_events:
            grid_id = int((ev["absolute_start"] + 0.0001) // self.quantize_beat)
            if grid_id not in time_grids:
                time_grids[grid_id] = []
            time_grids[grid_id].extend(ev["pitches"])
            
        quantized_sequence: dict = {}
        quantized_keys = sorted(time_grids.keys())
        all_pure_pitches = []
        for g_id in quantized_keys:
            basket = time_grids[g_id]
            if basket:
                survivor = max(basket) 
                quantized_sequence[g_id] = survivor
                all_pure_pitches.append(survivor)

        if not all_pure_pitches:
            self._write_out(data, [], output_path)
            return
            
        print(f"[合并报告] 消除极短粘连重弦：{len(timeline_events)} 处 -> {len(all_pure_pitches)} 处核心网格")

        best_delta_transposition = 0
        min_black_keys_count = 999999
        for delta in range(-5, 7):
            black_keys = sum(1 for p in all_pure_pitches if ((p + delta) % 12) not in self.white_keys_classes)
            if black_keys < min_black_keys_count:
                min_black_keys_count = black_keys
                best_delta_transposition = delta
                if min_black_keys_count == 0:
                    break

        avg_transposed_pitch = sum(p + best_delta_transposition for p in all_pure_pitches) / len(all_pure_pitches)
        delta_octave_cents = 72 - avg_transposed_pitch
        best_delta_octave = round(delta_octave_cents / 12) * 12
        global_delta_pitch = best_delta_transposition + best_delta_octave

        new_score = []
        max_grid_id = quantized_keys[-1]
        
        for grid_index in range(max_grid_id + 1):
            target_pitch_orig = quantized_sequence.get(grid_index)
            if target_pitch_orig is None:
                new_score.append({"notes": "0", "beat": self.quantize_beat})
            else:
                final_pitch = target_pitch_orig + global_delta_pitch
                while final_pitch > self.max_valid_pitch: final_pitch -= 12
                while final_pitch < self.min_valid_pitch: final_pitch += 12
                if (final_pitch % 12) not in self.white_keys_classes:
                    if ((final_pitch + 1) % 12) in self.white_keys_classes: final_pitch += 1
                    else: final_pitch -= 1
                new_score.append({"notes": self.upagain_valid_pitches_rev[final_pitch], "beat": self.quantize_beat})
                
        optimized_score = []
        for ev in new_score:
            if optimized_score and optimized_score[-1]["notes"] == "0" and ev["notes"] == "0":
                optimized_score[-1]["beat"] += ev["beat"]
            else:
                optimized_score.append(ev)

        self._write_out(data, optimized_score, output_path)

    def _write_out(self, original_data: dict, new_score: list, output_path: str) -> None:
        original_data["score"] = new_score
        original_data["keyboard"] = {"placeholder": {}}
        with codecs.open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(original_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def _parse_to_absolute_pitch(self, token: str) -> int | None:
        match = re.match(r'^([+-]*)([#bB]*)([1-7])$', token)
        if not match: return None
        octave_signs, accidental, note_digit = match.groups()
        pitch = 60 + self._note_steps[note_digit]
        for sign in octave_signs:
            if sign == "+": pitch += 12
            elif sign == "-": pitch -= 12
        for acc in accidental:
            if acc == "#": pitch += 1
            elif acc in ("b", "B"): pitch -= 1
        return pitch

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="网格化洗谱转换器")
    parser.add_argument("--input", required=True, help="输入 yaml 文件路径")
    parser.add_argument("--output", required=True, help="输出 yaml 文件路径")
    parser.add_argument("--quantize_beat", type=float, default=0.125, help="跳跃采样步长，默认1/8拍")
    return parser.parse_args()

def main() -> None:
    args = parse_arguments()
    converter = UpAgainTimeHopMusicalConverter(quantize_beat=args.quantize_beat)
    converter.convert(args.input, args.output)
    print(f"\n【网格清理完毕】使用步长 {args.quantize_beat} 拍完成频率合并吸附！")

if __name__ == "__main__":
    main()
