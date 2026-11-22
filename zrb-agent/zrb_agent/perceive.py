"""感知层：把 uiautomator 的 UI 树 XML 解析成结构化 PageState。

本层纯规则解析，不调用任何模型，也不读取截图像素。
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from zrb_agent.schema import Step

# 实测 resource-id（来源：悬赏大厅与详情页 uiautomator dump）
RID_HALL = "hall"
RID_JINGXUAN = "home_jingxuan"
RID_ITEM_TITLE = "item_title"
RID_ITEM_GONGZI = "item_gongzi"
RID_BB_TITLE = "bb_title"
RID_BB_MONEY = "bb_money"
# 详情页步骤：cap 需要截图证据，nocap 不需要（但仍要执行）；step_txt 是行内输入框。
RID_STEP_CAP = "step_detail_cap"
RID_STEP_NOCAP = "step_detail_nocap"
RID_STEP_TXT = "step_txt"
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_REWARD_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*元")
_STEP_PREFIX_RE = re.compile(r"^\s*\d")
_STEP_KEYWORDS = ("截图", "打开", "下载", "填写")


class StaleDumpError(RuntimeError):
    """坐标取自过期 dump（例如滚动前那一份），不能直接用于点击。"""


def dump_id_of(xml_text: str) -> str:
    """这份 dump 内容的短哈希，作为该次屏幕快照的标识。"""
    return hashlib.sha256(xml_text.encode("utf-8")).hexdigest()[:16]


@dataclass
class UiElement:
    """一个可交互/可读的 UI 节点。"""

    ref: str
    text: str
    resource_id: str
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]
    # 这份 bounds/center 取自哪次 dump；滚动后坐标失效，只能按 dump_id 校验。
    dump_id: str = ""

    def norm_center(self, width: int, height: int) -> tuple[float, float]:
        """把像素中心点归一化到 [0,1]。"""
        if width <= 0 or height <= 0:
            raise ValueError("width 和 height 必须为正数")
        return (self.center[0] / width, self.center[1] / height)


@dataclass
class PageState:
    """一屏的状态：activity、可见元素、文本、截图路径与这份 dump 的标识。"""

    activity: str
    elements: list[UiElement] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    dump_id: str = ""


def parse_ui_xml(xml_text: str, dump_id: str | None = None) -> list[UiElement]:
    """解析 XML，仅保留有 text 或 resource-id 的节点，ref 按保留顺序编号。

    每个元素都记下 ``dump_id``（默认按 XML 内容哈希），供 :func:`tap_target`
    拒绝陈旧坐标。
    """
    elements: list[UiElement] = []
    dump_id = dump_id if dump_id is not None else dump_id_of(xml_text)
    for node in ET.fromstring(xml_text).iter("node"):
        text = node.get("text", "") or ""
        resource_id = node.get("resource-id", "") or ""
        bounds = _BOUNDS_RE.search(node.get("bounds", "") or "")
        if (not text and not resource_id) or bounds is None:
            continue  # 纯布局容器或无法点击的节点丢弃
        x1, y1, x2, y2 = (int(v) for v in bounds.groups())
        elements.append(UiElement(
            ref=f"e{len(elements)}", text=text, resource_id=resource_id,
            bounds=(x1, y1, x2, y2), center=((x1 + x2) // 2, (y1 + y2) // 2),
            dump_id=dump_id,
        ))
    return elements


def build_page_state(xml_text: str, activity: str,
                     screenshot_path: str | None = None) -> PageState:
    """组合 XML、activity 与截图路径；文本统一做一次 HTML 实体反转义。"""
    dump_id = dump_id_of(xml_text)
    elements = parse_ui_xml(xml_text, dump_id)
    texts = [html.unescape(el.text) for el in elements if el.text]
    return PageState(activity, elements, texts, screenshot_path, dump_id)


def find_elements(elements: list[UiElement], *, text: str | None = None,
                  resource_id: str | None = None,
                  contains: str | None = None) -> list[UiElement]:
    """按精确 text、精确 resource-id 或 text 子串查找（多条件取交集）。"""
    hits: list[UiElement] = []
    for el in elements:
        if text is not None and el.text != text:
            continue
        if resource_id is not None and el.resource_id != resource_id:
            continue
        if contains is not None and contains not in el.text:
            continue
        hits.append(el)
    return hits


def tap_target(elements: list[UiElement], *, dump_id: str,
               text: str | None = None,
               contains: str | None = None) -> tuple[int, int] | None:
    """返回第一个命中元素的 center；未命中返回 None。

    ``dump_id`` 必须是「最近一次 dump」的标识：滚动后旧 bounds 已经指向别的
    内容，拿旧坐标去点会把动作落到错误位置，所以这里直接拒绝并抛错。
    """
    stale = [el.ref for el in elements if el.dump_id != dump_id]
    if stale:
        raise StaleDumpError(
            f"坐标来自过期 dump：元素 {stale[:3]} 不属于 {dump_id}；"
            "每次操作前必须重新 dump 取中心点"
        )
    hits = find_elements(elements, text=text, contains=contains)
    return hits[0].center if hits else None


def _rid(element: UiElement) -> str:
    """resource-id 只取最后一段，兼容「包名:id/字段」的完整写法。"""
    return element.resource_id.rsplit("/", 1)[-1]


def extract_steps(elements: list[UiElement]) -> list[Step]:
    """按 resource-id 把详情页步骤还原成结构化 :class:`Step`。

    ``step_detail_cap`` → ``needs_capture=True``；``step_detail_nocap`` → ``False``，
    但仍然要执行。``step_txt`` 是该行挂的输入框，归给前面最近的一步；输入框
    排在步骤行之前时归给后面第一步。没有 cap/nocap 节点时返回空列表。
    """
    rows: list[tuple[int, Step]] = []
    for index, element in enumerate(elements):
        rid = _rid(element)
        text = element.text.strip()
        if rid in (RID_STEP_CAP, RID_STEP_NOCAP) and text:
            rows.append((index, Step(text, needs_capture=rid == RID_STEP_CAP)))
    for index, element in enumerate(elements):
        if _rid(element) != RID_STEP_TXT:
            continue
        before = [step for pos, step in rows if pos < index]
        target = before[-1] if before else next(
            (step for pos, step in rows if pos > index), None)
        if target is not None:
            target.has_text_input = True
            target.input_hint = element.text.strip() or None
    return [step for _, step in rows]


def classify_detail_page(page: PageState) -> dict:
    """纯规则识别悬赏详情页 8 段结构。

    步骤优先按 ``step_detail_cap`` / ``step_detail_nocap`` / ``step_txt`` 的
    resource-id 抽成 :class:`Step`；页面没有这套结构时退回文本形状启发，行为
    与旧版本一致（仅供兼容旧页面）。
    """
    joined = "\n".join(page.texts)
    is_detail = ("报名条件" in joined or "操作步骤" in joined) and "立即报名" in joined
    reward = None
    for line in page.texts:
        if "赏" in line and (matched := _REWARD_RE.search(line)):
            reward = float(matched.group(1))
            break
    steps = extract_steps(page.elements)
    if not steps:
        steps = [Step(line) for line in page.texts if
                 _STEP_PREFIX_RE.match(line)
                 or (len(line) > 8 and any(k in line for k in _STEP_KEYWORDS))]
    return {"is_detail": is_detail, "reward": reward,
            "steps": steps, "steps_text": [step.text for step in steps],
            "restricted_regions_hint": []}
