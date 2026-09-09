"""AgentMonitor V3 —— SQLite 派生索引（会话元数据 + 日聚合）。

jsonl 仍是唯一事实源；本模块把解析出的 Session 写进 SQLite，供历史搜索(F4)
与成本聚合(F2)快速查询。SQLite 是派生数据、可随时 `rebuild()` 重建。

表结构照 `docs/PRD_20260908.md` §6.2，另加 task 列。
"""
import glob
import os
import sqlite3
from datetime import datetime

from agentmonitor import constants
from agentmonitor.parser import Session, parse_session_jsonl

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id   TEXT PRIMARY KEY,
  file_path    TEXT,
  cwd          TEXT,
  project      TEXT,
  model        TEXT,
  task         TEXT,
  created_at   REAL,
  last_active  REAL,
  status       TEXT,
  tokens_in    INTEGER DEFAULT 0,
  tokens_out   INTEGER DEFAULT 0,
  cost         REAL DEFAULT 0,
  source       TEXT
);
CREATE TABLE IF NOT EXISTS daily_usage (
  date          TEXT,
  project       TEXT,
  tokens_in     INTEGER DEFAULT 0,
  tokens_out    INTEGER DEFAULT 0,
  cost          REAL DEFAULT 0,
  session_count INTEGER DEFAULT 0,
  PRIMARY KEY (date, project)
);
"""


def _connect():
    return sqlite3.connect(constants.DB_PATH)


def init_db():
    """建表（IF NOT EXISTS），幂等。"""
    con = _connect()
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


def upsert_session(s: Session):
    """INSERT OR REPLACE 一条会话到 sessions 表。"""
    con = _connect()
    try:
        con.execute(
            "INSERT OR REPLACE INTO sessions "
            "(session_id, file_path, cwd, project, model, task, created_at, "
            " last_active, status, tokens_in, tokens_out, cost, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s.session_id, s.file_path, s.cwd, s.project_name, s.model, s.task,
             s.created_at, s.last_active_at, s.status, s.tokens_in,
             s.tokens_out, s.cost, s.source))
        con.commit()
    finally:
        con.close()


def upsert_daily_usage(s: Session):
    """累加一条会话到 daily_usage（按本地日期 + project）。"""
    date = datetime.fromtimestamp(s.last_active_at).strftime("%Y-%m-%d")
    con = _connect()
    try:
        con.execute(
            "INSERT INTO daily_usage "
            "(date, project, tokens_in, tokens_out, cost, session_count) "
            "VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(date, project) DO UPDATE SET "
            "tokens_in = tokens_in + excluded.tokens_in, "
            "tokens_out = tokens_out + excluded.tokens_out, "
            "cost = cost + excluded.cost, "
            "session_count = session_count + 1",
            (date, s.project_name, s.tokens_in, s.tokens_out, s.cost))
        con.commit()
    finally:
        con.close()


def search_sessions(q: str = "", project: str = ""):
    """按关键词 / 项目过滤会话，返回 dict 列表（按 last_active 降序）。"""
    con = _connect()
    try:
        con.row_factory = sqlite3.Row
        sql = "SELECT * FROM sessions"
        conds, params = [], []
        if q:
            like = f"%{q}%"
            conds.append(
                "(project LIKE ? OR cwd LIKE ? OR model LIKE ? OR task LIKE ?)")
            params += [like, like, like, like]
        if project:
            conds.append("project = ?")
            params.append(project)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY last_active DESC"
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def query_daily(start: str, end: str):
    """按日期区间（含端点）查日聚合，返回 dict 列表。"""
    con = _connect()
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT date, project, tokens_in, tokens_out, cost, session_count "
            "FROM daily_usage WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def rebuild():
    """清空两表，扫描 SESSIONS_DIR 全部 jsonl 重建索引。"""
    init_db()
    con = _connect()
    try:
        con.execute("DELETE FROM sessions")
        con.execute("DELETE FROM daily_usage")
        con.commit()
    finally:
        con.close()
    pattern = os.path.join(constants.SESSIONS_DIR, "**", "*.jsonl")
    for path in sorted(glob.glob(pattern, recursive=True)):
        s = parse_session_jsonl(path)
        if s is not None:
            upsert_session(s)
            upsert_daily_usage(s)
