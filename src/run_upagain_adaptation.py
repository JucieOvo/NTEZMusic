# 模块名称：UpAgain 自动适配与量化清洗控制脚本 (Adaptation Runner)
# 功能描述：
#     本模块将 MIDI 提取器（midi_to_upagain_yaml）与游戏清洗转换器（upagain_yaml_converter）
#     物理串联。加载多模态分编完的主旋律 MIDI，执行物理单音提纯与简谱翻译，
#     随即调起清洗器进行 C 大调最大化白键移调和网格量化，生成最终给模拟器自动播放的 yaml 配谱。
#
# 主要组件：
#     - main: 业务装配与执行入口
#
# 依赖说明：
#     - sys, os: 路径查找
#     - src.midi_to_upagain_yaml: 提取逆向翻译模块
#     - UpAgain.upagain_yaml_converter: 清洗转换模块
#
# 作者：JucieOvo
# 创建日期：2026-07-01
# 修改记录：
#     - 2026-07-01 JucieOvo: 初始编写，打通一键式游戏谱生成链

import os
import sys

# 注入项目根路径以防多重引用出错
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.midi_to_upagain_yaml import MidiMelodyExtractor
from UpAgain.upagain_yaml_converter import UpAgainTimeHopMusicalConverter

def main():
    print("======================================================================")
    print("           [UpAgain 游戏曲谱主旋律提选与 C大调自动缩编转换启动]         ")
    print("======================================================================")

    # 1. 物理定位输入输出
    input_midi = r"F:\NTEZMusic\work\final_hand_split_score.mid"
    temp_raw_yaml = r"F:\NTEZMusic\work\temp_raw_score.yaml"
    final_output_yaml = r"F:\NTEZMusic\work\upagain_playable.yaml"

    if not os.path.exists(input_midi):
        print(f"严重错误：基础对齐 MIDI 谱件未生成，无法执行游戏适配缩编: {input_midi}", file=sys.stderr)
        sys.exit(1)

    # 2. 物理提取主旋律并输出包含绝对唱名的临时简谱（保留变音）
    print("\n--- [步骤1] 物理提纯 MIDI 右手轨并翻译为简谱序列... ---")
    try:
        extractor = MidiMelodyExtractor(input_midi)
        # 抓取右手主旋律
        clean_notes = extractor.extract_melody_events(target_track_name="Right Hand")
        # 序列化为原始简谱
        extractor.serialize_to_yaml(clean_notes, temp_raw_yaml)
    except Exception as e:
        print(f"主旋律提取步骤失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. 物理执行《UpAgain》C大调移调转换及采样重组清洗
    print("\n--- [步骤2] 启动 C大调最佳移调器调性偏移与采样步长量化... ---")
    try:
        # 使用 0.125 拍 (1/8拍) 采样网格进行合并
        converter = UpAgainTimeHopMusicalConverter(quantize_beat=0.125)
        converter.convert(temp_raw_yaml, final_output_yaml)
        print("清洗转换器执行动作成功！已完成无损键盘映射折叠。")
    except Exception as e:
        print(f"C大调转换与网格化动作失败: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n======================================================================")
    print("           [改编完成！游戏自动播放谱件已生成]                         ")
    print(f"原始未处理稿: {temp_raw_yaml}")
    print(f"游戏直接可玩谱: {final_output_yaml}")
    print("======================================================================")

if __name__ == "__main__":
    main()
