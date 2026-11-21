"""executor.py 的单元测试：全部使用假 Device，不连接 adb、不 import adb。"""

import inspect
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zrb_agent.executor import ACTIONS, ExecStatus, Executor, _DUMP_FILE, _dump_xml

THUMBNAIL_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<hierarchy rotation="0">
  <node class="android.widget.ImageView" clickable="true" bounds="[900,900][1000,1000]" />
  <node class="android.widget.GridView" clickable="false" bounds="[0,200][1080,1920]">
    <node class="android.widget.FrameLayout" clickable="true" bounds="[0,200][200,400]">
      <node class="android.widget.ImageView"
            resource-id="com.android.providers.media.module:id/icon_thumbnail"
            clickable="false" bounds="[0,200][200,400]" />
    </node>
    <node class="android.widget.FrameLayout" clickable="true" bounds="[200,400][400,600]">
      <node class="android.widget.ImageView"
            resource-id="com.android.providers.media.module:id/icon_thumbnail"
            clickable="false" bounds="[200,400][400,600]" />
    </node>
  </node>
</hierarchy>"""

GALLERY_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<hierarchy rotation="0">
  <node class="android.widget.Button" resource-id="x:id/ok" clickable="true"
        bounds="[0,0][120,120]" />
  <node class="android.widget.ImageView" resource-id="com.app:id/not_clickable"
        clickable="false" bounds="[0,120][200,300]" />
  <node class="android.widget.ImageView" resource-id="" clickable="true"
        bounds="[300,550][500,750]" />
  <node class="android.widget.ImageView" resource-id="com.app:id/image" clickable="true"
        bounds="[300,300][500,500]" />
  <node class="android.widget.ImageView" resource-id="com.app:id/image" clickable="true"
        bounds="[0,550][200,750]" />
  <node class="android.widget.ImageView" resource-id="com.app:id/image" clickable="true"
        bounds="[0,300][200,500]" />
</hierarchy>"""


class FakeDevice:
    """记录每次调用 (方法名, 参数元组) 的假设备。"""

    def __init__(self, screen_size=(1080, 1920), xml="", error_on=None):
        self.calls = []
        self.size_calls = 0
        self._screen_size = screen_size
        self._xml = xml
        self._error_on = error_on

    def _emit(self, name, *args):
        self.calls.append((name, args))
        if self._error_on == name:
            raise RuntimeError(f"adb {name} 失败")

    def screen_size(self):
        self.size_calls += 1
        return self._screen_size

    def tap(self, x, y):
        self._emit("tap", x, y)

    def swipe(self, x1, y1, x2, y2, duration_ms):
        self._emit("swipe", x1, y1, x2, y2, duration_ms)

    def input_text(self, text):
        self._emit("input_text", text)

    def back(self):
        self._emit("back")

    def open_url(self, url):
        self._emit("open_url", url)

    def capture_to_gallery(self, name):
        self._emit("capture_to_gallery", name)

    def dump_ui(self, local_path):
        self._emit("dump_ui", local_path)
        if self._xml is None:  # 模拟 adb 把文件写到 Python 读不到的位置
            return
        with open(local_path, "w", encoding="utf-8") as stream:
            stream.write(self._xml)


def make_action(action_name, **params):
    """构造与 Planner 解耦的鸭子类型 Action。"""
    return SimpleNamespace(action=action_name, params=params)


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice()
        self.executor = Executor(self.device)
        self.device.calls.clear()

    def test_screen_size_cached_and_refreshable(self):
        self.executor.execute(make_action("tap", x=0.5, y=0.5))
        self.executor.execute(make_action("tap", x=0.5, y=0.5))
        self.assertEqual(self.device.size_calls, 1)
        self.device._screen_size = (720, 1280)
        self.assertEqual(self.executor.refresh_screen_size(), (720, 1280))
        self.executor.execute(make_action("tap", x=0.5, y=0.5))
        self.assertEqual(self.device.size_calls, 2)
        self.assertEqual(self.device.calls[-1], ("tap", (360, 640)))

    def test_tap_normalizes_coordinates(self):
        result = self.executor.execute(make_action("tap", x=0.5, y=0.5))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("tap", (540, 960))])

    def test_swipe(self):
        result = self.executor.execute(make_action(
            "swipe", x1=0.1, y1=0.2, x2=0.9, y2=0.8, duration_ms=500
        ))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("swipe", (108, 384, 972, 1536, 500))])

    def test_input_text(self):
        result = self.executor.execute(make_action("input_text", text="hello"))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("input_text", ("hello",))])

    def test_input_text_escapes_spaces(self):
        result = self.executor.execute(make_action("input_text", text="aa bb"))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("input_text", ("aa%sbb",))])

    def test_input_text_rejects_non_ascii(self):
        result = self.executor.execute(make_action("input_text", text="你好"))
        self.assertEqual(result.status, ExecStatus.FAILED)
        self.assertIn("非 ASCII", result.detail)
        self.assertIn("ask_human", result.detail)
        self.assertEqual(self.device.calls, [])

    def test_back(self):
        result = self.executor.execute(make_action("back"))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("back", ())])

    def test_wait(self):
        with patch("zrb_agent.executor.time.sleep") as sleep:
            result = self.executor.execute(make_action("wait", ms=2000))
        self.assertEqual(result.status, ExecStatus.OK)
        sleep.assert_called_once_with(2.0)
        self.assertEqual(self.device.calls, [])

    def test_open_url(self):
        result = self.executor.execute(make_action("open_url", url="https://example.com"))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("open_url", ("https://example.com",))])

    def test_capture_evidence(self):
        result = self.executor.execute(make_action("capture_evidence", name="ip_search"))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls, [("capture_to_gallery", ("ip_search",))])

    def test_pick_from_gallery_skips_non_image_and_non_clickable(self):
        self.device._xml = GALLERY_XML
        result = self.executor.execute(make_action("pick_from_gallery", index=0))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls[-1], ("tap", (100, 400)))

    def test_pick_from_gallery_uses_reading_order(self):
        """四个图片格子按行优先排序，index=2 是第二行第一张。"""
        self.device._xml = GALLERY_XML
        result = self.executor.execute(make_action("pick_from_gallery", index=2))
        self.assertEqual(result.status, ExecStatus.OK)
        self.assertEqual(self.device.calls[-1], ("tap", (100, 650)))

    def test_pick_from_gallery_uses_clickable_parent_thumbnail(self):
        """缩略图自身 clickable=false 时点父格子的中心，且不能被顶栏图标抢索引。"""
        self.device._xml = THUMBNAIL_XML
        first = self.executor.execute(make_action("pick_from_gallery", index=0))
        second = self.executor.execute(make_action("pick_from_gallery", index=1))
        self.assertEqual(first.status, ExecStatus.OK)
        self.assertEqual(second.status, ExecStatus.OK)
        taps = [call for call in self.device.calls if call[0] == "tap"]
        self.assertEqual(taps, [("tap", (100, 300)), ("tap", (300, 500))])

    def test_dump_xml_uses_relative_path_without_tempfile(self):
        source = inspect.getsource(_dump_xml)
        self.assertNotIn("tempfile", source)
        self.assertNotIn('"/tmp', source)
        self.device._xml = GALLERY_XML
        self.executor.execute(make_action("pick_from_gallery", index=0))
        dump_path = next(args[0] for name, args in self.device.calls if name == "dump_ui")
        self.assertFalse(os.path.isabs(dump_path), dump_path)

    def test_dump_xml_never_reads_stale_file(self):
        """adb 没把文件写到 Python 能读的位置时，绝不能读上一次的残留。"""
        stale = Path(_DUMP_FILE)
        stale.write_text("<hierarchy>STALE</hierarchy>", encoding="utf-8")
        self.addCleanup(stale.unlink, missing_ok=True)
        self.device._xml = None
        result = self.executor.execute(make_action("pick_from_gallery", index=0))
        self.assertEqual(result.status, ExecStatus.FAILED)
        self.assertIn("未产生本地文件", result.detail)
        self.assertNotIn("tap", [name for name, _ in self.device.calls])

    def test_dump_xml_rejects_empty_file(self):
        self.device._xml = ""
        result = self.executor.execute(make_action("pick_from_gallery", index=0))
        self.assertEqual(result.status, ExecStatus.FAILED)
        self.assertIn("未产生本地文件", result.detail)

    def test_pick_from_gallery_without_image_nodes(self):
        self.device._xml = (
            '<hierarchy><node class="android.widget.FrameLayout" clickable="true" '
            'bounds="[0,0][100,100]" /></hierarchy>'
        )
        result = self.executor.execute(make_action("pick_from_gallery", index=0))
        self.assertEqual(result.status, ExecStatus.FAILED)
        self.assertIn("未在选择器中找到图片节点", result.detail)
        self.assertNotIn("tap", [name for name, _ in self.device.calls])

    def test_pick_from_gallery_index_out_of_range(self):
        self.device._xml = GALLERY_XML
        result = self.executor.execute(make_action("pick_from_gallery", index=9))
        self.assertEqual(result.status, ExecStatus.FAILED)
        self.assertIn("未在选择器中找到图片节点", result.detail)

    def test_terminal_actions_touch_no_device(self):
        expected = {"ask_human": ExecStatus.NEEDS_HUMAN, "finish": ExecStatus.FINISHED}
        for name, status in expected.items():
            with self.subTest(action=name):
                self.device.calls.clear()
                result = self.executor.execute(make_action(name, reason="人工确认"))
                self.assertEqual(result.status, status)
                self.assertEqual(self.device.calls, [])

    def test_unknown_action_lists_legal_actions(self):
        result = self.executor.execute(make_action("teleport", x=1))
        self.assertEqual(result.status, ExecStatus.FAILED)
        for name in ACTIONS:
            self.assertIn(name, result.detail)

    def test_device_exception_becomes_failed(self):
        device = FakeDevice(error_on="back")
        executor = Executor(device)
        result = executor.execute(make_action("back"))
        self.assertEqual(result.status, ExecStatus.FAILED)
        self.assertIn("adb back 失败", result.detail)

    def test_on_record_called_once_with_payload(self):
        records = []
        executor = Executor(self.device, on_record=records.append)
        executor.execute(make_action("tap", x=0.5, y=0.5))
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["action"], "tap")
        self.assertEqual(record["status"], "OK")
        self.assertIsInstance(record["elapsed_ms"], int)
        self.assertEqual(record["params"], {"x": 0.5, "y": 0.5})
        self.assertRegex(record["at"], r"T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main()