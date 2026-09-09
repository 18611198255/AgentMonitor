"""网页版探测器：判定 pi-web 里哪些会话此刻正在跑 agent。

移植自 V2 `monitor_server.py` 的 `get_piweb_running_ids`。
`/api/agent/running` 返回 `runningSessionIds`（此刻 LLM 在生成或工具在执行
的会话 ID 子集），与磁盘 jsonl 持久化的全部会话是两个层面。看板以此作
"真活跃"标准。只读：仅一次 HTTP GET，任何异常降级返回 []，绝不抛异常。
"""
import json
import time
import urllib.request

from agentmonitor.constants import PIWEB_URL, WEB_CACHE_TTL

from .base import Detector, SessionRef

_CACHE = {"t": 0.0, "ids": [], "available": False}


def _fetch_running_ids():
    """拉取 `/api/agent/running` 的 runningSessionIds 列表。

    接口不可用时返回 ([], False)；可用时返回 (ids, True)。
    """
    ids = []
    try:
        req = urllib.request.Request(
            f"{PIWEB_URL}/api/agent/running",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        raw = data.get("runningSessionIds") or []
        ids = [str(x).strip() for x in raw if str(x).strip()]
        return ids, True
    except Exception:
        return [], False


class WebDetector(Detector):
    def detect(self) -> list[SessionRef]:
        global _CACHE
        now = time.time()
        if now - _CACHE["t"] < WEB_CACHE_TTL:
            ids = _CACHE["ids"]
        else:
            ids, available = _fetch_running_ids()
            _CACHE = {"t": now, "ids": ids, "available": available}

        return [
            SessionRef(cwd="", source="web", session_id=id, jumpable=False)
            for id in ids
        ]
