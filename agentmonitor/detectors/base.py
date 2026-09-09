"""探测器基类：Detector 接口 + SessionRef（轻量会话引用，非 Session）。

SessionRef 是探测层产出的最小事实：哪个进程/会话此刻在跑。session_id 由
server 合并层去 jsonl 里匹配，CLI 探测器只填进程级信息（cwd/pid/tty）。
"""
from dataclasses import dataclass


@dataclass
class SessionRef:
    cwd: str
    source: str = "cli"       # cli | web
    session_id: str = ""      # CLI 探测器返回空串（新进程可能还没写盘）；web 探测器填 id
    pid: str = ""
    tty: str = ""             # 仅 cli，供 iTerm 跳转用
    live: bool = True
    jumpable: bool = False    # cli=True，web=False


class Detector:
    def detect(self) -> list[SessionRef]:
        raise NotImplementedError
