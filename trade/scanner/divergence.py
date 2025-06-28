# -*- coding: utf-8 -*-
"""trade.scanner.divergence

이격도(divergence) 관련 분석 및 매매 시그널 생성을 담당합니다.
"""
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger
from trade.scanner.utils import (
    convert_to_dict_list as _convert_to_dict_list,
)

logger = setup_logger(__name__)

__all__ = [
    "analyze_divergence",
    "divergence_signal",
]


def analyze_divergence(stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
    """20일 가격 목록으로부터 이격도 종합 분석 결과를 반환.
    내부적으로 utils.technical_indicators.divergence_analysis 를 재사용합니다.
    """
    try:
        data_list = _convert_to_dict_list(ohlcv_data)
        if len(data_list) < 20:
            return None

        current_price = float(data_list[0].get("stck_clpr", 0))
        if current_price <= 0:
            return None

        prices: List[float] = [float(day.get("stck_clpr", 0)) for day in data_list[:20]]

        from utils.technical_indicators import divergence_analysis

        return divergence_analysis(prices)
    except Exception as e:
        logger.debug(f"이격도 분석 실패 {stock_code}: {e}")
        return None


def divergence_signal(analysis: Dict) -> Dict[str, Any]:  # type: ignore[name-defined]
    """이격도 분석 결과를 바탕으로 BUY/SELL/HOLD 신호와 점수를 반환합니다."""
    if not analysis:
        return {"signal": "HOLD", "reason": "분석 데이터 없음", "score": 0}

    divergences = analysis.get("divergences", {})

    sma_5_div = divergences.get("sma_5", 0)
    sma_10_div = divergences.get("sma_10", 0)
    sma_20_div = divergences.get("sma_20", 0)

    signal = "HOLD"
    reason: List[str] = []
    score = 0.0

    # 과매도(BUY)
    if sma_20_div <= -5 or (sma_10_div <= -3 and sma_5_div <= -2):
        signal = "BUY"
        score = 15 + abs(min(sma_20_div, sma_10_div, sma_5_div)) * 0.5
        reason.append(
            f"과매도 구간 (5일:{sma_5_div:.1f}%, 10일:{sma_10_div:.1f}%, 20일:{sma_20_div:.1f}%)"
        )
    # 상승 모멘텀
    elif 1 <= sma_5_div <= 3 and 0 <= sma_10_div <= 2 and -1 <= sma_20_div <= 1:
        signal = "MOMENTUM"
        score = 10
        reason.append(
            f"상승 모멘텀 (5일:{sma_5_div:.1f}%, 10일:{sma_10_div:.1f}%, 20일:{sma_20_div:.1f}%)"
        )
    # 과매수(SELL 경고)
    elif sma_20_div >= 10 or sma_10_div >= 7 or sma_5_div >= 5:
        signal = "OVERHEATED"
        score = -5
        reason.append(
            f"과열 구간 (5일:{sma_5_div:.1f}%, 10일:{sma_10_div:.1f}%, 20일:{sma_20_div:.1f}%)"
        )

    return {
        "signal": signal,
        "reason": "; ".join(reason) if reason else "중립",
        "score": score,
        "divergences": divergences,
    } 