# -*- coding: utf-8 -*-
"""trade.scanner.fundamental

실제 OHLCV(raw) 데이터로부터 거래량, 가격 변동, 기술적 지표(RSI, MACD 등)
그리고 이동평균 정배열 여부 등을 계산해 dict 형태로 반환합니다.
MarketScanner 뿐 아니라 향후 다른 스캐너/전략에서도 사용할 수 있도록
클래스가 아닌 순수 함수로 구현했습니다.
"""
from typing import Any, Dict, List, Optional

import pandas as pd
from utils.logger import setup_logger
from utils.technical_indicators import compute_indicators, check_ma_alignment
from trade.scanner.utils import (
    get_data_length as _get_data_length,
    convert_to_dict_list as _convert_to_dict_list,
)

logger = setup_logger(__name__)

__all__ = ["calculate_fundamentals"]


def calculate_fundamentals(stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
    """OHLCV DataFrame(또는 list) 기반 기본 지표 계산.

    Args:
        stock_code: 종목코드
        ohlcv_data: DataFrame or list(dict) – 최근 순(내림차순) 데이터

    Returns:
        Dict[str, Any] | None – 분석 실패/데이터 부족 시 None
    """
    # 최소 20일 데이터 필요
    if _get_data_length(ohlcv_data) < 20:
        logger.warning(f"데이터가 부족합니다 {stock_code}: {_get_data_length(ohlcv_data)}일")
        return None

    try:
        data_list = _convert_to_dict_list(ohlcv_data)
        if not data_list:
            logger.warning(f"OHLCV 데이터 변환 실패: {stock_code}")
            return None

        # 최근 20일 데이터 – API 특성상 최신→과거 순으로 이미 정렬되어 있다고 가정.
        recent_data: List[Dict] = data_list[:20]

        # 거래량 관련 지표
        recent_volumes = [float(day.get("acml_vol", 0)) for day in recent_data[:5]]
        previous_volumes = [float(day.get("acml_vol", 0)) for day in recent_data[5:10]]

        recent_avg_vol = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 1
        previous_avg_vol = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 1
        volume_increase_rate = recent_avg_vol / previous_avg_vol if previous_avg_vol > 0 else 1

        # 10일 평균 거래량 및 거래대금(저유동 필터)
        ten_day_data = recent_data[:10]
        all_volumes_10d = [float(day.get("acml_vol", 0)) for day in ten_day_data]
        avg_daily_volume_10d = sum(all_volumes_10d) / len(all_volumes_10d) if all_volumes_10d else 0
        avg_daily_trading_value = avg_daily_volume_10d * float(recent_data[0].get("stck_clpr", 0))

        # 가격 변동률(전일 대비)
        today_close = float(recent_data[0].get("stck_clpr", 0))
        yesterday_close = float(recent_data[1].get("stck_clpr", 0)) if len(recent_data) > 1 else today_close
        price_change_rate = (
            (today_close - yesterday_close) / yesterday_close if yesterday_close > 0 else 0
        )

        # 기술적 지표(RSI/MACD 등)
        try:
            df_full = pd.DataFrame(recent_data[::-1])  # 오래된→신규 순으로 변환
            indi = compute_indicators(df_full, close_col="stck_clpr", volume_col="acml_vol")
            rsi = indi.get("rsi", 50)
            macd_val = indi.get("macd", 0)
            macd_signal = indi.get("macd_signal", 0)
            macd_hist = indi.get("macd_hist", 0)
            volume_spike = indi.get("volume_spike", 1)
        except Exception as e:
            logger.debug(f"기술적 지표 계산 실패 {stock_code}: {e}")
            rsi = 50
            macd_val = macd_signal = macd_hist = 0
            volume_spike = 1

        # 이동평균선 정배열 여부
        closes_for_ma = [float(day.get("stck_clpr", 0)) for day in recent_data]
        ma_alignment = check_ma_alignment(closes_for_ma)

        return {
            "volume_increase_rate": volume_increase_rate,
            "yesterday_volume": int(recent_volumes[1]) if len(recent_volumes) > 1 else 0,
            "avg_daily_volume": avg_daily_volume_10d,
            "avg_daily_trading_value": avg_daily_trading_value,
            "price_change_rate": price_change_rate,
            "rsi": rsi,
            "macd_signal": macd_signal,
            "macd": macd_val,
            "macd_hist": macd_hist,
            "volume_spike_ratio": volume_spike,
            "ma_alignment": ma_alignment,
            "support_level": min(float(day.get("stck_lwpr", 0)) for day in recent_data[:10]),
            "resistance_level": max(float(day.get("stck_hgpr", 0)) for day in recent_data[:10]),
        }

    except Exception as e:
        logger.error(f"기본 분석 실패 {stock_code}: {e}")
        return None 