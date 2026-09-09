"""AgentMonitor V3 —— 异常告警（规则 + macOS 通知 + 冷却）。

规则（纯逻辑 `_evaluate`，可单测）：
  1) stuck：会话 status == "stuck" → 疑似卡住；
  2) silent：live 会话静默超 SILENT_THRESHOLD 且 status != "waiting"；
  3) piweb_down：网页版 Pi (localhost:30141) 未运行。
冷却：同一 (rule, session_id) 在 ALERT_COOLDOWN 内不重复告警。
通知：osascript display notification，失败静默（不崩服务）。

`_port_open` 是局部实现（不 import server，避免 server 的告警循环 import 本模块
形成循环依赖）。
"""
import socket
import subprocess
import time
from dataclasses import dataclass

from agentmonitor.constants import ALERT_COOLDOWN, SILENT_THRESHOLD
from agentmonitor.detectors.cli import CLIDetector
from agentmonitor.detectors.web import WebDetector
from agentmonitor.merge import merge_sessions


@dataclass
class Alert:
    rule: str        # "stuck" | "piweb_down" | "silent"
    session_id: str  # piweb_down 用 ""
    message: str


_last_fired: dict = {}          # (rule, session_id) -> epoch，冷却用
_recent: list[dict] = []        # 最近告警（/api/alerts 用，最多存 50 条）
_MAX_RECENT = 50


def _port_open(port, host="127.0.0.1", timeout=0.4):
    """检测本机端口是否在监听（局部实现，避免 import server 的循环依赖）。"""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        ok = s.connect_ex((host, port)) == 0
        s.close()
        return ok
    except Exception:
        return False


def _evaluate(sessions, piweb_up: bool, now: float) -> list[Alert]:
    """纯逻辑 + 冷却：把会话/pi-web 状态转成告警列表（可单测，不碰真实系统）。"""
    alerts = []

    def _fire(rule, session_id, message):
        key = (rule, session_id)
        last = _last_fired.get(key, 0.0)
        if now - last >= ALERT_COOLDOWN:
            _last_fired[key] = now
            alerts.append(Alert(rule, session_id, message))

    for s in sessions:
        if s.status == "stuck":
            _fire("stuck", s.session_id, f"会话疑似卡住：{s.project_name}")
        if s.live and s.age_seconds > SILENT_THRESHOLD and s.status != "waiting":
            _fire("silent", s.session_id, f"会话静默异常：{s.project_name}")
    if not piweb_up:
        _fire("piweb_down", "", "网页版 Pi (localhost:30141) 未运行")

    return alerts


def notify(a: Alert) -> None:
    """macOS 通知；异常静默（通知失败不崩服务）。"""
    msg = a.message.replace('"', '\\"')
    script = f'display notification "{msg}" with title "AgentMonitor 告警"'
    try:
        subprocess.run(["osascript", "-e", script])
    except Exception:
        pass


def check() -> list[Alert]:
    """采集真实状态 → _evaluate → 记录 + 通知，返回本次告警列表。"""
    sessions = merge_sessions(CLIDetector().detect(), WebDetector().detect())
    piweb_up = _port_open(30141)
    now = time.time()
    alerts = _evaluate(sessions, piweb_up, now)
    for a in alerts:
        _recent.append({"rule": a.rule, "session_id": a.session_id,
                        "message": a.message, "ts": now})
        if len(_recent) > _MAX_RECENT:
            _recent.pop(0)
        notify(a)
    return alerts


def recent() -> list[dict]:
    """返回最近告警列表（/api/alerts 用）。"""
    return _recent
