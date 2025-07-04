from __future__ import annotations

"""scoring.py
MarketScanner 외부로 분리된 종합 점수 계산 로직.
calculate_comprehensive_score(scanner, stock_code) 만 공개합니다.
"""

from typing import Optional, Dict, Any, TYPE_CHECKING

from utils.logger import setup_logger
from trade.scanner.utils import (
    is_data_empty as _is_data_empty,
    get_data_length as _get_data_length,
    convert_to_dict_list as _convert_to_dict_list,
)

logger = setup_logger(__name__)

if TYPE_CHECKING:
    from trade.market_scanner import MarketScanner

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_comprehensive_score(scanner: "MarketScanner", stock_code: str) -> Optional[float]:
    """MarketScanner.calculate_comprehensive_score 분리본.

    내부에서 scanner 인스턴스의 메서드와 설정에 접근합니다.
    외부 의존성을 최소화하기 위해 scanner 객체를 그대로 전달받습니다.
    """
    # 실제 API에서 데이터 조회 (한 번만 호출하여 효율성 향상)
    ohlcv_data = None
    try:
        from api.kis_market_api import get_inquire_daily_itemchartprice

        logger.debug(f"📊 {stock_code} API 호출 시작")
        ohlcv_data = get_inquire_daily_itemchartprice(
            output_dv="2",
            itm_no=stock_code,
            period_code="D",
            adj_prc="1",
        )

        if ohlcv_data is not None:
            logger.debug(
                f"📊 {stock_code} API 성공: 타입={type(ohlcv_data)}, 길이={len(ohlcv_data)}"
            )
        else:
            logger.debug(f"📊 {stock_code} API 실패: None 반환")
    except Exception as e:
        logger.debug(f"📊 {stock_code} API 호출 실패: {e}")

    # 기본 분석 (같은 데이터 재사용)
    if _is_data_empty(ohlcv_data):
        logger.debug(f"📊 {stock_code} 데이터 없음으로 종목 제외")
        return None

    logger.debug(f"📊 {stock_code} 기본 분석 시작")
    fundamentals = scanner._calculate_real_fundamentals(stock_code, ohlcv_data)
    if not fundamentals:
        logger.debug(f"📊 {stock_code} 기본 분석 실패로 종목 제외")
        return None

    # 저유동성 필터
    if fundamentals.get("avg_daily_trading_value", 0) < scanner.min_trading_value:
        logger.debug(
            f"📊 {stock_code} 평균 거래대금 {fundamentals.get('avg_daily_trading_value',0)/1_000_000:,.1f}M < "
            f"min_trading_value({scanner.min_trading_value/1_000_000}M) – 제외"
        )
        return None

    # 캔들패턴 분석
    if _get_data_length(ohlcv_data) < 5:
        logger.debug(
            f"📊 {stock_code} 캔들패턴 분석용 데이터 부족으로 종목 제외 (길이: {_get_data_length(ohlcv_data)})"
        )
        return None

    logger.debug(f"📊 {stock_code} 캔들패턴 분석 시작")
    patterns = scanner._analyze_real_candle_patterns(stock_code, ohlcv_data)
    if not patterns:
        logger.debug(f"📊 {stock_code} 캔들패턴 분석 실패로 종목 제외")
        return None

    # 이격도 분석
    logger.debug(f"📊 {stock_code} 이격도 분석 시작")
    divergence_analysis = scanner._get_divergence_analysis(stock_code, ohlcv_data)
    divergence_signal = (
        scanner._get_divergence_signal(divergence_analysis) if divergence_analysis else None
    )

    # 시간외 단일가 기반 점수 계산
    preopen_score = 0
    preopen_data: Dict[str, Any] = {}
    try:
        from api.kis_preopen_api import get_preopen_overtime_price

        pre_df = get_preopen_overtime_price(stock_code)
        if pre_df is not None and not pre_df.empty:
            row = pre_df.iloc[0]
            after_price = float(row.get("ovtm_untp_prpr", 0))
            after_volume = float(row.get("ovtm_untp_vol", 0))

            pre_trading_value = after_price * after_volume  # 원 단위

            if str(row.get("trht_yn", "N")).upper() == "Y":
                logger.debug(f"🚫 {stock_code} 거래정지 표시 – 제외")
                return None

            # 🔧 시간외 거래대금 필터링 로직 개선
            min_pre_val = scanner.performance_config.get("preopen_min_trading_value", 50_000_000)
            
            # 시간외 거래대금이 매우 낮거나 0일 때는 전일 거래대금 기준으로 판단
            if pre_trading_value < 10_000_000:  # 1000만원 미만이면 전일 기준 사용
                avg_daily_trading_value = fundamentals.get("avg_daily_trading_value", 0)
                # 전일 거래대금이 최소 기준의 50% 이상이면 통과
                min_daily_threshold = min_pre_val * 2  # 전일은 더 관대하게 (2배)
                if avg_daily_trading_value >= min_daily_threshold:
                    logger.debug(
                        f"📊 {stock_code} 시간외 거래 부족({pre_trading_value/1_000_000:,.1f}M)하지만 "
                        f"전일 거래대금({avg_daily_trading_value/1_000_000:,.1f}M) 충분 – 통과"
                    )
                    # 전일 기준으로 점수 조정 (조금 낮게)
                    if avg_daily_trading_value >= 1_000_000_000:  # 10억 이상
                        pre_val_score = 3
                    elif avg_daily_trading_value >= 500_000_000:  # 5억 이상
                        pre_val_score = 1
                    else:
                        pre_val_score = 0
                else:
                    logger.debug(
                        f"📊 {stock_code} 시간외 거래대금 {pre_trading_value/1_000_000:,.1f}M 및 "
                        f"전일 거래대금 {avg_daily_trading_value/1_000_000:,.1f}M 모두 부족 – 제외"
                    )
                    return None
            else:
                # 시간외 거래대금이 충분한 경우 기존 로직 사용
                if pre_trading_value < min_pre_val:
                    logger.debug(
                        f"📊 {stock_code} 시간외 거래대금 {pre_trading_value/1_000_000:,.1f}M <"
                        f" min_pre_val({min_pre_val/1_000_000}M) – 제외"
                    )
                    return None
                
                # 기존 점수 계산
                if pre_trading_value >= 500_000_000:
                    pre_val_score = 10
                elif pre_trading_value >= 100_000_000:
                    pre_val_score = 5
                elif pre_trading_value >= 50_000_000:
                    pre_val_score = 0
                else:
                    pre_val_score = -5

            try:
                data_list = _convert_to_dict_list(ohlcv_data)
                yesterday_close = float(data_list[0].get("stck_clpr", 0)) if data_list else 0
            except Exception:
                yesterday_close = 0

            if after_price > 0 and yesterday_close > 0:
                gap_rate = (after_price - yesterday_close) / yesterday_close * 100

                if gap_rate >= 5:
                    gap_score = 10
                elif gap_rate >= 3:
                    gap_score = 7
                elif gap_rate >= 1:
                    gap_score = 4
                elif gap_rate <= -3:
                    gap_score = -5
                elif gap_rate <= -1:
                    gap_score = -2
                else:
                    gap_score = 0

                preopen_score = gap_score + pre_val_score

                preopen_data = {
                    "gap_rate": gap_rate,
                    "trading_value": pre_trading_value,
                }

                logger.debug(
                    f"📊 {stock_code} 시간외 갭 {gap_rate:+.2f}% → preopen_score {preopen_score:+}"
                )
    except Exception as e:
        logger.debug(f"📊 {stock_code} 시간외 단일가 API 실패: {e}")

    # 유동성 점수
    try:
        liq_score = scanner.stock_manager.get_liquidity_score(stock_code)
    except AttributeError:
        liq_score = 0.0
    fundamentals["liquidity_score"] = liq_score

    # 데이트레이딩 최적화 점수 계산
    from utils.technical_indicators import calculate_daytrading_score

    total_score, score_detail = calculate_daytrading_score(
        fundamentals=fundamentals,
        patterns=patterns,
        divergence_signal=divergence_signal or {},
        preopen_data=preopen_data,
        config=scanner.daytrading_config,
    )

    logger.debug(f"📊 {stock_code} {score_detail}")

    return min(total_score, 100) 