"""device.py 的单元测试，所有 adb 调用均被 mock。"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zrb_agent.device import Device, denormalize


def _write_png(path, width, height):
    """写一个只含 PNG 头的假文件；_png_size 只读 IHDR 里的宽高。"""
    with open(path, "wb") as handle:
        handle.write(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        )


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.device = Device()

    def test_tap(self):
        with patch.object(self.device, "_run") as run:
            self.device.tap(540, 960)
        run.assert_called_once_with(["shell", "input", "tap", "540", "960"])

    def test_swipe(self):
        with patch.object(self.device, "_run") as run:
            self.device.swipe(10, 20, 30, 40)
        run.assert_called_once_with(
            ["shell", "input", "swipe", "10", "20", "30", "40", "300"]
        )

    def test_back(self):
        with patch.object(self.device, "_run") as run:
            self.device.back()
        self.assertEqual(run.call_args.args[0], ["shell", "input", "keyevent", "4"])

    def test_capture_to_gallery(self):
        with patch.object(self.device, "_run") as run:
            self.device.capture_to_gallery("ev1")
        self.assertEqual(run.call_count, 3)
        first, second, third = [call.args[0] for call in run.call_args_list]
        self.assertIn("mkdir", first)
        self.assertIn("-p", first)
        self.assertIn("/sdcard/DCIM/Camera", first)
        self.assertEqual(second, ["shell", "screencap", "-p", "/sdcard/DCIM/Camera/ev1.png"])
        self.assertIn("MEDIA_SCANNER_SCAN_FILE", " ".join(third))
        self.assertIn("file:///sdcard/DCIM/Camera/ev1.png", third)

    def test_open_url(self):
        with patch.object(self.device, "_run") as run:
            self.device.open_url("https://example.com")
        args = run.call_args.args[0]
        self.assertIn("android.intent.action.VIEW", args)
        self.assertIn("https://example.com", args)

    def test_screen_size_uses_png_not_wm_size(self):
        """wm size 报横屏 1920x1080 时，screen_size 必须返回截图/UI 树的竖屏尺寸。"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = handle.name
        self.addCleanup(os.unlink, path)
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[0] == "pull":
                _write_png(path, 1080, 1920)
            return "Physical size: 1920x1080" if args[0] == "shell" else ""

        with patch.object(self.device, "_run", side_effect=fake_run):
            self.assertEqual(self.device.screen_size(local_path=path), (1080, 1920))
        self.assertEqual(calls, [
            ["shell", "screencap", "-p", "/sdcard/_probe.png"],
            ["pull", "/sdcard/_probe.png", path],
        ])

    def test_screen_size_rejects_non_png(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = handle.name
        self.addCleanup(os.unlink, path)

        def fake_run(args):
            if args[0] == "pull":
                with open(path, "wb") as handle:
                    handle.write(b"not a png")
            return ""

        with patch.object(self.device, "_run", side_effect=fake_run):
            with self.assertRaises(ValueError):
                self.device.screen_size(local_path=path)

    def test_screen_size_deletes_stale_probe_first(self):
        """本地残留的旧 _probe.png 绝不能当成本次截图尺寸。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "_probe.png")
            _write_png(path, 720, 1280)  # 上一次运行留下的旧尺寸

            def fake_run(args):
                if args[0] == "pull":
                    _write_png(path, 1080, 1920)
                return ""

            with patch.object(self.device, "_run", side_effect=fake_run):
                self.assertEqual(self.device.screen_size(local_path=path), (1080, 1920))

    def test_screen_size_raises_when_pull_produces_nothing(self):
        """adb pull 没产出文件时必须抛错，不能静默返回旧值。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "_probe.png")
            _write_png(path, 720, 1280)
            with patch.object(self.device, "_run", return_value=""):
                with self.assertRaises(RuntimeError) as ctx:
                    self.device.screen_size(local_path=path)
            self.assertIn("未产生本地文件", str(ctx.exception))
            self.assertFalse(os.path.exists(path))

    def test_run_raises_on_nonzero_returncode(self):
        """adb 失败常把错误写在 stdout、stderr 为空，必须靠 returncode 识别。"""
        completed = subprocess.CompletedProcess(
            args=["adb"], returncode=1,
            stdout="adb: error: failed to stat remote object", stderr="",
        )
        with patch("zrb_agent.device.subprocess.run", return_value=completed):
            with self.assertRaises(RuntimeError) as ctx:
                self.device.back()
        self.assertIn("rc=1", str(ctx.exception))
        self.assertIn("failed to stat remote object", str(ctx.exception))
        self.assertEqual(self.device._last_stderr, "")

    def test_denormalize_center_is_center(self):
        x, y = denormalize(0.5, 0.5, 1080, 1920)
        self.assertEqual((x, y), (540, 960))
        self.assertLess(x, 1080)
        self.assertLess(y, 1920)

    def test_input_text_ascii(self):
        with patch.object(self.device, "_run") as run:
            self.device.input_text("abc123")
        run.assert_called_once_with(["shell", "input", "text", "abc123"])

    def test_input_text_rejects_non_ascii(self):
        with patch.object(self.device, "_run") as run:
            with self.assertRaises(ValueError) as ctx:
                self.device.input_text("你好")
        self.assertIn("ask_human", str(ctx.exception))
        run.assert_not_called()

    def test_input_text_replaces_space(self):
        with patch.object(self.device, "_run") as run:
            self.device.input_text("aa bb")
        run.assert_called_once_with(["shell", "input", "text", "aa%sbb"])


if __name__ == "__main__":
    unittest.main()
