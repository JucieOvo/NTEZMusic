# 模块名称：中途断音时域物理音符对齐检测脚本
# 功能描述：
#     分析在 31 拍左右（时间约 15~18 秒）的物理音符分布，
#     对比原始音频转写 MIDI (temp_transkun_base.mid) 与对齐后 MIDI (final_hand_split_score.mid)，
#     找出音符是否在此区间被错误分配到了左手或被过滤。
#
# 作者：JucieOvo
# 创建日期：2026-07-01

import pretty_midi

def main():
    base_midi_path = r"F:\NTEZMusic\work\temp_transkun_base.mid"
    split_midi_path = r"F:\NTEZMusic\work\final_hand_split_score.mid"

    base_pm = pretty_midi.PrettyMIDI(base_midi_path)
    split_pm = pretty_midi.PrettyMIDI(split_midi_path)

    # 31.625 拍在 BPM=120 下对应的秒数时间起点 = 31.625 * 0.5 = 15.8125s
    # 9拍对应的结束时间 = (31.625 + 9.0) * 0.5 = 20.3125s
    start_sec = 15.8
    end_sec = 20.3

    print(f"查找时间区间: {start_sec:.2f}s - {end_sec:.2f}s")

    # 1. 检查原始 MIDI
    raw_notes_in_window = []
    for inst in base_pm.instruments:
        for note in inst.notes:
            if start_sec <= note.start <= end_sec:
                raw_notes_in_window.append(note)

    print(f"原始转写数据中该区间内的音符总数: {len(raw_notes_in_window)}")
    for note in sorted(raw_notes_in_window, key=lambda n: n.start):
        print(f"  - 原始音符: pitch={note.pitch}, start={note.start:.3f}s, velocity={note.velocity}")

    # 2. 检查分轨后的 MIDI (Right Hand, Left Hand, Unassigned Hand)
    print("\n分轨后各音轨音符分配情况:")
    for inst in split_pm.instruments:
        inst_notes = [n for n in inst.notes if start_sec <= n.start <= end_sec]
        print(f"  轨道 '{inst.name}' 该区间内音符数: {len(inst_notes)}")
        for note in sorted(inst_notes, key=lambda n: n.start):
            print(f"    - pitch={note.pitch}, start={note.start:.3f}s, velocity={note.velocity}")

if __name__ == "__main__":
    main()
