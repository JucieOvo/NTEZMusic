# 模块名称：曲谱YAML结构与时值断流分析辅助脚本
# 功能描述：
#     分析 work/upagain_playable.yaml 中的时时事件序列，
#     找出其中是否存在超长休止符、断流或者音符合法性异常，辅助诊断“中途无音”问题。
#
# 作者：JucieOvo
# 创建日期：2026-07-01

import yaml

def main():
    yaml_path = r"F:\NTEZMusic\work\upagain_playable.yaml"
    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    score = data.get("score", [])
    print(f"曲谱事件总数: {len(score)}")

    # 1. 统计音符和休止符的分布
    rests_count = 0
    notes_count = 0
    total_beats = 0.0
    long_rests = []

    current_beat_pos = 0.0

    for i, ev in enumerate(score):
        notes = ev.get("notes", "0")
        beat = float(ev.get("beat", 0.0))
        total_beats += beat

        if notes == "0" or notes == "rest" or not notes:
            rests_count += 1
            # 如果休止符长度大于2拍（在BPM=120下相当于1秒以上）
            if beat > 2.0:
                long_rests.append({
                    "index": i,
                    "beat_position": current_beat_pos,
                    "rest_duration_beats": beat
                })
        else:
            notes_count += 1

        current_beat_pos += beat

    print(f"总计拍数: {total_beats:.3f} 拍")
    print(f"实际音符事件数: {notes_count}")
    print(f"休止符事件数: {rests_count}")
    print(f"超长休止符 (>= 2拍) 统计 (共 {len(long_rests)} 处):")
    for r in long_rests[:20]: # 打印前20个
        print(f"  - 索引 {r['index']}: 起始位置 {r['beat_position']:.3f} 拍, 持续 {r['rest_duration_beats']:.3f} 拍")

    if len(long_rests) > 20:
        print(f"  ... 以及其他 {len(long_rests) - 20} 处超长休止")

if __name__ == "__main__":
    main()
