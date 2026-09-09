"""CLI 探测器 smoke 测试（标准库 unittest，零第三方依赖）。

CLIDetector 依赖真实系统调用（pgrep / lsof），不适合 mock。这里只断言返回
结构：detect() 返回 list（可为空）、元素都有 cwd/pid/tty 字段、source=="cli"、
jumpable==True。不假设具体数量，有/无 pi 进程的环境都能通过。
"""
import unittest

from agentmonitor.detectors.base import SessionRef
from agentmonitor.detectors.cli import CLIDetector


class TestCLIDetector(unittest.TestCase):
    def test_returns_list_of_sessionrefs(self):
        refs = CLIDetector().detect()
        self.assertIsInstance(refs, list)
        for r in refs:
            self.assertIsInstance(r, SessionRef)
            self.assertIsInstance(r.cwd, str)
            self.assertIsInstance(r.pid, str)
            self.assertIsInstance(r.tty, str)
            self.assertEqual(r.source, "cli")
            self.assertTrue(r.jumpable)

    def test_detect_never_raises(self):
        # 即使 pgrep/lsof 失败（如无 pi 进程），也只降级返回 list，不抛异常。
        refs = CLIDetector().detect()
        self.assertIsInstance(refs, list)


if __name__ == "__main__":
    unittest.main()
