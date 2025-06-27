"""sell_processor.py – 매도 조건 분석·주문 실행 담당

BuyProcessor 와 동일한 구조로 매도 로직을 분리한다.
"""

from __future__ import annotations

from typing import Dict, Optional
from datetime import datetime

from models.stock import Stock
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)

class SellProcessor:
    """매도 조건 분석 + 주문 실행 전담 클래스"""

    def __init__(
        self,
        stock_manager,
        trade_executor,
        condition_analyzer,
        performance_config: Dict,
        risk_config: Dict,
    ):
        self.stock_manager = stock_manager
        self.trade_executor = trade_executor
        self.condition_analyzer = condition_analyzer
        self.performance_config = performance_config
        self.risk_config = risk_config

    # ------------------------------------------------------------
    # Wrapper
    # ------------------------------------------------------------
    def analyze_sell_conditions(
        self,
        stock: Stock,
        realtime_data: Dict,
        market_phase: Optional[str] = None,
    ) -> Optional[str]:
        return self.condition_analyzer.analyze_sell_conditions(
            stock, realtime_data, market_phase
        )

    def analyze_and_sell(
        self,
        stock: Stock,
        realtime_data: Dict,
        result_dict: Dict[str, int],
        market_phase: Optional[str] = None,
    ) -> bool:
        """조건 분석 후 매도 주문 실행 및 result 수치 업데이트"""
        try:
            sell_reason = self.analyze_sell_conditions(stock, realtime_data, market_phase)
            if not sell_reason:
                return False

            result_dict['signaled'] += 1

            price = realtime_data.get('current_price') or 0
            if price <= 0:
                return False

            success = self.trade_executor.execute_sell_order(
                stock=stock,
                price=price,
                reason=sell_reason,
            )

            if success:
                result_dict['ordered'] += 1
                logger.info(
                    f"📝 매도 주문 접수: {stock.stock_code} @{price:,}원 (사유: {sell_reason})"
                )
            else:
                logger.warning(
                    f"❌ 매도 주문 실패: {stock.stock_code} @{price:,}원 (사유: {sell_reason})"
                )
            return success
        except Exception as e:
            logger.error(f"analyze_and_sell 오류 {stock.stock_code}: {e}")
            return False

    # SellProcessor 는 RealTimeMonitor 에서 직접 analyze_and_sell 호출로 사용되므로
    # 추가 스텁 메서드는 필요하지 않다. 