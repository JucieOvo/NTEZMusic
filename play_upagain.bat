@echo off
:: 脚本名称：play_upagain.bat
:: 功能描述：一键调起 Windows 11 下的 Python 3.10 环境，自动读取刚才通过多模态缩编并量化好的
::           C大调简谱，直接进行模拟按键自动弹奏。
:: 使用说明：请确保 MuMu 模拟器或游戏客户端已处于活动窗口（第一焦点）。
:: 作者：JucieOvo

echo ======================================================================
echo                  UpAgain 自动弹奏一键启动器
echo ======================================================================
echo.
echo 正在指向的目标简谱文件: work\upagain_playable.yaml
echo.
echo 提示事项：
echo 1. 启动后有 2 秒的延时缓冲，请在此期间快速点击并聚焦到【模拟器游戏窗口】。
echo 2. 自动模拟需要以管理员权限或最高焦点状态发送底层 Scancode (硬件扫描码)。
echo.
echo ======================================================================

set PYTHON_EXEC="C:\Users\15311\AppData\Local\Programs\Python\Python310\python.exe"
set SCORE_CONFIG="work\upagain_playable.yaml"

%PYTHON_EXEC% UpAgain\upagain_auto_player.py --config %SCORE_CONFIG%

echo.
echo 演奏完毕！
pause
