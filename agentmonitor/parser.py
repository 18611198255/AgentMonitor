"""AgentMonitor V3 —— jsonl → Session 解析器（唯一事实源）。

移植自 V2 `monitor_server.py`（`parse_session_jsonl` / `infer_project` /
`home_cwd_title` / `_session_id_from_path`），并扩展 token 入/出拆分
（`tokens_in` / `tokens_out`，V2 只记了 total）。

字段名与结构以 `docs/jsonl格式参考_20260909.md` 为准，勿凭假设改动。
"""
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from agentmonitor.constants import HOME, IDLE_THRESHOLD, STUCK_THRESHOLD


@dataclass
class Session:
    session_id: str
    file_path: str
    cwd: str
    project_name: str
    model: str = ""
    status: str = "idle"          # running | waiting | stuck | idle
    activity: str = ""
    age_seconds: float = 0.0      # 距最后一条消息的秒数
    last_active_at: float = 0.0   # 最后一条消息的 epoch 秒
    created_at: float = 0.0       # 会话创建 epoch 秒（session.timestamp，缺省文件 mtime）
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    cost: float = 0.0
    current_tool: str = ""
    exchanges: int = 0
    live: bool = False
    in_terminal: bool = False
    jumpable: bool = False
    source: str = ""              # cli | web
    pid: str = ""


# ---------- 时间解析 ----------

def parse_ts_to_epoch(ts):
    """ISO 时间戳(含 Z/UTC 后缀) → epoch 秒。"""
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
    except Exception:
        try:
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
        except Exception:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
    return dt.astimezone(timezone.utc).timestamp()


# ---------- 会话 id ----------

def session_id_from_path(path):
    """jsonl 文件路径 → session id（文件名 `_` 后半段，去掉 `.jsonl`，36 位 hex）。"""
    base = os.path.basename(path or "")
    if "_" in base:
        return base.split("_", 1)[1].rsplit(".", 1)[0]
    return ""


# ---------- 项目文件夹推断（移植 V2） ----------

_SUBROOTS = {
    os.path.join(HOME, d)
    for d in ("Desktop", "Documents", "Downloads", "Dev", "dev", "code",
              "Projects", "项目", "工作", "Work")
}
_NOISY_DIRS = {".pi", ".config", ".npm", ".cache", ".local", ".git", "Library",
               "System", "Applications", "__pycache__", ".Trash", "venv",
               "node_modules", ".agents", ".claude", ".cursor", ".ssh", ".Trash"}
_PROJ_EXT = {".md", ".py", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx",
             ".json", ".yaml", ".yml", ".csv", ".xlsx", ".xls", ".docx", ".sh",
             ".ipynb", ".txt", ".css", ".svg", ".tsv", ".sql", ".toml", ".vue",
             ".swift", ".java", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cpp"}


def _noisy_dir(p):
    """路径里是否踩到系统/临时/工具目录（这些不算项目）。"""
    if not p.startswith("/"):
        return True
    for part in p.split("/"):
        if part in _NOISY_DIRS:
            return True
    if p.startswith(("/tmp", "/private/var", "/System", "/usr/", "/Volumes/")):
        return True
    return False


def _proj_root_of(abspath):
    """把文件绝对路径向上提，返回它所属的‘项目顶层文件夹根’；找不到返回 None。"""
    d = os.path.dirname(os.path.abspath(abspath))
    cur = d
    while cur and cur != "/":
        if _noisy_dir(cur):
            return None
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        par = os.path.dirname(cur)
        if par in _SUBROOTS or par == HOME:
            return cur
        cur = par
    return None


def infer_project(cwd, tool_paths):
    """从最近读/写的文件路径投票出该项目根（cwd 仅作参考与兜底）。"""
    votes = {}
    recency = []
    cache = {}
    for p in tool_paths[-80:]:
        ext = os.path.splitext(p)[1].lower()
        if ext not in _PROJ_EXT:
            continue
        root = cache.get(p)
        if root is None:
            if not os.path.exists(p):
                cache[p] = False
                continue
            r = _proj_root_of(p)
            root = r if r else None
            cache[p] = root
        if not root:
            continue
        key = os.path.normpath(root)
        votes[key] = votes.get(key, 0) + 1
        recency.append(key)
    if not votes:
        return None
    best_cnt = max(votes.values())
    cands = {k for k, v in votes.items() if v == best_cnt}
    if len(cands) == 1:
        return cands.pop()
    for k in recency:
        if k in cands:
            return k
    return cands.pop()


def home_cwd_title(first_user):
    """给<家目录 cwd、没读到具体工程文件>的会话一个可读、能区分的任务标题。"""
    tokens = ' '.join((first_user or '').split())
    if not tokens:
        return ''
    s = tokens
    # 1) 剥掉开头紧跟的文件/路径引用，那不是任务名。
    for _ in range(6):
        bx = s.strip("'\" 　")
        w = (bx.split(None, 1) or [None])[0]
        if not w:
            break
        path_like = (w[0] in '/~@' or '/' in w
                     or w.endswith(('.html', '.md', '.txt', '.json', '.pdf',
                                    '.docx', '.py', '.csv')))
        if path_like:
            s = bx[len(w):].lstrip("'\" ,、。　")
        else:
            s = bx
            break
    # 2) 只取第一个自然停顿前的子句。
    seg = re.split('[。！？?；:\n]', s, maxsplit=1)[0].strip()
    if seg:
        s = seg
    # 3) 反复剥常见客套引导。
    lead = ('请你帮我', '你帮我一下', '你帮我', '帮我一下', '帮我看看',
            '麻烦你帮我', '麻烦你', '帮忙', '帮我整理', '请帮我', '帮我',
            '可不可以', '能不能', '可以帮我', '可以', '请问', '看看', '请',
            '你好', '嗨', 'hi', 'hello')
    for _ in range(4):
        mv = False
        for lp in lead:
            if s.startswith(lp):
                s = s[len(lp):].lstrip()
                mv = True
                break
        if not mv:
            break
    s = ' '.join(s.split())
    # 4) 别把拉丁词掐半，截 18 字左右。
    if len(s) > 18:
        cut = s[:18]
        if cut and cut[-1].isalnum() and len(s) > 18 and s[18].isalnum():
            sp = cut.rfind(' ')
            if sp >= 4:
                cut = cut[:sp]
        s = cut
    s = s.strip(' ,、。：;')
    return s if s else tokens[:60]


# ---------- 主解析 ----------

def parse_session_jsonl(path, now=None):
    if now is None:
        now = time.time()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return None

    mtime = os.path.getmtime(path)

    session_id = ""
    cwd = ""
    display_name = ""
    model = ""
    model_change_model = ""
    created_at = mtime
    last_active_at = mtime

    last_status = "idle"
    last_activity = ""
    last_snippet = ""
    last_tool = ""
    tokens_in = 0.0
    tokens_out = 0.0
    tokens_total = 0.0
    cost = 0.0
    exchanges = 0
    first_user = ""
    tool_paths = []
    uwork = False  # 自最近一次用户输入后是否跑过工具/延续推进

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            e = json.loads(raw)
        except Exception:
            continue
        t = e.get("type", "")
        if t == "session":
            session_id = e.get("id", "")
            cwd = e.get("cwd", "")
            ts = e.get("timestamp")
            if ts:
                try:
                    created_at = parse_ts_to_epoch(ts)
                except Exception:
                    pass
        elif t == "session_info":
            n = e.get("name", "")
            if n:
                display_name = n
        elif t == "model_change":
            mid = e.get("modelId", "")
            if mid:
                model_change_model = mid
        elif t == "message":
            m = e.get("message", {}) or {}
            role = m.get("role", "")
            ts = e.get("timestamp")
            if ts:
                try:
                    last_active_at = parse_ts_to_epoch(ts)
                except Exception:
                    pass

            if role == "assistant":
                exchanges += 1
                usage = m.get("usage", {}) or {}
                tokens_in += float(usage.get("input", 0) or 0)
                tokens_out += float(usage.get("output", 0) or 0)
                tokens_total += float(usage.get("totalTokens", 0) or 0)
                cost += float((usage.get("cost") or {}).get("total", 0) or 0)
                if m.get("model"):
                    model = m["model"]
                stop = m.get("stopReason", "")
                blocks = m.get("content") or []
                was_tool = False
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "toolCall":
                        was_tool = True
                        last_tool = b.get("name", "")
                        args = b.get("arguments") or {}
                        if isinstance(args, dict):
                            pth = args.get("path") or args.get("file")
                            if isinstance(pth, str) and pth and pth.startswith("/"):
                                tool_paths.append(pth)
                    elif isinstance(b, dict) and b.get("type") == "text":
                        last_snippet = b.get("text", "") or last_snippet
                if was_tool:
                    uwork = True
                    last_status = "running"
                elif stop == "error":
                    last_status = "stuck"
                elif stop == "toolUse":
                    uwork = True
                    last_status = "running"
                else:
                    last_status = "running" if uwork else "waiting"
                if last_snippet and not was_tool:
                    last_activity = last_snippet
            elif role == "toolResult":
                is_err = bool(m.get("isError"))
                uwork = True
                last_status = "stuck" if is_err else "running"
                if m.get("toolName"):
                    last_tool = m["toolName"]
                usage = m.get("usage", {}) or {}
                tokens_in += float(usage.get("input", 0) or 0)
                tokens_out += float(usage.get("output", 0) or 0)
                tokens_total += float(usage.get("totalTokens", 0) or 0)
                cost += float((usage.get("cost") or {}).get("total", 0) or 0)
            elif role == "user":
                c = m.get("content")
                utxt = ""
                if isinstance(c, str):
                    utxt = c
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "text":
                            utxt = b.get("text", "")
                            break
                if utxt and not first_user:
                    first_user = utxt

    if not session_id:
        return None

    since = now - last_active_at
    if since > IDLE_THRESHOLD:
        if since > 3 * STUCK_THRESHOLD:
            last_status = "idle"
        else:
            last_status = "stuck"
    elif last_status in ("running", "waiting"):
        if since > STUCK_THRESHOLD:
            last_status = "stuck"

    if last_status == "waiting":
        act = "等待你的输入"
    elif last_status == "running":
        act = last_activity or (f"正在执行 {last_tool}…" if last_tool else "处理中…")
    elif last_status == "stuck":
        act = last_activity or "可能停滞，需检查"
    else:
        act = ("[已暂停] " + last_activity) if last_activity else "空闲 / 会话结束"

    # 项目名优先级：infer_project > display_name > home_cwd_title(first_user) > cwd 末段
    proj_path = infer_project(cwd, tool_paths)
    proj_name = os.path.basename(proj_path.rstrip("/")) if proj_path else ""
    cwd_trim = cwd.rstrip("/")
    cwd_base = os.path.basename(cwd_trim) if cwd_trim else cwd
    is_home_cwd = (cwd_trim == HOME.rstrip("/")
                   or cwd_trim == HOME.rstrip("/") + "/" + os.environ.get("USER", ""))
    if proj_name and proj_path != cwd_trim:
        project_name = proj_name
    elif display_name:
        project_name = display_name
    elif is_home_cwd:
        project_name = home_cwd_title(first_user) or cwd_base
    else:
        project_name = cwd_base

    return Session(
        session_id=session_id,
        file_path=path,
        cwd=cwd,
        project_name=project_name,
        model=model or model_change_model,
        status=last_status,
        activity=act,
        age_seconds=since,
        last_active_at=last_active_at,
        created_at=created_at,
        tokens_in=int(tokens_in),
        tokens_out=int(tokens_out),
        tokens_total=int(tokens_total),
        cost=cost,
        current_tool=last_tool,
        exchanges=exchanges,
    )
