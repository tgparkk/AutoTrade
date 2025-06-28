from __future__ import annotations

from typing import Optional, Any

from utils.logger import setup_logger
from models.stock import StockStatus

logger = setup_logger(__name__)


class VolatilityMonitor:
    """보유/관심 종목의 변동성을 계산하여 고변동성 여부를 판단하는 헬퍼.

    RealTimeMonitor._detect_high_volatility 메서드를 분리했습니다.
    """

    def __init__(
        self,
        stock_manager: Any,
        volatility_threshold: float = 0.02,
        high_volatility_position_ratio: float = 0.3,
    ) -> None:
        self.stock_manager = stock_manager
        self.volatility_threshold = volatility_threshold
        self.high_volatility_position_ratio = high_volatility_position_ratio

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def is_high_volatility(self) -> bool:
        """현재 포트폴리오가 고변동성 상태인지 여부를 반환한다."""
        try:
            positions = self.stock_manager.get_all_positions()
            if not positions:
                return False

            high_count = 0
            for pos in positions:
                if pos.status not in (StockStatus.BOUGHT, StockStatus.WATCHING):
                    continue
                current_price = pos.realtime_data.current_price
                ref_price = pos.reference_data.yesterday_close
                if ref_price <= 0:
                    continue
                change_pct = abs((current_price - ref_price) / ref_price)
                if change_pct >= self.volatility_threshold:
                    high_count += 1

            return high_count >= len(positions) * self.high_volatility_position_ratio
        except Exception as exc:
            logger.error(f"고변동성 계산 오류: {exc}")
            return False 