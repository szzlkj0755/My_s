"""伦理红牌：地区限制、伪造身份与虚假调研（纯逻辑，无副作用）。"""

from zrb_agent.schema import AbortReason, Bounty

# 本机公网 IP 归属地为中国云南省昆明市；换机器时必须按实际 IP 归属地修改。
DEVICE_REGION = "云南"

# 伪造身份：出现「姓名」且出现任一手机 / 验证码 / 地址 / 实名类词。
_IDENTITY_TERMS = ("手机", "验证码", "地址", "证件照", "证件", "实名", "绑卡", "认证")
# 虚假调研：要求填写或编造问卷，或指定「标准答案」「不要选」等答案。
_SURVEY_TERMS = ("调查", "问卷", "IPSOS", "标准答案", "不要选")
# 改 IP 绕过地区校验：不是「我在不在那儿」，而是「去骗校验」，属硬红线。
_VPN_TERMS = ("ip调到", "IP调到")

def check_ethics(bounty: Bounty) -> AbortReason | None:
    """返回伦理红牌原因；通过时返回 None。

    优先级：地区（拒）> 改 IP > 身份 > 调研 > 地区（未知）。地区是客观事实，
    伦理是主观决定，所以先讲事实再讲立场。地区语义以字段名为准：黑名单
    ``forbidden_regions`` 含本机即拒，白名单 ``allowed_regions`` 非空且不含本机即拒；
    空列表表示该方向没有限制；两个都为 ``None`` 时返回 ``UNKNOWN_REGIONS``。
    """
    if bounty.forbidden_regions and DEVICE_REGION in bounty.forbidden_regions:
        return AbortReason.REGION_REJECTED
    if bounty.allowed_regions and DEVICE_REGION not in bounty.allowed_regions:
        return AbortReason.REGION_REJECTED
    text = " ".join(bounty.step_texts)
    if any(term in text for term in _VPN_TERMS):
        return AbortReason.REQUIRES_VPN
    if "姓名" in text and any(term in text for term in _IDENTITY_TERMS):
        return AbortReason.ETHICS_FABRICATED_IDENTITY
    if any(term in text for term in _SURVEY_TERMS):
        return AbortReason.ETHICS_FAKE_SURVEY
    if bounty.allowed_regions is None and bounty.forbidden_regions is None:
        return AbortReason.UNKNOWN_REGIONS
    return None
