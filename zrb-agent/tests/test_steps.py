"""步骤结构化 + bounds 失效校验：全部离线夹具，不依赖真机。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zrb_agent.guard.feasibility import check_feasibility  # noqa: E402
from zrb_agent.perceive import (  # noqa: E402
    StaleDumpError, build_page_state, classify_detail_page, tap_target,
)
from zrb_agent.planner import build_messages  # noqa: E402
from zrb_agent.schema import Bounty, Step  # noqa: E402

RID = "com.jianzhiku.zhongrenbang:id/"

# 起点读书单的 4 步：1/3 要截图证据，2 是纯动作，第 4 步不要截图但挂着
# step_txt 输入框——nocap 不等于可跳过。
SAMPLE_XML = f"""<hierarchy>
  <node bounds="[35,100][1045,200]" text="赏金 2.13元" resource-id="bb_money" />
  <node bounds="[35,300][1045,400]" text="报名条件" resource-id="bb_title" />
  <node bounds="[35,500][1045,580]" text="打开链接" resource-id="{RID}step_detail_cap" />
  <node bounds="[35,600][1045,680]" text="打开起点读书搜索《剑来》" resource-id="{RID}step_detail_nocap" />
  <node bounds="[35,700][1045,780]" text="截图上传" resource-id="{RID}step_detail_cap" />
  <node bounds="[35,800][1045,880]" text="输入暗号 1" resource-id="{RID}step_detail_nocap" />
  <node bounds="[35,900][1045,980]" text="请输入暗号" resource-id="{RID}step_txt" />
  <node bounds="[35,1800][1045,1900]" text="立即报名" resource-id="btn_apply" />
</hierarchy>"""

# 同一页面滚动前后的两份 dump：main_btn 的 y 从 1740 变到 2269。
BEFORE_XML = f"""<hierarchy>
  <node bounds="[0,0][1080,1920]" text="" resource-id="main_layout" />
  <node bounds="[35,60][1045,140]" text="悬赏大厅" resource-id="{RID}title" />
  <node bounds="[960,1700][1045,1780]" text="打开链接" resource-id="{RID}main_btn" />
</hierarchy>"""

AFTER_XML = f"""<hierarchy>
  <node bounds="[0,0][1080,1920]" text="" resource-id="main_layout" />
  <node bounds="[35,60][1045,140]" text="悬赏大厅（已滚动）" resource-id="{RID}title" />
  <node bounds="[960,2229][1045,2309]" text="打开链接" resource-id="{RID}main_btn" />
</hierarchy>"""


class StepStructureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.steps = classify_detail_page(build_page_state(SAMPLE_XML, ".Detail"))["steps"]

    def test_four_step_flags(self) -> None:
        self.assertEqual([s.needs_capture for s in self.steps],
                         [True, False, True, False])
        self.assertEqual([s.has_text_input for s in self.steps],
                         [False, False, False, True])
        self.assertEqual(self.steps[3].input_hint, "请输入暗号")

    def test_nocap_is_not_skippable(self) -> None:
        self.assertEqual(self.steps[1].text, "打开起点读书搜索《剑来》")
        self.assertEqual(self.steps[1].required_actions, ())
        self.assertEqual(self.steps[0].required_actions, ("CAPTURE_EVIDENCE",))
        self.assertEqual(self.steps[3].required_actions, ("INPUT_TEXT",))

    def test_legacy_string_steps_still_load(self) -> None:
        bounty = Bounty("t1", "旧格式", 1.0, ["截图上传"])
        self.assertEqual(bounty.step_texts, ["截图上传"])
        self.assertEqual(Bounty.from_dict(bounty.to_dict()).steps, [Step("截图上传")])

    def test_structure_does_not_change_verdicts(self) -> None:
        """结构调整只影响动作要求，不许改判：同一文本的判定必须一致。"""
        for texts in (["截图上传"], ["打开支付宝"], ["点签到"]):
            plain = Bounty("t1", "t", 1.0, texts)
            structured = Bounty("t1", "t", 1.0,
                                [Step(text, needs_capture=True) for text in texts])
            self.assertEqual(check_feasibility(plain), check_feasibility(structured))

    def test_planner_gets_step_attributes(self) -> None:
        user = build_messages(build_page_state(SAMPLE_XML, ".Detail"),
                              self.steps[3], [], "起点单")[1]["content"]
        self.assertIn("必须输入文本=是", user)
        self.assertIn("输入框提示：请输入暗号", user)


class StaleBoundsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.before = build_page_state(BEFORE_XML, ".Hall")
        self.after = build_page_state(AFTER_XML, ".Hall")

    def test_dump_ids_differ_and_stamp_elements(self) -> None:
        self.assertNotEqual(self.before.dump_id, self.after.dump_id)
        self.assertTrue(all(el.dump_id == self.before.dump_id
                            for el in self.before.elements))

    def test_scroll_moves_button(self) -> None:
        old = tap_target(self.before.elements, dump_id=self.before.dump_id,
                         contains="打开链接")
        new = tap_target(self.after.elements, dump_id=self.after.dump_id,
                         contains="打开链接")
        self.assertEqual(old, (1002, 1740))
        self.assertEqual(new, (1002, 2269))

    def test_stale_dump_is_rejected(self) -> None:
        with self.assertRaises(StaleDumpError):
            tap_target(self.before.elements, dump_id=self.after.dump_id,
                       contains="打开链接")


if __name__ == "__main__":
    unittest.main()