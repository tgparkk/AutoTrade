from __future__ import annotations

from typing import Dict, Optional, TYPE_CHECKING

from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)

if TYPE_CHECKING:
    from trade.stock_manager import StockManager  # type: ignore

class RealtimeProvider:
    """StockManager 로부터 웹소켓 실시간 데이터를 제공하는 래퍼.

    RealTimeMonitor.get_realtime_data() 의 기능을 독립 모듈로 분리.
    """

    def __init__(self, stock_manager: "StockManager"):
        self.stock_manager = stock_manager

    # -------------------------------------------------
    # Public
    # -------------------------------------------------
    def get(self, stock_code: str) -> Optional[Dict]:
        """지정 종목의 실시간 데이터를 반환한다.

        Args:
            stock_code: 종목 코드 (str)
        Returns:
            dict 데이터 or None
        """
        try:
            stock = self.stock_manager.get_selected_stock(stock_code)
            if not stock:
                return None

            # Stock 의 realtime_data / reference_data 를 사용해 딕셔너리 생성
            rt = stock.realtime_data
            ref = stock.reference_data

            return {
                "stock_code": stock_code,
                "current_price": rt.current_price,
                "open_price": ref.yesterday_close,
                "high_price": rt.today_high,
                "low_price": rt.today_low,
                "volume": rt.today_volume,
                "contract_volume": rt.contract_volume,
                "price_change_rate": rt.price_change_rate,
                "volume_spike_ratio": rt.volume_spike_ratio,
                "bid_price": rt.bid_price,
                "ask_price": rt.ask_price,
                "bid_prices": rt.bid_prices,
                "ask_prices": rt.ask_prices,
                "bid_volumes": rt.bid_volumes,
                "ask_volumes": rt.ask_volumes,
                "timestamp": now_kst(),
                "last_updated": rt.last_updated,
                "source": "websocket",
            }
        except Exception as exc:
            logger.error(f"실시간 데이터 조회 실패 {stock_code}: {exc}")
            return None 