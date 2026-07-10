# 真实钢琴音源合规报告

> 作者：JucieOvo
> 日期：2026-07-11
> 状态：通过

## 音源

- 名称：Salamander Grand Piano (FreePats)
- 版本：V3+20200602 SF2
- 乐器：Yamaha C5 Grand Piano
- 样本：原始录音为 48 kHz / 24-bit，16 个力度层
- 格式：SoundFont 2 (`.sf2`)
- 上游页面：<https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html>
- 下载文件：`SalamanderGrandPiano-SF2-V3+20200602.tar.xz`
- 下载包 SHA-256：`15edb061d7ba60d58332f72dba8f8ce40988048cc703f935e6320f37d650e213`
- 解压 SF2 SHA-256：`712d0e681efbe5203a8014e9b3e84168f1908c82f2f6fb13bd2c77d6d72c70b7`

## 许可证

- 许可证：Creative Commons Attribution 3.0
- 许可证地址：<https://creativecommons.org/licenses/by/3.0/>
- 原作者：Alexander Holm
- FreePats SF2 组装：Roberto Gordo Saez / FreePats
- 本地许可证证据：`soundfont/SalamanderGrandPiano-SF2-V3+20200602/readme.txt`

该音源由真实 Yamaha C5 钢琴录音样本组成，不是正弦波、占位音源或物理建模替代品。本实验生成音频时保留上述署名信息。

## 渲染工具

- FluidSynth：2.5.6，LGPL-2.1
- FluidSynth 官方发行页：<https://github.com/FluidSynth/fluidsynth/releases/tag/v2.5.6>
- Windows x64 压缩包 SHA-256：`a4b8bd4f133b7b6770537f6c18b2b2b93579338d51e26f777d025e40e15a7e81`
- `fluidsynth.exe` SHA-256：`23bfbfa8d2e8fe88cb2698969e06fce8ed56217de834133904884784ff69ed1e`
- ffmpeg：8.0.1，当前系统 PATH 中的真实可执行文件

## 真实验证

`tests/test_arrangement_benchmark_rendering.py` 已使用项目真实缩编 MIDI 完成 FluidSynth WAV 渲染、ffmpeg MP3 编码以及 WAV/MP3 回读验证。
