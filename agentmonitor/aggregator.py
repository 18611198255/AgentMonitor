"""AgentMonitor V3 —— 成本/Token 聚合统计（F2）。

读 index.query_daily 全量 → 按 today / week / total / by_project / trend_7d 聚合。
_aggregate 是纯逻辑（不碰 IO），可独立单测；stats() 负责读 index 喂入。
"""
import datetime

from agentmonitor import index


def _sum(rows):
    """对一组 daily_usage 行求和，返回 {tokens_in, tokens_out, tokens_total, cost}。"""
    tokens_in = sum(r["tokens_in"] for r in rows)
    tokens_out = sum(r["tokens_out"] for r in rows)
    tokens_total = sum(r["tokens_total"] for r in rows)
    cost = round(sum((r["cost"] for r in rows), 0.0), 6)
    return {"tokens_in": int(tokens_in), "tokens_out": int(tokens_out),
            "tokens_total": int(tokens_total), "cost": cost}


def _aggregate(rows):
    today_date = datetime.date.today()
    today_str = today_date.strftime("%Y-%m-%d")
    week_start = (today_date - datetime.timedelta(days=6)).strftime("%Y-%m-%d")

    today_rows = [r for r in rows if r["date"] == today_str]
    week_rows = [r for r in rows if week_start <= r["date"] <= today_str]

    # by_project：按 project 分组求和，session_count 也累加
    by_project = {}
    for r in rows:
        acc = by_project.setdefault(r["project"], {
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "cost": 0.0, "session_count": 0})
        acc["tokens_in"] += r["tokens_in"]
        acc["tokens_out"] += r["tokens_out"]
        acc["tokens_total"] += r["tokens_total"]
        acc["cost"] += r["cost"]
        acc["session_count"] += r["session_count"]
    by_project_list = [
        {"project": p,
         "tokens_in": int(v["tokens_in"]),
         "tokens_out": int(v["tokens_out"]),
         "tokens_total": int(v["tokens_total"]),
         "cost": round(v["cost"], 6),
         "session_count": int(v["session_count"])}
        for p, v in by_project.items()
    ]
    by_project_list.sort(key=lambda x: x["cost"], reverse=True)

    # trend_7d：today-6 .. today 每天一条，缺失日补 0（跨 project 合并）
    daily = {}
    for r in rows:
        acc = daily.setdefault(r["date"],
                               {"tokens_in": 0, "tokens_out": 0,
                                "tokens_total": 0, "cost": 0.0})
        acc["tokens_in"] += r["tokens_in"]
        acc["tokens_out"] += r["tokens_out"]
        acc["tokens_total"] += r["tokens_total"]
        acc["cost"] += r["cost"]
    trend_7d = []
    for i in range(6, -1, -1):
        d = (today_date - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        if d in daily:
            trend_7d.append({
                "date": d,
                "tokens_in": int(daily[d]["tokens_in"]),
                "tokens_out": int(daily[d]["tokens_out"]),
                "tokens_total": int(daily[d]["tokens_total"]),
                "cost": round(daily[d]["cost"], 6),
            })
        else:
            trend_7d.append(
                {"date": d, "tokens_in": 0, "tokens_out": 0,
                 "tokens_total": 0, "cost": 0.0})

    return {
        "today": _sum(today_rows),
        "week": _sum(week_rows),
        "total": _sum(rows),
        "by_project": by_project_list,
        "trend_7d": trend_7d,
    }


def stats():
    """读 index.query_daily 全量 → _aggregate，供 /api/stats 直接使用。"""
    return _aggregate(index.query_daily("0000-00-00", "9999-12-31"))
