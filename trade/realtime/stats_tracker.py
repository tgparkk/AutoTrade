from __future__ import annotations

import threading
from typing import Dict


class StatsTracker:
    """모니터링 통계(스캔 횟수, 신호, 주문 체결 등)를
    스레드 안전하게 기록·조회하기 위한 헬퍼 클래스입니다.
    RealTimeMonitor 에서 분리하여 재사용성을 높였습니다.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._market_scan_count = 0
        self._buy_signals_detected = 0
        self._sell_signals_detected = 0
        self._buy_orders_executed = 0
        self._sell_orders_executed = 0

    # -------------------------------------------------
    # 내부 헬퍼
    # -------------------------------------------------
    def _inc(self, attr: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, attr, getattr(self, attr) + value)

    # -------------------------------------------------
    # 증가 메소드
    # -------------------------------------------------
    def inc_market_scan(self, n: int = 1) -> None:
        self._inc("_market_scan_count", n)

    def inc_buy_signal(self, n: int = 1) -> None:
        self._inc("_buy_signals_detected", n)

    def inc_sell_signal(self, n: int = 1) -> None:
        self._inc("_sell_signals_detected", n)

    def inc_buy_order(self, n: int = 1) -> None:
        self._inc("_buy_orders_executed", n)

    def inc_sell_order(self, n: int = 1) -> None:
        self._inc("_sell_orders_executed", n)

    # -------------------------------------------------
    # 프로퍼티 – 읽기 전용
    # -------------------------------------------------
    @property
    def market_scan_count(self) -> int:
        with self._lock:
            return self._market_scan_count

    @property
    def buy_signals_detected(self) -> int:
        with self._lock:
            return self._buy_signals_detected

    @property
    def sell_signals_detected(self) -> int:
        with self._lock:
            return self._sell_signals_detected

    @property
    def buy_orders_executed(self) -> int:
        with self._lock:
            return self._buy_orders_executed

    @property
    def sell_orders_executed(self) -> int:
        with self._lock:
            return self._sell_orders_executed

    @property
    def orders_executed(self) -> int:
        with self._lock:
            return self._buy_orders_executed + self._sell_orders_executed

    # -------------------------------------------------
    # 스냅샷
    # -------------------------------------------------
    def snapshot(self) -> Dict[str, int]:
        """현재 통계 값을 딕셔너리로 반환합니다."""
        with self._lock:
            return {
                "market_scan_count": self._market_scan_count,
                "buy_signals_detected": self._buy_signals_detected,
                "sell_signals_detected": self._sell_signals_detected,
                "buy_orders_executed": self._buy_orders_executed,
                "sell_orders_executed": self._sell_orders_executed,
            } 