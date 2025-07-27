"""
거래량 볼린저밴드 계산 모듈

거래량 볼린저밴드 공식:
- 중심선: avg(ma(v,n,단순),period)
- 상한선: avg(ma(v,n,단순),period) + d1*stdev((ma(v,n,단순),period)
- 하한선: avg(ma(v,n,단순),period) - d1*stdev((ma(v,n,단순),period)

기본 파라미터:
- period = 20
- d1 = 2  
- n = 3
"""

from typing import List, Dict, Optional, Tuple
import statistics
from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = [
    "calculate_volume_bollinger_bands",
    "check_volume_breakout",
    "is_volume_above_centerline",
    "get_volume_band_position"
]


def calculate_volume_moving_average(volumes: List[float], period: int = 3) -> List[float]:
    """거래량 이동평균 계산 (n일)
    
    Args:
        volumes: 거래량 리스트 (최신순)
        period: 이동평균 기간 (기본 3일)
        
    Returns:
        거래량 이동평균 리스트
    """
    if len(volumes) < period:
        return []
    
    ma_list = []
    for i in range(len(volumes) - period + 1):
        window = volumes[i:i + period]
        ma_value = sum(window) / period
        ma_list.append(ma_value)
    
    return ma_list


def calculate_volume_bollinger_bands(volumes: List[float], 
                                   period: int = 20, 
                                   std_multiplier: float = 2.0,
                                   ma_period: int = 3) -> Optional[Dict[str, float]]:
    """거래량 볼린저밴드 계산
    
    Args:
        volumes: 거래량 리스트 (최신순, 최소 period + ma_period - 1개 필요)
        period: 볼린저밴드 기간 (기본 20일)
        std_multiplier: 표준편차 배수 (기본 2.0)
        ma_period: 거래량 이동평균 기간 (기본 3일)
        
    Returns:
        볼린저밴드 딕셔너리 또는 None
        {
            'upper': 상한선,
            'middle': 중심선,
            'lower': 하한선,
            'current_ma': 현재 거래량 이동평균,
            'band_width': 밴드폭,
            'squeeze_ratio': 밀집도 (낮을수록 밀집)
        }
    """
    required_length = period + ma_period - 1
    if len(volumes) < required_length:
        logger.debug(f"거래량 데이터 부족: {len(volumes)} < {required_length}")
        return None
    
    try:
        # 1. 거래량 이동평균 계산
        volume_ma_list = calculate_volume_moving_average(volumes, ma_period)
        
        if len(volume_ma_list) < period:
            logger.debug(f"거래량 MA 데이터 부족: {len(volume_ma_list)} < {period}")
            return None
        
        # 2. 최근 period개의 거래량 이동평균 사용
        recent_ma_list = volume_ma_list[:period]
        
        # 3. 볼린저밴드 계산
        middle = sum(recent_ma_list) / len(recent_ma_list)  # 중심선
        
        # 표준편차 계산
        variance = sum((x - middle) ** 2 for x in recent_ma_list) / len(recent_ma_list)
        std_dev = variance ** 0.5
        
        upper = middle + (std_multiplier * std_dev)  # 상한선
        lower = middle - (std_multiplier * std_dev)  # 하한선
        
        # 4. 현재 거래량 이동평균
        current_ma = volume_ma_list[0] if volume_ma_list else 0
        
        # 5. 밴드폭 및 밀집도 계산
        band_width = upper - lower
        squeeze_ratio = band_width / middle if middle > 0 else 0
        
        return {
            'upper': upper,
            'middle': middle,
            'lower': lower,
            'current_ma': current_ma,
            'band_width': band_width,
            'squeeze_ratio': squeeze_ratio
        }
        
    except Exception as e:
        logger.error(f"거래량 볼린저밴드 계산 오류: {e}")
        return None


def check_volume_breakout(bands: Dict[str, float], 
                         threshold_ratio: float = 1.05) -> Dict[str, bool]:
    """거래량 볼린저밴드 돌파 여부 확인
    
    Args:
        bands: calculate_volume_bollinger_bands 결과
        threshold_ratio: 돌파 임계값 비율 (기본 1.05 = 5% 여유)
        
    Returns:
        돌파 상태 딕셔너리
        {
            'upper_breakout': 상한선 돌파,
            'lower_breakout': 하한선 돌파,
            'above_middle': 중심선 위,
            'breakout_strength': 돌파 강도 (배수)
        }
    """
    if not bands or 'current_ma' not in bands:
        return {
            'upper_breakout': False,
            'lower_breakout': False,
            'above_middle': False,
            'breakout_strength': 0.0
        }
    
    current_ma = bands['current_ma']
    upper = bands['upper']
    lower = bands['lower']
    middle = bands['middle']
    
    # 돌파 여부 확인
    upper_breakout = current_ma >= (upper * threshold_ratio)
    lower_breakout = current_ma <= (lower / threshold_ratio)
    above_middle = current_ma > middle
    
    # 돌파 강도 계산
    if upper_breakout and upper > 0:
        breakout_strength = current_ma / upper
    elif lower_breakout and lower > 0:
        breakout_strength = lower / current_ma
    else:
        breakout_strength = current_ma / middle if middle > 0 else 0
    
    return {
        'upper_breakout': upper_breakout,
        'lower_breakout': lower_breakout,
        'above_middle': above_middle,
        'breakout_strength': breakout_strength
    }


def is_volume_above_centerline(bands: Dict[str, float]) -> bool:
    """거래량이 볼린저밴드 중심선 위에 있는지 확인
    
    Args:
        bands: calculate_volume_bollinger_bands 결과
        
    Returns:
        중심선 위 여부
    """
    if not bands or 'current_ma' not in bands or 'middle' not in bands:
        return False
    
    return bands['current_ma'] > bands['middle']


def get_volume_band_position(bands: Dict[str, float]) -> Tuple[float, str]:
    """거래량의 볼린저밴드 내 위치 계산
    
    Args:
        bands: calculate_volume_bollinger_bands 결과
        
    Returns:
        (위치 비율, 위치 설명)
        위치 비율: 0.0(하한선) ~ 1.0(상한선)
    """
    if not bands or 'current_ma' not in bands:
        return 0.5, "데이터 없음"
    
    current_ma = bands['current_ma']
    upper = bands['upper']
    lower = bands['lower']
    middle = bands['middle']
    
    if upper <= lower:
        return 0.5, "밴드 오류"
    
    # 위치 비율 계산 (0.0 ~ 1.0)
    position_ratio = (current_ma - lower) / (upper - lower)
    position_ratio = max(0.0, min(1.0, position_ratio))
    
    # 위치 설명
    if position_ratio >= 0.8:
        description = "상단 영역"
    elif position_ratio >= 0.6:
        description = "상단 근처"
    elif position_ratio >= 0.4:
        description = "중앙 영역"
    elif position_ratio >= 0.2:
        description = "하단 근처"
    else:
        description = "하단 영역"
    
    return position_ratio, description


def analyze_volume_compression(bands_history: List[Dict[str, float]], 
                             compression_threshold: float = 0.3) -> Dict[str, any]:
    """거래량 볼린저밴드 압축(밀집) 상태 분석
    
    Args:
        bands_history: 과거 볼린저밴드 데이터 리스트 (최신순)
        compression_threshold: 압축 임계값 (squeeze_ratio 기준)
        
    Returns:
        압축 분석 결과
        {
            'is_compressed': 현재 압축 상태,
            'compression_days': 연속 압축 일수,
            'avg_squeeze_ratio': 평균 밀집도,
            'compression_trend': 압축 트렌드 ('increasing', 'decreasing', 'stable')
        }
    """
    if not bands_history:
        return {
            'is_compressed': False,
            'compression_days': 0,
            'avg_squeeze_ratio': 0.0,
            'compression_trend': 'stable'
        }
    
    # 현재 압축 상태
    current_bands = bands_history[0]
    is_compressed = current_bands.get('squeeze_ratio', 1.0) < compression_threshold
    
    # 연속 압축 일수 계산
    compression_days = 0
    for bands in bands_history:
        if bands.get('squeeze_ratio', 1.0) < compression_threshold:
            compression_days += 1
        else:
            break
    
    # 평균 밀집도
    squeeze_ratios = [bands.get('squeeze_ratio', 0) for bands in bands_history[:5]]
    avg_squeeze_ratio = sum(squeeze_ratios) / len(squeeze_ratios) if squeeze_ratios else 0
    
    # 압축 트렌드 분석 (최근 3일)
    if len(bands_history) >= 3:
        recent_ratios = [bands.get('squeeze_ratio', 0) for bands in bands_history[:3]]
        if recent_ratios[0] < recent_ratios[2]:
            compression_trend = 'increasing'  # 압축 강화
        elif recent_ratios[0] > recent_ratios[2]:
            compression_trend = 'decreasing'  # 압축 완화
        else:
            compression_trend = 'stable'
    else:
        compression_trend = 'stable'
    
    return {
        'is_compressed': is_compressed,
        'compression_days': compression_days,
        'avg_squeeze_ratio': avg_squeeze_ratio,
        'compression_trend': compression_trend
    }