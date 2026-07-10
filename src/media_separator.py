# 模块名称：多模态视频预处理模块
# 功能描述：
#     负责接收原始钢琴弹奏原始视频文件，将其精确地分离成视频轨道与音频轨道。
#     为后续视觉与音频两路流式处理提供基础原始数据文件。
#
# 主要组件：
#     - extract_media_streams(video_path, output_dir): 核心调度函数，抽离音视频
#
# 依赖说明：
#     - ffmpeg-python (或者通过 subprocess 调用 ffmpeg) : 用于进行无损音视频轨道分离
#     - os, logging : 处理路径与日志输出
#
# 作者：JucieOvo
# 创建日期：2026-07-01
# 修改记录：
#     - 2026-07-01 JucieOvo: 创建初始分离逻辑

import os
import subprocess
import logging

# 配置日志记录的基础行为
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("media_separator")

def extract_media_streams(video_path: str, output_dir: str) -> dict:
    """
    负责将传入的原始多媒体视频无损分离为独立的独立视频文件（无音频）与独立音频文件。
    该实现不允许使用模拟返回或者假设操作，必须真实调用系统 ffmpeg 环境进行流提取。

    :param video_path: 真实的原始视频输入绝对路径
    :param output_dir: 分离产物的确切目标存放目录
    :return: 包含分离后视频与音频完整物理路径的字典 {'video_path': ..., 'audio_path': ...}
    :raises FileNotFoundError: 当原始视频输入路径不存在时触发
    :raises RuntimeError: ffmpeg 执行分离任务失败时触发
    """
    # 1. 基础校验逻辑：确保输入输出环境安全真实
    if not os.path.exists(video_path):
        logger.error(f"严重错误：找不到原始视频文件: {video_path}")
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    # 2. 准备输出目标文件路径，确保路径格式和命名具有唯一性和可追溯性
    base_name = os.path.basename(video_path)
    file_prefix, _ = os.path.splitext(base_name)

    # 构建绝对路径，严格防止相对路径导致的环境异常
    out_video_path = os.path.join(output_dir, f"{file_prefix}_vision_only.mp4")
    out_audio_path = os.path.join(output_dir, f"{file_prefix}_audio_only.wav")

    # 确保保存目录结构真实存在
    os.makedirs(output_dir, exist_ok=True)

    # 3. 执行核心业务逻辑：调用外部编码转换依赖执行真实抽取
    # 我们采用 -c:v copy 无损拷贝视频流不含音轨，采用 -vn -c:a pcm_s16le 高保真转储音频流，坚决不做压缩降级
    try:
        # 分离出纯视觉流轨道 (-an 禁用音频)
        logger.info(f"开始真实提取核心纯视觉流至: {out_video_path}")
        subprocess.run(
            ['ffmpeg', '-i', video_path, '-c:v', 'copy', '-an', out_video_path, '-y'],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

        # 分离出纯音频流轨道 (-vn 禁用视频，固定保存为高质量无损 wav 用于特征分析)
        logger.info(f"开始真实提取核心纯音频流至: {out_audio_path}")
        subprocess.run(
            ['ffmpeg', '-i', video_path, '-vn', '-c:a', 'pcm_s16le', '-ar', '44100', '-ac', '2', out_audio_path, '-y'],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg执行发生硬性错误: \n{e.stderr.decode('utf-8', errors='ignore')}")
        raise RuntimeError("因FFmpeg依赖底座执行失败，视频分离流程彻底中断，不允许重试掩盖问题。")

    # 4. 数据组装与返回，返回真实存在的落盘路径字典
    result_paths = {
        "video_path": out_video_path,
        "audio_path": out_audio_path
    }

    logger.info("音视频模态数据双轨真实分离完成。")
    return result_paths
