#!/usr/bin/env python3
"""
매매 조건 분석 및 포지션 사이징을 담당하는 TradingConditionAnalyzer 클래스

주요 기능:
- 매수/매도 조건 분석
- 포지션 사이징 (매수량 계산)
- 매도 조건 성과 분석
- 시장 단계별 조건 조정
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime
from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class TradingConditionAnalyzer:
    """매매 조건 분석 및 포지션 사이징 전담 클래스"""
    
    def __init__(self, stock_manager, trade_executor):
        """TradingConditionAnalyzer 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            trade_executor: 매매 실행자 인스턴스
        """
        self.stock_manager = stock_manager
        self.trade_executor = trade_executor
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.performance_config = self.config_loader.load_performance_config()  # 🆕 성능 설정 추가
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 🔥 설정 기반 공식 문서 기반 고급 매매 지표 임계값 (하드코딩 제거)
        self.contract_strength_threshold = self.performance_config.get('contract_strength_threshold', 120.0)
        self.buy_ratio_threshold = self.performance_config.get('buy_ratio_threshold', 60.0)
        self.vi_activation_threshold = self.performance_config.get('vi_activation_threshold', True)
        self.market_pressure_weight = self.performance_config.get('market_pressure_weight', 0.3)
        
        logger.info("TradingConditionAnalyzer 초기화 완료")
    
    def get_market_phase(self) -> str:
        """현재 시장 단계 확인 (정확한 시장 시간 기준: 09:00~15:30)
        
        Returns:
            시장 단계 ('opening', 'active', 'lunch', 'pre_close', 'closing', 'closed')
        """
        from datetime import time as dt_time
        
        current_time = now_kst().time()
        current_weekday = now_kst().weekday()
        
        # 주말 체크 (토: 5, 일: 6)
        if current_weekday >= 5:
            return 'closed'
        
        # 🔥 정확한 시장 시간 기준 (09:00~15:30)
        market_open = dt_time(9, 0)    # 09:00
        market_close = dt_time(15, 30) # 15:30
        
        # 시장 마감 후
        if current_time > market_close:
            return 'closed'
        
        # 시장 개장 전
        if current_time < market_open:
            return 'closed'
        
        # 시장 시간 내 단계별 구분
        if current_time <= dt_time(9, 30):
            return 'opening'        # 09:00~09:30 장 초반
        elif current_time <= dt_time(12, 0):
            return 'active'         # 09:30~12:00 활성 거래
        elif current_time <= dt_time(13, 0):
            return 'lunch'          # 12:00~13:00 점심시간
        elif current_time <= dt_time(14, 50):
            return 'active'         # 13:00~14:50 활성 거래
        elif current_time <= dt_time(15, 0):
            return 'pre_close'      # 14:50~15:00 마감 전
        else:
            return 'closing'        # 15:00~15:30 마감 시간
    
    def analyze_buy_conditions(self, stock: Stock, realtime_data: Dict, 
                              market_phase: Optional[str] = None) -> bool:
        """매수 조건 분석 (점수 기반 현실적 시스템)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계 (옵션, None이면 자동 계산)
            
        Returns:
            매수 조건 충족 여부
        """
        try:
            # 시장 단계 결정
            if market_phase is None:
                market_phase = self.get_market_phase()
            
            # 기본 조건 체크
            price_change_rate = realtime_data.get('price_change_rate', 0) / 100  # % to decimal
            volume_spike_ratio = realtime_data.get('volume_spike_ratio', 1.0)
            
            # 🆕 공식 문서 기반 고급 지표 추출 (웹소켓에서 받은 추가 데이터)
            contract_strength = getattr(stock.realtime_data, 'contract_strength', 100.0)
            buy_ratio = getattr(stock.realtime_data, 'buy_ratio', 50.0)
            market_pressure = getattr(stock.realtime_data, 'market_pressure', 'NEUTRAL')
            vi_standard_price = getattr(stock.realtime_data, 'vi_standard_price', 0)
            trading_halt = getattr(stock.realtime_data, 'trading_halt', False)
            
            # 절대 금지 조건 (거래정지, VI발동)
            if trading_halt or (vi_standard_price > 0 and self.vi_activation_threshold):
                logger.debug(f"거래 제외: {stock.stock_code} (거래정지: {trading_halt}, VI발동: {vi_standard_price > 0})")
                return False
            
            # 🔥 점수 기반 매수 조건 분석 시작
            total_score = 0
            max_possible_score = 100
            condition_details = []
            
            # 🔥 설정 기반 시장 단계별 조건 조정 (하드코딩 제거)
            if market_phase == 'opening':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * self.strategy_config.get('opening_volume_multiplier', 1.5)
                price_threshold = self.strategy_config.get('opening_price_threshold', 0.015)
                contract_strength_min = self.contract_strength_threshold * self.strategy_config.get('opening_contract_strength_multiplier', 1.2)
                buy_ratio_min = self.buy_ratio_threshold * self.strategy_config.get('opening_buy_ratio_multiplier', 1.1)
                min_pattern_score = self.strategy_config.get('opening_pattern_score_threshold', 75.0)
                required_total_score = self.performance_config.get('buy_score_opening_threshold', 70)
            elif market_phase == 'pre_close':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * self.strategy_config.get('preclose_volume_multiplier', 2.0)
                price_threshold = self.strategy_config.get('preclose_price_threshold', 0.02)
                contract_strength_min = self.contract_strength_threshold * self.strategy_config.get('preclose_contract_strength_multiplier', 1.5)
                buy_ratio_min = self.buy_ratio_threshold * self.strategy_config.get('preclose_buy_ratio_multiplier', 1.2)
                min_pattern_score = self.strategy_config.get('opening_pattern_score_threshold', 75.0)
                required_total_score = self.performance_config.get('buy_score_preclose_threshold', 75)
            else:
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0)
                price_threshold = self.strategy_config.get('normal_price_threshold', 0.01)
                contract_strength_min = self.contract_strength_threshold
                buy_ratio_min = self.buy_ratio_threshold
                min_pattern_score = self.strategy_config.get('normal_pattern_score_threshold', 70.0)
                required_total_score = self.performance_config.get('buy_score_normal_threshold', 60)
            
            # === 점수 계산 시작 (총 100점 만점) ===
            
            # 1. 이격도 조건 (0~25점) - 데이트레이딩 핵심 지표 (가장 중요)
            divergence_score, divergence_info = self._analyze_divergence_buy_score(stock, market_phase)
            total_score += divergence_score
            condition_details.append(f"이격도(+{divergence_score}점, {divergence_info})")
            
            # 2. 거래량 조건 (0~20점) - 모멘텀 확인
            if volume_spike_ratio >= volume_threshold:
                volume_score = min(20, int(volume_spike_ratio / volume_threshold * 15))
                total_score += volume_score
                condition_details.append(f"거래량(+{volume_score}점)")
            elif volume_spike_ratio >= volume_threshold * 0.8:  # 80% 달성시 부분 점수
                volume_score = 12
                total_score += volume_score
                condition_details.append(f"거래량(+{volume_score}점, 부분달성)")
            
            # 3. 가격 상승률 조건 (0~15점)
            if price_change_rate >= price_threshold:
                price_score = min(15, int(price_change_rate / price_threshold * 12))
                total_score += price_score
                condition_details.append(f"상승률(+{price_score}점)")
            elif price_change_rate >= price_threshold * 0.7:  # 70% 달성시 부분 점수
                price_score = 8
                total_score += price_score
                condition_details.append(f"상승률(+{price_score}점, 부분달성)")
            
            # 4. 체결강도 조건 (0~15점)
            if contract_strength >= contract_strength_min:
                strength_score = min(15, int((contract_strength - contract_strength_min) / 20 + 10))
                total_score += strength_score
                condition_details.append(f"체결강도(+{strength_score}점)")
            elif contract_strength >= contract_strength_min * 0.85:  # 85% 달성시 부분 점수
                strength_score = 8
                total_score += strength_score
                condition_details.append(f"체결강도(+{strength_score}점, 부분달성)")
            
            # 5. 패턴 점수 조건 (0~10점)
            if stock.total_pattern_score >= min_pattern_score:
                pattern_score = min(10, int((stock.total_pattern_score - min_pattern_score) / 10 + 7))
                total_score += pattern_score
                condition_details.append(f"패턴(+{pattern_score}점)")
            elif stock.total_pattern_score >= min_pattern_score * 0.8:  # 80% 달성시 부분 점수
                pattern_score = 5
                total_score += pattern_score
                condition_details.append(f"패턴(+{pattern_score}점, 부분달성)")
            
            # 6. 매수비율 조건 (0~10점)
            if buy_ratio >= buy_ratio_min:
                ratio_score = min(10, int((buy_ratio - buy_ratio_min) / 10 + 7))
                total_score += ratio_score
                condition_details.append(f"매수비율(+{ratio_score}점)")
            elif buy_ratio >= buy_ratio_min * 0.8:  # 80% 달성시 부분 점수
                ratio_score = 5
                total_score += ratio_score
                condition_details.append(f"매수비율(+{ratio_score}점, 부분달성)")
            
            # 7. 최소 거래량 조건 (0~5점)
            min_volume = self.strategy_config.get('volume_min_threshold', 100000)
            current_volume = realtime_data.get('volume', 0)
            if current_volume >= min_volume:
                total_score += 5
                condition_details.append("최소거래량(+5점)")
            elif current_volume >= min_volume * 0.7:  # 70% 달성시 부분 점수
                total_score += 3
                condition_details.append("최소거래량(+3점, 부분달성)")
            
            # 8. 시장압력 조건 (0~5점)
            if market_pressure == 'BUY':
                total_score += 5
                condition_details.append("시장압력(+5점, 매수우세)")
            elif market_pressure == 'NEUTRAL':
                total_score += 3
                condition_details.append("시장압력(+3점, 중립)")
            # SELL인 경우 0점 (감점 없음)
            
            # 9. 호가 스프레드 조건 (0~5점)
            bid_price = realtime_data.get('bid_price', 0)
            ask_price = realtime_data.get('ask_price', 0)
            if bid_price > 0 and ask_price > 0:
                spread_rate = (ask_price - bid_price) / bid_price
                spread_threshold = self.strategy_config.get('spread_threshold', 0.01)
                if spread_rate <= spread_threshold:
                    total_score += 5
                    condition_details.append("호가스프레드(+5점)")
                elif spread_rate <= spread_threshold * 2:  # 2배 이내면 부분 점수
                    total_score += 2
                    condition_details.append("호가스프레드(+2점, 부분달성)")
            else:
                total_score += 3  # 데이터 없으면 중간 점수
                condition_details.append("호가스프레드(+3점, 데이터없음)")
            
            # === 최종 매수 신호 판단 ===
            max_possible_score = 100  # 총점 100점으로 수정
            buy_signal = total_score >= required_total_score
            
            if buy_signal:
                logger.info(f"🚀 {stock.stock_code}({stock.stock_name}) 매수 신호 ({market_phase}): "
                           f"총점 {total_score}/{max_possible_score}점 (기준:{required_total_score}점) "
                           f"- {', '.join(condition_details)}")
            else:
                logger.debug(f"❌ {stock.stock_code} 매수 조건 미달: "
                            f"총점 {total_score}/{max_possible_score}점 (기준:{required_total_score}점) "
                            f"- {', '.join(condition_details)}")
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    def _analyze_divergence_buy_score(self, stock: Stock, market_phase: str) -> Tuple[int, str]:
        """이격도 기반 매수 점수 계산 (0~25점) - 데이트레이딩 핵심 지표
        
        Args:
            stock: 주식 객체
            market_phase: 시장 단계
            
        Returns:
            (점수, 디버깅 정보)
        """
        try:
            current_price = stock.realtime_data.current_price
            if current_price > 0 and stock.reference_data.sma_20 > 0:
                sma_20_div = (current_price - stock.reference_data.sma_20) / stock.reference_data.sma_20 * 100
                
                # 당일 고저점 대비 위치 계산
                daily_pos = 50  # 기본값
                if stock.realtime_data.today_high > 0 and stock.realtime_data.today_low > 0:
                    day_range = stock.realtime_data.today_high - stock.realtime_data.today_low
                    if day_range > 0:
                        daily_pos = (current_price - stock.realtime_data.today_low) / day_range * 100
                
                # 🔥 데이트레이딩 최적화된 이격도 평가 (0~25점)
                base_score = 0
                
                # === 기본 이격도 점수 (0~18점) ===
                if sma_20_div <= -5.0:
                    base_score = 18  # 매우 과매도 - 최고 점수
                elif sma_20_div <= -3.0:
                    base_score = 15  # 과매도 - 높은 점수
                elif sma_20_div <= -1.5:
                    base_score = 12  # 약간 과매도 - 좋은 점수
                elif sma_20_div <= 0:
                    base_score = 10  # 20일선 아래 - 괜찮은 점수
                elif sma_20_div <= 1.5:
                    base_score = 7   # 약간 위 - 보통 점수
                elif sma_20_div <= 3.0:
                    base_score = 5   # 과매수 초기 - 낮은 점수
                elif sma_20_div <= 5.0:
                    base_score = 2   # 과매수 - 매우 낮은 점수
                else:
                    base_score = 0   # 심한 과매수 - 0점 (완전 배제는 아님)
                
                # === 일봉 위치 보정 (±5점) ===
                position_bonus = 0
                if daily_pos <= 15:
                    position_bonus = 5   # 저점 근처 - 최대 가산점
                elif daily_pos <= 30:
                    position_bonus = 3   # 저점 영역 - 가산점
                elif daily_pos <= 50:
                    position_bonus = 1   # 중간 영역 - 소폭 가산점
                elif daily_pos >= 85:
                    position_bonus = -3  # 고점 근처 - 감점
                elif daily_pos >= 70:
                    position_bonus = -1  # 고점 영역 - 소폭 감점
                
                # === 시장 단계별 추가 조정 (±2점) ===
                phase_adjustment = 0
                if market_phase == 'opening':
                    # 장 초반: 과매도 더 선호
                    if sma_20_div <= -2.0:
                        phase_adjustment = 2
                elif market_phase == 'pre_close':
                    # 마감 전: 매우 보수적
                    if sma_20_div >= 2.0:
                        phase_adjustment = -2  # 과매수 시 감점
                
                # === 최종 점수 계산 ===
                final_score = max(0, min(25, base_score + position_bonus + phase_adjustment))
                
                # === 상세 정보 생성 ===
                if sma_20_div <= -3.0:
                    trend_desc = "과매도우수"
                elif sma_20_div <= 0:
                    trend_desc = "과매도양호"
                elif sma_20_div <= 3.0:
                    trend_desc = "과매수주의"
                else:
                    trend_desc = "과매수위험"
                
                if daily_pos <= 30:
                    pos_desc = "저점권"
                elif daily_pos >= 70:
                    pos_desc = "고점권"
                else:
                    pos_desc = "중간권"
                
                info = f"{trend_desc}({sma_20_div:.1f}%), {pos_desc}({daily_pos:.0f}%)"
                
                return final_score, info
            else:
                return 12, "데이터부족"  # 데이터 부족시 중간 점수
                
        except Exception as e:
            logger.debug(f"이격도 점수 계산 실패 {stock.stock_code}: {e}")
            return 12, "계산실패"  # 실패시 중간 점수
    
    def analyze_sell_conditions(self, stock: Stock, realtime_data: Dict,
                               market_phase: Optional[str] = None) -> Optional[str]:
        """매도 조건 분석 (우선순위 기반 개선 버전)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계 (옵션, None이면 자동 계산)
            
        Returns:
            매도 사유 또는 None
        """
        try:
            # 시장 단계 결정
            if market_phase is None:
                market_phase = self.get_market_phase()
            
            current_price = realtime_data.get('current_price', stock.close_price)
            
            # 현재 손익 상황 계산
            current_pnl = 0
            current_pnl_rate = 0
            if stock.buy_price and current_price > 0:
                current_pnl = (current_price - stock.buy_price) * (stock.buy_quantity or 1)
                current_pnl_rate = (current_price - stock.buy_price) / stock.buy_price * 100
            
            # 보유 시간 계산 (분 단위)
            holding_minutes = 0
            if stock.order_time:
                holding_minutes = (now_kst() - stock.order_time).total_seconds() / 60
            
            # 🆕 공식 문서 기반 고급 지표 추출
            contract_strength = getattr(stock.realtime_data, 'contract_strength', 100.0)
            buy_ratio = getattr(stock.realtime_data, 'buy_ratio', 50.0)
            market_pressure = getattr(stock.realtime_data, 'market_pressure', 'NEUTRAL')
            trading_halt = getattr(stock.realtime_data, 'trading_halt', False)
            volatility = getattr(stock.realtime_data, 'volatility', 0.0)
            
            # === 우선순위 1: 즉시 매도 조건 (리스크 관리) ===
            
            # 1-1. 거래정지 시 즉시 매도
            if trading_halt:
                return "trading_halt"
            
            # 1-2. 마감 시간 무조건 매도
            if market_phase == 'closing':
                return "market_close"
            
            # 🔥 설정 기반 급락 감지 (하드코딩 제거)
            emergency_loss_rate = self.strategy_config.get('emergency_stop_loss_rate', -5.0)
            emergency_volatility = self.strategy_config.get('emergency_volatility_threshold', 3.0)
            if current_pnl_rate <= emergency_loss_rate and volatility >= emergency_volatility:
                return "emergency_stop"
            
            # === 우선순위 2: 손절 조건 ===
            
            # 2-1. 기본 손절 (설정 기반)
            if stock.should_stop_loss(current_price):
                return "stop_loss"
            
            # 2-2. 시간 기반 손절 강화 (보유 시간이 길수록 더 엄격)
            time_based_stop_loss_rate = self._get_time_based_stop_loss_rate(holding_minutes)
            if current_pnl_rate <= time_based_stop_loss_rate:
                return "time_based_stop_loss"
            
            # === 우선순위 3: 익절 조건 ===
            
            # 3-1. 기본 익절
            if stock.should_take_profit(current_price):
                return "take_profit"
            
            # 🔥 설정 기반 시장 단계별 보수적 익절 (하드코딩 제거)
            if market_phase == 'pre_close':
                preclose_profit_threshold = self.strategy_config.get('preclose_profit_threshold', 0.5)
                if current_pnl_rate >= preclose_profit_threshold:
                    return "pre_close_profit"
            
            # 🔥 설정 기반 시간 익절 (하드코딩 제거)
            long_hold_minutes = self.strategy_config.get('long_hold_minutes', 180)
            long_hold_profit_threshold = self.strategy_config.get('long_hold_profit_threshold', 0.3)
            if holding_minutes >= long_hold_minutes:
                if current_pnl_rate >= long_hold_profit_threshold:
                    return "long_hold_profit"
            
            # === 우선순위 4: 기술적 지표 기반 매도 ===
            
            # 🔥 설정 기반 체결강도 급락 (하드코딩 제거)
            weak_contract_strength_threshold = self.strategy_config.get('weak_contract_strength_threshold', 80.0)
            if contract_strength <= weak_contract_strength_threshold:
                # 손실 상황에서만 적용 (수익 상황에서는 너무 성급한 매도 방지)
                if current_pnl_rate <= 0:
                    return "weak_contract_strength"
            
            # 🔥 설정 기반 매수비율 급락 (하드코딩 제거)
            low_buy_ratio_threshold = self.strategy_config.get('low_buy_ratio_threshold', 30.0)
            if buy_ratio <= low_buy_ratio_threshold:
                # 손실 상황이거나 장시간 보유시에만 적용
                if current_pnl_rate <= 0 or holding_minutes >= 120:
                    return "low_buy_ratio"
            
            # 🔥 설정 기반 시장압력 변화 (하드코딩 제거)
            if market_pressure == 'SELL':
                market_pressure_loss_threshold = self.strategy_config.get('market_pressure_sell_loss_threshold', -1.0)
                if current_pnl_rate <= market_pressure_loss_threshold:
                    return "market_pressure_sell"
            
            # 🆕 4-4. 이격도 기반 매도 (과열 구간 감지)
            divergence_sell_reason = self._analyze_divergence_sell_signal(
                stock, market_phase, current_pnl_rate, holding_minutes
            )
            if divergence_sell_reason:
                return divergence_sell_reason
            
            # === 우선순위 5: 고변동성 기반 매도 ===
            
            # 🔥 설정 기반 고점 대비 하락 + 고변동성 (하드코딩 제거)
            high_volatility_threshold = self.strategy_config.get('high_volatility_threshold', 5.0)
            if volatility >= high_volatility_threshold:
                today_high = stock.realtime_data.today_high
                if today_high > 0:
                    price_from_high = (today_high - current_price) / today_high * 100
                    price_decline_threshold = self.strategy_config.get('price_decline_from_high_threshold', 0.03) * 100  # % 변환
                    
                    if price_from_high >= price_decline_threshold:
                        return "high_volatility_decline"
            
            # === 우선순위 6: 시간 기반 매도 ===
            
            # 6-1. 보유기간 초과
            if stock.is_holding_period_exceeded():
                return "holding_period"
            
            # 🔥 설정 기반 장시간 보유 + 소폭 손실 (하드코딩 제거)
            max_holding_minutes = self.strategy_config.get('max_holding_minutes', 240)
            if holding_minutes >= max_holding_minutes:
                min_loss = self.strategy_config.get('opportunity_cost_min_loss', -2.0)
                max_profit = self.strategy_config.get('opportunity_cost_max_profit', 1.0)
                if min_loss <= current_pnl_rate <= max_profit:
                    return "opportunity_cost"
            
            # === 우선순위 7: 적응적 매도 (최근 성과 기반) ===
            
            # 🔥 설정 기반 적응적 매도 (하드코딩 제거)
            recent_win_rate = self.trade_executor._calculate_recent_win_rate(5)
            conservative_win_rate_threshold = self.strategy_config.get('conservative_win_rate_threshold', 0.3)
            if recent_win_rate < conservative_win_rate_threshold:
                # 보수적 매도: 작은 수익도 확정, 작은 손실도 빠르게 정리
                conservative_profit_threshold = self.strategy_config.get('conservative_profit_threshold', 0.8)
                conservative_stop_threshold = self.strategy_config.get('conservative_stop_threshold', -1.5)
                if current_pnl_rate >= conservative_profit_threshold:
                    return "conservative_profit"
                elif current_pnl_rate <= conservative_stop_threshold:
                    return "conservative_stop"
            
            return None
            
        except Exception as e:
            logger.error(f"매도 조건 분석 오류 {stock.stock_code}: {e}")
            return None
    
    def _analyze_divergence_sell_signal(self, stock: Stock, market_phase: str,
                                       current_pnl_rate: float, holding_minutes: float) -> Optional[str]:
        """이격도 기반 매도 신호 분석
        
        Args:
            stock: 주식 객체
            market_phase: 시장 단계
            current_pnl_rate: 현재 손익률
            holding_minutes: 보유 시간 (분)
            
        Returns:
            매도 사유 또는 None
        """
        try:
            current_price = stock.realtime_data.current_price
            if current_price > 0 and stock.reference_data.sma_20 > 0:
                sma_20_div = (current_price - stock.reference_data.sma_20) / stock.reference_data.sma_20 * 100
                
                # 당일 고저점 대비 위치 계산
                daily_pos = 50  # 기본값
                if stock.realtime_data.today_high > 0 and stock.realtime_data.today_low > 0:
                    day_range = stock.realtime_data.today_high - stock.realtime_data.today_low
                    if day_range > 0:
                        daily_pos = (current_price - stock.realtime_data.today_low) / day_range * 100
                
                # 🔥 설정 기반 과열 구간 매도 조건 (하드코딩 제거)
                if market_phase == 'pre_close':
                    overheated_threshold = self.strategy_config.get('sell_overheated_threshold_preclose', 4.0)
                    high_position_threshold = self.strategy_config.get('sell_high_position_threshold_preclose', 75.0)
                else:
                    overheated_threshold = self.strategy_config.get('sell_overheated_threshold', 5.0)
                    high_position_threshold = self.strategy_config.get('sell_high_position_threshold', 80.0)
                
                # 강한 과열 신호: 높은 이격도 + 고점 근처 + 수익 상황
                if (sma_20_div >= overheated_threshold and daily_pos >= high_position_threshold and current_pnl_rate >= 1.0):
                    return "divergence_overheated"
                
                # 🔥 설정 기반 중간 과열 신호 (하드코딩 제거)
                mild_overheated_threshold = self.strategy_config.get('sell_mild_overheated_threshold', 3.0)
                mild_position_threshold = self.strategy_config.get('sell_mild_position_threshold', 70.0)
                if (sma_20_div >= mild_overheated_threshold and daily_pos >= mild_position_threshold and 
                    current_pnl_rate >= 0.5 and holding_minutes >= 120):
                    return "divergence_mild_overheated"
            
            return None
                        
        except Exception as e:
            logger.debug(f"이격도 매도 조건 확인 실패 {stock.stock_code}: {e}")
            return None
    
    def _get_time_based_stop_loss_rate(self, holding_minutes: float) -> float:
        """보유 시간에 따른 동적 손절률 계산
        
        Args:
            holding_minutes: 보유 시간 (분)
            
        Returns:
            손절률 (음수)
        """
        base_stop_loss = self.risk_config.get('stop_loss_rate', -0.02)
        
        # 🔥 설정 기반 보유 시간별 손절 배수 (하드코딩 제거)
        if holding_minutes <= 30:  # 30분 이내
            multiplier = self.strategy_config.get('time_stop_30min_multiplier', 1.0)
        elif holding_minutes <= 120:  # 2시간 이내
            multiplier = self.strategy_config.get('time_stop_2hour_multiplier', 0.8)
        elif holding_minutes <= 240:  # 4시간 이내
            multiplier = self.strategy_config.get('time_stop_4hour_multiplier', 0.6)
        else:  # 4시간 초과
            multiplier = self.strategy_config.get('time_stop_over4hour_multiplier', 0.4)
        
        return base_stop_loss * multiplier
    
    def calculate_buy_quantity(self, stock: Stock) -> int:
        """매수량 계산 (설정 기반 개선 버전)
        
        Args:
            stock: 주식 객체
            
        Returns:
            매수량
        """
        try:
            # 🔥 설정에서 기본 투자 금액 로드
            base_amount = self.risk_config.get('base_investment_amount', 1000000)
            use_account_ratio = self.risk_config.get('use_account_ratio', False)
            
            # 계좌 잔고 기반 비율 사용 여부
            if use_account_ratio:
                from api.kis_market_api import get_account_balance
                account_balance = get_account_balance()
                
                if account_balance and isinstance(account_balance, dict):
                    # 총 계좌 자산 = 보유주식 평가액 + 매수가능금액
                    stock_value = account_balance.get('total_value', 0)  # 보유주식 평가액
                    available_amount = account_balance.get('available_amount', 0)  # 매수가능금액
                    total_balance = stock_value + available_amount  # 총 계좌 자산
                    
                    if total_balance > 0:
                        position_ratio = self.risk_config.get('position_size_ratio', 0.1)
                        base_amount = total_balance * position_ratio
                        
                        # 매수가능금액 체크 (안전장치)
                        if base_amount > available_amount:
                            logger.warning(f"계산된 투자금액({base_amount:,}원)이 매수가능금액({available_amount:,}원)을 초과 - 매수가능금액으로 제한")
                            base_amount = available_amount
            
            # 시장 단계별 투자 금액 조정 (설정 기반)
            market_phase = self.get_market_phase()
            
            if market_phase == 'opening':
                # 장 초반 비율 적용
                reduction_ratio = self.risk_config.get('opening_reduction_ratio', 0.5)
                investment_amount = base_amount * reduction_ratio
                logger.debug(f"장 초반 투자금액 조정: {base_amount:,}원 × {reduction_ratio} = {investment_amount:,}원")
            elif market_phase == 'pre_close':
                # 마감 전 비율 적용
                reduction_ratio = self.risk_config.get('preclose_reduction_ratio', 0.3)
                investment_amount = base_amount * reduction_ratio
                logger.debug(f"마감 전 투자금액 조정: {base_amount:,}원 × {reduction_ratio} = {investment_amount:,}원")
            else:
                # 일반 시간대는 100% 투자
                investment_amount = base_amount
                logger.debug(f"일반시간 투자금액: {investment_amount:,}원")
            
            # 포지션 크기에 따른 추가 조정 (설정 기반)
            current_positions = len(self.stock_manager.get_stocks_by_status(StockStatus.BOUGHT))
            max_positions = self.risk_config.get('max_positions', 5)
            
            if current_positions >= max_positions * 0.8:  # 80% 이상 차면 보수적
                conservative_ratio = self.risk_config.get('conservative_ratio', 0.7)
                investment_amount *= conservative_ratio
                logger.debug(f"보수적 조정: × {conservative_ratio} = {investment_amount:,}원 (포지션: {current_positions}/{max_positions})")
            
            # 최대 포지션 크기 제한 적용
            max_position_size = self.risk_config.get('max_position_size', 1000000)
            if investment_amount > max_position_size:
                investment_amount = max_position_size
                logger.debug(f"최대 포지션 크기 제한 적용: {max_position_size:,}원")
            
            # 매수량 계산
            current_price = stock.realtime_data.current_price if stock.realtime_data.current_price > 0 else stock.close_price
            quantity = int(investment_amount / current_price)
            
            # 최소 1주 보장
            final_quantity = max(quantity, 1)
            final_amount = final_quantity * current_price
            
            logger.info(f"💰 매수량 계산 완료: {stock.stock_code}({stock.stock_name}) "
                       f"{final_quantity}주 @{current_price:,}원 = {final_amount:,}원 "
                       f"(시장단계: {market_phase}, 기준금액: {base_amount:,}원)")
            
            return final_quantity
            
        except Exception as e:
            logger.error(f"매수량 계산 오류 {stock.stock_code}: {e}")
            return 0
    
    def get_sell_condition_analysis(self) -> Dict:
        """매도 조건 분석 성과 조회
        
        Returns:
            매도 조건별 성과 분석 딕셔너리
        """
        try:
            # TradeExecutor의 최근 거래 기록에서 매도 사유별 성과 분석
            recent_trades = self.trade_executor.get_recent_trades_summary(20)
            
            # 매도 사유별 통계
            sell_reason_stats = {}
            total_trades = 0
            total_pnl = 0
            
            for trade in recent_trades['trades']:
                reason = trade['sell_reason']
                if reason not in sell_reason_stats:
                    sell_reason_stats[reason] = {
                        'count': 0,
                        'win_count': 0,
                        'total_pnl': 0.0,
                        'avg_pnl': 0.0,
                        'win_rate': 0.0,
                        'avg_holding_minutes': 0.0
                    }
                
                stats = sell_reason_stats[reason]
                stats['count'] += 1
                if trade['is_winning']:
                    stats['win_count'] += 1
                stats['total_pnl'] += trade['realized_pnl']
                stats['avg_holding_minutes'] += trade['holding_minutes']
                
                total_trades += 1
                total_pnl += trade['realized_pnl']
            
            # 각 사유별 평균값 계산
            for reason in sell_reason_stats:
                stats = sell_reason_stats[reason]
                if stats['count'] > 0:
                    stats['win_rate'] = (stats['win_count'] / stats['count']) * 100
                    stats['avg_pnl'] = stats['total_pnl'] / stats['count']
                    stats['avg_holding_minutes'] = stats['avg_holding_minutes'] / stats['count']
            
            # 매도 조건 효과성 순위
            effectiveness_ranking = sorted(
                sell_reason_stats.items(),
                key=lambda x: (x[1]['win_rate'], x[1]['avg_pnl']),
                reverse=True
            )
            
            return {
                'sell_reason_stats': sell_reason_stats,
                'effectiveness_ranking': effectiveness_ranking,
                'overall_stats': {
                    'total_trades': total_trades,
                    'total_pnl': total_pnl,
                    'avg_pnl': total_pnl / total_trades if total_trades > 0 else 0.0
                },
                'recommendations': self._generate_sell_condition_recommendations(sell_reason_stats)
            }
            
        except Exception as e:
            logger.error(f"매도 조건 분석 성과 조회 오류: {e}")
            return {}
    
    def _generate_sell_condition_recommendations(self, sell_reason_stats: Dict) -> List[str]:
        """매도 조건 개선 권장사항 생성
        
        Args:
            sell_reason_stats: 매도 사유별 통계
            
        Returns:
            권장사항 리스트
        """
        recommendations = []
        
        try:
            for reason, stats in sell_reason_stats.items():
                if stats['count'] < 3:  # 샘플이 너무 적으면 건너뛰기
                    continue
                
                # 승률 기반 권장사항
                if stats['win_rate'] < 30:
                    recommendations.append(f"❌ '{reason}' 매도 조건의 승률이 낮습니다 ({stats['win_rate']:.1f}%) - 조건 재검토 필요")
                elif stats['win_rate'] > 70:
                    recommendations.append(f"✅ '{reason}' 매도 조건이 효과적입니다 ({stats['win_rate']:.1f}%) - 유지 권장")
                
                # 평균 손익 기반 권장사항
                if stats['avg_pnl'] < -10000:
                    recommendations.append(f"🔻 '{reason}' 매도시 평균 손실이 큽니다 ({stats['avg_pnl']:,.0f}원) - 더 빠른 매도 검토")
                elif stats['avg_pnl'] > 5000:
                    recommendations.append(f"🔺 '{reason}' 매도시 평균 수익이 좋습니다 ({stats['avg_pnl']:,.0f}원) - 조건 확대 검토")
                
                # 보유 시간 기반 권장사항
                if stats['avg_holding_minutes'] > 240:  # 4시간 초과
                    recommendations.append(f"⏰ '{reason}' 매도시 보유 시간이 깁니다 ({stats['avg_holding_minutes']:.0f}분) - 더 빠른 매도 검토")
            
            # 전체적인 권장사항
            if len(sell_reason_stats) > 10:
                recommendations.append("📊 매도 사유가 너무 많습니다 - 주요 조건으로 단순화 검토")
            
            # 특정 조건별 권장사항
            if 'stop_loss' in sell_reason_stats:
                stop_loss_stats = sell_reason_stats['stop_loss']
                if stop_loss_stats['count'] > 5 and stop_loss_stats['win_rate'] < 20:
                    recommendations.append("🚨 손절 조건이 너무 늦습니다 - 더 빠른 손절 검토")
            
            if 'take_profit' in sell_reason_stats:
                take_profit_stats = sell_reason_stats['take_profit']
                if take_profit_stats['count'] > 3 and take_profit_stats['avg_pnl'] < 5000:
                    recommendations.append("💰 익절 수익이 작습니다 - 익절 목표 상향 검토")
                    
        except Exception as e:
            logger.error(f"매도 조건 권장사항 생성 오류: {e}")
        
        return recommendations 