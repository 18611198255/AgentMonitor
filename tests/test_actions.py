"""会话管理 smoke 测试（标准库 unittest，零第三方依赖）。

kill_session / restart_session 依赖真实进程 / iTerm，不做 mock 单测。这里只测
不杀真实进程的纯逻辑：
- kill_session 对不存在的 pid（"99999999"）返回 False（ProcessLookupError 处理），不抛异常。
- restart_session 对非法 cwd（空 / 相对路径）安全返回 False 而不崩、不弹 iTerm。

真实的 kill / restart 端到端验证由 Task 13 统一做。
"""
import unittest

from agentmonitor import actions


class TestKillSession(unittest.TestCase):
    def test_kill_nonexistent_pid_returns_false(self):
        # 99999999 不是任何真实进程，SIGTERM 抛 ProcessLookupError → False，不崩。
        self.assertFalse(actions.kill_session("99999999"))

    def test_kill_non_numeric_pid_returns_false(self):
        # 非数字 pid → int() 失败 → False，不抛异常。
        self.assertFalse(actions.kill_session("not-a-number"))


class TestRestartSession(unittest.TestCase):
    def test_restart_empty_cwd_returns_false(self):
        self.assertFalse(actions.restart_session(""))

    def test_restart_relative_cwd_returns_false(self):
        # 相对路径不满足「以 / 开头」，应在 osascript 前返回 False，不弹 iTerm。
        self.assertFalse(actions.restart_session("relative/path"))


if __name__ == "__main__":
    unittest.main()
