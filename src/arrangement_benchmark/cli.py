"""
模块名称：arrangement_benchmark.cli
功能描述：
    提供多歌曲缩编算法真实基准实验的 Windows 命令行入口。

主要组件：
    - main: 执行 preflight 或完整 run

依赖说明：
    - arrangement_benchmark.config_loader: 加载正式集中配置
    - arrangement_benchmark.runner: 执行真实基准状态机

作者：JucieOvo
创建日期：2026-07-11
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from arrangement_benchmark.config_loader import load_benchmark_config
from arrangement_benchmark.runner import BenchmarkRunner


def build_argument_parser() -> argparse.ArgumentParser:
    """构建严格命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="运行 NTEZMusic 多歌曲缩编算法真实基准实验")
    parser.add_argument("--config", required=True, type=Path, help="集中实验 YAML 配置")
    parser.add_argument("command", choices=("preflight", "run"), help="执行环境预检或完整基准")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    执行命令行入口。

    :param argv: 可选参数序列，None 时读取系统命令行
    :return: 0 表示真实执行成功
    """
    arguments = build_argument_parser().parse_args(argv)
    config = load_benchmark_config(arguments.config)
    runner = BenchmarkRunner(config)
    if arguments.command == "preflight":
        report = runner.preflight()
        if not report.passed:
            raise RuntimeError("实验预检失败: " + "; ".join(report.failures))
        return 0
    runner.run_full_benchmark()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
