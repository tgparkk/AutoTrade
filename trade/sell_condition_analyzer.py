#!/usr/bin/env python3
"""
매도 조건 분석 전담 클래스 (Static 메서드 기반)

TradingConditionAnalyzer에서 분리하여 매도 조건 분석만을 담당하는 독립적인 클래스
모든 메서드를 static으로 구성하여 인스턴스 생성 없이 사용 가능
"""

from typing import Dict, Optional
from datetime import datetime
from models.stock import Stock
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


class SellConditionAnalyzer:
    """매도 조건 분석 전담 클래스 (Static 메서드 기반)"""
    
    @staticmethod
    def analyze_sell_conditions(stock: Stock, realtime_data: Dict, market_phase: str,
                               strategy_config: Dict, risk_config: Dict, performance_config: Dict) -> Optional[str]:
        """매도 조건 분석 (우선순위 기반 개선 버전)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계
            strategy_config: 전략 설정
            risk_config: 리스크 설정
            performance_config: 성과 설정
            
        Returns:
            매도 사유 또는 None
        """
        try:
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
            
            # 고급 지표 추출
            contract_strength = getattr(stock.realtime_data, 'contract_strength', 100.0)
            buy_ratio = getattr(stock.realtime_data, 'buy_ratio', 50.0)
            market_pressure = getattr(stock.realtime_data, 'market_pressure', 'NEUTRAL')
            trading_halt = getattr(stock.realtime_data, 'trading_halt', False)
            volatility = getattr(stock.realtime_data, 'volatility', 0.0)
            
            # === 우선순위 1: 즉시 매도 조건 (리스크 관리) ===
            immediate_sell_reason = SellConditionAnalyzer._check_immediate_sell_conditions(
                stock, realtime_data, market_phase, current_pnl_rate, 
                trading_halt, volatility, strategy_config
            )
            if immediate_sell_reason:
                return immediate_sell_reason
            
            # === 우선순위 2: 손절 조건 ===
            stop_loss_reason = SellConditionAnalyzer._check_stop_loss_conditions(
                stock, realtime_data, current_price, current_pnl_rate, 
                holding_minutes, strategy_config, risk_config
            )
            if stop_loss_reason:
                return stop_loss_reason
            
            # === 우선순위 3: 익절 조건 ===
            take_profit_reason = SellConditionAnalyzer._check_take_profit_conditions(
                stock, current_price, current_pnl_rate, holding_minutes, 
                market_phase, strategy_config
            )
            if take_profit_reason:
                return take_profit_reason
            
            # === 우선순위 4: 기술적 지표 기반 매도 ===
            technical_sell_reason = SellConditionAnalyzer._check_technical_sell_conditions(
                stock, realtime_data, current_pnl_rate, holding_minutes, 
                market_phase, contract_strength, buy_ratio, market_pressure, strategy_config, performance_config
            )
            if technical_sell_reason:
                return technical_sell_reason
            
            # === 우선순위 4-1: 호가잔량 기반 매도 (신규 추가) ===
            orderbook_sell_reason = SellConditionAnalyzer._check_orderbook_sell_conditions(
                stock, realtime_data, current_pnl_rate, holding_minutes, strategy_config
            )
            if orderbook_sell_reason:
                return orderbook_sell_reason
            
            # === 우선순위 4-2: 거래량 패턴 기반 매도 (신규 추가) ===
            volume_pattern_reason = SellConditionAnalyzer._check_volume_pattern_sell_conditions(
                stock, realtime_data, holding_minutes, strategy_config
            )
            if volume_pattern_reason:
                return volume_pattern_reason
            
            # === 우선순위 4-3: 강화된 체결 불균형 매도 (신규 추가) ===
            enhanced_contract_reason = SellConditionAnalyzer._check_enhanced_contract_sell_conditions(
                stock, realtime_data, current_pnl_rate, holding_minutes, strategy_config
            )
            if enhanced_contract_reason:
                return enhanced_contract_reason
            
            # === 우선순위 5: 고변동성 기반 매도 ===
            volatility_sell_reason = SellConditionAnalyzer._check_volatility_sell_conditions(
                stock, current_price, volatility, strategy_config
            )
            if volatility_sell_reason:
                return volatility_sell_reason
            
            # === 우선순위 6: 시간 기반 매도 ===
            time_sell_reason = SellConditionAnalyzer._check_time_based_sell_conditions(
                stock, current_pnl_rate, holding_minutes, strategy_config
            )
            if time_sell_reason:
                return time_sell_reason
            
            return None
            
        except Exception as e:
            logger.error(f"매도 조건 분석 오류 {stock.stock_code}: {e}")
            return None
    
    @staticmethod
    def _check_immediate_sell_conditions(stock: Stock, realtime_data: Dict, market_phase: str,
                                        current_pnl_rate: float, trading_halt: bool, 
                                        volatility: float, strategy_config: Dict) -> Optional[str]:
        """즉시 매도 조건 확인"""
        # 거래정지 시 즉시 매도
        if trading_halt:
            return "trading_halt"
        
        # 마감 시간 무조건 매도
        if market_phase == 'closing':
            return "market_close"
        
        # 상한가 직전(+29%) 도달 시 즉시 익절 매도
        try:
            limit_up_rate = strategy_config.get('limit_up_profit_rate', 29.0)
            current_price = realtime_data.get('current_price', stock.close_price)
            yesterday_close = getattr(stock.reference_data, 'yesterday_close', 0)

            if yesterday_close > 0 and current_price > 0:
                daily_change_rate = (current_price - yesterday_close) / yesterday_close * 100
                if daily_change_rate >= limit_up_rate:
                    return "limit_up_take_profit"
        except Exception as e:
            logger.debug(f"Limit-up sell check error {stock.stock_code}: {e}")
        
        # 급락 감지
        emergency_loss_rate = strategy_config.get('emergency_stop_loss_rate', -5.0)
        emergency_volatility = strategy_config.get('emergency_volatility_threshold', 3.0)
        if current_pnl_rate <= emergency_loss_rate and volatility >= emergency_volatility:
            return "emergency_stop"
        
        return None
    
    @staticmethod
    def _check_stop_loss_conditions(stock: Stock, realtime_data: Dict, current_price: float,
                                   current_pnl_rate: float, holding_minutes: float,
                                   strategy_config: Dict, risk_config: Dict) -> Optional[str]:
        """손절 조건 확인"""
        # 기본 손절
        if stock.should_stop_loss(current_price):
            return "stop_loss"
        
        # 시간 기반 손절 강화
        time_based_stop_loss_rate = SellConditionAnalyzer._get_time_based_stop_loss_rate(
            holding_minutes, strategy_config, risk_config
        )
        if current_pnl_rate <= time_based_stop_loss_rate:
            return "time_based_stop_loss"
        
        # 가격 급락 보호
        rapid_decline_reason = SellConditionAnalyzer._analyze_rapid_decline_sell_signal(
            stock, realtime_data, current_pnl_rate, strategy_config
        )
        if rapid_decline_reason:
            return rapid_decline_reason
        
        return None
    
    @staticmethod
    def _check_take_profit_conditions(stock: Stock, current_price: float, current_pnl_rate: float,
                                     holding_minutes: float, market_phase: str,
                                     strategy_config: Dict) -> Optional[str]:
        """익절 조건 확인"""
        # 🆕 트레일링 스탑 익절
        if strategy_config.get('trailing_stop_enabled', True):
            dyn_target = getattr(stock, 'dynamic_target_price', 0.0)
            if dyn_target > 0 and current_price <= dyn_target and current_pnl_rate > 0:
                return "trailing_take_profit"
        
        # 기본 익절
        if stock.should_take_profit(current_price):
            return "take_profit"
        
        # 시장 단계별 보수적 익절
        if market_phase == 'pre_close':
            preclose_profit_threshold = strategy_config.get('preclose_profit_threshold', 0.5)
            if current_pnl_rate >= preclose_profit_threshold:
                return "pre_close_profit"
        
        # 시간 익절
        long_hold_minutes = strategy_config.get('long_hold_minutes', 180)
        long_hold_profit_threshold = strategy_config.get('long_hold_profit_threshold', 0.3)
        if holding_minutes >= long_hold_minutes:
            if current_pnl_rate >= long_hold_profit_threshold:
                return "long_hold_profit"
        
        return None
    
    @staticmethod
    def _check_technical_sell_conditions(stock: Stock, realtime_data: Dict, current_pnl_rate: float,
                                        holding_minutes: float, market_phase: str, 
                                        contract_strength: float, buy_ratio: float,
                                        market_pressure: str, strategy_config: Dict, performance_config: Dict) -> Optional[str]:
        """기술적 지표 기반 매도 조건 확인"""
        # 최소 보유시간 이전이면 체결강도 약화 신호를 무시 (쿨다운)
        cooldown_min = strategy_config.get('min_holding_minutes_before_sell', 
                                           performance_config.get('min_holding_minutes_before_sell', 1))
        within_cooldown = holding_minutes < cooldown_min
        
        weak_contract_strength_threshold = strategy_config.get('weak_contract_strength_threshold', 80.0)
        if (not within_cooldown) and contract_strength <= weak_contract_strength_threshold:
            if current_pnl_rate <= 0:
                return "weak_contract_strength"
        
        # 매수비율 급락
        low_buy_ratio_threshold = strategy_config.get('low_buy_ratio_threshold', 30.0)
        if (not within_cooldown) and buy_ratio <= low_buy_ratio_threshold:
            if current_pnl_rate <= 0 or holding_minutes >= 120:
                return "low_buy_ratio"
        
        # 시장압력 변화
        if market_pressure == 'SELL':
            market_pressure_loss_threshold = strategy_config.get('market_pressure_sell_loss_threshold', -1.0)
            if current_pnl_rate <= market_pressure_loss_threshold:
                return "market_pressure_sell"
        
        # 기타 기술적 지표들 (간단한 형태로 유지)
        return None
    
    @staticmethod
    def _check_volatility_sell_conditions(stock: Stock, current_price: float, 
                                         volatility: float, strategy_config: Dict) -> Optional[str]:
        """고변동성 기반 매도 조건 확인"""
        high_volatility_threshold = strategy_config.get('high_volatility_threshold', 5.0)
        if volatility >= high_volatility_threshold:
            today_high = stock.realtime_data.today_high
            if today_high > 0:
                price_from_high = (today_high - current_price) / today_high * 100
                price_decline_threshold = strategy_config.get('price_decline_from_high_threshold', 0.03) * 100
                
                if price_from_high >= price_decline_threshold:
                    return "high_volatility_decline"
        
        return None
    
    @staticmethod
    def _check_time_based_sell_conditions(stock: Stock, current_pnl_rate: float,
                                         holding_minutes: float, strategy_config: Dict) -> Optional[str]:
        """시간 기반 매도 조건 확인"""
        # 보유기간 초과
        if stock.is_holding_period_exceeded():
            return "holding_period"
        
        # 장시간 보유 + 소폭 손실
        max_holding_minutes = strategy_config.get('max_holding_minutes', 240)
        if holding_minutes >= max_holding_minutes:
            min_loss = strategy_config.get('opportunity_cost_min_loss', -2.0)
            max_profit = strategy_config.get('opportunity_cost_max_profit', 1.0)
            if min_loss <= current_pnl_rate <= max_profit:
                return "opportunity_cost"
        
        return None
    
    # === 헬퍼 메서드들 ===
    
    @staticmethod
    def _get_time_based_stop_loss_rate(holding_minutes: float, strategy_config: Dict, 
                                      risk_config: Dict) -> float:
        """보유 시간에 따른 동적 손절률 계산"""
        base_stop_loss = risk_config.get('stop_loss_rate', -0.02)
        
        if holding_minutes <= 30:
            multiplier = strategy_config.get('time_stop_30min_multiplier', 1.0)
        elif holding_minutes <= 120:
            multiplier = strategy_config.get('time_stop_2hour_multiplier', 0.8)
        elif holding_minutes <= 240:
            multiplier = strategy_config.get('time_stop_4hour_multiplier', 0.6)
        else:
            multiplier = strategy_config.get('time_stop_over4hour_multiplier', 0.4)
        
        # 현재 current_pnl_rate 는 백분율(%) 단위로 계산되어 있다.
        # base_stop_loss 는 소수(-0.02) 형태이므로 100 을 곱해 동일한 단위(%)로 변환한다.
        return base_stop_loss * multiplier * 100
    
    @staticmethod
    def _analyze_rapid_decline_sell_signal(stock: Stock, realtime_data: Dict, current_pnl_rate: float,
                                          strategy_config: Dict) -> Optional[str]:
        """가격 급락 보호 매도 신호 분석 (간단한 버전)"""
        try:
            current_price = realtime_data.get('current_price', stock.close_price)
            buy_price = stock.buy_price or current_price
            
            # 매수가 대비 급락 체크
            if buy_price > 0:
                decline_from_buy = (buy_price - current_price) / buy_price * 100
                rapid_decline_threshold = strategy_config.get('rapid_decline_from_buy_threshold', 2.5)
                
                if decline_from_buy >= rapid_decline_threshold:
                    return "rapid_decline_from_buy"
            
            # 단기 변동성 급증 체크
            price_change_rate = realtime_data.get('price_change_rate', 0) / 100
            if price_change_rate <= -0.015:  # 1.5% 이상 하락
                volatility = getattr(stock.realtime_data, 'volatility', 0.0)
                high_volatility_for_decline = strategy_config.get('high_volatility_for_decline', 4.0)
                
                if volatility >= high_volatility_for_decline:
                    return "high_volatility_rapid_decline"
            
            return None
            
        except Exception as e:
            logger.debug(f"가격 급락 보호 매도 조건 확인 실패 {stock.stock_code}: {e}")
            return None
    
    @staticmethod
    def _check_orderbook_sell_conditions(stock: Stock, realtime_data: Dict, 
                                       current_pnl_rate: float, holding_minutes: float, strategy_config: Dict) -> Optional[str]:
        """호가잔량 기반 매도 조건 확인 (신규 추가)"""
        try:
            # 호가잔량 데이터 추출
            total_ask_qty = getattr(stock.realtime_data, 'total_ask_qty', 0)
            total_bid_qty = getattr(stock.realtime_data, 'total_bid_qty', 0)
            
            if total_ask_qty <= 0 or total_bid_qty <= 0:
                return None
            
            # 최소 보유시간 검사 (쿨다운)
            min_holding_for_orderbook = strategy_config.get('min_holding_for_orderbook', 1)  # 기본 1분
            if holding_minutes < min_holding_for_orderbook:
                return None
            
            # 1. 매도호가 급증 (매도압력 3배 이상)
            ask_bid_ratio = total_ask_qty / total_bid_qty
            high_ask_pressure_threshold = strategy_config.get('high_ask_pressure_threshold', 3.0)
            
            if ask_bid_ratio >= high_ask_pressure_threshold:
                # 손실 상황이거나 소폭 이익일 때만 매도
                max_profit_for_ask_sell = strategy_config.get('max_profit_for_ask_sell', 1.5)
                if current_pnl_rate <= max_profit_for_ask_sell:
                    return "high_ask_pressure"
            
            # 2. 매수호가 급감 (매수 관심 급락)
            bid_ask_ratio = total_bid_qty / total_ask_qty
            low_bid_interest_threshold = strategy_config.get('low_bid_interest_threshold', 0.3)
            
            if bid_ask_ratio <= low_bid_interest_threshold:
                # 약간의 손실이라도 매도
                min_loss_for_bid_sell = strategy_config.get('min_loss_for_bid_sell', -0.5)
                if current_pnl_rate <= min_loss_for_bid_sell:
                    return "low_bid_interest"
            
            # 3. 호가 스프레드 급확대 (유동성 부족)
            bid_price = realtime_data.get('bid_price', 0) or getattr(stock.realtime_data, 'bid_price', 0)
            ask_price = realtime_data.get('ask_price', 0) or getattr(stock.realtime_data, 'ask_price', 0)
            
            if bid_price > 0 and ask_price > 0:
                spread_rate = (ask_price - bid_price) / bid_price
                wide_spread_threshold = strategy_config.get('wide_spread_threshold', 0.03)  # 3%
                
                if spread_rate >= wide_spread_threshold:
                    # 유동성 부족으로 매도 어려워질 수 있으니 빠른 매도
                    return "wide_spread_liquidity"
            
            return None
            
        except Exception as e:
            logger.debug(f"호가잔량 매도 조건 확인 실패 {stock.stock_code}: {e}")
            return None
    
    @staticmethod
    def _check_volume_pattern_sell_conditions(stock: Stock, realtime_data: Dict,
                                            holding_minutes: float, strategy_config: Dict) -> Optional[str]:
        """거래량 패턴 기반 매도 조건 확인 (신규 추가)"""
        try:
            # 거래량 관련 데이터 추출
            volume_turnover_rate = getattr(stock.realtime_data, 'volume_turnover_rate', 0.0)
            prev_same_time_volume_rate = getattr(stock.realtime_data, 'prev_same_time_volume_rate', 100.0)
            current_volume = getattr(stock.realtime_data, 'today_volume', 0)
            
            # 1. 거래량 급감 (관심 상실)
            volume_drying_threshold = strategy_config.get('volume_drying_threshold', 0.4)  # 40%
            min_holding_for_volume_check = strategy_config.get('min_holding_for_volume_check', 15)  # 15분
            
            if (holding_minutes >= min_holding_for_volume_check and 
                prev_same_time_volume_rate <= volume_drying_threshold * 100):
                return "volume_drying_up"
            
            # 2. 거래량 회전율 급락
            low_turnover_threshold = strategy_config.get('low_turnover_threshold', 0.5)  # 0.5%
            if volume_turnover_rate <= low_turnover_threshold:
                # 30분 이상 보유한 경우에만 적용
                min_holding_for_turnover = strategy_config.get('min_holding_for_turnover', 30)
                if holding_minutes >= min_holding_for_turnover:
                    return "low_volume_turnover"
            
            # 3. 장중 거래량 패턴 분석 (간단한 버전)
            # 현재 시간대에 거래량이 너무 적으면 관심 상실로 판단
            current_hour = now_kst().hour
            if 10 <= current_hour <= 14:  # 활발한 거래 시간대
                expected_min_volume_ratio = strategy_config.get('expected_min_volume_ratio', 0.8)
                if prev_same_time_volume_rate <= expected_min_volume_ratio * 100:
                    # 거래량이 전일 동시간 대비 80% 미만이면 관심 상실
                    min_holding_for_pattern = strategy_config.get('min_holding_for_pattern', 45)
                    if holding_minutes >= min_holding_for_pattern:
                        return "volume_pattern_weak"
            
            return None
            
        except Exception as e:
            logger.debug(f"거래량 패턴 매도 조건 확인 실패 {stock.stock_code}: {e}")
            return None
    
    @staticmethod
    def _check_enhanced_contract_sell_conditions(stock: Stock, realtime_data: Dict,
                                               current_pnl_rate: float, holding_minutes: float, 
                                               strategy_config: Dict) -> Optional[str]:
        """강화된 체결 불균형 매도 조건 확인 (신규 추가)"""
        try:
            # 체결 데이터 추출
            sell_contract_count = getattr(stock.realtime_data, 'sell_contract_count', 0)
            buy_contract_count = getattr(stock.realtime_data, 'buy_contract_count', 0)
            contract_strength = getattr(stock.realtime_data, 'contract_strength', 100.0)
            
            total_contracts = sell_contract_count + buy_contract_count
            if total_contracts <= 0:
                return None
            
            # 1. 연속 매도체결 우세 (70% 이상 매도체결)
            sell_contract_ratio = sell_contract_count / total_contracts
            sell_dominance_threshold = strategy_config.get('sell_dominance_threshold', 0.7)
            min_holding_for_contract = strategy_config.get('min_holding_for_contract', 20)  # 20분
            
            if (sell_contract_ratio >= sell_dominance_threshold and 
                holding_minutes >= min_holding_for_contract):
                return "sell_contract_dominance"
            
            # 2. 체결강도 급락 + 시간 요소 결합 (기존 조건 강화)
            weak_strength_enhanced_threshold = strategy_config.get('weak_strength_enhanced_threshold', 70.0)
            strength_time_threshold = strategy_config.get('strength_time_threshold', 30)  # 30분
            
            if (contract_strength <= weak_strength_enhanced_threshold and 
                holding_minutes >= strength_time_threshold):
                # 손실이 아니어도 장시간 보유시 매도 고려
                max_profit_for_weak_strength = strategy_config.get('max_profit_for_weak_strength', 0.8)
                if current_pnl_rate <= max_profit_for_weak_strength:
                    return "weak_strength_prolonged"
            
            # 3. 급격한 체결강도 하락 감지 (단기간 내 급락)
            # 이전 값과 비교는 복잡하므로, 현재는 절대값 기준으로 판단
            very_weak_strength_threshold = strategy_config.get('very_weak_strength_threshold', 60.0)
            immediate_strength_check = strategy_config.get('immediate_strength_check', 10)  # 10분
            
            if (contract_strength <= very_weak_strength_threshold and 
                holding_minutes >= immediate_strength_check):
                # 매우 약한 체결강도는 즉시 매도 고려
                if current_pnl_rate <= 0:  # 손실이거나 본전일 때
                    return "very_weak_strength"
            
            # 4. 체결 불균형 + 호가 불균형 결합 조건
            total_ask_qty = getattr(stock.realtime_data, 'total_ask_qty', 0)
            total_bid_qty = getattr(stock.realtime_data, 'total_bid_qty', 0)
            
            if total_ask_qty > 0 and total_bid_qty > 0:
                ask_bid_qty_ratio = total_ask_qty / total_bid_qty
                combined_sell_pressure_threshold = strategy_config.get('combined_sell_pressure_threshold', 2.0)
                
                if (sell_contract_ratio >= 0.6 and  # 매도체결 60% 이상
                    ask_bid_qty_ratio >= combined_sell_pressure_threshold and  # 매도호가 2배 이상
                    current_pnl_rate <= 1.0):  # 1% 이하 수익일 때
                    return "combined_sell_pressure"
            
            return None
            
        except Exception as e:
            logger.debug(f"강화된 체결 불균형 매도 조건 확인 실패 {stock.stock_code}: {e}")
            return None
