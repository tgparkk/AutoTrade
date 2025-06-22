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
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 🆕 공식 문서 기반 고급 매매 지표 임계값
        self.contract_strength_threshold = 120.0  # 체결강도 임계값
        self.buy_ratio_threshold = 60.0          # 매수비율 임계값 (%)
        self.vi_activation_threshold = True       # VI 발동 시 거래 중단 여부
        self.market_pressure_weight = 0.3        # 시장압력 가중치
        
        logger.info("TradingConditionAnalyzer 초기화 완료")
    
    def get_market_phase(self) -> str:
        """현재 시장 단계 확인 (외부에서 주입받을 수도 있음)
        
        Returns:
            시장 단계 ('opening', 'active', 'lunch', 'pre_close', 'closing', 'closed')
        """
        from datetime import time as dt_time
        
        current_time = now_kst().time()
        
        # 간단한 시장 단계 판단 (필요시 외부에서 주입)
        if current_time <= dt_time(9, 30):
            return 'opening'
        elif current_time <= dt_time(12, 0):
            return 'active'
        elif current_time <= dt_time(13, 0):
            return 'lunch'
        elif current_time <= dt_time(14, 50):
            return 'active'
        elif current_time <= dt_time(15, 0):
            return 'pre_close'
        else:
            return 'closing'
    
    def analyze_buy_conditions(self, stock: Stock, realtime_data: Dict, 
                              market_phase: Optional[str] = None) -> bool:
        """매수 조건 분석 (공식 문서 기반 고급 지표 활용)
        
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
            
            # VI 발동 및 거래정지 체크
            if trading_halt or (vi_standard_price > 0 and self.vi_activation_threshold):
                logger.debug(f"거래 제외: {stock.stock_code} (거래정지: {trading_halt}, VI발동: {vi_standard_price > 0})")
                return False
            
            # 시장 단계별 조건 조정
            if market_phase == 'opening':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * 1.5
                price_threshold = 0.015  # 1.5%
                contract_strength_min = self.contract_strength_threshold * 1.2
                buy_ratio_min = self.buy_ratio_threshold * 1.1
            elif market_phase == 'pre_close':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * 2.0
                price_threshold = 0.02   # 2%
                contract_strength_min = self.contract_strength_threshold * 1.5
                buy_ratio_min = self.buy_ratio_threshold * 1.2
            else:
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0)
                price_threshold = 0.01   # 1%
                contract_strength_min = self.contract_strength_threshold
                buy_ratio_min = self.buy_ratio_threshold
            
            # 🔥 고급 매수 조건 (공식 문서 기반)
            
            # 1. 기본 조건
            volume_condition = volume_spike_ratio >= volume_threshold
            price_condition = price_change_rate >= price_threshold
            
            # 2. 최소 거래량 조건
            min_volume = self.strategy_config.get('volume_min_threshold', 100000)
            volume_min_condition = realtime_data.get('volume', 0) >= min_volume
            
            # 3. 패턴 점수 조건 (시장 단계별 조정)
            min_pattern_score = 70.0 if market_phase != 'opening' else 75.0
            pattern_condition = stock.total_pattern_score >= min_pattern_score
            
            # 4. 🆕 체결강도 조건 (KIS 공식 필드)
            strength_condition = contract_strength >= contract_strength_min
            
            # 5. 🆕 매수비율 조건 (KIS 공식 필드)
            buy_ratio_condition = buy_ratio >= buy_ratio_min
            
            # 6. 🆕 시장압력 조건 (KIS 공식 필드)
            market_pressure_condition = market_pressure in ['BUY', 'NEUTRAL']
            
            # 7. 호가 스프레드 조건 (너무 넓은 스프레드 제외)
            bid_price = realtime_data.get('bid_price', 0)
            ask_price = realtime_data.get('ask_price', 0)
            spread_condition = True
            if bid_price > 0 and ask_price > 0:
                spread_rate = (ask_price - bid_price) / bid_price
                spread_condition = spread_rate <= 0.01  # 1% 이하 스프레드만 허용
            
            # 🆕 8. 이격도 조건 (핵심 매수 타이밍 지표)
            divergence_condition, divergence_info = self._analyze_divergence_buy_signal(
                stock, market_phase
            )
            
            # 🔥 최종 매수 신호 판단 (이격도 조건 추가)
            buy_signal = (volume_condition and price_condition and 
                         volume_min_condition and pattern_condition and
                         strength_condition and buy_ratio_condition and
                         market_pressure_condition and spread_condition and
                         divergence_condition)
            
            if buy_signal:
                logger.info(f"🚀 {stock.stock_code}({stock.stock_name}) 매수 신호 ({market_phase}): "
                           f"거래량({volume_spike_ratio:.1f}배≥{volume_threshold:.1f}), "
                           f"상승률({price_change_rate:.2%}≥{price_threshold:.1%}), "
                           f"체결강도({contract_strength:.1f}≥{contract_strength_min:.1f}), "
                           f"매수비율({buy_ratio:.1f}%≥{buy_ratio_min:.1f}%), "
                           f"시장압력({market_pressure}), "
                           f"패턴점수({stock.total_pattern_score:.1f}≥{min_pattern_score}), "
                           f"{divergence_info}")
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    def _analyze_divergence_buy_signal(self, stock: Stock, market_phase: str) -> Tuple[bool, str]:
        """이격도 기반 매수 신호 분석
        
        Args:
            stock: 주식 객체
            market_phase: 시장 단계
            
        Returns:
            (조건 충족 여부, 디버깅 정보)
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
                
                # 매수 신호 판단 (시장 단계별 차등 적용)
                if market_phase == 'opening':
                    # 장 초반: 강한 과매도 + 저점 근처
                    condition = (sma_20_div <= -3.5 and daily_pos <= 25)
                elif market_phase == 'pre_close':
                    # 마감 전: 매우 보수적 (깊은 과매도)
                    condition = (sma_20_div <= -4.0 and daily_pos <= 20)
                else:
                    # 일반 시간: 표준 과매도 조건
                    condition = (sma_20_div <= -2.5 and daily_pos <= 35)
                
                # 디버깅 정보
                signal_strength = abs(sma_20_div) if sma_20_div < 0 else 0
                info = f"이격도(20일선:{sma_20_div:.1f}%, 일봉위치:{daily_pos:.0f}%, 강도:{signal_strength:.1f})"
                
                return condition, info
            else:
                return True, "이격도(데이터부족)"  # 데이터 부족시 통과
                
        except Exception as e:
            logger.debug(f"이격도 조건 확인 실패 {stock.stock_code}: {e}")
            return True, "이격도(계산실패)"  # 실패시 통과 (다른 조건에 의존)
    
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
            
            # 1-3. 급락 감지 (5% 이상 손실 + 고변동성)
            if current_pnl_rate <= -5.0 and volatility >= 3.0:
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
            
            # 3-2. 시장 단계별 보수적 익절
            if market_phase == 'pre_close':
                # 마감 전에는 0.5% 이상 수익시 매도
                if current_pnl_rate >= 0.5:
                    return "pre_close_profit"
            
            # 3-3. 시간 기반 익절 (장시간 보유시 작은 수익도 확정)
            if holding_minutes >= 180:  # 3시간 이상 보유
                if current_pnl_rate >= 0.3:  # 0.3% 이상 수익
                    return "long_hold_profit"
            
            # === 우선순위 4: 기술적 지표 기반 매도 ===
            
            # 4-1. 체결강도 급락 (설정 기반 임계값)
            contract_strength_threshold = self.strategy_config.get('sell_contract_strength_threshold', 80.0)
            if contract_strength <= contract_strength_threshold:
                # 손실 상황에서만 적용 (수익 상황에서는 너무 성급한 매도 방지)
                if current_pnl_rate <= 0:
                    return "weak_contract_strength"
            
            # 4-2. 매수비율 급락 (설정 기반 임계값)
            buy_ratio_threshold = self.strategy_config.get('sell_buy_ratio_threshold', 30.0)
            if buy_ratio <= buy_ratio_threshold:
                # 손실 상황이거나 장시간 보유시에만 적용
                if current_pnl_rate <= 0 or holding_minutes >= 120:
                    return "low_buy_ratio"
            
            # 4-3. 시장압력 변화
            if market_pressure == 'SELL':
                # 손실 상황에서만 적용
                if current_pnl_rate <= -1.0:
                    return "market_pressure_sell"
            
            # 🆕 4-4. 이격도 기반 매도 (과열 구간 감지)
            divergence_sell_reason = self._analyze_divergence_sell_signal(
                stock, market_phase, current_pnl_rate, holding_minutes
            )
            if divergence_sell_reason:
                return divergence_sell_reason
            
            # === 우선순위 5: 고변동성 기반 매도 ===
            
            # 5-1. 고점 대비 하락 + 고변동성
            volatility_threshold = self.strategy_config.get('sell_volatility_threshold', 5.0)
            if volatility >= volatility_threshold:
                today_high = stock.realtime_data.today_high
                if today_high > 0:
                    price_from_high = (today_high - current_price) / today_high * 100
                    high_drop_threshold = self.strategy_config.get('sell_high_drop_threshold', 3.0)
                    
                    if price_from_high >= high_drop_threshold:
                        return "high_volatility_decline"
            
            # === 우선순위 6: 시간 기반 매도 ===
            
            # 6-1. 보유기간 초과
            if stock.is_holding_period_exceeded():
                return "holding_period"
            
            # 6-2. 장시간 보유 + 소폭 손실 (기회비용 고려)
            max_holding_minutes = self.strategy_config.get('max_holding_minutes', 240)  # 4시간
            if holding_minutes >= max_holding_minutes:
                if -2.0 <= current_pnl_rate <= 1.0:  # -2%~1% 범위
                    return "opportunity_cost"
            
            # === 우선순위 7: 적응적 매도 (최근 성과 기반) ===
            
            # 최근 승률이 낮으면 더 보수적으로 매도
            recent_win_rate = self.trade_executor._calculate_recent_win_rate(5)
            if recent_win_rate < 0.3:  # 최근 승률 30% 미만
                # 보수적 매도: 작은 수익도 확정, 작은 손실도 빠르게 정리
                if current_pnl_rate >= 0.8:  # 0.8% 수익시 매도
                    return "conservative_profit"
                elif current_pnl_rate <= -1.5:  # 1.5% 손실시 매도
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
                
                # 과열 구간 매도 조건 (시장 단계별 차등 적용)
                overheated_threshold = 5.0 if market_phase == 'normal' else 4.0  # 마감 전/장 초반 더 엄격
                high_position_threshold = 80.0 if market_phase == 'normal' else 75.0
                
                # 강한 과열 신호: 높은 이격도 + 고점 근처 + 수익 상황
                if (sma_20_div >= overheated_threshold and daily_pos >= high_position_threshold and current_pnl_rate >= 1.0):
                    return "divergence_overheated"
                
                # 중간 과열 신호: 장시간 보유 + 과열 + 소폭 수익
                elif (sma_20_div >= 3.0 and daily_pos >= 70.0 and current_pnl_rate >= 0.5 and holding_minutes >= 120):
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
        
        # 보유 시간이 길수록 더 엄격한 손절 적용
        if holding_minutes <= 30:  # 30분 이내
            return base_stop_loss  # 기본 손절률
        elif holding_minutes <= 120:  # 2시간 이내
            return base_stop_loss * 0.8  # 1.6% 손절
        elif holding_minutes <= 240:  # 4시간 이내
            return base_stop_loss * 0.6  # 1.2% 손절
        else:  # 4시간 초과
            return base_stop_loss * 0.4  # 0.8% 손절
    
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