from __future__ import annotations

from typing import Dict, TYPE_CHECKING

from utils.logger import setup_logger

logger = setup_logger(__name__)

if TYPE_CHECKING:
    from trade.realtime_monitor import RealTimeMonitor


class SellRunner:
    """RealTimeMonitor.process_sell_ready_stocks 로직을 분리한 모듈."""

    def __init__(self, monitor: "RealTimeMonitor") -> None:
        self.m = monitor

    def run(self) -> Dict[str, int]:
        result: Dict[str, int] = {"checked": 0, "signaled": 0, "ordered": 0}
        from models.stock import StockStatus  # local import

        try:
            holding = (
                self.m.stock_manager.get_stocks_by_status(StockStatus.BOUGHT)
                + self.m.stock_manager.get_stocks_by_status(StockStatus.PARTIAL_BOUGHT)
                + self.m.stock_manager.get_stocks_by_status(StockStatus.PARTIAL_SOLD)
            )
            if not holding:
                return result

            rt_dict = {}
            for stk in holding:
                try:
                    rt = self.m.get_realtime_data(stk.stock_code)
                    if rt:
                        rt_dict[stk.stock_code] = rt
                except Exception as exc:
                    logger.debug(f"실시간 데이터 조회 실패 {stk.stock_code}: {exc}")

            for stk in holding:
                result["checked"] += 1
                rt = rt_dict.get(stk.stock_code)
                if not rt:
                    continue
                try:
                    prev_sig = result["signaled"]
                    success = self.m.sell_processor.analyze_and_sell(
                        stock=stk,
                        realtime_data=rt,
                        result_dict=result,
                        market_phase=self.m.get_market_phase(),
                    )
                    if result["signaled"] > prev_sig:
                        self.m.stats_tracker.inc_sell_signal()
                        if success:
                            self.m.stats_tracker.inc_sell_order()
                        self.m.alert_sent.discard(f"{stk.stock_code}_buy")
                except Exception as exc:
                    logger.error(f"매도 처리 오류 {stk.stock_code}: {exc}")
        except Exception as exc:
            logger.error(f"매도 준비 종목 처리 오류: {exc}")
        return result 