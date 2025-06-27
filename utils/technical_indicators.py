from __future__ import annotations

"""Technical indicator helper functions

Light-weight pure-python implementations of commonly used indicators.
All calculations use pandas and do **not** depend on TA-Lib.
Only the latest value(s) are returned for speed – enough for scoring logic.
"""

from typing import Dict, Any

import pandas as pd

__all__ = [
    "compute_indicators",
    "calculate_daytrading_score",
    "DaytradingScoreCalculator",
    "calculate_sma",
    "calculate_divergence_rate",
    "check_ma_alignment",
    "calculate_rsi",
    "calculate_macd_signal_simple",
    "detect_candle_patterns",
    "analyze_candle_patterns",
    "divergence_analysis",
]


def _ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average using pandas ewm."""
    return series.ewm(span=period, adjust=False).mean()


def compute_indicators(
    df: pd.DataFrame,
    close_col: str = "stck_clpr",
    volume_col: str | None = None,
) -> Dict[str, Any]:
    """Calculate basic indicators and return dict with the latest values.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (최신 행이 index 0 인 상태여도 무방함)
    close_col : str
        종가 열 이름
    volume_col : str | None
        거래량 열 이름 – spike 계산용 (선택)
    """
    if df.empty or close_col not in df.columns:
        return {}

    # 정렬: 오래된 → 최신 순으로 가정. 만약 반대라면 sort_index()
    closes = df[close_col].astype(float).reset_index(drop=True)

    # RSI(14)
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14, min_periods=14).mean()
    avg_loss = loss.rolling(window=14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    # MACD (12,26,9)
    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, 9)
    macd_hist = macd_line - signal_line

    # Bollinger Bands(20,2)
    ma20 = closes.rolling(window=20).mean()
    std20 = closes.rolling(window=20).std()
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20

    out: Dict[str, Any] = {
        "rsi": float(rsi.iloc[-1]) if not rsi.empty else None,
        "macd": float(macd_line.iloc[-1]) if not macd_line.empty else None,
        "macd_signal": float(signal_line.iloc[-1]) if not signal_line.empty else None,
        "macd_hist": float(macd_hist.iloc[-1]) if not macd_hist.empty else None,
        "bb_upper": float(bb_upper.iloc[-1]) if not bb_upper.empty else None,
        "bb_lower": float(bb_lower.iloc[-1]) if not bb_lower.empty else None,
        "bb_middle": float(ma20.iloc[-1]) if not ma20.empty else None,
    }

    # Volume spike ratio (1일/20일평균)
    if volume_col and volume_col in df.columns:
        vols = df[volume_col].astype(float)
        vol_spike = None
        if len(vols) >= 20:
            vol_1 = vols.iloc[-1]
            vol_avg_20 = vols.iloc[-20:].mean()
            if vol_avg_20 > 0:
                vol_spike = vol_1 / vol_avg_20
        out["volume_spike"] = vol_spike

    return out


class DaytradingScoreCalculator:
    """데이트레이딩 최적화 종합 점수 계산기"""
    
    def __init__(self, config: Dict[str, Any]):
        """설정 기반 초기화
        
        Args:
            config: 설정 딕셔너리 (PERFORMANCE 섹션)
        """
        self.config = config
        
        # 가중치 설정
        self.volume_weight = config.get('daytrading_volume_weight', 28)
        self.momentum_weight = config.get('daytrading_momentum_weight', 18)
        self.divergence_weight = config.get('daytrading_divergence_weight', 15)
        self.pattern_weight = config.get('daytrading_pattern_weight', 14)
        self.technical_weight = config.get('daytrading_technical_weight', 10)
        self.ma_weight = config.get('daytrading_ma_weight', 15)
        
        # RSI 최적 구간
        self.rsi_optimal_min = config.get('daytrading_rsi_optimal_min', 45)
        self.rsi_optimal_max = config.get('daytrading_rsi_optimal_max', 70)
        self.rsi_momentum_min = config.get('daytrading_rsi_momentum_min', 50)
        
        # 모멘텀 구간별 임계값
        self.momentum_tier1_min = config.get('daytrading_momentum_tier1_min', 0.3) / 100
        self.momentum_tier2_min = config.get('daytrading_momentum_tier2_min', 1.0) / 100
        self.momentum_tier3_min = config.get('daytrading_momentum_tier3_min', 2.5) / 100
        self.momentum_danger_max = config.get('daytrading_momentum_danger_max', 8.0) / 100
        
        # 갭 점수 임계값
        self.gap_optimal_min = config.get('daytrading_gap_optimal_min', 1.0)
        self.gap_optimal_max = config.get('daytrading_gap_optimal_max', 3.0)
        self.gap_acceptable_max = config.get('daytrading_gap_acceptable_max', 5.0)
        self.gap_danger_threshold = config.get('daytrading_gap_danger_threshold', 7.0)
    
    def calculate_volume_score(self, volume_increase_rate: float) -> float:
        """거래량 점수 계산 (28%)
        
        Args:
            volume_increase_rate: 거래량 증가율 (배수)
            
        Returns:
            거래량 점수 (0 ~ volume_weight)
        """
        # 설정 기반 거래량 구간
        tier4_min = self.config.get('daytrading_volume_tier4_min', 3.0)
        tier3_min = self.config.get('daytrading_volume_tier3_min', 2.5)
        tier2_min = self.config.get('daytrading_volume_tier2_min', 2.0)
        tier1_min = self.config.get('daytrading_volume_tier1_min', 1.5)
        
        if volume_increase_rate >= tier4_min:  # 3배 이상 폭증
            return self.volume_weight
        elif volume_increase_rate >= tier3_min:  # 2.5배 이상
            return self.volume_weight * 0.9
        elif volume_increase_rate >= tier2_min:  # 2배 이상  
            return self.volume_weight * 0.8
        elif volume_increase_rate >= tier1_min:  # 1.5배 이상
            return self.volume_weight * 0.6
        else:
            return self.volume_weight * 0.3  # 최소 점수
    
    def calculate_momentum_score(self, price_change_rate: float) -> float:
        """모멘텀 점수 계산 (18%)
        
        Args:
            price_change_rate: 가격 변화율 (소수, 0.03 = 3%)
            
        Returns:
            모멘텀 점수 (0 ~ momentum_weight)
        """
        if price_change_rate >= self.momentum_danger_max:  # 8% 이상 급등 위험
            return self.momentum_weight * 0.3
        elif price_change_rate >= self.momentum_tier3_min:  # 2.5% 이상 강한 상승
            return self.momentum_weight
        elif price_change_rate >= self.momentum_tier2_min:  # 1% 이상 상승
            return self.momentum_weight * 0.8
        elif price_change_rate >= self.momentum_tier1_min:  # 0.3% 이상 미상승
            return self.momentum_weight * 0.6
        elif price_change_rate >= 0:  # 보합
            return self.momentum_weight * 0.3
        else:  # 하락
            return self.momentum_weight * 0.1
    
    def calculate_technical_score(self, rsi: float) -> float:
        """RSI 기술적 점수 계산 (10%)
        
        Args:
            rsi: RSI 값 (0~100)
            
        Returns:
            기술적 점수 (0 ~ technical_weight)
        """
        if self.rsi_momentum_min <= rsi <= self.rsi_optimal_max:  # 최적 상승 모멘텀 구간
            return self.technical_weight
        elif self.rsi_optimal_min <= rsi < self.rsi_momentum_min:  # 적정 구간
            return self.technical_weight * 0.8
        elif rsi > self.rsi_optimal_max:  # 과매수 구간
            return self.technical_weight * 0.4
        else:  # 과매도 구간
            return self.technical_weight * 0.6
    
    def calculate_pattern_score(self, pattern_score: float, max_pattern_score: float = 18) -> float:
        """패턴 점수 계산 (14%)
        
        Args:
            pattern_score: 원본 패턴 점수
            max_pattern_score: 패턴 점수 최대값
            
        Returns:
            정규화된 패턴 점수 (0 ~ pattern_weight)
        """
        if max_pattern_score <= 0:
            return 0
        return min(pattern_score / max_pattern_score * self.pattern_weight, self.pattern_weight)
    
    def calculate_ma_score(self, ma_alignment: bool, current_price: float = 0, sma_5: float = 0) -> float:
        """이동평균 점수 계산 (15%)
        
        Args:
            ma_alignment: 정배열 여부
            current_price: 현재가 (부분 점수 계산용)
            sma_5: 5일 이동평균 (부분 점수 계산용)
            
        Returns:
            이동평균 점수 (0 ~ ma_weight)
        """
        if ma_alignment:
            return self.ma_weight  # 정배열 완전 점수
        else:
            # 현재가가 5일선 위에 있으면 부분 점수
            if current_price > sma_5 > 0:
                return self.ma_weight * 0.6  # 부분 점수
            else:
                return self.ma_weight * 0.2  # 최소 점수
    
    def calculate_divergence_score(self, divergence_signal: Dict[str, Any]) -> float:
        """이격도 점수 계산 (15%)
        
        Args:
            divergence_signal: 이격도 신호 딕셔너리
            
        Returns:
            이격도 점수 (-divergence_weight*0.5 ~ divergence_weight)
        """
        if not divergence_signal:
            return self.divergence_weight * 0.2  # 기본 점수
        
        signal_type = divergence_signal.get('signal', 'HOLD')
        base_score = divergence_signal.get('score', 0)
        
        if signal_type == 'BUY':
            return min(base_score * 0.6, self.divergence_weight)  # 과매도 최고 점수
        elif signal_type == 'MOMENTUM':
            return min(base_score * 0.8, self.divergence_weight * 0.8)  # 상승 모멘텀
        elif signal_type == 'OVERHEATED':
            return max(base_score * 0.5, -self.divergence_weight * 0.5)  # 과열 감점
        else:
            return self.divergence_weight * 0.2  # HOLD 중립 점수
    
    def calculate_gap_score(self, gap_rate: float, pre_trading_value: float) -> float:
        """시간외 갭 점수 계산
        
        Args:
            gap_rate: 갭 비율 (%, 3.5 = 3.5%)
            pre_trading_value: 시간외 거래대금 (원)
            
        Returns:
            갭 점수 (-5 ~ 15)
        """
        # 거래대금 점수
        if pre_trading_value >= 500_000_000:  # 5억 이상
            pre_val_score = 10
        elif pre_trading_value >= 100_000_000:  # 1억 이상
            pre_val_score = 5
        elif pre_trading_value >= 50_000_000:  # 0.5억 이상
            pre_val_score = 0
        else:
            pre_val_score = -5
        
        # 갭 점수 (데이트레이딩 최적화)
        if self.gap_optimal_min <= gap_rate <= self.gap_optimal_max:  # 1-3% 최적
            gap_score = 10
        elif gap_rate <= self.gap_acceptable_max:  # 3-5% 허용
            gap_score = 6
        elif gap_rate >= self.gap_danger_threshold:  # 7% 이상 위험
            gap_score = -3
        elif gap_rate >= 5:  # 5-7% 주의
            gap_score = 2
        elif gap_rate <= -3:  # -3% 이하 급락
            gap_score = -5
        elif gap_rate <= -1:  # -1% 이하 하락
            gap_score = -2
        elif gap_rate < self.gap_optimal_min:  # 1% 미만 미약
            gap_score = 1
        else:
            gap_score = 0
        
        return gap_score + pre_val_score


def calculate_daytrading_score(
    fundamentals: Dict[str, Any],
    patterns: Dict[str, Any],
    divergence_signal: Dict[str, Any],
    preopen_data: Dict[str, Any],
    config: Dict[str, Any]
) -> tuple[float, str]:
    """데이트레이딩 최적화 종합 점수 계산
    
    Args:
        fundamentals: 기본 분석 결과
        patterns: 패턴 분석 결과  
        divergence_signal: 이격도 신호
        preopen_data: 시간외 데이터
        config: 설정 딕셔너리
        
    Returns:
        (종합점수, 점수상세내역) 튜플
    """
    calculator = DaytradingScoreCalculator(config)
    
    # 각 점수 계산
    volume_score = calculator.calculate_volume_score(fundamentals.get('volume_increase_rate', 1.0))
    momentum_score = calculator.calculate_momentum_score(fundamentals.get('price_change_rate', 0.0))
    technical_score = calculator.calculate_technical_score(fundamentals.get('rsi', 50))
    pattern_score = calculator.calculate_pattern_score(patterns.get('pattern_score', 0))
    ma_score = calculator.calculate_ma_score(
        fundamentals.get('ma_alignment', False),
        fundamentals.get('current_price', 0),
        fundamentals.get('sma_5', 0)
    )
    divergence_score = calculator.calculate_divergence_score(divergence_signal)
    
    # 시간외 갭 점수
    gap_score = 0
    if preopen_data:
        gap_score = calculator.calculate_gap_score(
            preopen_data.get('gap_rate', 0),
            preopen_data.get('trading_value', 0)
        )
    
    # 유동성 점수 (별도 처리)
    liquidity_score = fundamentals.get('liquidity_score', 0) * config.get('liquidity_weight', 1.0)
    
    total_score = (volume_score + momentum_score + technical_score + pattern_score + 
                   ma_score + divergence_score + gap_score + liquidity_score)
    
    # 점수 상세 내역
    score_detail = (
        f"거래량({volume_score:.1f}/{calculator.volume_weight}) + "
        f"모멘텀({momentum_score:.1f}/{calculator.momentum_weight}) + "
        f"이격도({divergence_score:+.1f}/{calculator.divergence_weight}) + "
        f"패턴({pattern_score:.1f}/{calculator.pattern_weight}) + "
        f"MA({ma_score:.1f}/{calculator.ma_weight}) + "
        f"RSI({technical_score:.1f}/{calculator.technical_weight}) + "
        f"시간외({gap_score:+}) + 유동성({liquidity_score:+.1f}) = {total_score:.1f}"
    )
    
    return min(total_score, 100), score_detail


# -----------------------------
# Basic utility functions moved from MarketScanner
# -----------------------------

def calculate_sma(prices: list[float], period: int) -> float:
    """단순이동평균(SMA) 계산

    Parameters
    ----------
    prices : list[float]
        가격(종가) 리스트 – 최신값이 index 0 인 형태를 가정해도 무방함
    period : int
        기간(일)

    Returns
    -------
    float
        SMA 값. 데이터 부족 시 0 반환.
    """
    if period <= 0 or len(prices) < period:
        return 0.0

    valid_prices = [p for p in prices[:period] if p > 0]
    if not valid_prices:
        return 0.0

    return sum(valid_prices) / len(valid_prices)


def calculate_divergence_rate(current_price: float, ma_price: float) -> float:
    """현재가 대비 이동평균선 이격도(%) 계산"""
    if current_price <= 0 or ma_price <= 0:
        return 0.0
    return (current_price - ma_price) / ma_price * 100


def check_ma_alignment(closes: list[float]) -> bool:
    """정배열 여부 확인 (현재가 > MA5 > MA10 > MA20)

    Parameters
    ----------
    closes : list[float]
        최근 20개 종가 리스트 (index 0 = 최신)
    """
    if len(closes) < 20:
        return False

    ma5 = calculate_sma(closes, 5)
    ma10 = calculate_sma(closes, 10)
    ma20 = calculate_sma(closes, 20)

    current_price = closes[0]
    return current_price > ma5 > ma10 > ma20


# -------------------------------------------------
# Additional light-weight indicator utilities
# -------------------------------------------------


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """RSI 계산 (단순 Python 구현)

    Parameters
    ----------
    closes : list[float]
        종가 배열 (index 0 = 최신값이거나 상관없음, 순서 무관)
    period : int, default 14
        RSI 기간

    Returns
    -------
    float
        RSI 값 (0~100). 데이터 부족 시 50 반환.
    """
    if len(closes) < period + 1:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        change = closes[i - 1] - closes[i]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd_signal_simple(closes: list[float]) -> str:
    """MACD 신호 간단 계산

    EMA 근사를 위해 단순 평균을 사용(12/26) – 속도 우선.

    Returns
    -------
    str
        'positive' | 'negative' | 'neutral'
    """
    if len(closes) < 26:
        return "neutral"

    ema12 = sum(closes[:12]) / 12
    ema26 = sum(closes[:26]) / 26
    macd_line = ema12 - ema26

    if macd_line > 0:
        return "positive"
    elif macd_line < 0:
        return "negative"
    else:
        return "neutral"


# -------------------------------------------------
# Candle pattern & divergence utilities (from MarketScanner)
# -------------------------------------------------


def detect_candle_patterns(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
) -> dict[str, float]:
    """단일 봉에서 캔들 패턴 탐지 후 점수 매핑

    Parameters
    ----------
    open_p, high_p, low_p, close_p : float
        시고저종 가격 (원화)

    Returns
    -------
    dict[str, float]
        패턴명 → 신뢰도(0~1)
    """
    total_range = high_p - low_p
    if total_range <= 0:
        return {}

    body_size = abs(close_p - open_p)
    upper_shadow = high_p - max(open_p, close_p)
    lower_shadow = min(open_p, close_p) - low_p

    body_ratio = body_size / total_range
    upper_ratio = upper_shadow / total_range
    lower_ratio = lower_shadow / total_range

    patterns: dict[str, float] = {}

    # Hammer
    if lower_ratio > 0.5 and upper_ratio < 0.1 and body_ratio < 0.3:
        patterns["hammer"] = 0.8

    # Bullish engulfing (simplified)
    if close_p > open_p and body_ratio > 0.6:
        patterns["bullish_engulfing"] = 0.9

    # Doji / Dragonfly Doji
    if body_ratio < 0.1:
        if lower_ratio > 0.3:
            patterns["dragonfly_doji"] = 0.7
        else:
            patterns["doji"] = 0.5

    # Inverted hammer
    if upper_ratio > 0.5 and lower_ratio < 0.1 and body_ratio < 0.3:
        patterns["inverted_hammer"] = 0.65

    return patterns


def analyze_candle_patterns(ohlcv_dicts: list[dict]) -> dict | None:
    """최근 5개 일봉 데이터를 분석해 패턴 점수 산출

    Parameters
    ----------
    ohlcv_dicts : list[dict]
        OHLCV 기록 (최신이 index 0)
    """
    if len(ohlcv_dicts) < 5:
        return None

    try:
        recent5 = ohlcv_dicts[:5]

        detected_patterns: dict[str, float] = {}
        pattern_scores: dict[str, float] = {}

        reliability = 1.0
        total_score = 0.0

        for day in recent5:
            op = float(day.get("stck_oprc", 0))
            hp = float(day.get("stck_hgpr", 0))
            lp = float(day.get("stck_lwpr", 0))
            cp = float(day.get("stck_clpr", 0))

            pats = detect_candle_patterns(op, hp, lp, cp)
            for k, v in pats.items():
                detected_patterns[k] = max(detected_patterns.get(k, 0), v)

        # 간단 가중 평균: 패턴별 최고 신뢰도 합
        for pat, conf in detected_patterns.items():
            score = conf  # 0~1
            pattern_scores[pat] = score
            total_score += score

        reliability = min(total_score / len(detected_patterns), 1.0) if detected_patterns else 0
        pattern_score = min(total_score * 18, 18)

        return {
            "detected_patterns": detected_patterns,
            "pattern_scores": pattern_scores,
            "total_pattern_score": total_score,
            "reliability": reliability,
            "pattern_score": pattern_score,
        }
    except Exception:
        return None


def divergence_analysis(prices: list[float]) -> dict | None:
    """20일 가격 리스트로 SMA·이격도 계산"""
    if len(prices) < 20:
        return None

    current_price = prices[0]
    if current_price <= 0:
        return None

    from utils.technical_indicators import calculate_sma, calculate_divergence_rate

    sma5 = calculate_sma(prices, 5)
    sma10 = calculate_sma(prices, 10)
    sma20 = calculate_sma(prices, 20)

    divergences: dict[str, float] = {}
    if sma5 > 0:
        divergences["sma_5"] = calculate_divergence_rate(current_price, sma5)
    if sma10 > 0:
        divergences["sma_10"] = calculate_divergence_rate(current_price, sma10)
    if sma20 > 0:
        divergences["sma_20"] = calculate_divergence_rate(current_price, sma20)

    # yesterday change
    if len(prices) > 1 and prices[1] > 0:
        divergences["yesterday_change"] = calculate_divergence_rate(current_price, prices[1])

    return {
        "current_price": current_price,
        "divergences": divergences,
        "sma_values": {"sma_5": sma5, "sma_10": sma10, "sma_20": sma20},
    }