"""sell_processor.py – 매도 조건 분석·주문 실행 담당

BuyProcessor 와 동일한 구조로 매도 로직을 분리한다.
"""

from __future__ import annotations

from typing import Dict, Optional, TYPE_CHECKING, Any
from datetime import datetime

from models.stock import Stock
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from trade.stock_manager import StockManager
    from trade.trade_executor import TradeExecutor
    from trade.trading_condition_analyzer import TradingConditionAnalyzer

logger = setup_logger(__name__)

class SellProcessor:
    """매도 조건 분석 + 주문 실행 전담 클래스"""

    def __init__(
        self,
        stock_manager: "StockManager",
        trade_executor: "TradeExecutor",
        condition_analyzer: "TradingConditionAnalyzer",
        performance_config: Dict[str, Any],
        risk_config: Dict[str, Any],
    ):
        self.stock_manager: "StockManager" = stock_manager
        self.trade_executor: "TradeExecutor" = trade_executor
        self.condition_analyzer: "TradingConditionAnalyzer" = condition_analyzer
        self.performance_config: Dict[str, Any] = performance_config
        self.risk_config: Dict[str, Any] = risk_config

    def _determine_sell_price(self, realtime_data: Dict[str, Any]) -> float:
        """매도 주문가를 계산하여 반환한다.

        1) 실시간 매도 1호가(ask_price)가 존재하면 우선 사용한다.
        2) ask_price가 현재가(current_price)보다 낮으면 현재가로 보정하여
           "현재가 이하"로 매도 주문이 나가는 것을 방지한다.
        3) 두 값 모두 유효하지 않으면 0을 반환한다.
        """
        ask_price = realtime_data.get("ask_price") or 0
        current_price = realtime_data.get("current_price") or 0

        # 매도 1호가 우선 사용, 없으면 현재가
        price = ask_price if ask_price > 0 else current_price

        # 보호 로직: 주문가가 현재가보다 낮아지지 않도록 보정
        if price < current_price:
            price = current_price

        return price

    # ------------------------------------------------------------
    # Wrapper
    # ------------------------------------------------------------
    def analyze_sell_conditions(
        self,
        stock: Stock,
        realtime_data: Dict[str, Any],
        market_phase: Optional[str] = None,
    ) -> Optional[str]:
        return self.condition_analyzer.analyze_sell_conditions(
            stock, realtime_data, market_phase
        )

    def analyze_and_sell(
        self,
        stock: Stock,
        realtime_data: Dict[str, Any],
        result_dict: Dict[str, int],
        market_phase: Optional[str] = None,
    ) -> bool:
        """조건 분석 후 매도 주문 실행 및 result 수치 업데이트"""
        try:
            # 🆕 트레일링 스탑 목표가 갱신
            if self.performance_config.get('trailing_stop_enabled', True):
                trail_ratio = self.performance_config.get('trailing_stop_ratio', 1.0)
                current_price = realtime_data.get('current_price', 0)
                if current_price > 0:
                    stock.update_trailing_target(trail_ratio, current_price)

            sell_reason = self.analyze_sell_conditions(stock, realtime_data, market_phase)
            if not sell_reason:
                return False

            result_dict['signaled'] += 1

            price = self._determine_sell_price(realtime_data)
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