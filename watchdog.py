#!/usr/bin/env python3
"""AgentMonitor V3 8571 服务守护进程（watchdog）

行为：
- 双 fork + setsid 做成 macOS 守护进程；写 pid 文件到 /tmp/agentmonitor_watchdog_v3.pid
- 每 10s 检查 8571 端口 + curl 健康检查；死了就用 setsid Popen 拉起服务
- 自身 SIGTERM/SIGINT 优雅退出（删除 pid 文件）
- 日志写到 /tmp/agentmonitor_watchdog_v3.log

启动方式（一次性）：
    python3 watchdog.py
由 start.sh 在服务未起时检测并 spawn。

移植自 V2 agentmonitor_watchdog.py，改动点：
- 入口从「绝对路径 monitor_server.py」改为「python3 -m agentmonitor.server」
  （V3 是包，需 cwd=PROJECT_ROOT 才能 import 到 agentmonitor 包）。
- pid/日志文件加 _v3 后缀，避免与 V2 的 /tmp 文件冲突。
"""

import os
import sys
import time
import signal
import socket
import subprocess
import urllib.request

PORT = 8571
PROJECT_ROOT = "/Users/kelvinjiang/Desktop/实用工具开发/AgentMonitor-v3"
URL = f"http://127.0.0.1:{PORT}/api/services"
PID_FILE = "/tmp/agentmonitor_watchdog_v3.pid"
LOG_FILE = "/tmp/agentmonitor_watchdog_v3.log"
SERVER_LOG = "/tmp/agentmonitor_server_v3.log"
INTERVAL = 10.0  # 秒
START_TIMEOUT = 6.0  # 等服务起来的最大秒数

MANAGED_PY = "/Users/kelvinjiang/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
SYSTEM_PY = "/usr/local/bin/python3"


def pick_python():
    for p in (MANAGED_PY, SYSTEM_PY):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return sys.executable


def log(msg):
    # daemon 模式后 fd 1/2 已 dup 到 LOG_FILE；用 stdout 写入并立即 flush。
    try:
        sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stdout.flush()
    except Exception:
        pass


def is_port_listening():
    try:
        s = socket.socket()
        s.settimeout(0.5)
        ok = s.connect_ex(("127.0.0.1", PORT)) == 0
        s.close()
        return ok
    except Exception:
        return False


def http_ok():
    try:
        req = urllib.request.Request(URL, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def service_alive():
    return is_port_listening() and http_ok()


def start_server(py):
    # V3 是包：检查 agentmonitor/server.py 是否存在（而非 V2 的单文件路径）
    srv_file = os.path.join(PROJECT_ROOT, "agentmonitor", "server.py")
    if not os.path.isfile(srv_file):
        log(f"server module not found: {srv_file}")
        return False
    try:
        # 拉起时把 stdin 指 /dev/null、stdout/stderr 落到文件，start_new_session 完全脱离
        # 当前会话：完全脱离 launcher 的进程组，避免父退出时被回收
        # cwd=PROJECT_ROOT 让 `-m agentmonitor.server` 能 import 到包
        log(f"starting server: {py} -m agentmonitor.server (cwd={PROJECT_ROOT})")
        logf = open(SERVER_LOG, "ab", buffering=0)
        subprocess.Popen(
            [py, "-m", "agentmonitor.server"],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
            close_fds=True,
            start_new_session=True,
        )
    except Exception as e:
        log(f"start_server failed: {e!r}")
        return False
    # 等就绪
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.25)
        if service_alive():
            log("server is up")
            return True
    log("server still not alive after timeout")
    return False


def become_daemon():
    """双 fork + setsid 守护进程化；写 pid 文件。"""
    # 第一叉：父进程退出，shell 误以为进程结束，不再送 SIGHUP
    pid = os.fork()
    if pid > 0:
        # 父进程立刻退出
        os._exit(0)
    os.setsid()
    # 第二叉：确保不再是会话首进程，无法再获得控制终端
    pid = os.fork()
    if pid > 0:
        os._exit(0)
    os.chdir("/tmp")
    os.umask(0)
    # 重定向 std
    sys.stdout.flush()
    sys.stderr.flush()
    fd_devnull = os.open(os.devnull, os.O_RDONLY)
    fd_log = os.open(LOG_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.dup2(fd_devnull, 0)
    os.dup2(fd_log, 1)
    os.dup2(fd_log, 2)
    os.close(fd_devnull)
    # 写 pid
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


_alive = True


def _term(signum, frame):
    global _alive
    _alive = False


def main():
    # 信号
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    py = pick_python()
    log(f"watchdog up pid={os.getpid()} py={py} interval={INTERVAL}s")

    # 第一次启动：直接确认服务健康，不健康则拉
    if not service_alive():
        start_server(py)

    while _alive:
        try:
            if not service_alive():
                log("service down — restarting")
                start_server(py)
        except Exception as e:
            log(f"loop err: {e!r}")
        # 用 sleep 碎片化以便响应信号
        end = time.time() + INTERVAL
        while _alive and time.time() < end:
            time.sleep(0.5)

    log("watchdog exit")
    try:
        os.unlink(PID_FILE)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    become_daemon()
    main()
