"""感知层单元测试：全部使用内联最小 XML，不依赖真机或截图文件。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zrb_agent.perceive import (  # noqa: E402
    UiElement,
    build_page_state,
    classify_detail_page,
    find_elements,
    parse_ui_xml,
    tap_target,
)

BASE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" class="android.widget.FrameLayout" text="" resource-id="">
    <node bounds="[35,100][1045,200]" text="赏金任务" resource-id="item_title" />
    <node bounds="[35,1821][1045,1905]" text="立即报名" resource-id="btn_apply" />
  </node>
</hierarchy>"""

DETAIL_XML = """<hierarchy>
  <node bounds="[35,100][1045,200]" text="赏金 2.13元" resource-id="bb_money" />
  <node bounds="[35,300][1045,400]" text="报名条件" resource-id="bb_title" />
  <node bounds="[35,1800][1045,1900]" text="立即报名" resource-id="btn_apply" />
</hierarchy>"""

ENTITY_XML = (
    '<hierarchy><node bounds="[0,0][100,100]" '
    'text="A&amp;B" resource-id="x" /></hierarchy>'
)


class PerceiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.elements = parse_ui_xml(BASE_XML)

    def test_parse_ui_xml_drops_container(self) -> None:
        self.assertEqual(len(self.elements), 2)
        self.assertEqual([e.ref for e in self.elements], ["e0", "e1"])

    def test_bounds_and_center(self) -> None:
        self.assertEqual(self.elements[1].bounds, (35, 1821, 1045, 1905))
        self.assertEqual(self.elements[1].center, (540, 1863))

    def test_norm_center(self) -> None:
        el = UiElement("e0", "x", "", (0, 0, 1080, 1920), (540, 960))
        self.assertEqual(el.norm_center(1080, 1920), (0.5, 0.5))

    def test_find_elements_contains(self) -> None:
        self.assertEqual(len(find_elements(self.elements, contains="报名")), 1)

    def test_tap_target_hit(self) -> None:
        self.assertEqual(tap_target(self.elements, dump_id=self.elements[0].dump_id,
                                    contains="立即报名"), (540, 1863))

    def test_tap_target_miss(self) -> None:
        self.assertIsNone(tap_target(self.elements, dump_id=self.elements[0].dump_id,
                                     contains="不存在的东西"))

    def test_classify_detail_page(self) -> None:
        result = classify_detail_page(build_page_state(DETAIL_XML, ".Detail"))
        self.assertTrue(result["is_detail"])
        self.assertEqual(result["reward"], 2.13)

    def test_unescape_entities(self) -> None:
        self.assertEqual(parse_ui_xml(ENTITY_XML)[0].text, "A&B")
        self.assertEqual(build_page_state(ENTITY_XML, ".Main").texts, ["A&B"])


if __name__ == "__main__":
    unittest.main()
