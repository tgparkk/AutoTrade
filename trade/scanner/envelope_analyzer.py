"""
엔벨로프 및 신고가 분석 모듈

엔벨로프(Envelope) 계산:
- period: 10
- percent: 10
- 상한선: MA(period) * (1 + percent/100)
- 하한선: MA(period) * (1 - percent/100)

200일 신고가 및 엔벨로프 돌파 조건 분석
"""

from typing import List, Dict, Optional, Tuple
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = [
    "calculate_envelope",
    "check_envelope_breakout",
    "is_200day_high",
    "analyze_envelope_conditions",
    "get_envelope_position"
]


def calculate_envelope(prices: List[float], 
                      period: int = 10, 
                      percent: float = 10.0) -> Optional[Dict[str, float]]:
    """엔벨로프 계산
    
    Args:
        prices: 가격 리스트 (최신순)
        period: 이동평균 기간 (기본 10일)
        percent: 엔벨로프 퍼센트 (기본 10%)
        
    Returns:
        엔벨로프 딕셔너리 또는 None
        {
            'ma': 이동평균,
            'upper': 상한선,
            'lower': 하한선,
            'current_price': 현재 가격,
            'envelope_width': 엔벨로프 폭
        }
    """
    if len(prices) < period:
        logger.debug(f"가격 데이터 부족: {len(prices)} < {period}")
        return None
    
    try:
        # 이동평균 계산
        recent_prices = prices[:period]
        ma = sum(recent_prices) / period
        
        # 엔벨로프 계산
        multiplier = percent / 100.0
        upper = ma * (1 + multiplier)
        lower = ma * (1 - multiplier)
        
        current_price = prices[0] if prices else 0
        envelope_width = upper - lower
        
        return {
            'ma': ma,
            'upper': upper,
            'lower': lower,
            'current_price': current_price,
            'envelope_width': envelope_width
        }
        
    except Exception as e:
        logger.error(f"엔벨로프 계산 오류: {e}")
        return None


def check_envelope_breakout(envelope: Dict[str, float], 
                          breakout_threshold: float = 1.0) -> Dict[str, bool]:
    """엔벨로프 돌파 여부 확인
    
    Args:
        envelope: calculate_envelope 결과
        breakout_threshold: 돌파 임계값 퍼센트 (기본 1%)
        
    Returns:
        돌파 상태 딕셔너리
        {
            'upper_breakout': 상한선 돌파,
            'lower_breakout': 하한선 돌파,
            'above_ma': 이동평균 위,
            'breakout_strength': 돌파 강도
        }
    """
    if not envelope or 'current_price' not in envelope:
        return {
            'upper_breakout': False,
            'lower_breakout': False,
            'above_ma': False,
            'breakout_strength': 0.0
        }
    
    current_price = envelope['current_price']
    upper = envelope['upper']
    lower = envelope['lower']
    ma = envelope['ma']
    
    # 돌파 임계값 적용
    threshold_multiplier = 1 + (breakout_threshold / 100.0)
    
    upper_breakout = current_price >= (upper * threshold_multiplier)
    lower_breakout = current_price <= (lower / threshold_multiplier)
    above_ma = current_price > ma
    
    # 돌파 강도 계산
    if upper_breakout and upper > 0:
        breakout_strength = (current_price / upper - 1) * 100
    elif lower_breakout and lower > 0:
        breakout_strength = (1 - current_price / lower) * 100
    else:
        breakout_strength = (current_price / ma - 1) * 100 if ma > 0 else 0
    
    return {
        'upper_breakout': upper_breakout,
        'lower_breakout': lower_breakout,
        'above_ma': above_ma,
        'breakout_strength': breakout_strength
    }


def is_200day_high(prices: List[float], lookback_period: int = 200) -> Dict[str, any]:
    """200일 신고가 여부 확인
    
    Args:
        prices: 가격 리스트 (최신순, 종가 기준)
        lookback_period: 조회 기간 (기본 200일)
        
    Returns:
        신고가 분석 결과
        {
            'is_new_high': 신고가 여부,
            'days_since_high': 신고가 이후 경과일,
            'high_price': 최고가,
            'current_price': 현재가,
            'high_ratio': 현재가/최고가 비율
        }
    """
    if not prices:
        return {
            'is_new_high': False,
            'days_since_high': -1,
            'high_price': 0,
            'current_price': 0,
            'high_ratio': 0
        }
    
    current_price = prices[0]
    
    # 조회 기간 제한
    check_period = min(len(prices), lookback_period)
    if check_period <= 1:
        return {
            'is_new_high': True,
            'days_since_high': 0,
            'high_price': current_price,
            'current_price': current_price,
            'high_ratio': 1.0
        }
    
    # 최고가 찾기
    price_window = prices[:check_period]
    high_price = max(price_window)
    
    # 신고가 여부
    is_new_high = current_price >= high_price
    
    # 최고가 이후 경과일 계산
    days_since_high = 0
    if not is_new_high:
        for i, price in enumerate(price_window):
            if price == high_price:
                days_since_high = i
                break
    
    # 현재가/최고가 비율
    high_ratio = current_price / high_price if high_price > 0 else 0
    
    return {
        'is_new_high': is_new_high,
        'days_since_high': days_since_high,
        'high_price': high_price,
        'current_price': current_price,
        'high_ratio': high_ratio
    }


def analyze_envelope_conditions(prices: List[float], 
                               volumes: List[float],
                               opens: List[float] = None,
                               highs: List[float] = None,
                               lows: List[float] = None) -> Dict[str, any]:
    """엔벨로프 기반 종합 조건 분석
    
    주어진 조건들:
    A. 최고종가: [일]0봉 전 종가가 200봉 중 최고종가
    B. [일]0봉 전 Envelope(10,10) 종가가 Envelope 상한선 이상
    C. 주가비교: [일]0봉 전 시가 < 0봉 전 종가  
    D. 전일 동시간 대비 거래량 비율 100% 이상
    E. 주가비교: [일]0봉 전 종가 > 0봉 전 (고가+저가)/2
    F. 5일 평균 거래대금(단위: 백만) 5000이상 (금일 제외)
    G. 기간 내 등락률: [일]0봉 전 1봉 이내에서 전일 종가대비 시가 7% 이상
    H. 기간 내 등락률: [일]1봉 전 1봉 이내에서 전일 종가대비 종가 10% 이상
    I. 기간내 등락률: [일]0봉 전 1봉 이내에서 시가대비 종가 3% 이상
    
    조건식: A and B and C and D and E and F and !G and !H and I
    """
    if len(prices) < 2:
        return {'conditions_met': False, 'details': {}}
    
    try:
        # 기본 데이터 설정
        current_close = prices[0]  # 당일 종가
        prev_close = prices[1] if len(prices) > 1 else current_close  # 전일 종가
        current_open = opens[0] if opens and len(opens) > 0 else current_close
        current_high = highs[0] if highs and len(highs) > 0 else current_close
        current_low = lows[0] if lows and len(lows) > 0 else current_close
        current_volume = volumes[0] if volumes and len(volumes) > 0 else 0
        
        # A. 200일 신고가 확인
        high_analysis = is_200day_high(prices, 200)
        condition_a = high_analysis['is_new_high']
        
        # B. 엔벨로프 상한선 돌파
        envelope = calculate_envelope(prices[1:], period=10, percent=10)  # 전일 기준
        condition_b = False
        if envelope:
            condition_b = current_close >= envelope['upper']
        
        # C. 시가 < 종가 (양봉)
        condition_c = current_open < current_close
        
        # D. 거래량 비율 (간단화: 전일 대비)
        condition_d = False
        if len(volumes) > 1 and volumes[1] > 0:
            volume_ratio = current_volume / volumes[1]
            condition_d = volume_ratio >= 1.0
        
        # E. 종가 > (고가+저가)/2
        mid_price = (current_high + current_low) / 2
        condition_e = current_close > mid_price
        
        # F. 5일 평균 거래대금 (단순화: 5000만원 이상)
        condition_f = True  # 실제 구현시 거래대금 계산 필요
        if len(volumes) >= 6:  # 금일 제외하고 5일
            avg_volume = sum(volumes[1:6]) / 5
            # 거래대금 = 평균거래량 * 평균가격 추정
            avg_price = sum(prices[1:6]) / 5
            avg_trading_value = avg_volume * avg_price / 1_000_000  # 백만원 단위
            condition_f = avg_trading_value >= 5000
        
        # G. 시가 전일종가 대비 7% 이상 상승 (제외 조건)
        condition_g = False
        if prev_close > 0:
            open_change_rate = (current_open / prev_close - 1) * 100
            condition_g = open_change_rate >= 7.0
        
        # H. 전일 종가 전전일종가 대비 10% 이상 상승 (제외 조건)
        condition_h = False
        if len(prices) >= 3 and prices[2] > 0:
            prev_change_rate = (prev_close / prices[2] - 1) * 100
            condition_h = prev_change_rate >= 10.0
        
        # I. 시가 대비 종가 3% 이상 상승
        condition_i = False
        if current_open > 0:
            intraday_change_rate = (current_close / current_open - 1) * 100
            condition_i = intraday_change_rate >= 3.0
        
        # 최종 조건: A and B and C and D and E and F and !G and !H and I
        conditions_met = (condition_a and condition_b and condition_c and 
                         condition_d and condition_e and condition_f and 
                         not condition_g and not condition_h and condition_i)
        
        details = {
            'A_200day_high': condition_a,
            'B_envelope_breakout': condition_b,
            'C_bullish_candle': condition_c,
            'D_volume_increase': condition_d,
            'E_above_mid_price': condition_e,
            'F_trading_value': condition_f,
            'G_gap_up_7pct': condition_g,
            'H_prev_up_10pct': condition_h,
            'I_intraday_up_3pct': condition_i,
            'envelope_data': envelope,
            'high_analysis': high_analysis
        }
        
        return {
            'conditions_met': conditions_met,
            'details': details
        }
        
    except Exception as e:
        logger.error(f"엔벨로프 조건 분석 오류: {e}")
        return {'conditions_met': False, 'details': {}}


def get_envelope_position(envelope: Dict[str, float]) -> Tuple[float, str]:
    """엔벨로프 내 가격 위치 계산
    
    Args:
        envelope: calculate_envelope 결과
        
    Returns:
        (위치 비율, 위치 설명)
        위치 비율: 0.0(하한선) ~ 1.0(상한선)
    """
    if not envelope or 'current_price' not in envelope:
        return 0.5, "데이터 없음"
    
    current_price = envelope['current_price']
    upper = envelope['upper']
    lower = envelope['lower']
    
    if upper <= lower:
        return 0.5, "엔벨로프 오류"
    
    # 위치 비율 계산
    position_ratio = (current_price - lower) / (upper - lower)
    position_ratio = max(0.0, min(1.0, position_ratio))
    
    # 위치 설명
    if position_ratio >= 0.9:
        description = "상한선 돌파"
    elif position_ratio >= 0.7:
        description = "상단 영역"
    elif position_ratio >= 0.3:
        description = "중앙 영역"
    elif position_ratio >= 0.1:
        description = "하단 영역"
    else:
        description = "하한선 돌파"
    
    return position_ratio, description


def calculate_envelope_momentum(prices: List[float], 
                               period: int = 10,
                               lookback: int = 5) -> Dict[str, float]:
    """엔벨로프 기준 모멘텀 계산
    
    Args:
        prices: 가격 리스트 (최신순)
        period: 엔벨로프 기간
        lookback: 모멘텀 계산 기간
        
    Returns:
        모멘텀 분석 결과
    """
    if len(prices) < period + lookback:
        return {'momentum': 0, 'trend': 'neutral'}
    
    try:
        # 현재와 과거 엔벨로프 계산
        current_envelope = calculate_envelope(prices[:period], period)
        past_envelope = calculate_envelope(prices[lookback:period+lookback], period)
        
        if not current_envelope or not past_envelope:
            return {'momentum': 0, 'trend': 'neutral'}
        
        # 엔벨로프 위치 변화 계산
        current_pos, _ = get_envelope_position(current_envelope)
        past_pos, _ = get_envelope_position(past_envelope)
        
        momentum = current_pos - past_pos
        
        if momentum > 0.1:
            trend = 'bullish'
        elif momentum < -0.1:
            trend = 'bearish'
        else:
            trend = 'neutral'
        
        return {
            'momentum': momentum,
            'trend': trend,
            'current_position': current_pos,
            'past_position': past_pos
        }
        
    except Exception as e:
        logger.error(f"엔벨로프 모멘텀 계산 오류: {e}")
        return {'momentum': 0, 'trend': 'neutral'}