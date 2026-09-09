"""CLI 探测器：判定此刻哪些 pi CLI 进程在终端里跑。

移植自 V2 `monitor_server.py` 的 `get_pi_proc_map`（`pgrep -x pi` + `lsof`）。
不用 `ps`（macOS TCC/sandbox 下 `ps -p` 会抛 PermissionError，V2 已验证），
cwd / tty 全用 lsof 拿。任何异常降级返回 []，绝不抛异常。
"""
import subprocess
import time

from agentmonitor.constants import CLI_CACHE_TTL

from .base import Detector, SessionRef

_CACHE = {"t": 0.0, "data": []}


def _run(args):
    """跑一个只读子进程，返回 stdout 文本；失败返回空串。"""
    try:
        return subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except Exception:
        return ""


class CLIDetector(Detector):
    def detect(self, force: bool = False) -> list[SessionRef]:
        global _CACHE
        now = time.time()
        if not force and now - _CACHE["t"] < CLI_CACHE_TTL:
            return _CACHE["data"]

        refs = []
        try:
            # pgrep -x pi 匹配 argv[0]=="pi"（ps comm 是 node）
            pids = [p.strip() for p in _run(["pgrep", "-x", "pi"]).split() if p.strip()]
            for pid in pids:
                cwd = ""
                tty = ""
                try:
                    # lsof 拿 cwd（fd type=cwd → 仅输出 n 行）
                    for line in _run(
                        ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"]
                    ).splitlines():
                        if line.startswith("n"):
                            cwd = line[1:]
                    # lsof 拿 stdin tty（fd 0）——终端跑的 pi 一定有 /dev/ttysXXX
                    for line in _run(
                        ["lsof", "-a", "-p", pid, "-d", "0", "-Fn"]
                    ).splitlines():
                        if line.startswith("n") and "/dev/ttys" in line:
                            tty = line[1:]
                            break
                except Exception:
                    pass
                refs.append(SessionRef(cwd=cwd, pid=pid, tty=tty,
                                       source="cli", jumpable=True))
        except Exception:
            refs = []

        _CACHE = {"t": now, "data": refs}
        return refs
