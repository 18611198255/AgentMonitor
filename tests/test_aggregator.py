"""aggregator.py 单元测试（标准库 unittest，零第三方依赖）。

_aggregate 是纯逻辑，直接喂构造的 daily_usage 行；stats() 端到端则重定向
constants.DB_PATH + index.upsert_daily_usage 造数验证读 index 正确。
"""
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta

from agentmonitor import constants
from agentmonitor.aggregator import _aggregate, stats
from agentmonitor.index import init_db, upsert_daily_usage
from agentmonitor.parser import Session


def today_str():
    return date.today().strftime("%Y-%m-%d")


def days_ago(n):
    """今天往前 n 天的日期字符串（YYYY-MM-DD）。"""
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


def drow(date_str, project="p", tokens_in=0, tokens_out=0, cost=0.0,
         session_count=1):
    """构造一条 daily_usage 行（与 index.query_daily 返回结构一致）。"""
    return {"date": date_str, "project": project, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "cost": cost,
            "session_count": session_count}


class TestAggregate(unittest.TestCase):
    def test_today_week_total_sums(self):
        rows = [
            drow(days_ago(0), tokens_in=10, tokens_out=5, cost=0.001),
            drow(days_ago(6), tokens_in=20, tokens_out=10, cost=0.002),
            drow(days_ago(7), tokens_in=100, tokens_out=50, cost=0.1),
            drow("2020-01-01", tokens_in=1000, tokens_out=500, cost=1.0),
        ]
        res = _aggregate(rows)
        self.assertEqual(res["today"]["tokens_in"], 10)
        self.assertEqual(res["today"]["tokens_out"], 5)
        self.assertAlmostEqual(res["today"]["cost"], 0.001, places=6)

        self.assertEqual(res["week"]["tokens_in"], 30)
        self.assertEqual(res["week"]["tokens_out"], 15)
        self.assertAlmostEqual(res["week"]["cost"], 0.003, places=6)

        self.assertEqual(res["total"]["tokens_in"], 1130)
        self.assertEqual(res["total"]["tokens_out"], 565)
        self.assertAlmostEqual(res["total"]["cost"], 1.103, places=6)

    def test_week_boundary_inclusive_exclusive(self):
        rows = [
            drow(days_ago(0), tokens_in=1),
            drow(days_ago(6), tokens_in=1),
            drow(days_ago(7), tokens_in=1),
        ]
        res = _aggregate(rows)
        self.assertEqual(res["week"]["tokens_in"], 2)   # today + today-6 含
        self.assertEqual(res["total"]["tokens_in"], 3)  # today-7 只在 total

    def test_by_project_grouping_and_cost_desc(self):
        rows = [
            drow(days_ago(0), project="cheap", tokens_in=1, tokens_out=1,
                 cost=0.001),
            drow(days_ago(0), project="mid", tokens_in=1, tokens_out=1,
                 cost=0.002, session_count=1),
            drow(days_ago(0), project="mid", tokens_in=1, tokens_out=1,
                 cost=0.001, session_count=1),
            drow(days_ago(0), project="expensive", tokens_in=1, tokens_out=1,
                 cost=0.1),
        ]
        bp = _aggregate(rows)["by_project"]
        self.assertEqual([x["project"] for x in bp],
                         ["expensive", "mid", "cheap"])
        expensive, mid, cheap = bp[0], bp[1], bp[2]
        self.assertEqual(expensive["tokens_in"], 1)
        self.assertAlmostEqual(expensive["cost"], 0.1, places=6)
        self.assertEqual(mid["tokens_in"], 2)
        self.assertEqual(mid["tokens_out"], 2)
        self.assertAlmostEqual(mid["cost"], 0.003, places=6)
        self.assertEqual(mid["session_count"], 2)
        self.assertEqual(cheap["session_count"], 1)

    def test_trend_7d_fills_missing_dates_in_order(self):
        rows = [
            drow(days_ago(0), tokens_in=5, tokens_out=2, cost=0.001),
            drow(days_ago(3), tokens_in=3, tokens_out=1, cost=0.0005),
        ]
        trend = _aggregate(rows)["trend_7d"]
        self.assertEqual(len(trend), 7)
        self.assertEqual([t["date"] for t in trend],
                         [days_ago(i) for i in range(6, -1, -1)])
        self.assertEqual(trend[6]["tokens_in"], 5)   # 今天
        self.assertEqual(trend[3]["tokens_in"], 3)   # today-3
        self.assertEqual(trend[0]["tokens_in"], 0)   # today-6 缺失补 0
        self.assertEqual(trend[0]["tokens_out"], 0)
        self.assertEqual(trend[0]["cost"], 0.0)

    def test_empty_rows_all_zero(self):
        res = _aggregate([])
        self.assertEqual(res["today"],
                         {"tokens_in": 0, "tokens_out": 0, "cost": 0.0})
        self.assertEqual(res["week"],
                         {"tokens_in": 0, "tokens_out": 0, "cost": 0.0})
        self.assertEqual(res["total"],
                         {"tokens_in": 0, "tokens_out": 0, "cost": 0.0})
        self.assertEqual(res["by_project"], [])
        self.assertEqual(len(res["trend_7d"]), 7)
        for t in res["trend_7d"]:
            self.assertEqual(t["tokens_in"], 0)
            self.assertEqual(t["tokens_out"], 0)
            self.assertEqual(t["cost"], 0.0)


class TestStatsEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="am_agg_")
        self._orig_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmpdir, "test.sqlite")
        init_db()

    def tearDown(self):
        constants.DB_PATH = self._orig_db
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stats_reads_index_end_to_end(self):
        # 用「今天」的本地时间戳造一条会话，保证 daily_usage 落在今天
        today_epoch = datetime.strptime(
            f"{today_str()} 12:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
        s = Session(
            session_id="e2e-1", file_path="/tmp/f.jsonl", cwd="/tmp/p",
            project_name="projE2E", model="deepseek-v4", task="",
            last_active_at=today_epoch, created_at=today_epoch - 100,
            tokens_in=40, tokens_out=20, cost=0.0025, source="cli")
        upsert_daily_usage(s)

        res = stats()
        self.assertEqual(res["today"]["tokens_in"], 40)
        self.assertEqual(res["today"]["tokens_out"], 20)
        self.assertAlmostEqual(res["today"]["cost"], 0.0025, places=6)
        self.assertEqual(res["total"]["tokens_in"], 40)
        self.assertEqual(len(res["by_project"]), 1)
        self.assertEqual(res["by_project"][0]["project"], "projE2E")
        self.assertEqual(res["by_project"][0]["session_count"], 1)
        self.assertEqual(len(res["trend_7d"]), 7)
        self.assertEqual(res["trend_7d"][-1]["tokens_in"], 40)  # 今天


if __name__ == "__main__":
    unittest.main()
