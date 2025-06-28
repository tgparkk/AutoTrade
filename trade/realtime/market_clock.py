from datetime import time as dt_time
from typing import Dict, Any

from utils.korean_time import now_kst


class MarketClock:
    """시장 개장/거래 가능 여부를 판단하는 유틸리티 클래스입니다.

    RealTimeMonitor 의 시간 관련 메소드를 분리하여, 테스트와 재사용성을 높였습니다.
    """

    def __init__(self, strategy_config: Dict[str, Any]):
        self.market_open_time = dt_time(
            strategy_config.get("market_open_hour", 9),
            strategy_config.get("market_open_minute", 0),
        )
        self.market_close_time = dt_time(
            strategy_config.get("market_close_hour", 15),
            strategy_config.get("market_close_minute", 30),
        )
        self.day_trading_exit_time = dt_time(
            strategy_config.get("day_trading_exit_hour", 15),
            strategy_config.get("day_trading_exit_minute", 0),
        )

    # -------------------------------------------------
    # 공개 API
    # -------------------------------------------------
    def is_market_open(self) -> bool:
        """코스피/코스닥 정규장 개장 여부."""
        current_dt = now_kst()
        current_time = current_dt.time()

        # 주말(토, 일) 휴장
        if current_dt.weekday() >= 5:
            return False

        return self.market_open_time <= current_time <= self.market_close_time

    def is_trading_time(self) -> bool:
        """데이 트레이딩 가능 여부 (시장 개장 & 데이트레이딩 종료 전)."""
        if not self.is_market_open():
            return False

        if now_kst().time() >= self.day_trading_exit_time:
            return False

        return True 