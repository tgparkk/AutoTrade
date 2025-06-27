"""monitor_core.py – 메인 모니터 코어 (스캐폴드)

Step A: 빈 메서드를 정의해 두고 기존 realtime_monitor.py 기능을
점진적으로 이전할 예정.
"""

from typing import Any

class RealTimeMonitorCore:
    """코어 루프 및 시스템 상태 관리 (스캐폴드)"""

    def __init__(self, *args: Any, **kwargs: Any):
        # 실제 구현은 Step B에서 채워집니다.
        self._args = args
        self._kwargs = kwargs

    def loop(self):
        """메인 모니터링 루프 (미구현)"""
        raise NotImplementedError

    def adjust_monitoring_frequency(self):
        """모니터링 주기 조정 로직 (미구현)"""
        raise NotImplementedError 