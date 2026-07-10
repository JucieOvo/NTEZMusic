"""
模块名称：test_svsep_hand_separator_audit
功能描述：
    验证 piano_svsep 声部分离审计结构的真实校验逻辑。
    不运行模型推理，只测试 audit 对象的纯逻辑校验。

主要组件：
    - test_svsep_audit_accepts_valid_distribution
    - test_svsep_audit_rejects_low_match_ratio
    - test_svsep_audit_rejects_empty_hand_result
    - test_svsep_audit_rejects_unknown_staff

作者：JucieOvo
创建日期：2026-07-01
"""

import pytest
from svsep_hand_separator import SvsepSeparationAudit


def _audit(**overrides) -> SvsepSeparationAudit:
    """
    构造测试用 SvsepSeparationAudit。

    提供一组合理的默认值，可通过 overrides 覆盖特定字段。

    :param overrides: 需要覆盖的字段键值对
    :return: 构造完成的 SvsepSeparationAudit 实例
    """
    values = {
        "total_note_array_count": 100,
        "predicted_staff_count": 100,
        "matched_original_note_count": 98,
        "unmatched_original_note_count": 2,
        "left_note_count": 40,
        "right_note_count": 58,
        "unknown_staff_count": 0,
    }
    values.update(overrides)
    return SvsepSeparationAudit(**values)


def test_svsep_audit_accepts_valid_distribution():
    """验证有效的声部分离统计能通过校验。"""
    audit = _audit()
    # 不应抛出异常
    audit.validate()
    # 验证匹配比例计算正确
    assert audit.match_ratio == pytest.approx(0.98)


def test_svsep_audit_rejects_low_match_ratio():
    """验证匹配比例过低时抛出 ValueError。"""
    audit = _audit(matched_original_note_count=80, unmatched_original_note_count=20)
    with pytest.raises(ValueError, match="匹配比例"):
        audit.validate()


def test_svsep_audit_rejects_empty_hand_result():
    """验证左右手全空时抛出 ValueError。"""
    audit = _audit(
        left_note_count=0,
        right_note_count=0,
        matched_original_note_count=0,
        unmatched_original_note_count=100,
    )
    with pytest.raises(ValueError, match="左右手音符"):
        audit.validate()


def test_svsep_audit_rejects_unknown_staff():
    """验证存在未知 staff 标签时抛出 ValueError。"""
    audit = _audit(unknown_staff_count=1)
    with pytest.raises(ValueError, match="未知 staff"):
        audit.validate()
