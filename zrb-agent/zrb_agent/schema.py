"""数据模型：悬赏任务、执行判定与中止原因。"""

from dataclasses import asdict, dataclass
from enum import Enum

@dataclass
class Step:
    """一步悬赏任务：原文 + 两个执行属性。

    详情页用 resource-id 编码了「这一步要不要交截图证据」：``step_detail_cap``
    需要截图，``step_detail_nocap`` 不需要。``_nocap`` 不等于可跳过，它只是
    不交截图；挂着 ``step_txt`` 输入框的步骤仍必须填入 ``input_hint`` 才能完成。
    把这两个约定显式放进结构，判定与规划层不必再猜 resource-id。
    """

    text: str
    needs_capture: bool = False
    has_text_input: bool = False
    input_hint: str | None = None

    @classmethod
    def from_value(cls, value: object) -> "Step":
        """从字符串（旧格式）或字典还原一步；其它类型直接报错。"""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, dict):
            return cls(str(value["text"]),
                       bool(value.get("needs_capture", False)),
                       bool(value.get("has_text_input", False)),
                       value.get("input_hint"))
        raise TypeError(f"无法把 {type(value).__name__} 还原为 Step")

    @property
    def required_actions(self) -> tuple[str, ...]:
        """该步必需的动作名；输入先于截图（截的是填完后的完成态）。"""
        actions = []
        if self.has_text_input:
            actions.append("INPUT_TEXT")
        if self.needs_capture:
            actions.append("CAPTURE_EVIDENCE")
        return tuple(actions)

class Verdict(Enum):
    DO = "DO"
    ABORT = "ABORT"

class AbortReason(Enum):
    ETHICS_FABRICATED_IDENTITY = "ETHICS_FABRICATED_IDENTITY"
    ETHICS_FAKE_SURVEY = "ETHICS_FAKE_SURVEY"
    REGION_REJECTED = "REGION_REJECTED"
    UNKNOWN_REGIONS = "UNKNOWN_REGIONS"
    REQUIRES_VPN = "REQUIRES_VPN"
    NEEDS_WECHAT = "NEEDS_WECHAT"
    NEEDS_ALIPAY = "NEEDS_ALIPAY"
    NEEDS_SCANNER = "NEEDS_SCANNER"
    # 任务动作需要登录态（账号/手机号/短信验证码）才能完成。
    NEEDS_LOGIN = "NEEDS_LOGIN"
    NEEDS_THIRD_PARTY_APP = "NEEDS_THIRD_PARTY_APP"
    UNKNOWN_REWARD = "UNKNOWN_REWARD"
    LOW_REWARD = "LOW_REWARD"
    UNKNOWN_STEPS = "UNKNOWN_STEPS"

@dataclass
class Bounty:
    """一条众包悬赏任务。

    约定：``None`` 表示「没读到」，空列表表示「读到了，确实为空」，两者不能互相代偿。
    ``reward=None`` 是赏金没读到，不能当 0 元判 ``LOW_REWARD``；``allowed_regions``
    是白名单（非空且不含本机即拒），``forbidden_regions`` 是黑名单（含本机即拒），
    空列表表示该方向没有限制。两个地区字段都为 ``None`` 时返回 ``UNKNOWN_REGIONS``；
    两个字段同时非空是相反语义混用，构造时直接 ``ValueError``。

    ``type`` / ``limite`` 是详情页的「类型标签」与「报名条件」原文。两者是
    ``None`` 表示没读到，空串表示读到了但为空；登录态判定会读这两项（人群限定
    词只出现在这两个字段的文案里），所以不要把没读到写成空串来冒充「无限制」。

    ``steps`` 是 :class:`Step` 列表：旧数据里的纯字符串在构造时按
    ``needs_capture=False`` 还原，``step_texts`` 供只关心原文的守卫层使用。
    """

    id: str
    title: str
    reward: float | None
    steps: list[Step]
    allowed_regions: list[str] | None = None
    forbidden_regions: list[str] | None = None
    type: str | None = None
    limite: str | None = None

    def __post_init__(self) -> None:
        """地区字段语义相反；同时把旧格式的字符串步骤统一还原为 :class:`Step`。"""
        if self.allowed_regions and self.forbidden_regions:
            raise ValueError("allowed_regions 与 forbidden_regions 不能同时设置")
        self.steps = [Step.from_value(step) for step in self.steps]

    @property
    def step_texts(self) -> list[str]:
        """步骤原文列表；守卫层只读文本，不关心截图/输入属性。"""
        return [step.text for step in self.steps]

    def to_dict(self) -> dict:
        """转换为可 JSON 序列化的字典（``None`` 原样保留）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Bounty":
        """从字典还原任务，兼容已废弃的 ``restricted_regions``（按 allowed 处理）。

        地区字段缺失按「已知为空」处理，显式 ``null`` 才表示「没读到」；
        ``type`` / ``limite`` 没有历史默认值，缺失与 ``null`` 都按「没读到」处理。
        ``steps`` 同时接受旧字符串与新结构字典，由 :meth:`Step.from_value` 统一。
        """
        reward = data.get("reward")
        allowed = data.get("allowed_regions", data.get("restricted_regions", []))
        forbidden = data.get("forbidden_regions", [])
        return cls(
            id=data["id"], title=data["title"],
            reward=None if reward is None else float(reward),
            steps=list(data.get("steps", [])),
            allowed_regions=None if allowed is None else list(allowed),
            forbidden_regions=None if forbidden is None else list(forbidden),
            type=data.get("type"),
            limite=data.get("limite"),
        )
