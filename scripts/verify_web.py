#!/usr/bin/env python3
"""只读验证脚本：打印 WebDetector().detect() 的结果。

只发一次 HTTP GET，不改任何状态。当前 pi-web 若无活跃会话，会打印 0 个 ref。
"""
import os
import sys

# 允许直接 `python3 scripts/verify_web.py` 运行（把项目根加进 sys.path）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentmonitor.detectors.web import WebDetector


def main():
    refs = WebDetector().detect()
    print(f"Web 探测器返回 {len(refs)} 个 SessionRef：")
    for r in refs:
        print(f"  session_id={r.session_id!r} source={r.source!r} "
              f"jumpable={r.jumpable}")


if __name__ == "__main__":
    main()
