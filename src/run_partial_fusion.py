# 模块名称：增量测试局部融合平滑运行脚本
# 功能描述：
#     跳过耗时数分钟的 FFmpeg、Transkun 和 MediaPipe 视觉提取阶段，
#     直接加载已生成的中间层物理数据，执行新版双模态向心强对齐，并自动调起适应缩编输出。
#
# 作者：JucieOvo
# 创建日期：2026-07-01
# 修改记录：
#     - 2026-07-01 JucieOvo: 初始编写，加速算法迭代验证

import os
import sys

# 注入主路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fusion_engine import MultiModalFusionEngine
from src.run_upagain_adaptation import main as run_adaptation

def main():
    print("======================================================================")
    # 物理数据指向
    temp_midi_path = r"F:\NTEZMusic\work\temp_transkun_base.mid"
    temp_vision_json = r"F:\NTEZMusic\work\temp_hand_tracking.json"
    final_output_midi = r"F:\NTEZMusic\work\final_hand_split_score.mid"

    if not os.path.exists(temp_midi_path) or not os.path.exists(temp_vision_json):
        print("错误：未找到基础物理缓存文件，需完整运行 pipeline_runner.py", file=sys.stderr)
        sys.exit(1)

    print("--- [增量步骤1] 载入缓冲文件并运转全新手部对位引擎... ---")
    fusion_engine = MultiModalFusionEngine(temp_midi_path, temp_vision_json)
    fusion_engine.load_data()

    # 高频时空配准 (两端强标签分配版)
    fused_notes = fusion_engine.align_and_assign(time_tolerance=0.15)

    # 标注主旋律
    final_events = fusion_engine.determine_melody_by_velocity(fused_notes, window_size=2.0)

    # 导出
    fusion_engine.export_separated_midi(final_events, final_output_midi)
    print("分离MIDI生成成功！落盘位置: ", final_output_midi)

    print("\n--- [增量步骤2] 自动调起 UpAgain 主旋律提纯与 C大调转换... ---")
    run_adaptation()

if __name__ == "__main__":
    main()
