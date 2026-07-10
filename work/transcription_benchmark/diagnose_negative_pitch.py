"""
诊断脚本：追踪 Transkun notes_est 中负音高的根因。

运行一次完整推理，原始输出 Note 对象的属性值与 writeMidi 产物的 MIDI 音符对比。
"""

import os, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import librosa
import torch
import soxr
import transkun as _transkun
import moduleconf
from transkun.Data import writeMidi, Note as TranskunNote

# 加载模型
pkg_dir = os.path.dirname(_transkun.__file__)
weight_path = os.path.join(pkg_dir, "pretrained", "2.0.pt")
conf_path = os.path.join(pkg_dir, "pretrained", "2.0.conf")
print(f"[1] 模型权重: {weight_path}")
print(f"[1] 模型配置: {conf_path}")

conf_manager = moduleconf.parseFromFile(conf_path)
TransKun = conf_manager["Model"].module.TransKun
conf = conf_manager["Model"].config
device = torch.device("cuda")
checkpoint = torch.load(weight_path, map_location=device)
model = TransKun(conf=conf).to(device)
if "best_state_dict" in checkpoint:
    model.load_state_dict(checkpoint["best_state_dict"], strict=False)
else:
    model.load_state_dict(checkpoint["state_dict"], strict=False)
model.eval()
torch.set_grad_enabled(False)
print(f"[1] 模型加载完成, device={device}, model.fs={model.fs}")

# 加载音频
audio_path = r"F:\NTEZMusic\work\transcription_benchmark\bach_bwv846\input.mp3"
raw_audio, sr = librosa.load(audio_path, sr=None, mono=True)
print(f"[2] 音频: sr={sr}, samples={len(raw_audio)}, duration={len(raw_audio)/sr:.1f}s")
if sr != model.fs:
    raw_audio = soxr.resample(raw_audio, sr, model.fs)
    print(f"[2] 重采样: {sr} → {model.fs}")
audio_tensor = torch.from_numpy(raw_audio).to(device)
if audio_tensor.ndim == 1:
    audio_tensor = audio_tensor.unsqueeze(-1)

# 推理
print(f"[3] 开始推理...")
notes_est = model.transcribe(audio_tensor)
print(f"[3] 推理完成, notes_est 数量: {len(notes_est)}")

# 检查 Note 对象结构
note0 = notes_est[0]
print(f"\n=== Note 对象结构 ===")
print(f"类型: {type(note0)}")
print(f"是否为 Note: {isinstance(note0, TranskunNote)}")
print(f"vars(): {vars(note0)}")
attrs = [a for a in dir(note0) if not a.startswith("_")]
print(f"公开属性: {attrs}")

# 检查 __dict__ 中的 key 顺序
note_dict = vars(note0)
print(f"\nNote.__init__ 签名中参数顺序: start, end, pitch, velocity, hasOnset, hasOffset")
print(f"vars() 实际返回: {note_dict}")
print(f"vars() keys: {list(note_dict.keys())}")

# 直接属性访问 vs __dict__ 访问对比
print(f"\n=== 直接属性 vs vars() 对比 (前3条) ===")
for i in range(min(3, len(notes_est))):
    n = notes_est[i]
    d = vars(n)
    print(f"[{i}] attr.pitch={n.pitch}, vars['pitch']={d['pitch']}, "
          f"equal={n.pitch == d['pitch']}")

# 扫描异常 pitch（直接从 Note 属性读取）
print(f"\n=== 异常音高扫描 ===")
negative_pitches = []
zero_pitches = []
for i, n in enumerate(notes_est):
    p_raw = n.pitch
    p_dict = vars(n).get("pitch")
    if p_raw != p_dict:
        print(f"  !! MISMATCH [{i}]: attr={p_raw}, dict={p_dict}")
    if p_raw < 0:
        negative_pitches.append((i, p_raw, n.start, n.end, n.velocity, n.hasOnset, n.hasOffset))
    elif p_raw == 0:
        zero_pitches.append((i, p_raw, n.start, n.end, n.velocity))

print(f"pitch < 0: {len(negative_pitches)}")
print(f"pitch = 0: {len(zero_pitches)}")
print(f"pitch > 0: {len(notes_est) - len(negative_pitches) - len(zero_pitches)}")

if negative_pitches:
    print(f"\n前10条负音高详情:")
    for i, p, s, e, v, on_val, off_val in negative_pitches[:10]:
        print(f"  [{i}] pitch={p}, start={s:.4f}, end={e:.4f}, vel={v}, onset={on_val}, offset={off_val}")

# writeMidi 输出对比
print(f"\n=== writeMidi 输出 ===")
out_midi = writeMidi(notes_est)
midi_notes = []
for inst in out_midi.instruments:
    for n in inst.notes:
        midi_notes.append(n.pitch)
print(f"MIDI note_on 数量: {len(midi_notes)}")
print(f"MIDI pitch 范围: [{min(midi_notes)}, {max(midi_notes)}]")
midi_low = [p for p in midi_notes if p < 0]
midi_zero = [p for p in midi_notes if p == 0]
print(f"MIDI pitch < 0: {len(midi_low)}")
print(f"MIDI pitch = 0: {len(midi_zero)}")

# 关键对比
print(f"\n=== 差异分析 ===")
print(f"notes_est 总数: {len(notes_est)}")
print(f"MIDI notes 总数: {len(midi_notes)}")
print(f"差值: {len(notes_est) - len(midi_notes)}")
print(f"负音高数量: {len(negative_pitches)}")
print(f"note数量差 ≈ 负音高数量? {abs(len(notes_est) - len(midi_notes) - len(negative_pitches)) <= 5}")

# 检查 writeMidi 源码行为
import inspect
try:
    src = inspect.getsource(writeMidi)
    # 查找 pitch 相关逻辑
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if "pitch" in line.lower():
            print(f"  writeMidi[{i}]: {line.strip()}")
except Exception:
    print("  无法获取 writeMidi 源码")
