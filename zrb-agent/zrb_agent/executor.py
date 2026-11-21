"""执行层：把 Action 翻译成具体 adb 调用，并如实回报执行结果。

本层不含任何模型调用，也不做业务判断，只负责“把这一步真的做出去”。
它接受鸭子类型的 Action（任何有 `.action` 字符串与 `.params` 字典的对象），
这样执行层与决策层解耦，单测可以直接构造假 Action 与假 Device。
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from .device import denormalize

# 合法动作名（未知动作的报错里按此顺序列出）
ACTIONS = (
    "tap", "swipe", "input_text", "back", "wait", "open_url",
    "capture_evidence", "pick_from_gallery", "ask_human", "finish",
)
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class ExecStatus(Enum):
    """一次动作执行的结局。"""

    OK = "OK"
    FAILED = "FAILED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FINISHED = "FINISHED"


@dataclass
class ExecResult:
    """一次动作执行的可回放结果。"""

    status: ExecStatus
    action: str
    params: dict
    detail: str = ""
    elapsed_ms: int = 0


# UI 树落盘用相对路径：WSL 下 adb 是 Windows 程序，/tmp 绝对路径会被它
# 解析成 Windows 的另一个盘（D:\tmp），adb 报成功但 Python 永远读不到。
_DUMP_FILE = f".zrb_ui_dump_{os.getpid()}.xml"


def _dump_xml(device) -> str:
    """导出当前 UI 树并读回 XML 文本（最小实现，不改 perceive.py）。

    相对路径 + 先删旧文件 + 校验非空：既避开 WSL/adb.exe 的盘符差异，
    也避免把上一次的残留或 0 字节文件当成有效 UI 树。
    """
    path = Path(_DUMP_FILE)
    path.unlink(missing_ok=True)
    try:
        device.dump_ui(str(path))
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"dump_ui 未产生本地文件：{path}")
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


def _image_nodes(xml_text: str) -> list[tuple[tuple[int, int, int, int], tuple[int, int]]]:
    """挑出图片节点，返回 (节点 bounds, 点击中心)，按行优先排序。

    选择器常把 clickable 放在格子父节点上（缩略图自身 clickable=false），
    所以自身不可点击时向上找最近的 clickable 祖先并点它的中心。resource-id
    明确含 image/thumbnail 的节点优先，避免把顶栏图标当成第一张图。
    """
    root = ET.fromstring(xml_text)
    parent = {child: node for node in root.iter() for child in node}
    found: list[tuple[int, tuple[int, int, int, int], tuple[int, int]]] = []
    for node in root.iter("node"):
        resource_id = (node.get("resource-id") or "").lower()
        named = "image" in resource_id or "thumbnail" in resource_id
        if not named and not (node.get("class") or "").endswith("ImageView"):
            continue
        bounds = _BOUNDS_RE.search(node.get("bounds") or "")
        clickable = node
        while clickable is not None and clickable.get("clickable") != "true":
            clickable = parent.get(clickable)
        if bounds is None or clickable is None:
            continue
        x1, y1, x2, y2 = (int(value) for value in bounds.groups())
        found.append((0 if named else 1, (x1, y1, x2, y2),
                      ((x1 + x2) // 2, (y1 + y2) // 2)))
    if not found:
        return []
    best = min(rank for rank, _, _ in found)
    nodes = [(box, center) for rank, box, center in found if rank == best]
    nodes.sort(key=lambda item: (item[0][1], item[0][0]))
    return nodes


class Executor:
    """把 Action 落到 adb 上；任何失败都转成 ExecResult，不向调用方抛异常。"""

    def __init__(self, device, on_record: Callable[[dict], None] | None = None):
        self.device = device
        self.on_record = on_record
        # screen_size() 每次都要起 adb 进程，这里只读一次；旋转屏幕后手动刷新。
        self._screen_size: tuple[int, int] = device.screen_size()

    def refresh_screen_size(self) -> tuple[int, int]:
        """重新读取屏幕尺寸（例如旋转屏幕后调用）。"""
        self._screen_size = tuple(self.device.screen_size())
        return self._screen_size

    def execute(self, action) -> ExecResult:
        """执行一个动作：未知动作与设备异常都返回 FAILED，不抛异常。"""
        started = time.monotonic()
        name, params = "", {}
        try:
            name = str(getattr(action, "action", "") or "")
            params = dict(getattr(action, "params", None) or {})
            if name not in ACTIONS:
                status, detail = ExecStatus.FAILED, (
                    f"未知动作 {name!r}，合法动作：{', '.join(ACTIONS)}"
                )
            else:
                status, detail = getattr(self, f"_do_{name}")(params)
        except Exception as exc:  # 设备/参数异常一律降级为 FAILED
            status, detail = ExecStatus.FAILED, str(exc) or type(exc).__name__
        result = ExecResult(
            status=status,
            action=name,
            params=params,
            detail=detail,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        self._record(result)
        return result

    def _record(self, result: ExecResult) -> None:
        """把执行结果抛给账本回调；记账失败不应打断执行。"""
        if self.on_record is None:
            return
        payload = {
            "action": result.action,
            "params": result.params,
            "status": result.status.value,
            "detail": result.detail,
            "elapsed_ms": result.elapsed_ms,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        try:
            self.on_record(payload)
        except Exception:
            pass

    def _do_tap(self, params: dict) -> tuple[ExecStatus, str]:
        x, y = denormalize(params["x"], params["y"], *self._screen_size)
        self.device.tap(x, y)
        return ExecStatus.OK, f"tap({x},{y})"

    def _do_swipe(self, params: dict) -> tuple[ExecStatus, str]:
        width, height = self._screen_size
        x1, y1 = denormalize(params["x1"], params["y1"], width, height)
        x2, y2 = denormalize(params["x2"], params["y2"], width, height)
        duration = int(params.get("duration_ms", 300))
        self.device.swipe(x1, y1, x2, y2, duration)
        return ExecStatus.OK, f"swipe({x1},{y1})->({x2},{y2}) {duration}ms"

    def _do_input_text(self, params: dict) -> tuple[ExecStatus, str]:
        text = str(params["text"])
        if not text.isascii():
            return ExecStatus.FAILED, "input_text 不支持非 ASCII，请改用 ask_human"
        self.device.input_text(text.replace(" ", "%s"))
        return ExecStatus.OK, f"已输入 {len(text)} 个字符"

    def _do_back(self, params: dict) -> tuple[ExecStatus, str]:
        self.device.back()
        return ExecStatus.OK, "back"

    def _do_wait(self, params: dict) -> tuple[ExecStatus, str]:
        ms = max(int(params.get("ms", 1000)), 0)
        if ms:
            time.sleep(ms / 1000.0)
        return ExecStatus.OK, f"等待 {ms}ms"

    def _do_open_url(self, params: dict) -> tuple[ExecStatus, str]:
        url = str(params["url"])
        self.device.open_url(url)
        return ExecStatus.OK, f"open_url({url})"

    def _do_capture_evidence(self, params: dict) -> tuple[ExecStatus, str]:
        name = str(params["name"])
        self.device.capture_to_gallery(name)
        return ExecStatus.OK, f"已截图存相册：{name}"

    def _do_pick_from_gallery(self, params: dict) -> tuple[ExecStatus, str]:
        index = int(params.get("index", 0))
        nodes = _image_nodes(_dump_xml(self.device))
        if not nodes:
            return ExecStatus.FAILED, "未在选择器中找到图片节点"
        if index < 0 or index >= len(nodes):
            return ExecStatus.FAILED, (
                f"未在选择器中找到图片节点：index={index}，共 {len(nodes)} 个"
            )
        x, y = nodes[index][1]
        self.device.tap(x, y)
        return ExecStatus.OK, f"选择第 {index} 张图片 @({x},{y})"

    def _do_ask_human(self, params: dict) -> tuple[ExecStatus, str]:
        return ExecStatus.NEEDS_HUMAN, str(params.get("reason", "")) or "需要人工介入"

    def _do_finish(self, params: dict) -> tuple[ExecStatus, str]:
        return ExecStatus.FINISHED, str(params.get("reason", "")) or "任务结束"
