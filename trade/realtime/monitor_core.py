"""monitor_core.py – RealTimeMonitor 의 핵심 루프 분리 모듈
현재 단계에서는 기존 monitor_cycle 로직을 그대로 호출만 하여
행동을 변경하지 않는다. 이후 단계에서 점진적으로 run_cycle 본문을
이동할 예정.
"""

from __future__ import annotations

from typing import Any

class MonitorCore:
    """RealTimeMonitor 의 monitor_cycle 로직 래퍼"""

    def __init__(self, monitor: "Any"):
        self.monitor = monitor  # RealTimeMonitor 인스턴스

    def run_cycle(self):
        """현행 monitor_cycle_legacy 로 그대로 위임"""
        return self.monitor.monitor_cycle_legacy()

    def loop(self):
        """메인 모니터링 루프 (미구현)"""
        raise NotImplementedError

    def adjust_monitoring_frequency(self):
        """모니터링 주기 조정 로직 (미구현)"""
        raise NotImplementedError 