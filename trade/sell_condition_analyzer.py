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
                               strategy_config: Dict, risk_config: Dict) -> Optional[str]:
        """매도 조건 분석 (우선순위 기반 개선 버전)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계
            strategy_config: 전략 설정
            risk_config: 리스크 설정
            
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
                market_phase, contract_strength, buy_ratio, market_pressure, strategy_config
            )
            if technical_sell_reason:
                return technical_sell_reason
            
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
                                        market_pressure: str, strategy_config: Dict) -> Optional[str]:
        """기술적 지표 기반 매도 조건 확인"""
        # 체결강도 급락
        weak_contract_strength_threshold = strategy_config.get('weak_contract_strength_threshold', 80.0)
        if contract_strength <= weak_contract_strength_threshold:
            if current_pnl_rate <= 0:
                return "weak_contract_strength"
        
        # 매수비율 급락
        low_buy_ratio_threshold = strategy_config.get('low_buy_ratio_threshold', 30.0)
        if buy_ratio <= low_buy_ratio_threshold:
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
        
        return base_stop_loss * multiplier
    
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
