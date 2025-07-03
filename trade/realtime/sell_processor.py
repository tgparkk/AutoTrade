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

        1) 매도 1호가(ask_price)와 현재가(current_price) 중 더 높은 값을 사용해
           "헐값" 매도를 방지한다.
        2) 두 값 모두 유효(>0)가 아닐 때는 0 을 반환하여 주문을 건너뛴다.
        3) (옵션) 실시간 데이터가 너무 오래됐으면 0 반환 – data_max_age(sec) 설정.
        """
        ask_price = realtime_data.get("ask_price") or 0
        current_price = realtime_data.get("current_price") or 0

        # 두 값 중 더 높은 값 선택
        price = max(ask_price, current_price)

        # 유효 가격이 없으면 주문하지 않음
        if price <= 0:
            return 0

        # 추가 안전장치: 데이터 신선도 확인 (기본 2초)
        last_ts = realtime_data.get("last_updated") or realtime_data.get("timestamp")
        if isinstance(last_ts, datetime):
            max_age = self.performance_config.get("data_max_age", 2)
            if (now_kst() - last_ts).total_seconds() > max_age:
                # 데이터가 너무 오래됨 → 주문 보류
                return 0

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
            # 🆕 트레일링 스탑 목표가 갱신 (설정에 따라)
            if self.performance_config.get('trailing_stop_enabled', False):
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