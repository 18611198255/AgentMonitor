"""parser.py 单元测试（标准库 unittest，零第三方依赖）。

用真实 jsonl 结构（参考 docs/jsonl格式参考_20260909.md「五」）构造临时 jsonl。
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from agentmonitor.constants import IDLE_THRESHOLD, STUCK_THRESHOLD
from agentmonitor.parser import parse_session_jsonl, session_id_from_path

SID = "01a08172-be76-7748-a2fa-1f78011d065d"
BASE = 1750000000.0  # 固定基准 epoch，保证测试可复现


def iso(epoch):
    """epoch 秒 → ISO 时间戳（毫秒 + Z），与 jsonl 落盘格式一致。"""
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )


def write_jsonl(events):
    """把事件列表写入临时 jsonl，返回文件路径。"""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def session_event(cwd="/tmp/proj", ts=BASE):
    return {"type": "session", "version": 3, "id": SID,
            "timestamp": iso(ts), "cwd": cwd}


def assistant_event(model, ts, usage, stop_reason="endTurn", text="hi"):
    return {"type": "message", "timestamp": iso(ts),
            "message": {"role": "assistant", "model": model,
                        "stopReason": stop_reason,
                        "content": [{"type": "text", "text": text}],
                        "usage": usage}}


class TestSessionIdFromPath(unittest.TestCase):
    def test_extracts_36_hex_after_underscore(self):
        path = f"/tmp/2026-09-01T09-20-51-343Z_{SID}.jsonl"
        self.assertEqual(session_id_from_path(path), SID)

    def test_no_underscore_returns_empty(self):
        self.assertEqual(session_id_from_path("/tmp/foo.jsonl"), "")


class TestParseTokensAndModel(unittest.TestCase):
    def test_sums_tokens_and_cost_and_picks_model(self):
        last_ts = BASE + 200
        events = [
            session_event(),
            {"type": "message", "timestamp": iso(BASE + 100),
             "message": {"role": "user", "content": "帮我做点事"}},
            assistant_event("model-a", BASE + 150, {
                "input": 10, "output": 5, "totalTokens": 15,
                "cost": {"total": 0.0001}}),
            assistant_event("model-b", last_ts, {
                "input": 20, "output": 10, "totalTokens": 30,
                "cost": {"total": 0.0002}}),
        ]
        path = write_jsonl(events)
        try:
            s = parse_session_jsonl(path, now=last_ts + 5)
            self.assertIsNotNone(s)
            self.assertEqual(s.tokens_in, 30)
            self.assertEqual(s.tokens_out, 15)
            self.assertEqual(s.tokens_total, 45)
            self.assertAlmostEqual(s.cost, 0.0003, places=6)
            self.assertEqual(s.model, "model-b")
            self.assertEqual(s.exchanges, 2)
            self.assertEqual(s.session_id, SID)
            self.assertEqual(s.cwd, "/tmp/proj")
        finally:
            os.unlink(path)


class TestStatusWaiting(unittest.TestCase):
    def test_last_assistant_text_without_tool_is_waiting(self):
        last_ts = BASE + 200
        events = [
            session_event(),
            {"type": "message", "timestamp": iso(BASE + 100),
             "message": {"role": "user", "content": "hi"}},
            assistant_event("deepseek-v4-flash", last_ts, {
                "input": 10, "output": 5, "totalTokens": 15,
                "cost": {"total": 0.0001}}),
        ]
        path = write_jsonl(events)
        try:
            s = parse_session_jsonl(path, now=last_ts + 5)
            self.assertEqual(s.status, "waiting")
            self.assertEqual(s.activity, "等待你的输入")
        finally:
            os.unlink(path)


class TestStatusRunning(unittest.TestCase):
    def test_tool_result_marks_running_and_sets_current_tool(self):
        last_ts = BASE + 200
        events = [
            session_event(),
            {"type": "message", "timestamp": iso(BASE + 100),
             "message": {"role": "assistant", "model": "m",
                         "stopReason": "toolUse",
                         "content": [{"type": "toolCall", "name": "bash",
                                      "arguments": {"command": "ls"}}],
                         "usage": {"input": 1, "output": 1, "totalTokens": 2,
                                   "cost": {"total": 0}}}},
            {"type": "message", "timestamp": iso(last_ts),
             "message": {"role": "toolResult", "isError": False,
                         "toolCallId": "t1", "toolName": "bash",
                         "content": "ok"}},
        ]
        path = write_jsonl(events)
        try:
            s = parse_session_jsonl(path, now=last_ts + 5)
            self.assertEqual(s.status, "running")
            self.assertEqual(s.current_tool, "bash")
        finally:
            os.unlink(path)


class TestTimeFallback(unittest.TestCase):
    def test_long_silence_becomes_idle(self):
        last_ts = BASE + 200
        events = [
            session_event(),
            assistant_event("m", last_ts, {
                "input": 1, "output": 1, "totalTokens": 2,
                "cost": {"total": 0}}),
        ]
        path = write_jsonl(events)
        try:
            now = last_ts + 3 * STUCK_THRESHOLD + 100
            s = parse_session_jsonl(path, now=now)
            self.assertEqual(s.status, "idle")
        finally:
            os.unlink(path)

    def test_waiting_past_stuck_threshold_becomes_stuck(self):
        last_ts = BASE + 200
        events = [
            session_event(),
            assistant_event("m", last_ts, {
                "input": 1, "output": 1, "totalTokens": 2,
                "cost": {"total": 0}}),
        ]
        path = write_jsonl(events)
        try:
            now = last_ts + STUCK_THRESHOLD + 100
            s = parse_session_jsonl(path, now=now)
            self.assertEqual(s.status, "stuck")
        finally:
            os.unlink(path)

    def test_idle_threshold_boundary(self):
        # 恰好落在 IDLE 与 3*STUCK 之间 → stuck（沿用 V2 兜底逻辑）
        last_ts = BASE + 200
        events = [
            session_event(),
            assistant_event("m", last_ts, {
                "input": 1, "output": 1, "totalTokens": 2,
                "cost": {"total": 0}}),
        ]
        path = write_jsonl(events)
        try:
            now = last_ts + IDLE_THRESHOLD + 100
            s = parse_session_jsonl(path, now=now)
            self.assertEqual(s.status, "stuck")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
