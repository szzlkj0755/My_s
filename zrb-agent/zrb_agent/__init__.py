"""zrb_agent：视觉驱动 Android 众包悬赏执行 Agent 骨架（M0 仅守卫层）。"""

from zrb_agent.guard import DEVICE_REGION, check_ethics, check_feasibility
from zrb_agent.schema import AbortReason, Bounty, Step, Verdict

__all__ = ["DEVICE_REGION", "AbortReason", "Bounty", "Step", "Verdict",
           "check_ethics", "check_feasibility"]
