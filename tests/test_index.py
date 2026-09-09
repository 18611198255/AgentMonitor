"""index.py 单元测试（标准库 unittest，零第三方依赖）。

把 constants.DB_PATH 重定向到临时 sqlite 文件，构造 Session 喂入覆盖索引读写；
rebuild 测试再把 constants.SESSIONS_DIR 重定向到临时目录，写真实 jsonl 重建。
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from agentmonitor import constants
from agentmonitor.index import (
    init_db,
    query_daily,
    rebuild,
    search_sessions,
    upsert_daily_usage,
    upsert_session,
)
from agentmonitor.parser import Session

SID = "01a08172-be76-7748-a2fa-1f78011d065d"
BASE = 1750000000.0  # 固定基准 epoch，保证测试可复现


def iso(epoch):
    """epoch 秒 → ISO 时间戳（毫秒 + Z），与 jsonl 落盘格式一致。"""
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )


def epoch_local(date_str, hour=12):
    """本地日期 → epoch 秒（naive → 本地时间，与 index 内部一致）。"""
    return datetime.strptime(
        f"{date_str} {hour:02d}:00:00", "%Y-%m-%d %H:%M:%S").timestamp()


def make_session(session_id=SID, project="projA", task="", model="deepseek-v4",
                 tokens_in=100, tokens_out=50, cost=0.001,
                 last_active_at=BASE, cwd="/tmp/projA", source="cli"):
    return Session(
        session_id=session_id,
        file_path="/tmp/fake.jsonl",
        cwd=cwd,
        project_name=project,
        model=model,
        task=task,
        last_active_at=last_active_at,
        created_at=last_active_at - 100,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        source=source,
    )


class IndexTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="am_index_")
        self._orig_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmpdir, "test.sqlite")
        init_db()

    def tearDown(self):
        constants.DB_PATH = self._orig_db
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestUpsertAndSearch(IndexTestBase):
    def test_upsert_then_search_returns_fields(self):
        upsert_session(make_session(
            session_id=SID, project="projA", task="写个脚本",
            model="deepseek-v4", tokens_in=100, tokens_out=50, cost=0.001))
        rows = search_sessions()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["session_id"], SID)
        self.assertEqual(r["project"], "projA")
        self.assertEqual(r["model"], "deepseek-v4")
        self.assertEqual(r["task"], "写个脚本")
        self.assertEqual(r["cwd"], "/tmp/projA")
        self.assertEqual(r["tokens_in"], 100)
        self.assertEqual(r["tokens_out"], 50)
        self.assertAlmostEqual(r["cost"], 0.001, places=6)
        self.assertEqual(r["source"], "cli")

    def test_search_by_project_filters(self):
        upsert_session(make_session(session_id="s1", project="projX"))
        upsert_session(make_session(session_id="s2", project="projY"))
        rows = search_sessions(project="projX")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], "s1")

    def test_search_by_q_matches_task_model_cwd(self):
        upsert_session(make_session(
            session_id="s1", task="修复登录 bug", model="deepseek-v4",
            cwd="/tmp/projA", project="projA"))
        self.assertEqual(len(search_sessions(q="登录")), 1)       # task
        self.assertEqual(len(search_sessions(q="deepseek")), 1)   # model
        self.assertEqual(len(search_sessions(q="projA")), 1)      # cwd
        self.assertEqual(len(search_sessions(q="不存在")), 0)

    def test_search_no_args_returns_all_sorted_desc(self):
        early = make_session(session_id="early", last_active_at=BASE)
        late = make_session(session_id="late", last_active_at=BASE + 500)
        upsert_session(early)
        upsert_session(late)
        rows = search_sessions()
        self.assertEqual([r["session_id"] for r in rows], ["late", "early"])


class TestDailyUsage(IndexTestBase):
    def test_accumulates_same_date_project(self):
        d = epoch_local("2026-09-01")
        upsert_daily_usage(make_session(
            tokens_in=100, tokens_out=50, cost=0.001, last_active_at=d,
            project="A"))
        upsert_daily_usage(make_session(
            tokens_in=40, tokens_out=20, cost=0.0005, last_active_at=d + 60,
            project="A"))
        upsert_daily_usage(make_session(
            tokens_in=7, tokens_out=3, cost=0.0001, last_active_at=d,
            project="B"))
        rows = query_daily("2026-09-01", "2026-09-01")
        self.assertEqual(len(rows), 2)
        a = next(r for r in rows if r["project"] == "A")
        b = next(r for r in rows if r["project"] == "B")
        self.assertEqual(a["tokens_in"], 140)
        self.assertEqual(a["tokens_out"], 70)
        self.assertAlmostEqual(a["cost"], 0.0015, places=6)
        self.assertEqual(a["session_count"], 2)
        self.assertEqual(b["tokens_in"], 7)
        self.assertEqual(b["session_count"], 1)

    def test_query_daily_range_inclusive(self):
        for i, day in enumerate(
                ["2026-09-01", "2026-09-15", "2026-09-30", "2026-10-05"]):
            upsert_daily_usage(make_session(
                tokens_in=1, tokens_out=0, cost=0,
                last_active_at=epoch_local(day), project=f"p{i}"))
        rows = query_daily("2026-09-01", "2026-09-30")
        self.assertEqual([r["date"] for r in rows],
                         ["2026-09-01", "2026-09-15", "2026-09-30"])


class TestRebuild(IndexTestBase):
    def test_rebuild_scans_jsonl_dir(self):
        sess_dir = tempfile.mkdtemp(prefix="am_sess_")
        orig_sess = constants.SESSIONS_DIR
        constants.SESSIONS_DIR = sess_dir
        try:
            ts = BASE
            events = [
                {"type": "session", "version": 3, "id": SID,
                 "timestamp": iso(ts), "cwd": "/tmp/projX"},
                {"type": "message", "timestamp": iso(ts + 100),
                 "message": {"role": "user",
                             "content": "帮我修复登录 bug"}},
                {"type": "message", "timestamp": iso(ts + 200),
                 "message": {"role": "assistant", "model": "deepseek-v4",
                             "stopReason": "endTurn",
                             "content": [{"type": "text", "text": "好的"}],
                             "usage": {"input": 30, "output": 10,
                                       "totalTokens": 40,
                                       "cost": {"total": 0.0005}}}},
            ]
            path = os.path.join(
                sess_dir, f"2026-09-01T09-20-51-343Z_{SID}.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")

            rebuild()

            rows = search_sessions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["session_id"], SID)
            self.assertEqual(rows[0]["project"], "projX")
            self.assertEqual(rows[0]["tokens_in"], 30)
            self.assertEqual(rows[0]["task"], "帮我修复登录 bug")

            daily = query_daily("2000-01-01", "2100-01-01")
            self.assertEqual(len(daily), 1)
            self.assertEqual(daily[0]["project"], "projX")
            self.assertEqual(daily[0]["session_count"], 1)
            self.assertEqual(daily[0]["tokens_in"], 30)
        finally:
            constants.SESSIONS_DIR = orig_sess
            shutil.rmtree(sess_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
