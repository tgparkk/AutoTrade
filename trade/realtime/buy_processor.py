"""buy_processor.py – 매수 조건/수량 계산 담당 (스캐폴드)"""

from typing import Any

class BuyProcessor:
    def __init__(self, *args: Any, **kwargs: Any):
        self._args = args
        self._kwargs = kwargs

    # ---------- public API ----------
    def analyze_buy_conditions(self, *args: Any, **kwargs: Any) -> bool:
        raise NotImplementedError

    def calculate_buy_quantity(self, *args: Any, **kwargs: Any) -> int:
        raise NotImplementedError

    # fast / standard 분석 내부용 (미구현)
    def _analyze_fast_buy_conditions(self, *args: Any, **kwargs: Any) -> bool:
        raise NotImplementedError

    def _analyze_standard_buy_conditions(self, *args: Any, **kwargs: Any) -> bool:
        raise NotImplementedError 