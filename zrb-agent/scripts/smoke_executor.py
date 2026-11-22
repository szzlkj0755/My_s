#!/usr/bin/env python3
"""执行层真机冒烟：验证 adb 写出的本地文件真的落在 Python 读得到的位置。

用法（在 zrb-agent 目录下运行，需先连好设备）：

    python3 scripts/smoke_executor.py
    python3 scripts/smoke_executor.py --adb /mnt/d/tool/platform-tools/adb.exe --serial emulator-5554

输出 PASS/FAIL <检查名> <实测值>，任一 FAIL 退出码 1。
这些检查单测覆盖不了：单测里的 FakeDevice 用 Python 写文件，绕开了 adb.exe
这个 Windows 进程如何解释路径（WSL 的 /tmp/x 会被它当成 D:\\tmp\\x）。
"""

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from zrb_agent.device import Device, _png_size  # noqa: E402
from zrb_agent.executor import Executor, _dump_xml  # noqa: E402

PROBE = "_probe.png"
FRESH = "_smoke_screen.png"
REMOTE_SHOT = "/sdcard/_smoke_screen.png"


def make_png(path: str, width: int, height: int) -> None:
    """写一个只有 PNG 头的小文件；_png_size 只读 IHDR 里的宽高。"""
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big") + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="zrb-agent 执行层真机冒烟")
    parser.add_argument("--adb", default="adb",
                        help="adb 可执行文件；WSL 下通常是 adb.exe 的路径")
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--index", type=int, default=0, help="选择器里点第几张图")
    args = parser.parse_args(argv)

    results: list[bool] = []

    def check(name: str, value: object, ok: bool) -> None:
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name} {value}")

    device = Device(adb_path=args.adb, serial=args.serial)

    # 1) _dump_xml：adb 拉回的 UI 树必须非空、可解析
    xml_text = ""
    try:
        xml_text = _dump_xml(device)
        check("_dump_xml 非空", len(xml_text), bool(xml_text))
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本要把失败原因打印出来
        check("_dump_xml 非空", repr(exc), False)
    try:
        check("_dump_xml 可解析", ET.fromstring(xml_text).tag, True)
    except Exception as exc:  # noqa: BLE001
        check("_dump_xml 可解析", repr(exc), False)

    # 2) screen_size：预置脏 _probe.png，缓存值仍必须等于独立截图
    make_png(PROBE, 720, 1280)
    executor = None
    try:
        executor = Executor(device)
        device.screencap(FRESH, REMOTE_SHOT)
        fresh = _png_size(FRESH)
        cached = executor._screen_size
        check("screen_size 与截图一致", f"{cached} == {fresh}", cached == fresh)
        refreshed = executor.refresh_screen_size()
        check("screen_size 无脏文件（两次一致）",
              f"{cached} / {refreshed}", cached == refreshed == fresh)
    except Exception as exc:  # noqa: BLE001
        check("screen_size 与截图一致", repr(exc), False)
    finally:
        for name in (PROBE, FRESH):
            Path(name).unlink(missing_ok=True)

    # 3) pick_from_gallery：打开系统相册选择器，点第 index 张
    if executor is not None:
        try:
            device._run(["shell", "am", "start", "-a",
                         "android.intent.action.GET_CONTENT", "-t", "image/*"])
            time.sleep(2)
            result = executor.execute(SimpleNamespace(
                action="pick_from_gallery", params={"index": args.index}
            ))
            check("pick_from_gallery 在相册页成功", result.detail,
                  result.status.value == "OK")
        except Exception as exc:  # noqa: BLE001
            check("pick_from_gallery 在相册页成功", repr(exc), False)

    # 4) capture_evidence：截图 → 相册入库（MediaStore 能查到）
    if executor is not None:
        name = f"smoke_{int(time.time())}"
        try:
            result = executor.execute(SimpleNamespace(
                action="capture_evidence", params={"name": name}
            ))
            check("capture_evidence 截图", result.detail, result.status.value == "OK")
            time.sleep(1.5)
            rows = device._run(["shell", "content", "query", "--uri",
                                "content://media/external/images/media",
                                "--projection", "_data"])
            check("capture_evidence 相册入库", name, name in rows)
        except Exception as exc:  # noqa: BLE001
            check("capture_evidence 相册入库", repr(exc), False)

    print(f"\n{sum(results)}/{len(results)} PASS")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())