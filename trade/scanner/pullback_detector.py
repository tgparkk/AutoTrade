"""
눌림목 패턴 감지 모듈

눌림목 매매 전략:
1. 중심가격 [(고가+저가)/2] 을 지켜주는 흐름
2. 일봉 상 대량 거래량이 동반될 조짐
3. 분봉차트에서 상승폭이 하락폭보다 긴 것을 확인하거나 예상될 때

핵심 개념:
- 이등분선: 당일 고가와 저가를 더한 후 2로 나눈 값 (당일 등락폭의 중간)
- 대량 거래량을 동반해 장대양봉으로 마감하는 종목은 이등분선을 지키는 경향이 높음
"""

from typing import List, Dict, Optional, Tuple
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = [
    "calculate_midpoint_support",
    "detect_pullback_pattern",
    "analyze_volume_momentum",
    "check_uptrend_dominance",
    "calculate_pullback_score"
]


def calculate_midpoint_support(highs: List[float], 
                              lows: List[float], 
                              closes: List[float]) -> Dict[str, float]:
    """중심가격(이등분선) 지지 분석
    
    Args:
        highs: 고가 리스트 (최신순)
        lows: 저가 리스트 (최신순)  
        closes: 종가 리스트 (최신순)
        
    Returns:
        중심가격 분석 결과
        {
            'current_midpoint': 당일 중심가격,
            'current_close': 현재 종가,
            'above_midpoint': 중심가격 위 여부,
            'midpoint_ratio': 종가/중심가격 비율,
            'support_strength': 지지 강도 (최근 5일)
        }
    """
    if not highs or not lows or not closes:
        return {
            'current_midpoint': 0,
            'current_close': 0,
            'above_midpoint': False,
            'midpoint_ratio': 0,
            'support_strength': 0
        }
    
    try:
        # 당일 중심가격 계산
        current_high = highs[0]
        current_low = lows[0]
        current_close = closes[0]
        current_midpoint = (current_high + current_low) / 2
        
        # 중심가격 위 여부
        above_midpoint = current_close > current_midpoint
        
        # 종가/중심가격 비율
        midpoint_ratio = current_close / current_midpoint if current_midpoint > 0 else 0
        
        # 지지 강도 계산 (최근 5일간 중심가격 위에서 마감한 비율)
        support_count = 0
        check_days = min(5, len(highs), len(lows), len(closes))
        
        for i in range(check_days):
            day_midpoint = (highs[i] + lows[i]) / 2
            if closes[i] > day_midpoint:
                support_count += 1
        
        support_strength = support_count / check_days if check_days > 0 else 0
        
        return {
            'current_midpoint': current_midpoint,
            'current_close': current_close,
            'above_midpoint': above_midpoint,
            'midpoint_ratio': midpoint_ratio,
            'support_strength': support_strength
        }
        
    except Exception as e:
        logger.error(f"중심가격 지지 분석 오류: {e}")
        return {
            'current_midpoint': 0,
            'current_close': 0,
            'above_midpoint': False,
            'midpoint_ratio': 0,
            'support_strength': 0
        }


def analyze_volume_momentum(volumes: List[float], 
                           volume_threshold_multiplier: float = 3.0) -> Dict[str, any]:
    """거래량 모멘텀 분석
    
    Args:
        volumes: 거래량 리스트 (최신순)
        volume_threshold_multiplier: 거래량 급증 임계값 (기본 3배)
        
    Returns:
        거래량 모멘텀 분석 결과
        {
            'current_volume': 현재 거래량,
            'avg_volume': 평균 거래량 (최근 5일),
            'volume_ratio': 현재/평균 비율,
            'volume_surge': 거래량 급증 여부,
            'momentum_trend': 모멘텀 트렌드
        }
    """
    if not volumes:
        return {
            'current_volume': 0,
            'avg_volume': 0,
            'volume_ratio': 0,
            'volume_surge': False,
            'momentum_trend': 'neutral'
        }
    
    try:
        current_volume = volumes[0]
        
        # 평균 거래량 계산 (최근 5일, 당일 제외)
        if len(volumes) > 1:
            recent_volumes = volumes[1:min(6, len(volumes))]
            avg_volume = sum(recent_volumes) / len(recent_volumes)
        else:
            avg_volume = current_volume
        
        # 거래량 비율
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        
        # 거래량 급증 여부
        volume_surge = volume_ratio >= volume_threshold_multiplier
        
        # 모멘텀 트렌드 (최근 3일 거래량 변화)
        momentum_trend = 'neutral'
        if len(volumes) >= 3:
            recent_3day = volumes[:3]
            if recent_3day[0] > recent_3day[1] > recent_3day[2]:
                momentum_trend = 'increasing'
            elif recent_3day[0] < recent_3day[1] < recent_3day[2]:
                momentum_trend = 'decreasing'
            elif recent_3day[0] > recent_3day[2]:
                momentum_trend = 'volatile_up'
            else:
                momentum_trend = 'neutral'
        
        return {
            'current_volume': current_volume,
            'avg_volume': avg_volume,
            'volume_ratio': volume_ratio,
            'volume_surge': volume_surge,
            'momentum_trend': momentum_trend
        }
        
    except Exception as e:
        logger.error(f"거래량 모멘텀 분석 오류: {e}")
        return {
            'current_volume': 0,
            'avg_volume': 0,
            'volume_ratio': 0,
            'volume_surge': False,
            'momentum_trend': 'neutral'
        }


def check_uptrend_dominance(opens: List[float],
                           highs: List[float], 
                           lows: List[float],
                           closes: List[float],
                           lookback_period: int = 5) -> Dict[str, any]:
    """상승폭이 하락폭보다 긴지 확인 (분봉 기준)
    
    Args:
        opens: 시가 리스트 (최신순)
        highs: 고가 리스트 (최신순)
        lows: 저가 리스트 (최신순)
        closes: 종가 리스트 (최신순)
        lookback_period: 분석 기간 (기본 5봉)
        
    Returns:
        상승 우세 분석 결과
        {
            'uptrend_dominance': 상승 우세 여부,
            'up_candle_ratio': 양봉 비율,
            'avg_up_body': 평균 양봉 몸통,
            'avg_down_body': 평균 음봉 몸통,
            'body_ratio': 양봉/음봉 몸통 비율
        }
    """
    if not all([opens, highs, lows, closes]) or len(opens) < lookback_period:
        return {
            'uptrend_dominance': False,
            'up_candle_ratio': 0,
            'avg_up_body': 0,
            'avg_down_body': 0,
            'body_ratio': 0
        }
    
    try:
        check_period = min(lookback_period, len(opens))
        up_candles = 0
        up_bodies = []
        down_bodies = []
        
        for i in range(check_period):
            open_price = opens[i]
            close_price = closes[i]
            body_size = abs(close_price - open_price)
            
            if close_price > open_price:  # 양봉
                up_candles += 1
                up_bodies.append(body_size)
            else:  # 음봉
                down_bodies.append(body_size)
        
        # 양봉 비율
        up_candle_ratio = up_candles / check_period
        
        # 평균 몸통 크기
        avg_up_body = sum(up_bodies) / len(up_bodies) if up_bodies else 0
        avg_down_body = sum(down_bodies) / len(down_bodies) if down_bodies else 0
        
        # 몸통 비율 (양봉/음봉)
        body_ratio = avg_up_body / avg_down_body if avg_down_body > 0 else float('inf')
        
        # 상승 우세 판단 기준
        uptrend_dominance = (up_candle_ratio >= 0.6 and body_ratio >= 1.2)
        
        return {
            'uptrend_dominance': uptrend_dominance,
            'up_candle_ratio': up_candle_ratio,
            'avg_up_body': avg_up_body,
            'avg_down_body': avg_down_body,
            'body_ratio': body_ratio
        }
        
    except Exception as e:
        logger.error(f"상승 우세 분석 오류: {e}")
        return {
            'uptrend_dominance': False,
            'up_candle_ratio': 0,
            'avg_up_body': 0,
            'avg_down_body': 0,
            'body_ratio': 0
        }


def detect_pullback_pattern(opens: List[float],
                           highs: List[float],
                           lows: List[float], 
                           closes: List[float],
                           volumes: List[float],
                           pullback_threshold: float = 0.02) -> Dict[str, any]:
    """눌림목 패턴 감지
    
    Args:
        opens: 시가 리스트
        highs: 고가 리스트  
        lows: 저가 리스트
        closes: 종가 리스트
        volumes: 거래량 리스트
        pullback_threshold: 눌림목 임계값 (기본 2%)
        
    Returns:
        눌림목 패턴 분석 결과
    """
    if not all([opens, highs, lows, closes, volumes]):
        return {'is_pullback': False, 'confidence': 0}
    
    try:
        # 1. 중심가격 지지 분석
        midpoint_analysis = calculate_midpoint_support(highs, lows, closes)
        
        # 2. 거래량 모멘텀 분석  
        volume_analysis = analyze_volume_momentum(volumes)
        
        # 3. 상승 우세 분석
        uptrend_analysis = check_uptrend_dominance(opens, highs, lows, closes)
        
        # 4. 눌림목 여부 판단
        current_high = highs[0]
        recent_high = max(highs[:min(5, len(highs))])
        pullback_from_high = (recent_high - closes[0]) / recent_high if recent_high > 0 else 0
        
        # 눌림목 조건들
        conditions = {
            'midpoint_support': midpoint_analysis['above_midpoint'],
            'volume_surge': volume_analysis['volume_surge'],
            'uptrend_dominance': uptrend_analysis['uptrend_dominance'],
            'mild_pullback': 0 < pullback_from_high <= pullback_threshold,
            'strong_support': midpoint_analysis['support_strength'] >= 0.6
        }
        
        # 신뢰도 계산
        condition_scores = {
            'midpoint_support': 30,
            'volume_surge': 25,
            'uptrend_dominance': 20,
            'mild_pullback': 15,
            'strong_support': 10
        }
        
        confidence = sum(condition_scores[k] for k, v in conditions.items() if v)
        
        # 눌림목 패턴 여부 (70점 이상)
        is_pullback = confidence >= 70
        
        return {
            'is_pullback': is_pullback,
            'confidence': confidence,
            'conditions': conditions,
            'midpoint_analysis': midpoint_analysis,
            'volume_analysis': volume_analysis,
            'uptrend_analysis': uptrend_analysis,
            'pullback_from_high': pullback_from_high
        }
        
    except Exception as e:
        logger.error(f"눌림목 패턴 감지 오류: {e}")
        return {'is_pullback': False, 'confidence': 0}


def calculate_pullback_score(pullback_data: Dict[str, any],
                            volume_weight: float = 0.3,
                            midpoint_weight: float = 0.3,
                            uptrend_weight: float = 0.4) -> float:
    """눌림목 종합 점수 계산
    
    Args:
        pullback_data: detect_pullback_pattern 결과
        volume_weight: 거래량 가중치
        midpoint_weight: 중심가격 가중치  
        uptrend_weight: 상승 추세 가중치
        
    Returns:
        종합 점수 (0-100)
    """
    if not pullback_data or 'confidence' not in pullback_data:
        return 0
    
    try:
        base_confidence = pullback_data['confidence']
        
        # 세부 점수 계산
        volume_score = 0
        if 'volume_analysis' in pullback_data:
            vol_data = pullback_data['volume_analysis']
            volume_score = min(100, vol_data.get('volume_ratio', 0) * 20)
        
        midpoint_score = 0  
        if 'midpoint_analysis' in pullback_data:
            mid_data = pullback_data['midpoint_analysis']
            midpoint_score = mid_data.get('support_strength', 0) * 100
        
        uptrend_score = 0
        if 'uptrend_analysis' in pullback_data:
            up_data = pullback_data['uptrend_analysis']
            uptrend_score = (up_data.get('up_candle_ratio', 0) * 50 + 
                           min(50, up_data.get('body_ratio', 0) * 25))
        
        # 가중평균 계산
        weighted_score = (
            volume_score * volume_weight +
            midpoint_score * midpoint_weight + 
            uptrend_score * uptrend_weight
        )
        
        # 기본 신뢰도와 결합
        final_score = (base_confidence * 0.4 + weighted_score * 0.6)
        
        return min(100, max(0, final_score))
        
    except Exception as e:
        logger.error(f"눌림목 점수 계산 오류: {e}")
        return 0


def analyze_entry_timing(pullback_data: Dict[str, any],
                        current_price: float,
                        target_profit_rate: float = 0.03) -> Dict[str, any]:
    """눌림목 진입 타이밍 분석
    
    Args:
        pullback_data: 눌림목 분석 결과
        current_price: 현재 가격
        target_profit_rate: 목표 수익률 (기본 3%)
        
    Returns:
        진입 타이밍 분석 결과
    """
    if not pullback_data or not pullback_data.get('is_pullback'):
        return {
            'entry_signal': False,
            'entry_strength': 'weak',
            'target_price': 0,
            'stop_loss': 0
        }
    
    try:
        midpoint_data = pullback_data.get('midpoint_analysis', {})
        current_midpoint = midpoint_data.get('current_midpoint', current_price)
        
        # 진입 신호 강도
        confidence = pullback_data.get('confidence', 0)
        if confidence >= 80:
            entry_strength = 'strong'
        elif confidence >= 70:
            entry_strength = 'medium'
        else:
            entry_strength = 'weak'
        
        # 진입 신호 (중심가격 근처에서 반등할 때)
        entry_signal = (
            pullback_data.get('is_pullback', False) and
            midpoint_data.get('above_midpoint', False) and
            confidence >= 70
        )
        
        # 목표 가격 (현재가 + 목표 수익률)
        target_price = current_price * (1 + target_profit_rate)
        
        # 손절가 (중심가격 하회시)
        stop_loss = current_midpoint * 0.98  # 2% 여유
        
        return {
            'entry_signal': entry_signal,
            'entry_strength': entry_strength,
            'target_price': target_price,
            'stop_loss': stop_loss,
            'confidence': confidence
        }
        
    except Exception as e:
        logger.error(f"진입 타이밍 분석 오류: {e}")
        return {
            'entry_signal': False,
            'entry_strength': 'weak',
            'target_price': 0,
            'stop_loss': 0
        }