from typing import Dict, Any

from models.stock import Stock
from utils.technical_indicators import (
    calculate_divergence_rate,
    calculate_sma,
)

__all__ = [
    "get_stock_divergence_rates",
    "get_stock_divergence_signal",
]


def get_stock_divergence_rates(stock: Stock) -> Dict[str, float]:
    """Stock 객체의 실시간 이격도 계산

    Args:
        stock: Stock 객체

    Returns:
        key = 지표명, value = 이격도(%).
    """
    current_price = stock.realtime_data.current_price
    if current_price <= 0:
        return {}

    divergences: Dict[str, float] = {}

    # 20일선 이격도 (기준 데이터에서)
    if stock.reference_data.sma_20 > 0:
        divergences["sma_20"] = calculate_divergence_rate(
            current_price, stock.reference_data.sma_20
        )

    # 전일 종가 이격도
    if stock.reference_data.yesterday_close > 0:
        divergences["yesterday_close"] = calculate_divergence_rate(
            current_price, stock.reference_data.yesterday_close
        )

    # 당일 시가 이격도 (분봉 데이터가 있을 경우)
    if stock.minute_1_data:
        first_candle = stock.minute_1_data[0]
        if first_candle.open_price > 0:
            divergences["today_open"] = calculate_divergence_rate(
                current_price, first_candle.open_price
            )

    # 5분봉 단순 이동평균 이격도 (최근 5개 캔들)
    if len(stock.minute_5_data) >= 5:
        recent_prices = [candle.close_price for candle in stock.minute_5_data[-5:]]
        sma_5min = calculate_sma(recent_prices, 5)
        if sma_5min > 0:
            divergences["sma_5min"] = calculate_divergence_rate(current_price, sma_5min)

    # 당일 고저점 대비 위치 (%)
    if stock.realtime_data.today_high > 0 and stock.realtime_data.today_low > 0:
        day_range = stock.realtime_data.today_high - stock.realtime_data.today_low
        if day_range > 0:
            divergences["daily_position"] = (
                (current_price - stock.realtime_data.today_low) / day_range * 100
            )

    return divergences


def get_stock_divergence_signal(stock: Stock) -> Dict[str, Any]:
    """Stock 객체의 이격도 기반 실시간 매매 신호

    Args:
        stock: Stock 객체

    Returns:
        dict with signal, reason, strength, divergences keys.
    """
    divergences = get_stock_divergence_rates(stock)
    if not divergences:
        return {"signal": "HOLD", "reason": "이격도 계산 불가", "strength": 0}

    sma_20_div = divergences.get("sma_20", 0)
    sma_5min_div = divergences.get("sma_5min", 0)
    daily_pos = divergences.get("daily_position", 50)

    signal = "HOLD"
    reason = []
    strength = 0  # 신호 강도 (0~10)

    # 강한 매수 신호
    if sma_20_div <= -3 and daily_pos <= 20:
        signal = "STRONG_BUY"
        strength = 8 + min(abs(sma_20_div), 7)
        reason.append(
            f"강한 매수 (20일선:{sma_20_div:.1f}%, 일봉위치:{daily_pos:.0f}%)"
        )

    # 일반 매수 신호
    elif sma_20_div <= -2 or (sma_5min_div <= -1.5 and daily_pos <= 30):
        signal = "BUY"
        strength = 5 + min(abs(sma_20_div), 3)
        reason.append(
            f"매수 신호 (20일선:{sma_20_div:.1f}%, 5분선:{sma_5min_div:.1f}%)"
        )

    # 강한 매도 신호
    elif sma_20_div >= 5 and daily_pos >= 80:
        signal = "STRONG_SELL"
        strength = -(8 + min(sma_20_div, 7))
        reason.append(
            f"강한 매도 (20일선:{sma_20_div:.1f}%, 일봉위치:{daily_pos:.0f}%)"
        )

    # 일반 매도 신호
    elif sma_20_div >= 3 or (sma_5min_div >= 2 and daily_pos >= 70):
        signal = "SELL"
        strength = -(5 + min(sma_20_div, 3))
        reason.append(
            f"매도 신호 (20일선:{sma_20_div:.1f}%, 5분선:{sma_5min_div:.1f}%)"
        )

    # 중립
    elif abs(sma_20_div) <= 1 and 30 <= daily_pos <= 70:
        signal = "NEUTRAL"
        strength = 1
        reason.append("이격도 중립")

    return {
        "signal": signal,
        "reason": "; ".join(reason) if reason else "보류",
        "strength": strength,
        "divergences": divergences,
    } 