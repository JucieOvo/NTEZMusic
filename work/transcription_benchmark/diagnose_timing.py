"""诊断：时间校准前后逐音评测差异"""
import numpy as np, pretty_midi, mir_eval

def extract(path):
    pm = pretty_midi.PrettyMIDI(path)
    iv, pt, vl = [], [], []
    for inst in pm.instruments:
        for n in inst.notes:
            iv.append([n.start, n.end])
            pt.append(440.0*(2**((n.pitch-69)/12)))
            vl.append(n.velocity)
    return np.array(iv), np.array(pt), np.array(vl)

def evaluate(name, ref_p, est_p):
    ri, rp, rv = extract(ref_p)
    ei, ep, ev = extract(est_p)
    rd, ed = ri[-1,1], ei[-1,1]
    s = rd/ed
    print("=" * 60)
    print(f"  {name}")
    print(f"  Ref: {len(ri)} notes, {rd:.2f}s  |  Est: {len(ei)} notes, {ed:.2f}s  |  scale={s:.6f}")
    pu, ru, fu, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ri, rp, ei, ep, onset_tolerance=0.05, pitch_tolerance=50.0,
        offset_ratio=0.2, offset_min_tolerance=0.05)
    print(f"  UNCALIBRATED  P+Ons+Off F1: {fu:.4f} (P={pu:.4f} R={ru:.4f})")
    ec = ei * s
    p1, r1, f1, a1 = mir_eval.transcription.precision_recall_f1_overlap(
        ri, rp, ec, ep, onset_tolerance=0.05, pitch_tolerance=50.0,
        offset_ratio=0.2, offset_min_tolerance=0.05)
    po = mir_eval.transcription.evaluate(
        ri, rp, ec, ep, onset_tolerance=0.05, pitch_tolerance=50.0,
        offset_ratio=0.2, offset_min_tolerance=0.05)
    ms = mir_eval.transcription.match_notes(ri, rp, ec, ep, offset_ratio=0.2)
    oe = [abs(ec[e, 0] - ri[r, 0]) * 1000 for r, e in ms]
    rm = set(m[0] for m in ms)
    em = set(m[1] for m in ms)
    nm, ne = len(ri) - len(rm), len(ei) - len(em)
    print(f"  CALIBRATED  P+Ons+Off F1: {f1:.4f} (P={p1:.4f} R={r1:.4f})  AvgOverlap={a1:.4f}")
    print(f"  CALIBRATED  P+Ons      F1: {po['F-measure']:.4f} (P={po['Precision']:.4f} R={po['Recall']:.4f})")
    print(f"  Matched: {len(ms)}  Missed: {nm}  Extra: {ne}")
    print(f"  OnsetErrors(ms): MAE={np.mean(oe):.1f}  P95={np.percentile(oe,95):.1f}  P99={np.percentile(oe,99):.1f}")
    print()

evaluate("Bach BWV 846",
    "work/transcription_benchmark/bach_bwv846/reference_performance.midi",
    "work/transcription_benchmark/bach_bwv846/pipeline/midi/input.mid")
evaluate("Chopin Op.10 No.12",
    "work/transcription_benchmark/chopin_op10_no12/reference_performance.midi",
    "work/transcription_benchmark/chopin_op10_no12/pipeline/midi/input.mid")
