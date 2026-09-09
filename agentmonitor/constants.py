"""AgentMonitor V3 全局常量（值取自 V2 monitor_server.py，以代码为准）。"""
import os

HOME = os.path.expanduser("~")
SESSIONS_DIR = os.path.join(HOME, ".pi", "agent", "sessions")

PIWEB_URL = "http://127.0.0.1:30141"
PORT = 8571

# SQLite 派生索引（jsonl 仍是唯一事实源）
DB_PATH = os.path.join(HOME, ".pi", "agent", "agentmonitor_v3.sqlite")

# 会话状态判定阈值（秒，与 V2 一致）
STUCK_THRESHOLD = 600        # 10 分钟无活动 → 疑似卡住
IDLE_THRESHOLD = 2 * STUCK_THRESHOLD
RECENT_WINDOW = 24 * 3600    # 近 24h 视为活跃
PROCESSLESS_STALE = 2 * 3600  # 无进程会话：距最近写入最长容忍 2 小时
USER_VISIBLE_WINDOW = 90 * 60  # 终端会话"长跑等输入"窗口
PIWEB_GRACE_WINDOW = 30 * 60   # 网页版"挂着"过渡窗口（非 idle 保留显示）
ACTIVE_WINDOW = 24 * 3600      # 网页版真活跃会话 mtime 最长容忍

# 探测器缓存（秒，与 V2 一致）
CLI_CACHE_TTL = 3.0
WEB_CACHE_TTL = 5.0

# 告警阈值与冷却（秒）
SILENT_THRESHOLD = 15 * 60   # live 会话静默超时 → agent 静默告警
ALERT_COOLDOWN = 10 * 60     # 同规则冷却期，避免刷屏
