from __future__ import annotations

from typing import Dict, TYPE_CHECKING

from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)

if TYPE_CHECKING:
    from trade.realtime_monitor import RealTimeMonitor
    from models.stock import Stock


class BuyRunner:
    """RealTimeMonitor.process_buy_ready_stocks 로직을 분리한 모듈."""

    def __init__(self, monitor: "RealTimeMonitor") -> None:
        self.m = monitor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> Dict[str, int]:
        """매수 준비 상태 종목 처리 후 결과 dict 반환."""
        result: Dict[str, int] = {"checked": 0, "signaled": 0, "ordered": 0}

        try:
            # 장 마감 임박 시 신규 진입 금지
            now_time = now_kst().time()
            if now_time >= self.m.pre_close_time or now_time >= self.m.day_trading_exit_time:
                logger.debug("pre_close_time/day_trading_exit_time 이후 - 신규 매수 스킵")
                return result

            # WATCHING / BOUGHT 배치 조회
            from models.stock import StockStatus  # 로컬 import 순환 회피
            batch = self.m.stock_manager.get_stocks_by_status_batch(
                [StockStatus.WATCHING, StockStatus.BOUGHT]
            )
            ready_stocks = batch[StockStatus.WATCHING]
            current_positions = len(batch[StockStatus.BOUGHT])

            if not ready_stocks:
                return result

            # 실시간 데이터 미리 수집
            rt_dict = {}
            for stk in ready_stocks:
                try:
                    rt = self.m.get_realtime_data(stk.stock_code)
                    if rt:
                        rt_dict[stk.stock_code] = rt
                except Exception as exc:  # pylint: disable=broad-except
                    logger.debug(f"실시간 데이터 조회 실패 {stk.stock_code}: {exc}")

            for stk in ready_stocks:
                result["checked"] += 1
                rt = rt_dict.get(stk.stock_code)
                if not rt:
                    continue

                try:
                    # 1) 신호 판단 (BuyProcessor 사용)
                    buy_signal = self.m.buy_processor.analyze_buy_conditions(
                        stk, rt, self.m.get_market_phase()
                    )
                    if not buy_signal:
                        continue
                    result["signaled"] += 1

                    # 2) 주문 실행 (BuyProcessor 사용)
                    success = self.m.buy_processor.analyze_and_buy(
                        stock=stk,
                        realtime_data=rt,
                        current_positions_count=current_positions,
                        market_phase=self.m.get_market_phase(),
                    )

                    if success:
                        result["ordered"] += 1
                        self.m.stats_tracker.inc_buy_order()
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error(f"매수 처리 오류 {stk.stock_code}: {exc}")
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"매수 준비 종목 처리 오류: {exc}")
        return result 