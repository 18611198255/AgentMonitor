"""AgentMonitor V3 探测器（CLI 进程探测器等）。"""
from .base import Detector, SessionRef
from .cli import CLIDetector
from .web import WebDetector

__all__ = ["Detector", "SessionRef", "CLIDetector", "WebDetector"]
