"""合并层：把 CLI/网页版探测器结果 + jsonl 落盘信号合并成活跃会话列表。

移植自 V2 `monitor_server.py` 的 `scan_sessions`（活跃模式），把纠缠在单一
函数里的两段启发式逻辑归位：
  1) CLI 段：每个运行中的 pi 进程 → 按其 cwd 匹配近期 jsonl（或占位）；
  2) 网页版/无进程段：未被 CLI 认领的 jsonl，按「真活跃 / 挂着」双层判定。

纯逻辑：只读磁盘 jsonl + 调用 parser，不碰探测器系统调用（pgrep/lsof/HTTP）。
"""
import glob
import os
import time

from agentmonitor import constants
from agentmonitor.detectors.base import SessionRef
from agentmonitor.parser import Session, parse_session_jsonl


def _is_fresh(s: Session, now: float) -> bool:
    """jsonl mtime 是否仍在终端会话「长跑等输入」窗口内。"""
    try:
        return (now - os.path.getmtime(s.file_path)) <= constants.USER_VISIBLE_WINDOW
    except OSError:
        return False  # jsonl 被并发删除，视为不新鲜


def _make_placeholder(cwd: str, pid: str) -> Session:
    """没有会话文件的运行中 pi 进程 → 占位条目（新开/等待开始的小窗）。"""
    cwd_trim = cwd.rstrip("/")
    name = os.path.basename(cwd_trim) if cwd_trim else "终端"
    return Session(
        session_id=f"winc-{pid}",
        file_path="",
        cwd=cwd,
        project_name=name,
        status="none",
        activity=f"Pi 已启动，正在等待你输入第一个任务 (小窗 {pid})",
        source="cli",
        live=True,
        in_terminal=True,
        jumpable=True,
        pid=pid,
    )


def merge_sessions(
    cli_refs: list[SessionRef],
    web_refs: list[SessionRef],
    now: float | None = None,
) -> list[Session]:
    """合并 CLI/网页版探测器结果 + jsonl → 活跃会话列表（按 age_seconds 升序）。"""
    now = now or time.time()
    web_running_ids = {ref.session_id for ref in web_refs}

    # 1) 扫 jsonl，解析所有近期会话（仅 mtime 在 RECENT_WINDOW 内）
    sessions_by_cwd = {}
    all_sessions = []
    for path in glob.glob(
        os.path.join(constants.SESSIONS_DIR, "**", "*.jsonl"), recursive=True
    ):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue  # jsonl 在 glob 与 getmtime 之间被删，跳过
        if now - mtime > constants.RECENT_WINDOW:
            continue
        s = parse_session_jsonl(path, now)
        if not s:
            continue
        sessions_by_cwd.setdefault(s.cwd, []).append(s)
        all_sessions.append(s)

    seen = set()   # 已认领的 file_path
    out = []

    # 2) CLI 段：每个运行中的 pi 进程 → 匹配其 cwd 下的 jsonl
    for ref in cli_refs:
        cands = [s for s in sessions_by_cwd.get(ref.cwd, []) if _is_fresh(s, now)]
        if not cands:
            out.append(_make_placeholder(ref.cwd, ref.pid))
            continue
        best = min(cands, key=lambda s: s.age_seconds)  # 取最新
        best.in_terminal = True
        best.live = True
        best.jumpable = True
        best.source = "cli"
        best.pid = ref.pid
        # 状态重分类：进程在跑 + 解析成 stuck/idle → 实际是等你输入
        if best.status in ("stuck", "idle"):
            best.status = "waiting"
            if not best.activity.startswith("等待"):
                best.activity = "等待你的输入"
        out.append(best)
        seen.add(best.file_path)

    # 3) 网页版/无进程段：未被 CLI 认领的 jsonl，按 真活/挂着 双层判定
    for s in all_sessions:
        if s.file_path in seen:
            continue
        is_live = bool(web_running_ids) and (s.session_id in web_running_ids)
        try:
            mtime = os.path.getmtime(s.file_path)
        except OSError:
            continue  # jsonl 被并发删除，跳过
        if is_live:                       # 真活：引擎正在调 LLM
            if now - mtime > constants.ACTIVE_WINDOW:
                continue
            s.live = True
            if s.status in ("stuck", "idle"):
                s.status = "running"
            s.source = "web"
            out.append(s)
            seen.add(s.file_path)
        else:                             # 挂着：刚答完/过渡期，非 idle 就保留显示
            if now - mtime > constants.PIWEB_GRACE_WINDOW:
                continue
            if s.status == "idle":
                continue
            s.live = False
            s.source = "web"
            out.append(s)
            seen.add(s.file_path)

    out.sort(key=lambda s: s.age_seconds)
    return out
