"""First-use evidence and error recovery; no browser permissions or paid tasks."""
import unittest
from aiflame.integrations.chatgpt_web.guide import verification_text, offline_html, STEPS, CHECKS, STORE_URL

class BrowserOnboardingTests(unittest.TestCase):
    def snapshot(self, status="verified", source="chatgpt-web", started=101):
        return {"sources":[{"id":source,"enabled":True}], "verification":{source:{"status":status,"started_at":started,"last_seen":110}},"settings":{},"display":{}}

    def test_old_success_other_platform_and_simulations_do_not_prove_current_setup(self):
        self.assertNotIn("已验证", verification_text(self.snapshot(started=10), {"chatgpt-web":100}))
        self.assertNotIn("已验证", verification_text(self.snapshot(source="claude-web"), {}, "chatgpt-web"))
        self.assertNotIn("已验证", verification_text({"sources":[{"id":"chatgpt-web","enabled":True}],"verification":{}}, {}))
        self.assertIn("已收到任务开始", verification_text(self.snapshot("working"), {"chatgpt-web":100}))
        self.assertIn("网页接入已验证", verification_text(self.snapshot(), {"chatgpt-web":100}))

    def test_disabled_and_failed_task_remain_actionable(self):
        data = self.snapshot()
        self.assertIn("没有正常完成", verification_text(self.snapshot("cancelled"), {}))
        data["sources"][0]["enabled"]=False
        self.assertIn("开关已关闭", verification_text(data, {}, "chatgpt-web"))

    def test_store_steps_have_success_criteria_and_no_manual_loading(self):
        self.assertEqual(len(STEPS), 2)
        self.assertEqual(len(STEPS), len(CHECKS))
        text = offline_html()
        self.assertNotIn("迁移", text)
        self.assertNotIn("<script", text)
        self.assertNotIn("<svg", text)
        self.assertNotIn("加载未打包", text)
        self.assertIn(STORE_URL, text)
        self.assertIn("添加至 Chrome", text)
        self.assertIn("完成标志", text)
        self.assertIn("仅打开商店页面不代表安装完成", text)
