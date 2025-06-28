# -*- coding: utf-8 -*-
"""trade.scanner.orderbook

호가창(orderbook) 분석 점수 계산.
현재 로직은 KIS `get_inquire_price` API 결과를 사용하여
스프레드와 매수/매도잔량 비율을 평가합니다.
"""
from typing import Tuple

from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = ["analyze_orderbook"]


def analyze_orderbook(stock_code: str, max_spread_pct: float = 4.0) -> Tuple[float, str]:
    """주어진 종목의 호가 정보를 분석하여 (점수, 사유) 반환.

    기존 MarketScanner._analyze_orderbook_for_daytrading_flexible 로직을 그대로 가져왔습니다.

    Returns:
        score (float), reason (str)
    """
    try:
        from api.kis_market_api import get_inquire_price

        price_data = get_inquire_price(div_code="J", itm_no=stock_code)
        if price_data is None or price_data.empty:
            return 0.0, ""

        row = price_data.iloc[0]

        # 스프레드 계산
        best_ask = float(row.get("askp1", 0))
        best_bid = float(row.get("bidp1", 0))
        if best_ask <= 0 or best_bid <= 0:
            spread_score = 0
            spread_reason = ""
            spread_pct = None
        else:
            spread_pct = (best_ask - best_bid) / best_bid * 100
            if spread_pct <= 1.0:
                spread_score = 5
                spread_reason = f"저스프레드({spread_pct:.2f}%)"
            elif spread_pct <= 2.0:
                spread_score = 3
                spread_reason = f"적정스프레드({spread_pct:.2f}%)"
            elif spread_pct <= max_spread_pct:
                spread_score = 1
                spread_reason = f"보통스프레드({spread_pct:.2f}%)"
            else:
                return 0.0, f"고스프레드({spread_pct:.2f}%)"

        # 잔량 비율 계산
        ask_qty = float(row.get("askp_rsqn1", 0))
        bid_qty = float(row.get("bidp_rsqn1", 0))
        if ask_qty > 0 and bid_qty > 0:
            bid_ask_ratio = bid_qty / (ask_qty + bid_qty)
            if bid_ask_ratio >= 0.55:
                volume_score = 3
                volume_reason = f"매수우세({bid_ask_ratio:.1%})"
            elif bid_ask_ratio >= 0.35:
                volume_score = 1
                volume_reason = f"호가균형({bid_ask_ratio:.1%})"
            else:
                volume_score = 0
                volume_reason = f"매도우세({bid_ask_ratio:.1%})"
        else:
            volume_score = 0
            volume_reason = ""

        total_score = spread_score + volume_score
        reasons = "+".join([r for r in [spread_reason, volume_reason] if r])

        return float(total_score), reasons

    except Exception as e:
        logger.debug(f"호가창 분석 실패 {stock_code}: {e}")
        return 0.0, "" 