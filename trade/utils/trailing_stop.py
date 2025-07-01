from __future__ import annotations

from typing import Optional, TYPE_CHECKING
# Forward references for type checking
if TYPE_CHECKING:
    from trade.stock_manager import StockManager
    from trade.trade_executor import TradeExecutor

from models.stock import Stock, StockStatus
from utils.logger import setup_logger

logger = setup_logger(__name__)


def trailing_stop_check(
    stock_manager: "StockManager",
    trade_executor: "TradeExecutor",
    stock_code: str,
    current_price: float,
    trail_ratio: float = 1.0,
) -> None:
    """트레일링 스탑 조건을 검사하고 즉시 매도 수행

    Args:
        stock_manager: StockManager 인스턴스
        trade_executor: TradeExecutor 인스턴스
        stock_code: 종목코드
        current_price: 현재가
        trail_ratio: 최고가 대비 허용 하락폭 (%)
    """
    try:
        # BOUGHT 상태에서만 동작 (모듈화된 안전한 접근)
        stock_obj: Optional[Stock] = stock_manager.get_selected_stock(stock_code)
        if not stock_obj or stock_obj.status != StockStatus.BOUGHT:
            return

        # 최고가·익절가 갱신
        stock_obj.update_trailing_target(trail_ratio, current_price)
        dyn_target = stock_obj.dynamic_target_price
        if dyn_target > 0 and current_price <= dyn_target:
            logger.info(
                f"🔔 [트레일링] {stock_code} {current_price:,} ≤ {dyn_target:,} – 즉시 매도"
            )
            # 중복 방지 (안전한 상태 확인)
            if stock_obj.status == StockStatus.BOUGHT:
                trade_executor.execute_sell_order(
                    stock=stock_obj,
                    price=current_price,
                    reason="trailing_take_profit",
                )
    except Exception as err:
        logger.error(f"트레일링 스탑 처리 오류 {stock_code}: {err}") 