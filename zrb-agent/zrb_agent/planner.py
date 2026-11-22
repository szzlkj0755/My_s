"""决策层：页面 + 步骤 + 历史 → 受限动作空间里的唯一动作。

只依赖感知层结构、不信任其内容：地区字段可能为 None（没读到），坐标以本屏 UI 树 bounds 为准，不读缓存屏幕尺寸。
"""
import json
import re
from pathlib import Path

from zrb_agent.perceive import PageState
from zrb_agent.schema import Step
from zrb_agent.schema_action import Action, ActionType

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "planner.md"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_OBJECT = re.compile(r"\{.*\}", re.S)


class InvalidActionError(Exception):
    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


def _step_block(step: Step | str) -> str:
    """把本步原文和结构化属性一起交给模型，避免它自己猜要不要截图/输入。"""
    if isinstance(step, str):
        return step
    lines = [step.text,
             f"本步要求：需要截图证据={'是' if step.needs_capture else '否'}；"
             f"必须输入文本={'是' if step.has_text_input else '否'}"]
    if step.input_hint:
        lines.append(f"输入框提示：{step.input_hint}")
    return "\n".join(lines)


def build_messages(page: PageState, step_text: Step | str, history: list[Action],
                   task_title: str) -> list[dict]:
    """构造 chat messages；坐标只用本屏 UI 树，历史只取最近 8 条。"""
    els = "\n".join(f"[{el.ref}] {el.text[:40]}  rid={el.resource_id}  center={el.center}"
                    for el in page.elements) or "（本屏无可操作元素）"
    acts = "\n".join(f"{i}. {a.action.value} {json.dumps(a.params, ensure_ascii=False)}"
                     f" -> 预期：{a.expect}" for i, a in enumerate(history[-8:], 1)) or "（无）"
    user = (f"任务：{task_title}\n当前步骤：{_step_block(step_text)}\n页面：{page.activity}\n"
            f"元素：\n{els}\n最近动作：\n{acts}")
    return [{"role": "system", "content": _PROMPT.read_text(encoding="utf-8")},
            {"role": "user", "content": user}]


def parse_action(raw: str) -> Action:
    """从模型输出里抽出 JSON 并校验；失败抛 InvalidActionError。"""
    text = raw.strip()
    if fenced := _FENCE.search(text):
        text = fenced.group(1).strip()
    if not (match := _OBJECT.search(text)):
        raise InvalidActionError("输出中没有 JSON 对象", raw)
    try:
        action = Action.from_dict(json.loads(match.group(0)))
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidActionError(f"JSON 无法还原为动作：{exc}", raw) from exc
    if errors := action.validate():
        raise InvalidActionError("动作不合法：" + "；".join(errors), raw)
    return action


def plan(page: PageState, step_text: str, history: list[Action], task_title: str,
         llm_call) -> Action:
    """调模型一次；解析失败带错误重试一次，仍失败转 ASK_HUMAN。"""
    messages = build_messages(page, step_text, history, task_title)
    raw = llm_call(messages)
    for attempt in (1, 2):
        try:
            return parse_action(raw)
        except InvalidActionError as exc:
            if attempt == 2:
                break
            messages += [{"role": "assistant", "content": raw}, {"role": "user", "content":
                          f"上一次输出不合法：{exc}。只输出严格 JSON，重新给一个动作。"}]
            raw = llm_call(messages)
    return Action("模型输出无法解析", ActionType.ASK_HUMAN,
                  {"question": "模型连续两次输出无法解析，请人工确认当前页面。"}, "人工介入")
