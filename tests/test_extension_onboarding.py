"""First-use evidence and error recovery; no browser permissions or paid tasks."""
import unittest
from aiflame.integrations.chatgpt_web.guide import verification_text, offline_html, STEPS, CHECKS, IMAGES, EXTRA_IMAGES

class BrowserOnboardingTests(unittest.TestCase):
    def snapshot(self, status="verified", source="chatgpt-web", started=101):
        return {"sources":[{"id":source,"enabled":True}], "verification":{source:{"status":status,"started_at":started,"last_seen":110}},"settings":{},"display":{}}

    def test_old_success_other_platform_and_simulations_do_not_prove_current_setup(self):
        self.assertNotIn("已验证", verification_text(self.snapshot(started=10), {"chatgpt-web":100}))
        self.assertNotIn("已验证", verification_text(self.snapshot(source="claude-web"), {}, "chatgpt-web"))
        self.assertNotIn("已验证", verification_text({"sources":[{"id":"chatgpt-web","enabled":True}],"verification":{}}, {}))
        self.assertIn("已收到任务开始", verification_text(self.snapshot("working"), {"chatgpt-web":100}))
        self.assertIn("网页接入已验证", verification_text(self.snapshot(), {"chatgpt-web":100}))

    def test_quiet_disabled_and_failed_task_remain_actionable(self):
        data = self.snapshot()
        data["settings"] = {"paused": True}
        self.assertIn("暂停", verification_text(data, {"chatgpt-web":100}))
        self.assertIn("没有正常完成", verification_text(self.snapshot("cancelled"), {}))
        data["sources"][0]["enabled"]=False
        self.assertIn("开关已关闭", verification_text(data, {}, "chatgpt-web"))

    def test_all_steps_have_success_criteria_real_assets_and_no_directory_migration(self):
        self.assertEqual(len(STEPS),len(CHECKS))
        for _,_,filename in STEPS:
            if filename:
                self.assertTrue((IMAGES/filename).is_file())
        for filename, _ in EXTRA_IMAGES.values():
            self.assertTrue((IMAGES/filename).is_file())
        text=offline_html()
        self.assertNotIn("迁移",text)
        self.assertNotIn("<script",text)
        self.assertNotIn("<svg",text)
        self.assertIn("⌘⇧G",text)
        self.assertIn("完成标志",text)
