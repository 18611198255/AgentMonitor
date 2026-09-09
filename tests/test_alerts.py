"""alerts.py 单元测试（unittest，零第三方依赖）。

`_evaluate` 是纯逻辑 + 冷却：测试用构造的 Session 列表喂入，不碰真实 pi/pi-web。
冷却状态存在模块级 `_last_fired` dict，setUp 里 `clear()` 保证用例隔离。
"""
import unittest

from agentmonitor import alerts
from agentmonitor.constants import ALERT_COOLDOWN, SILENT_THRESHOLD
from agentmonitor.parser import Session

NOW = 1750000000.0


def make_session(session_id="s1", project="proj1", status="idle",
                 age_seconds=0.0, live=False):
    return Session(
        session_id=session_id,
        file_path="",
        cwd="/tmp/proj",
        project_name=project,
        status=status,
        age_seconds=age_seconds,
        live=live,
    )


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        alerts._last_fired.clear()

    def test_stuck_status_fires_stuck_alert(self):
        s = make_session(status="stuck")
        out = alerts._evaluate([s], piweb_up=True, now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule, "stuck")
        self.assertEqual(out[0].session_id, "s1")
        self.assertIn("proj1", out[0].message)

    def test_silent_alert_for_live_old_non_waiting(self):
        s = make_session(status="running",
                         age_seconds=SILENT_THRESHOLD + 1, live=True)
        out = alerts._evaluate([s], piweb_up=True, now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule, "silent")

    def test_waiting_status_no_silent_alert(self):
        s = make_session(status="waiting",
                         age_seconds=SILENT_THRESHOLD + 1, live=True)
        out = alerts._evaluate([s], piweb_up=True, now=NOW)
        self.assertEqual(out, [])

    def test_piweb_down_fires_alert(self):
        out = alerts._evaluate([], piweb_up=False, now=NOW)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule, "piweb_down")
        self.assertEqual(out[0].session_id, "")

    def test_piweb_up_no_alert(self):
        out = alerts._evaluate([], piweb_up=True, now=NOW)
        self.assertEqual(out, [])

    def test_cooldown_blocks_repeat_until_expiry(self):
        s = make_session(status="stuck")
        first = alerts._evaluate([s], piweb_up=True, now=NOW)
        self.assertEqual(len(first), 1)
        # 冷却期内同 (rule, session_id) 不重复
        second = alerts._evaluate([s], piweb_up=True,
                                  now=NOW + ALERT_COOLDOWN - 1)
        self.assertEqual(second, [])
        # 超过冷却后再次 fire
        third = alerts._evaluate([s], piweb_up=True,
                                 now=NOW + ALERT_COOLDOWN + 1)
        self.assertEqual(len(third), 1)

    def test_cooldown_is_per_session(self):
        s1 = make_session(session_id="s1", status="stuck")
        s2 = make_session(session_id="s2", status="stuck")
        out = alerts._evaluate([s1, s2], piweb_up=True, now=NOW)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
