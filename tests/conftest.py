"""
模块名称：conftest
功能描述：
    为 pytest 测试集统一配置项目源代码导入路径。

主要组件：
    - PROJECT_ROOT: 项目根目录
    - SRC_DIR: 源代码目录

依赖说明：
    - sys: 注入测试运行时模块搜索路径。
    - pathlib: 计算跨平台路径。

作者：JucieOvo
创建日期：2026-07-09
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
