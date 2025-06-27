"""sell_processor.py – 매도 조건 및 포지션 청산 처리 (스캐폴드)"""

from typing import Any, List, Tuple

class SellProcessor:
    def __init__(self, *args: Any, **kwargs: Any):
        self._args = args
        self._kwargs = kwargs

    def analyze_sell_conditions(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    def process_sell_ready_stocks(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    def force_sell_all_positions(self, *args: Any, **kwargs: Any) -> int:
        raise NotImplementedError 