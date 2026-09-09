"""会话管理 smoke 测试（标准库 unittest，零第三方依赖，mock 为 stdlib）。

kill_session / restart_session 依赖真实进程 / iTerm，故破坏性/外联路径用 mock 隔离：
- kill_session 对不存在的 pid（"99999999"）返回 False（ProcessLookupError 处理），不抛异常。
- restart_session 对非法 cwd（空 / 相对路径）安全返回 False 而不崩、不弹 iTerm。
- restart_session 对含 " / \\ / ' 的绝对路径 cwd 三层转义后不崩（mock osascript，不真弹 iTerm）。
- do_POST 对非 dict body（如 [1,2]）返回 400 bad_body。

真实的 kill / restart 端到端验证由 Task 13 统一做。
"""
import io
import json
import unittest
from unittest import mock

from agentmonitor import actions
from agentmonitor.detectors.base import SessionRef
from agentmonitor.server import Handler


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

    def test_restart_escapes_quotes_and_backslash(self):
        # 非破坏：mock subprocess.run，不真弹 iTerm。断言含 " / \ 的 cwd 三层转义
        # （\→\\、"→\"、'→'\''）后 applescript 双引号字符串结构完整，无注入。
        cwd = '/tmp/a" b\\c'
        expected = '/tmp/a\\" b\\\\c'
        with mock.patch.object(actions.subprocess, "run",
                               return_value=mock.Mock(returncode=1)) as m:
            self.assertFalse(actions.restart_session(cwd))
            script = m.call_args[0][0][2]  # ["osascript", "-e", script]
            self.assertIn(f"cd '{expected}' && pi", script)


class _FakeHandler:
    """最小 fake，供 do_POST 注入 body（不初始化 socket/request）。"""


class TestDoPostBodyGuard(unittest.TestCase):
    def test_non_dict_body_returns_400(self):
        h = _FakeHandler()
        h.headers = {"Content-Length": "5"}
        h.rfile = io.BytesIO(b"[1,2]")
        h.path = "/api/kill"
        h._send = mock.Mock()
        Handler.do_POST(h)
        h._send.assert_called_once_with(400, {"ok": False, "result": "bad_body"})


class TestRestartValidation(unittest.TestCase):
    def _post(self, body):
        h = _FakeHandler()
        raw = json.dumps(body).encode()
        h.headers = {"Content-Length": str(len(raw))}
        h.rfile = io.BytesIO(raw)
        h.path = "/api/restart"
        h._send = mock.Mock()
        Handler.do_POST(h)
        return h

    @mock.patch("agentmonitor.server.actions")
    @mock.patch("agentmonitor.server.CLIDetector")
    def test_restart_rejects_non_whitelisted_pid(self, MockDet, MockActions):
        MockDet.return_value.detect.return_value = [SessionRef(cwd="/tmp/a", pid="1")]
        h = self._post({"pid": "999", "cwd": "/tmp/a"})
        self.assertEqual(h._send.call_args[0][0], 403)
        MockActions.kill_session.assert_not_called()
        MockActions.restart_session.assert_not_called()

    @mock.patch("agentmonitor.server.actions")
    @mock.patch("agentmonitor.server.CLIDetector")
    def test_restart_rejects_cwd_not_in_live_sessions(self, MockDet, MockActions):
        MockDet.return_value.detect.return_value = [SessionRef(cwd="/tmp/a", pid="1")]
        h = self._post({"pid": "1", "cwd": "/tmp/elsewhere"})
        self.assertEqual(h._send.call_args[0][0], 403)
        MockActions.kill_session.assert_not_called()
        MockActions.restart_session.assert_not_called()

    @mock.patch("agentmonitor.server.actions")
    @mock.patch("agentmonitor.server.CLIDetector")
    def test_restart_kills_then_restarts(self, MockDet, MockActions):
        MockDet.return_value.detect.return_value = [SessionRef(cwd="/tmp/a", pid="1")]
        MockActions.kill_session.return_value = True
        MockActions.restart_session.return_value = True
        h = self._post({"pid": "1", "cwd": "/tmp/a"})
        self.assertEqual(h._send.call_args[0][0], 200)
        MockActions.kill_session.assert_called_once_with("1")
        MockActions.restart_session.assert_called_once_with("/tmp/a")


if __name__ == "__main__":
    unittest.main()
