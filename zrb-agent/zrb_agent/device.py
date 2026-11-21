"""Android 设备控制层。

这一层是“手”：只负责把结构化命令翻译成 adb 调用，不含业务判断，也不做模型调用。
"""

import subprocess
import time
from pathlib import Path


def denormalize(x_norm: float, y_norm: float, width: int, height: int) -> tuple[int, int]:
    """把 0~1 的归一化坐标还原成像素坐标。

    所有落库坐标都是 0~1，归一化换算在别的模块完成，这里只负责还原。
    """
    return round(x_norm * width), round(y_norm * height)


def _png_size(path: str) -> tuple[int, int]:
    """读取 PNG 文件 IHDR 里的宽高，避免引入图像库依赖。

    PNG 头固定为 8 字节签名 + 4 字节块长度 + 4 字节块类型 IHDR，
    随后 4 字节宽、4 字节高，均为大端序。
    """
    with open(path, "rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError(f"不是 PNG 文件：{path!r}")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


class Device:
    """基于 adb 的 Android 设备控制层。"""

    def __init__(self, adb_path: str = "adb", serial: str = "emulator-5554"):
        self.adb_path = adb_path
        self.serial = serial
        self._last_stderr = ""

    def _run(self, args: list[str]) -> str:
        """执行 adb 命令并返回 stdout；返回码非零一律抛错。

        `adb pull` 失败时会在 stdout 报错、returncode=1 且 stderr 为空，
        只看 stderr 会把“adb 说失败”当成成功，所以这里必须检查返回码。
        """
        completed = subprocess.run(
            [self.adb_path, "-s", self.serial] + args, capture_output=True, text=True
        )
        self._last_stderr = completed.stderr or ""
        if completed.returncode != 0:
            message = (completed.stdout or completed.stderr or "").strip()
            raise RuntimeError(
                f"adb {' '.join(args)} 失败(rc={completed.returncode}): {message}"
            )
        return completed.stdout or ""

    def tap(self, x: int, y: int) -> str:
        """点击像素坐标；坐标换算由调用方负责。"""
        return self._run(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        """从 (x1, y1) 滑到 (x2, y2)，duration_ms 为滑动时长。"""
        return self._run(
            ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
        )

    def keyevent(self, code: int) -> str:
        """发送按键事件。"""
        return self._run(["shell", "input", "keyevent", str(code)])

    def back(self) -> str:
        """返回键（keyevent 4）。"""
        return self.keyevent(4)

    def input_text(self, text: str) -> str:
        """输入 ASCII 文本；空格写成 %s，非 ASCII 输入显式拒绝。

        `input text` 里字面空格会被截断，必须写成 `%s`。
        非 ASCII（中文、全角字符、emoji 等）在 Android 14 的
        InputShellCommand.sendText() 里会因 keymap 查不到字符返回 null 并 NPE，
        任何管道/脚本绕过都无效；与其静默失败，这里明确抛错，
        让调用方改用 ask_human 交由人处理。
        """
        if not text.isascii():
            raise ValueError(
                f"input_text 只支持 ASCII，收到非 ASCII 输入：{text!r}。"
                "需要中文等非 ASCII 输入请改用 ask_human。"
            )
        return self._run(["shell", "input", "text", text.replace(" ", "%s")])

    def screencap(self, local_path: str, remote_path: str = "/sdcard/_shot.png") -> str:
        """截图并拉回本地。"""
        self._run(["shell", "screencap", "-p", remote_path])
        return self._run(["pull", remote_path, local_path])

    def dump_ui(self, local_path: str = "ui.xml") -> str:
        """导出 UI 层级并拉回本地。

        设备未进入空闲状态时 uiautomator 会失败，最多重试 2 次，每次间隔 800ms。
        `_run` 现在会对非零返回码抛错，因此这里把失败也纳入重试。
        """
        remote_path = "/sdcard/ui.xml"
        for attempt in range(3):
            try:
                self._run(["shell", "uiautomator", "dump", remote_path])
                if "could not get idle state" not in self._last_stderr:
                    break
            except RuntimeError:
                if attempt == 2:
                    raise
            if attempt < 2:
                time.sleep(0.8)
        else:
            raise RuntimeError("uiautomator dump 失败：设备始终未进入空闲状态")
        return self._run(["pull", remote_path, local_path])

    def capture_to_gallery(self, name: str) -> str:
        """截屏保存到相册；不先建目录时 screencap 会静默失败。"""
        remote_path = f"/sdcard/DCIM/Camera/{name}.png"
        self._run(["shell", "mkdir", "-p", "/sdcard/DCIM/Camera"])
        self._run(["shell", "screencap", "-p", remote_path])
        return self._run(
            [
                "shell", "am", "broadcast",
                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", f"file://{remote_path}",
            ]
        )

    def open_url(self, url: str) -> str:
        """用系统默认浏览器打开链接。"""
        return self._run(
            ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url]
        )

    def screen_size(
        self, probe_path: str = "/sdcard/_probe.png", local_path: str = "_probe.png"
    ) -> tuple[int, int]:
        """读取当前屏幕像素尺寸（与 UI 树、截图同一坐标系）。

        不能用 `adb shell wm size`：它报的是面板物理方向（例如横屏 1920x1080），
        而 uiautomator 的 bounds 和截图都是旋转后的方向（例如竖屏 1080x1920）。
        这里截一张图、读 PNG 头里的真实像素宽高，三者只在同一坐标系下一致。
        本地旧文件会遮蔽本次截图，因此先删掉，并确认 adb pull 真的产生了非空文件。
        """
        local = Path(local_path)
        local.unlink(missing_ok=True)
        self._run(["shell", "screencap", "-p", probe_path])
        self._run(["pull", probe_path, str(local)])
        if not local.is_file() or local.stat().st_size == 0:
            raise RuntimeError(f"screencap 未产生本地文件：{local_path}")
        return _png_size(str(local))

    def current_focus(self) -> str:
        """返回 dumpsys window 中 mCurrentFocus= 所在的那一行。"""
        for line in self._run(["shell", "dumpsys", "window"]).splitlines():
            if "mCurrentFocus=" in line:
                return line.strip()
        return ""
