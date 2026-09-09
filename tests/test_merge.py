"""merge.py 单元测试（unittest，零第三方依赖）。

merge_sessions 是纯逻辑（只读磁盘 jsonl + parser），测试用临时目录构造真实
jsonl + 构造的 SessionRef 喂入。只把 constants.SESSIONS_DIR 重定向到临时目录，
不 mock 任何系统调用——glob / getmtime / parse 都跑真文件。
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from agentmonitor import constants
from agentmonitor.detectors.base import SessionRef
from agentmonitor.merge import merge_sessions

SID = "01a08172-be76-7748-a2fa-1f78011d065d"
BASE = 1750000000.0
CWD = "/tmp/proj"


def iso(epoch):
    """epoch 秒 → ISO 时间戳（毫秒 + Z），与 jsonl 落盘格式一致。"""
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )


def session_event(cwd=CWD, ts=BASE):
    return {"type": "session", "version": 3, "id": SID,
            "timestamp": iso(ts), "cwd": cwd}


def assistant_event(ts, stop_reason="endTurn", text="hi"):
    return {"type": "message", "timestamp": iso(ts),
            "message": {"role": "assistant", "model": "deepseek-v4-flash",
                        "stopReason": stop_reason,
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input": 1, "output": 1, "totalTokens": 2,
                                  "cost": {"total": 0}}}}


class MergeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="am_merge_")
        self._orig_sessions_dir = constants.SESSIONS_DIR
        constants.SESSIONS_DIR = self.tmpdir

    def tearDown(self):
        constants.SESSIONS_DIR = self._orig_sessions_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def write_jsonl(self, events, mtime, sid=SID):
        """把事件写入临时 jsonl，并把文件 mtime 固定为指定值。"""
        path = os.path.join(self.tmpdir, f"2026-09-08T00-00-00-000Z_{sid}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.utime(path, (mtime, mtime))
        return path


class TestCLIMerge(MergeTestBase):
    def test_cli_ref_matches_jsonl(self):
        now = BASE + 20
        self.write_jsonl([session_event(), assistant_event(BASE)], mtime=now - 10)
        ref = SessionRef(cwd=CWD, source="cli", pid="123",
                         tty="/dev/ttys001", jumpable=True)
        out = merge_sessions([ref], [], now=now)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(s.session_id, SID)
        self.assertEqual(s.source, "cli")
        self.assertEqual(s.pid, "123")
        self.assertTrue(s.in_terminal)
        self.assertTrue(s.live)
        self.assertTrue(s.jumpable)

    def test_cli_ref_without_jsonl_makes_placeholder(self):
        ref = SessionRef(cwd="/some/other/dir", source="cli",
                         pid="456", jumpable=True)
        out = merge_sessions([ref], [], now=BASE + 20)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(s.status, "none")
        self.assertEqual(s.source, "cli")
        self.assertEqual(s.pid, "456")
        self.assertEqual(s.session_id, "winc-456")
        self.assertEqual(s.file_path, "")
        self.assertTrue(s.in_terminal)
        self.assertTrue(s.live)
        self.assertTrue(s.jumpable)

    def test_cli_ref_stuck_reclassified_to_waiting(self):
        now = BASE + 20
        self.write_jsonl(
            [session_event(), assistant_event(BASE, stop_reason="error", text="boom")],
            mtime=now - 10,
        )
        ref = SessionRef(cwd=CWD, source="cli", pid="789", jumpable=True)
        out = merge_sessions([ref], [], now=now)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(s.status, "waiting")
        self.assertTrue(s.activity.startswith("等待"))


class TestWebMerge(MergeTestBase):
    def test_web_ref_marks_live(self):
        now = BASE + 20
        self.write_jsonl([session_event(), assistant_event(BASE)], mtime=now - 10)
        ref = SessionRef(cwd="", source="web", session_id=SID, jumpable=False)
        out = merge_sessions([], [ref], now=now)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertTrue(s.live)
        self.assertEqual(s.source, "web")

    def test_web_hanging_session_kept_with_live_false(self):
        now = BASE + 20
        self.write_jsonl([session_event(), assistant_event(BASE)], mtime=now - 10)
        out = merge_sessions([], [], now=now)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertFalse(s.live)
        self.assertEqual(s.source, "web")

    def test_web_idle_session_excluded(self):
        # 最后消息 2000s 前 → parser 判 idle；mtime 仍在 30min 内 → 走到 idle 剔除
        now = BASE + 2000
        self.write_jsonl([session_event(), assistant_event(BASE)], mtime=now - 100)
        out = merge_sessions([], [], now=now)
        self.assertEqual(out, [])


class TestDedup(MergeTestBase):
    def test_cli_claimed_session_not_duplicated_by_web(self):
        now = BASE + 20
        self.write_jsonl([session_event(), assistant_event(BASE)], mtime=now - 10)
        cli_ref = SessionRef(cwd=CWD, source="cli", pid="123", jumpable=True)
        web_ref = SessionRef(cwd="", source="web", session_id=SID, jumpable=False)
        out = merge_sessions([cli_ref], [web_ref], now=now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source, "cli")


if __name__ == "__main__":
    unittest.main()
