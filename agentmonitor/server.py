"""AgentMonitor V3 —— HTTP 服务层（路由 + iTerm 跳转 + 安全白名单）。

整个工具的对外入口：`python3 -m agentmonitor.server [port]`（默认 8571）。

移植自 V2 `monitor_server.py` 的 `Handler`（`_send`/`do_GET`/`_serve_dashboard`）、
`jump_to_path`（iTerm 跳转）、`_path_within_sessions`（路径校验）、`_port_open`
（端口健康检查）。V2 是单文件（dashboard.html 与代码同目录），V3 是包：
server.py 在 agentmonitor/ 内，dashboard.html 在项目根，故 serve 静态文件用
项目根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))。

路由白名单：除看板页与 /api/* 外一律 404，防泄露源码/.git 等。
零第三方依赖（http.server / subprocess / socket / urllib / osascript）。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agentmonitor import actions, aggregator, alerts, index
from agentmonitor.constants import PORT, SESSIONS_DIR
from agentmonitor.detectors.cli import CLIDetector
from agentmonitor.detectors.web import WebDetector
from agentmonitor.merge import merge_sessions

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- 路径校验 / 端口健康检查 ----------

def _path_within_sessions(target):
    """校验 target 是否落在 SESSIONS_DIR 内（防任意文件读取）。"""
    if not target:
        return False
    try:
        real = os.path.realpath(target)
        base = os.path.realpath(SESSIONS_DIR)
        return real == base or real.startswith(base + os.sep)
    except Exception:
        return False


def _port_open(port, host="127.0.0.1", timeout=0.4):
    """检测本机端口是否在监听（用于探测 pi-web 等子服务是否在跑）。"""
    try:
        s = socket.socket()
        s.settimeout(timeout)
        ok = s.connect_ex((host, port)) == 0
        s.close()
        return ok
    except Exception:
        return False


# ---------- iTerm 跳转 ----------

def jump_to_path(target_file):
    """根据 jsonl 路径找到匹配的 iTerm2 会话并 select。

    返回 ok:<uid> / iterm_script_failed / read_failed / no_session / jump_failed:<err>
    """
    # 1) pi 进程 → tty（跳转需实时 tty 匹配，跳过 3s 缓存）
    refs = CLIDetector().detect(force=True)

    # 2) iTerm sessions tty -> uniqueID
    script = (
        'tell application "iTerm2"\n'
        '    set out to ""\n'
        '    repeat with w in windows\n'
        '        repeat with t in tabs of w\n'
        '            repeat with s in sessions of t\n'
        '                try\n'
        '                    set out to out & tty of s & "|" & (the unique id of s) & linefeed\n'
        '                end try\n'
        '            end repeat\n'
        '        end repeat\n'
        '    end repeat\n'
        '    return out\n'
        'end tell'
    )
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=8)
        iterm_map = {}
        for line in r.stdout.splitlines():
            if "|" in line:
                tt, uid = line.split("|", 1)
                iterm_map[tt.strip()] = uid.strip()
    except Exception:
        return "iterm_script_failed"

    # 3) 目标文件所在 cwd（找第一条 type=="session" 的 cwd）
    target_cwd = ""
    try:
        with open(target_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    h = json.loads(line)
                except Exception:
                    continue
                if h.get("type") == "session":
                    target_cwd = h.get("cwd", "")
                    break
    except Exception:
        return "read_failed"

    # 4) 用 cwd 匹配 pi 进程 → tty → uniqueID
    matched_uid = None
    for ref in refs:
        if ref.cwd != target_cwd:
            continue
        # 统一 tty 格式：info.tty 为 'ttysXXX' 或 '/dev/ttysXXX'
        tty = ref.tty or ""
        tty2 = tty if tty.startswith("/dev/") else ("/dev/" + tty if tty else "")
        if tty2 and tty2 in iterm_map:
            matched_uid = iterm_map[tty2]
            break

    if not matched_uid:
        return "no_session"

    jump = (
        'tell application "iTerm2"\n'
        '    activate\n'
        '    repeat with w in windows\n'
        '        repeat with t in tabs of w\n'
        '            repeat with s in sessions of t\n'
        '                try\n'
        f'                    if (the unique id of s) = "{matched_uid}" then select s\n'
        '                end try\n'
        '            end repeat\n'
        '        end repeat\n'
        '    end repeat\n'
        'end tell'
    )
    try:
        subprocess.run(["osascript", "-e", jump], capture_output=True, timeout=8)
        return f"ok:{matched_uid}"
    except Exception as e:
        return f"jump_failed:{e}"


# ---------- HTTP 服务 ----------

def _serialize_session(s):
    """Session → dict（字段名 = dataclass 字段名），cost 四舍五入避免浮点噪声。"""
    d = asdict(s)
    d["cost"] = round(d["cost"], 6)
    return d


def _row_to_dict(row):
    """index 行 → dict，字段名与 Session dataclass 完全一致（历史/搜索归一化）。

    index 列名 project/last_active → Session 字段 project_name/age_seconds，
    让看板只消费一种形状（活跃模式 asdict 与历史模式同一契约）。
    """
    last_active = row["last_active"]
    return {
        "session_id": row["session_id"], "file_path": row["file_path"], "cwd": row["cwd"],
        "project_name": row["project"], "model": row["model"], "task": row["task"],
        "status": row["status"], "activity": "", "current_tool": "",
        "age_seconds": int(time.time() - last_active), "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"], "tokens_total": row["tokens_total"],
        "cost": round(row["cost"], 6), "exchanges": 0, "live": False, "in_terminal": False,
        "jumpable": False, "source": row["source"], "pid": "",
    }


class Handler(SimpleHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", extra_headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        # API/HTML 始终禁止浏览器缓存旧数据（用户曾报"全关了还显示老任务"，原根因）
        if ctype.startswith("text/html") or ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/sessions":
            q = parse_qs(parsed.query)
            query = q.get("q", [""])[0]
            project = q.get("project", [""])[0]
            all_flag = q.get("all", [""])[0]
            if query or project or all_flag == "1":
                # 历史/搜索模式：走 SQLite 索引（q / project / all=1）
                rows = index.search_sessions(query, project)
                self._send(200, {"ok": True, "time": time.time(),
                                 "sessions": [_row_to_dict(r) for r in rows]})
            else:
                # 活跃模式：合并 CLI/网页版探测器结果（原有逻辑不变）
                sessions = merge_sessions(CLIDetector().detect(), WebDetector().detect())
                # 运行中增量写索引：真实会话（有 jsonl 的）upsert 进 sessions，
                # daily_usage 从 sessions 全量重算（幂等，避免累加失真）。
                for s in sessions:
                    if not s.file_path:
                        continue  # 占位会话（无 jsonl）不进索引
                    index.upsert_session(s)
                index.refresh_daily_usage()
                self._send(200, {"ok": True, "time": time.time(),
                                 "sessions": [_serialize_session(s) for s in sessions]})
        elif path == "/api/pi-procs":
            # 当前真实在跑的 pi CLI 进程（pid + tty + cwd），绕过缓存
            refs = CLIDetector().detect(force=True)
            self._send(200, {"ok": True, "procs": [asdict(r) for r in refs]})
        elif path == "/api/jump":
            q = parse_qs(parsed.query)
            target = q.get("file", [""])[0]
            # 只允许跳转 SESSIONS_DIR 下的会话文件，防任意文件读取
            if not _path_within_sessions(target):
                self._send(403, {"ok": False, "result": "forbidden"})
                return
            res = jump_to_path(target)
            self._send(200, {"ok": res.startswith("ok"), "result": res})
        elif path == "/api/services":
            self._send(200, {
                "dashboard": {"up": True},
                "piweb": {"up": _port_open(30141)},
            })
        elif path == "/api/stats":
            self._send(200, {"ok": True, **aggregator.stats()})
        elif path == "/api/alerts":
            self._send(200, {"ok": True, "alerts": alerts.recent()})
        elif path in ("/", "/index.html", "/dashboard.html"):
            self._serve_dashboard()
        else:
            # 白名单：除看板页与 api 外一律 404，避免暴露源码/.git 等
            self._send(404, {"ok": False, "result": "not_found"})

    def do_POST(self):
        # 会话管理写操作：/api/kill、/api/restart（本期仅 CLI 会话，网页版无独立 pid）
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self._send(400, {"ok": False, "result": "bad_request"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "result": "bad_request"})
            return
        if not isinstance(data, dict):
            self._send(400, {"ok": False, "result": "bad_body"})
            return

        path = urlparse(self.path).path
        if path == "/api/kill":
            pid = str(data.get("pid", "")).strip()
            if not pid:
                self._send(400, {"ok": False, "result": "bad_request"})
                return
            # 先校验 pid 是当前 pi 进程（破坏性操作，白名单校验），再杀
            pids = {r.pid for r in CLIDetector().detect(force=True)}
            if pid not in pids:
                self._send(200, {"ok": False, "result": "not_a_pi_process"})
                return
            ok = actions.kill_session(pid)
            self._send(200, {"ok": ok, "result": "killed" if ok else "kill_failed"})
        elif path == "/api/restart":
            cwd = str(data.get("cwd", "")).strip()
            if not cwd or not cwd.startswith("/"):
                self._send(400, {"ok": False, "result": "bad_request"})
                return
            ok = actions.restart_session(cwd)
            self._send(200, {"ok": ok, "result": "restarted" if ok else "restart_failed"})
        else:
            self._send(404, {"ok": False, "result": "not_found"})

    def do_HEAD(self):
        # 不继承 SimpleHTTPRequestHandler.do_HEAD（会走 send_head→translate_path，
        # 把路径解析到 cwd 后返回 200+Content-Length，泄露项目根源码/.git 的存在与大小）。
        self._send(404, {"ok": False, "result": "not_found"})

    def _serve_dashboard(self):
        # 读项目根 dashboard.html（V2 单文件与代码同目录，V3 移到项目根）
        dash = os.path.join(PROJECT_ROOT, "dashboard.html")
        try:
            with open(dash, "r", encoding="utf-8") as f:
                html = f.read()
            self._send(200, html, "text/html; charset=utf-8")
        except Exception:
            self._send(404, "dashboard.html not found", "text/plain")

    def log_message(self, *args):
        pass  # 静默日志


def _alert_loop():
    """后台告警循环：每 30 秒触发一次 alerts.check()（内部已 notify）。"""
    while True:
        try:
            for _ in alerts.check():
                pass
        except Exception:
            pass
        # 碎片化 sleep（30 × 1s），便于进程退出时快速回收线程
        for _ in range(30):
            time.sleep(1)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    # 启动即建索引表（同步、快），再后台重建（扫 489 个 jsonl 需几秒，别阻塞 serve_forever）
    index.init_db()
    threading.Thread(target=index.rebuild, daemon=True).start()
    threading.Thread(target=_alert_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True  # 主线程退出时回收请求线程
    print(f"Agent Monitor 服务运行中: http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
