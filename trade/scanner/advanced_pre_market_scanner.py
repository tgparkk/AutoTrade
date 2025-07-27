"""
고급 장전 스캐너 - 눌림목 매매 전략 기반

주요 특징:
1. 거래량 급증 + 장대양봉 패턴 감지
2. 200일 신고가 + 엔벨로프 돌파 조건
3. 눌림목 진입 타이밍 분석
4. 모듈화된 구조로 유지보수성 향상

스캐닝 전략:
- 당일 거래량 급증 (평소 대비 3배 이상)
- 분봉에서 상승폭 > 하락폭
- 중심가격 지지 유지
- 엔벨로프 상한선 돌파
"""

from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import asyncio

from models.stock import Stock
from utils.logger import setup_logger
from utils.korean_time import now_kst

# 모듈 imports
from .volume_bollinger import (
    calculate_volume_bollinger_bands,
    check_volume_breakout,
    is_volume_above_centerline
)
from .envelope_analyzer import (
    calculate_envelope,
    check_envelope_breakout,
    is_200day_high,
    analyze_envelope_conditions
)
from .pullback_detector import (
    detect_pullback_pattern,
    calculate_pullback_score,
    analyze_entry_timing
)

logger = setup_logger(__name__)

__all__ = ["AdvancedPreMarketScanner"]


class AdvancedPreMarketScanner:
    """고급 장전 스캐너 - 눌림목 매매 전략"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """스캐너 초기화
        
        Args:
            config: 스캐너 설정 딕셔너리
        """
        self.config = config or {}
        
        # 기본 설정값
        self.volume_surge_threshold = self.config.get('volume_surge_threshold', 3.0)
        self.min_trading_value = self.config.get('min_trading_value', 5000)  # 백만원
        self.pullback_threshold = self.config.get('pullback_threshold', 0.02)  # 2%
        self.max_gap_up = self.config.get('max_gap_up', 0.07)  # 7%
        self.max_prev_gain = self.config.get('max_prev_gain', 0.10)  # 10%
        self.min_intraday_gain = self.config.get('min_intraday_gain', 0.03)  # 3%
        self.early_surge_limit = self.config.get('early_surge_limit', 0.20)  # 20%
        
        # 점수 가중치
        self.weights = {
            'volume': self.config.get('volume_weight', 0.25),
            'envelope': self.config.get('envelope_weight', 0.25),
            'pullback': self.config.get('pullback_weight', 0.30),
            'momentum': self.config.get('momentum_weight', 0.20)
        }
        
        logger.info("AdvancedPreMarketScanner 초기화 완료")
    
    def analyze_stock(self, stock_code: str, stock_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """개별 종목 분석
        
        Args:
            stock_code: 종목 코드
            stock_data: 종목 데이터 (OHLCV + 기타)
            
        Returns:
            분석 결과 딕셔너리 또는 None
        """
        try:
            # 데이터 추출
            prices = stock_data.get('closes', [])
            volumes = stock_data.get('volumes', [])
            opens = stock_data.get('opens', [])
            highs = stock_data.get('highs', [])
            lows = stock_data.get('lows', [])
            
            if not all([prices, volumes, opens, highs, lows]) or len(prices) < 10:
                logger.debug(f"{stock_code}: 데이터 부족")
                return None
            
            # 1. 기본 필터링
            if not self._basic_filter(stock_code, stock_data):
                return None
            
            # 2. 거래량 분석
            volume_analysis = self._analyze_volume_pattern(volumes)
            if not volume_analysis['qualified']:
                logger.debug(f"{stock_code}: 거래량 조건 미충족")
                return None
            
            # 3. 엔벨로프 분석
            envelope_analysis = self._analyze_envelope_pattern(
                prices, volumes, opens, highs, lows
            )
            
            # 4. 눌림목 패턴 분석
            pullback_analysis = self._analyze_pullback_pattern(
                opens, highs, lows, prices, volumes
            )
            
            # 5. 종합 점수 계산
            final_score = self._calculate_final_score(
                volume_analysis, envelope_analysis, pullback_analysis
            )
            
            # 6. 결과 구성
            result = {
                'stock_code': stock_code,
                'timestamp': now_kst().isoformat(),
                'final_score': final_score,
                'volume_analysis': volume_analysis,
                'envelope_analysis': envelope_analysis,
                'pullback_analysis': pullback_analysis,
                'current_price': prices[0],
                'entry_signal': self._generate_entry_signal(
                    final_score, pullback_analysis
                ),
                'risk_level': self._assess_risk_level(
                    stock_data, envelope_analysis, pullback_analysis
                )
            }
            
            return result
            
        except Exception as e:
            logger.error(f"{stock_code} 분석 오류: {e}")
            return None
    
    def _basic_filter(self, stock_code: str, stock_data: Dict[str, Any]) -> bool:
        """기본 필터링 조건
        
        Args:
            stock_code: 종목 코드
            stock_data: 종목 데이터
            
        Returns:
            필터링 통과 여부
        """
        try:
            prices = stock_data.get('closes', [])
            volumes = stock_data.get('volumes', [])
            opens = stock_data.get('opens', [])
            
            if not all([prices, volumes, opens]):
                return False
            
            current_price = prices[0]
            current_volume = volumes[0]
            current_open = opens[0]
            
            # 최소 가격 조건 (100원 이상)
            if current_price < 100:
                return False
            
            # 장 초반 급등 제외 (20% 이상 상승시)
            if len(prices) > 1:
                prev_close = prices[1]
                intraday_gain = (current_price / prev_close - 1) if prev_close > 0 else 0
                if intraday_gain > self.early_surge_limit:
                    logger.debug(f"{stock_code}: 장 초반 급등 제외 ({intraday_gain:.1%})")
                    return False
            
            # 최소 거래량 조건
            if current_volume < 100000:  # 10만주
                return False
            
            # 일봉 단위 거래대금 조건 (100억 이상)
            trading_value = current_price * current_volume / 100_000_000  # 억원 단위
            if trading_value < 100:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"{stock_code} 기본 필터링 오류: {e}")
            return False
    
    def _analyze_volume_pattern(self, volumes: List[float]) -> Dict[str, Any]:
        """거래량 패턴 분석
        
        Args:
            volumes: 거래량 리스트
            
        Returns:
            거래량 분석 결과
        """
        try:
            # 거래량 볼린저밴드 계산
            vol_bands = calculate_volume_bollinger_bands(volumes)
            if not vol_bands:
                return {'qualified': False, 'score': 0}
            
            # 거래량 돌파 확인
            breakout_analysis = check_volume_breakout(vol_bands)
            
            # 중심선 위 여부
            above_centerline = is_volume_above_centerline(vol_bands)
            
            # 거래량 급증 확인
            current_volume = volumes[0]
            avg_volume = vol_bands['middle']
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
            
            # 점수 계산
            score = 0
            if breakout_analysis['upper_breakout']:
                score += 40
            elif above_centerline:
                score += 20
            
            if volume_ratio >= self.volume_surge_threshold:
                score += 30
            elif volume_ratio >= 2.0:
                score += 15
            
            # 볼린저밴드 압축 후 확장 보너스
            squeeze_ratio = vol_bands.get('squeeze_ratio', 1.0)
            if squeeze_ratio < 0.3 and breakout_analysis['upper_breakout']:
                score += 20
            
            qualified = (score >= 50 and volume_ratio >= 2.0)
            
            return {
                'qualified': qualified,
                'score': min(100, score),
                'volume_ratio': volume_ratio,
                'breakout_analysis': breakout_analysis,
                'vol_bands': vol_bands,
                'above_centerline': above_centerline
            }
            
        except Exception as e:
            logger.error(f"거래량 패턴 분석 오류: {e}")
            return {'qualified': False, 'score': 0}
    
    def _analyze_envelope_pattern(self, prices: List[float], volumes: List[float],
                                 opens: List[float], highs: List[float], 
                                 lows: List[float]) -> Dict[str, Any]:
        """엔벨로프 패턴 분석
        
        Args:
            prices: 종가 리스트
            volumes: 거래량 리스트
            opens: 시가 리스트
            highs: 고가 리스트
            lows: 저가 리스트
            
        Returns:
            엔벨로프 분석 결과
        """
        try:
            # 엔벨로프 조건 분석
            envelope_conditions = analyze_envelope_conditions(
                prices, volumes, opens, highs, lows
            )
            
            # 200일 신고가 확인
            high_analysis = is_200day_high(prices)
            
            # 엔벨로프 계산
            envelope = calculate_envelope(prices[1:], period=10, percent=10)
            breakout = check_envelope_breakout(envelope) if envelope else {}
            
            # 점수 계산
            score = 0
            details = envelope_conditions.get('details', {})
            
            # 주요 조건들 점수화
            if details.get('A_200day_high'):
                score += 25
            if details.get('B_envelope_breakout'):
                score += 25
            if details.get('C_bullish_candle'):
                score += 15
            if details.get('E_above_mid_price'):
                score += 15
            if details.get('I_intraday_up_3pct'):
                score += 20
            
            # 제외 조건들 페널티
            if details.get('G_gap_up_7pct'):
                score -= 30
            if details.get('H_prev_up_10pct'):
                score -= 30
            
            qualified = envelope_conditions.get('conditions_met', False)
            
            return {
                'qualified': qualified,
                'score': max(0, min(100, score)),
                'envelope_conditions': envelope_conditions,
                'high_analysis': high_analysis,
                'envelope': envelope,
                'breakout': breakout
            }
            
        except Exception as e:
            logger.error(f"엔벨로프 패턴 분석 오류: {e}")
            return {'qualified': False, 'score': 0}
    
    def _analyze_pullback_pattern(self, opens: List[float], highs: List[float],
                                 lows: List[float], closes: List[float],
                                 volumes: List[float]) -> Dict[str, Any]:
        """눌림목 패턴 분석
        
        Args:
            opens: 시가 리스트
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
            volumes: 거래량 리스트
            
        Returns:
            눌림목 분석 결과
        """
        try:
            # 눌림목 패턴 감지
            pullback_data = detect_pullback_pattern(
                opens, highs, lows, closes, volumes, self.pullback_threshold
            )
            
            # 눌림목 점수 계산
            pullback_score = calculate_pullback_score(pullback_data)
            
            # 진입 타이밍 분석
            entry_timing = analyze_entry_timing(
                pullback_data, closes[0], target_profit_rate=0.03
            )
            
            qualified = pullback_data.get('is_pullback', False)
            
            return {
                'qualified': qualified,
                'score': pullback_score,
                'pullback_data': pullback_data,
                'entry_timing': entry_timing,
                'confidence': pullback_data.get('confidence', 0)
            }
            
        except Exception as e:
            logger.error(f"눌림목 패턴 분석 오류: {e}")
            return {'qualified': False, 'score': 0}
    
    def _calculate_final_score(self, volume_analysis: Dict[str, Any],
                              envelope_analysis: Dict[str, Any],
                              pullback_analysis: Dict[str, Any]) -> float:
        """최종 종합 점수 계산
        
        Args:
            volume_analysis: 거래량 분석 결과
            envelope_analysis: 엔벨로프 분석 결과  
            pullback_analysis: 눌림목 분석 결과
            
        Returns:
            최종 점수 (0-100)
        """
        try:
            volume_score = volume_analysis.get('score', 0)
            envelope_score = envelope_analysis.get('score', 0)
            pullback_score = pullback_analysis.get('score', 0)
            
            # 모멘텀 보너스 (모든 조건이 양호할 때)
            momentum_bonus = 0
            if (volume_analysis.get('qualified', False) and
                envelope_analysis.get('qualified', False) and
                pullback_analysis.get('qualified', False)):
                momentum_bonus = 15
            
            # 가중평균 계산
            final_score = (
                volume_score * self.weights['volume'] +
                envelope_score * self.weights['envelope'] +
                pullback_score * self.weights['pullback'] +
                momentum_bonus * self.weights['momentum']
            )
            
            return min(100, max(0, final_score))
            
        except Exception as e:
            logger.error(f"최종 점수 계산 오류: {e}")
            return 0
    
    def _generate_entry_signal(self, final_score: float, 
                              pullback_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """매수 진입 신호 생성
        
        Args:
            final_score: 최종 점수
            pullback_analysis: 눌림목 분석 결과
            
        Returns:
            진입 신호 정보
        """
        try:
            entry_timing = pullback_analysis.get('entry_timing', {})
            
            # 신호 강도 결정
            if final_score >= 80:
                signal_strength = 'strong'
            elif final_score >= 70:
                signal_strength = 'medium'
            elif final_score >= 60:
                signal_strength = 'weak'
            else:
                signal_strength = 'none'
            
            # 진입 신호 여부
            entry_signal = (
                final_score >= 70 and
                entry_timing.get('entry_signal', False)
            )
            
            return {
                'signal': entry_signal,
                'strength': signal_strength,
                'score': final_score,
                'target_price': entry_timing.get('target_price', 0),
                'stop_loss': entry_timing.get('stop_loss', 0),
                'confidence': entry_timing.get('confidence', 0)
            }
            
        except Exception as e:
            logger.error(f"진입 신호 생성 오류: {e}")
            return {'signal': False, 'strength': 'none'}
    
    def _assess_risk_level(self, stock_data: Dict[str, Any],
                          envelope_analysis: Dict[str, Any],
                          pullback_analysis: Dict[str, Any]) -> str:
        """리스크 레벨 평가
        
        Args:
            stock_data: 종목 데이터
            envelope_analysis: 엔벨로프 분석
            pullback_analysis: 눌림목 분석
            
        Returns:
            리스크 레벨 ('low', 'medium', 'high')
        """
        try:
            risk_factors = 0
            
            # 변동성 리스크
            prices = stock_data.get('closes', [])
            if len(prices) >= 5:
                recent_volatility = max(prices[:5]) / min(prices[:5]) - 1
                if recent_volatility > 0.15:  # 15% 이상 변동
                    risk_factors += 1
            
            # 거래량 리스크 (과도한 급증)
            volumes = stock_data.get('volumes', [])
            if len(volumes) >= 2:
                volume_ratio = volumes[0] / volumes[1] if volumes[1] > 0 else 0
                if volume_ratio > 10:  # 10배 이상 급증
                    risk_factors += 1
            
            # 엔벨로프 리스크
            envelope_details = envelope_analysis.get('envelope_conditions', {}).get('details', {})
            if envelope_details.get('G_gap_up_7pct') or envelope_details.get('H_prev_up_10pct'):
                risk_factors += 1
            
            # 눌림목 신뢰도
            pullback_confidence = pullback_analysis.get('confidence', 0)
            if pullback_confidence < 60:
                risk_factors += 1
            
            # 리스크 레벨 결정
            if risk_factors >= 3:
                return 'high'
            elif risk_factors >= 2:
                return 'medium'
            else:
                return 'low'
                
        except Exception as e:
            logger.error(f"리스크 평가 오류: {e}")
            return 'medium'
    
    def scan_multiple_stocks(self, stocks_data: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """복수 종목 스캔
        
        Args:
            stocks_data: {종목코드: 종목데이터} 딕셔너리
            
        Returns:
            정렬된 분석 결과 리스트
        """
        results = []
        
        for stock_code, stock_data in stocks_data.items():
            analysis_result = self.analyze_stock(stock_code, stock_data)
            if analysis_result and analysis_result['final_score'] > 0:
                results.append(analysis_result)
        
        # 점수순 정렬
        results.sort(key=lambda x: x['final_score'], reverse=True)
        
        logger.info(f"스캔 완료: {len(results)}개 종목 분석")
        return results
    
    def get_top_candidates(self, scan_results: List[Dict[str, Any]], 
                          top_n: int = 10, min_score: float = 60) -> List[Dict[str, Any]]:
        """상위 후보 종목 선별
        
        Args:
            scan_results: 스캔 결과 리스트
            top_n: 상위 n개 선별
            min_score: 최소 점수 기준
            
        Returns:
            선별된 상위 후보 리스트
        """
        # 최소 점수 이상 필터링
        qualified = [r for r in scan_results if r['final_score'] >= min_score]
        
        # 상위 n개 선별
        top_candidates = qualified[:top_n]
        
        logger.info(f"상위 후보 선별: {len(top_candidates)}개 (최소점수: {min_score})")
        return top_candidates