"""Web 探测器 smoke 测试（标准库 unittest，零第三方依赖）。

WebDetector 依赖真实的 pi-web `/api/agent/running`，不适合 mock。这里只断言
返回结构：detect() 返回 list（可为空）、元素若有则 source=="web"、
session_id 非空、jumpable==False。不假设数量（pi-web 此刻有无活跃会话都能过）。
"""
import unittest

from agentmonitor.detectors.base import SessionRef
from agentmonitor.detectors.web import WebDetector


class TestWebDetector(unittest.TestCase):
    def test_returns_list_of_sessionrefs(self):
        refs = WebDetector().detect()
        self.assertIsInstance(refs, list)
        for r in refs:
            self.assertIsInstance(r, SessionRef)
            self.assertEqual(r.source, "web")
            self.assertTrue(r.session_id)
            self.assertFalse(r.jumpable)

    def test_detect_never_raises(self):
        # 接口不可用（超时/异常）也只降级返回 list，不抛异常。
        refs = WebDetector().detect()
        self.assertIsInstance(refs, list)


if __name__ == "__main__":
    unittest.main()
