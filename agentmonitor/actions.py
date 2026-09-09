"""会话管理操作：kill / restart（本期仅 CLI 会话，网页版无独立 pid）。

移植自 V2 `monitor_server.py` 的 `/api/kill-pi`（SIGTERM → 等一会 → SIGKILL，
只杀命中 pi 进程），restart 用 osascript 让 iTerm2 新开 tab 跑 pi。

零第三方依赖（os / signal / time / subprocess）。任何异常降级返回 False，
绝不抛异常（kill 前由 server 层用 CLIDetector 校验 pid 是 pi 进程）。
"""
import os
import signal
import subprocess
import time

from agentmonitor.detectors.cli import CLIDetector


def _pi_pids():
    """当前真实在跑的 pi CLI 进程 pid 集合（绕过 3s 缓存）。"""
    return {r.pid for r in CLIDetector().detect(force=True)}


def kill_session(pid: str) -> bool:
    """SIGTERM → 等 2 秒 → 若还活着 SIGKILL。返回是否成功发出信号。

    - pid 非数字 / SIGTERM 抛 ProcessLookupError（进程本就不存在）→ False。
    - SIGTERM 发出后，2 秒内进程自行退出 → True（已退出视为成功）。
    - 2 秒后仍在 pi 进程集合里 → SIGKILL；SIGKILL 时已退出仍视为成功。
    """
    try:
        pid_int = int(pid)
    except (ValueError, TypeError):
        return False

    # 第一步：SIGTERM。进程本就不存在（如测试用的 "99999999"）→ 未发出信号 → False。
    try:
        os.kill(pid_int, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return False

    # 第二步：等 2 秒，还在 pi 进程集合里就 SIGKILL（补刀）。
    time.sleep(2)
    if str(pid_int) in _pi_pids():
        try:
            os.kill(pid_int, signal.SIGKILL)
        except ProcessLookupError:
            return True  # 2 秒窗口内已退出，视为成功
        except (PermissionError, OSError):
            return False
    return True


def restart_session(cwd: str) -> bool:
    """在 iTerm2 新开一个 tab，cd 到 cwd 并运行 pi。返回 osascript 是否成功。

    cwd 必须非空且以 `/` 开头（防注入相对路径）；单引号先转义。非法 cwd /
    osascript 失败（含自动化权限被拒）→ False，不抛异常。
    """
    if not cwd or not cwd.startswith("/"):
        return False

    # 三层转义（顺序敏感：先反斜杠 → 双引号 → 单引号）。
    # cwd 会插进 applescript 双引号字符串 write text "cd '<escaped>' && pi" 里，
    # 若只转义单引号，含 " 或 \ 的 cwd 可闭合字符串注入任意 applescript。
    escaped = cwd.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''")
    script = (
        'tell application "iTerm2"\n'
        '    tell current window\n'
        '        create tab with default profile\n'
        '        tell current session\n'
        f'            write text "cd \'{escaped}\' && pi"\n'
        '        end tell\n'
        '    end tell\n'
        'end tell'
    )
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False
