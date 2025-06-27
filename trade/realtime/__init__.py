"""Real-time monitoring sub-package (scaffold).

Step A: 빈 클래스/함수 정의로 구성. 이후 단계에서 기존
trade.realtime_monitor 의 기능을 점진적으로 옮겨올 예정.

사용자는 여전히 trade.realtime_monitor.RealTimeMonitor 를
사용하면 됩니다. 리팩터링 완료 전까지 외부 API 는 변경되지
않습니다.
"""

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

__all__ = [
    "monitor_core",
    "buy_processor",
    "sell_processor",
    "scan_worker",
    "ws_subscription",
    "performance_logger",
]

# 동적 서브모듈 로딩 (지연 import)

def __getattr__(name: str) -> ModuleType:  # type: ignore[override]
    if name in __all__:
        return import_module(f"trade.realtime.{name}")
    raise AttributeError(name)

if TYPE_CHECKING:
    from . import monitor_core, buy_processor, sell_processor, scan_worker, ws_subscription, performance_logger 