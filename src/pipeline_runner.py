# 模块名称：多模态核心测试与总装流水线 (Pipeline Runner)
# 功能描述：
#     串联所有的底层子模块：音视频彻底分离 -> 音轨转写获取音高与力度底图 -> 视频轨追踪手部指尖下落及左右归属坐标 ->
#     双轨事件对齐熔断分析与力度判定核心算法 -> 写出包含左右手/主旋律声部的全新谱件。
#     实现一个坚固的、无降级伪合的钢琴排制物理流程。
#
# 主要组件：
#     - run_entire_pipeline(video_in, out_dir): 实现全部步骤串联的总控函数
#
# 依赖说明：
#     - os, sys: 文件路径管理与系统注入
#     - src.media_separator: 分离模块
#     - src.dual_stream_extractor: 双轨特征物理分析模块
#     - src.fusion_engine: 汇总补偿融合引擎
#
# 作者：JucieOvo
# 创建日期：2026-07-01
# 修改记录：
#     - 2026-07-01 JucieOvo: 创建整体总装测试逻辑

import os
import sys

# 保证本地自定义包查找正确
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.media_separator import extract_media_streams
from src.dual_stream_extractor import AudioFeatureExtractor, VisionFeatureExtractor
from src.fusion_engine import MultiModalFusionEngine

def run_entire_pipeline(video_input_path: str, output_directory: str):
    """
    驱动一站式钢琴视频提取流程，强制实行双向对准校验。

    :param video_input_path: 待转译的钢琴弹奏视频物理绝对位置
    :param output_directory: 各处理阶段中间产物与终产物的保存目录
    """
    print("======================================================================")
    print("           [双模态钢琴左右手/主旋律实时提取与缩编流水线启动]            ")
    print("======================================================================")
    if not os.path.exists(video_input_path):
        raise FileNotFoundError(f"主测试源不存在: {video_input_path}")

    os.makedirs(output_directory, exist_ok=True)

    # 1. 媒体分离阶段
    print("\n--- [第1阶段] 物理音频/视频提取中... ---")
    media_paths = extract_media_streams(video_input_path, output_directory)
    audio_path = media_paths["audio_path"]
    video_path = media_paths["video_path"]
    print(f"分离成功！\n视觉源：{video_path}\n听觉源：{audio_path}")

    # 准备中间校验结果存放的临时绝对物理路径
    temp_midi_path = os.path.join(output_directory, "temp_transkun_base.mid")
    temp_vision_json = os.path.join(output_directory, "temp_hand_tracking.json")

    # 2. 单独处理音频轨
    print("\n--- [第2阶段A] 音频转写：启动 Transkun 解析底层音高力度图... ---")
    audio_extractor = AudioFeatureExtractor(audio_path)
    audio_extractor.run_transcription(temp_midi_path)

    # 3. 单独处理视觉轨
    print("\n--- [第2阶段B] 视觉捕获：启动 OpenCV + MediaPipe 手部追踪分析... ---")
    vision_extractor = VisionFeatureExtractor(video_path)
    vision_extractor.run_visual_tracking(temp_vision_json)

    # 4. 双轨合并校正及力度分配阶段
    print("\n--- [第3阶段] 双重补差对齐与力度判断引擎运转... ---")
    fusion_engine = MultiModalFusionEngine(temp_midi_path, temp_vision_json)
    fusion_engine.load_data()

    # 高频时空配准
    fused_notes = fusion_engine.align_and_assign(time_tolerance=0.15)

    # 基于手部音量判定主旋律声部
    final_events = fusion_engine.determine_melody_by_velocity(fused_notes, window_size=2.0)

    # 5. 写入最终目标生成数据并落盘
    final_output_midi = os.path.join(output_directory, "final_hand_split_score.mid")
    fusion_engine.export_separated_midi(final_events, final_output_midi)

    print("\n======================================================================")
    print("           [流水线执行胜利结束！已生成高精度双声部带力度MIDI]            ")
    print(f"产物物理位置: {final_output_midi}")
    print("======================================================================")


if __name__ == "__main__":
    # 执行我们的“缩编测试”，使用用户指定的物理测试路径
    INPUT_VIDEO_SOURCE = r"F:\NTEZMusic\ANo\test.mp4"
    OUTPUT_WORK_DIR = r"F:\NTEZMusic\work"

    try:
        run_entire_pipeline(INPUT_VIDEO_SOURCE, OUTPUT_WORK_DIR)
    except Exception as err:
        print(f"\n流水线发生物理硬性中断: {err}", file=sys.stderr)
        sys.exit(1)
