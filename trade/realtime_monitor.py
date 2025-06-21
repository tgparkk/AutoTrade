"""
장시간 실시간 모니터링을 담당하는 RealTimeMonitor 클래스 (웹소켓 기반 최적화 버전)
"""

import time
import asyncio
import threading
from typing import Dict, List, Optional, Set
from datetime import datetime, time as dt_time
from collections import defaultdict, deque
from models.stock import Stock, StockStatus
from .stock_manager import StockManager
from .trade_executor import TradeExecutor
from .order_recovery_manager import OrderRecoveryManager
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class RealTimeMonitor:
    """장시간 실시간 모니터링을 담당하는 클래스 (웹소켓 기반 최적화 버전)"""
    
    def __init__(self, stock_manager: StockManager, trade_executor: TradeExecutor):
        """RealTimeMonitor 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            trade_executor: 매매 실행자 인스턴스
        """
        self.stock_manager = stock_manager
        self.trade_executor = trade_executor
        
        # OrderRecoveryManager 초기화
        self.order_recovery_manager = OrderRecoveryManager(stock_manager, trade_executor)
        
        # StockManager에 자신의 참조 설정 (체결통보 통계 업데이트용)
        self.stock_manager.set_realtime_monitor_ref(self)
        
        # StockManager에 OrderRecoveryManager 참조 설정
        self.stock_manager.set_order_recovery_manager(self.order_recovery_manager)
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.market_config = self.config_loader.load_market_schedule_config()
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 장시간 최적화 설정
        self.fast_monitoring_interval = 3   # 빠른 모니터링: 3초
        self.normal_monitoring_interval = 10  # 일반 모니터링: 10초
        self.current_monitoring_interval = self.fast_monitoring_interval
        
        # 모니터링 상태
        self.is_monitoring = False
        self.monitor_thread = None
        self.websocket_manager = None
        
        # 중복 알림 방지 (유지)
        self.alert_sent = set()
        
        # 장시간 통계
        self.market_scan_count = 0
        self.buy_signals_detected = 0
        self.sell_signals_detected = 0
        self.orders_executed = 0
        
        # 세분화된 주문 통계
        self.buy_orders_submitted = 0    # 매수 주문 접수 수
        self.sell_orders_submitted = 0   # 매도 주문 접수 수
        self.buy_orders_executed = 0     # 매수 체결 수 (웹소켓에서 업데이트)
        self.sell_orders_executed = 0    # 매도 체결 수 (웹소켓에서 업데이트)
        
        # 시장 시간 설정
        self.market_open_time = dt_time(9, 0)   # 09:00
        self.market_close_time = dt_time(15, 30)  # 15:30
        self.day_trading_exit_time = dt_time(15, 0)  # 15:00 (데이트레이딩 종료)
        self.pre_close_time = dt_time(14, 50)  # 14:50 (마감 10분 전)
        
        # 장시간 동적 조정
        self.market_volatility_threshold = 0.02  # 2% 이상 변동시 빠른 모니터링
        self.high_volume_threshold = 3.0  # 3배 이상 거래량 증가시 빠른 모니터링
        
        # 🆕 공식 문서 기반 고급 매매 지표 임계값
        self.contract_strength_threshold = 120.0  # 체결강도 임계값
        self.buy_ratio_threshold = 60.0          # 매수비율 임계값 (%)
        self.vi_activation_threshold = True       # VI 발동 시 거래 중단 여부
        self.market_pressure_weight = 0.3        # 시장압력 가중치
        
        logger.info("RealTimeMonitor 초기화 완료 (웹소켓 기반 최적화 버전)")
    
    def is_market_open(self) -> bool:
        """시장 개장 여부 확인
        
        Returns:
            시장 개장 여부
        """
        current_time = now_kst().time()
        current_weekday = now_kst().weekday()
        
        # 주말 체크 (토: 5, 일: 6)
        if current_weekday >= 5:
            return False
        
        # 시장 시간 체크
        return self.market_open_time <= current_time <= self.market_close_time
    
    def is_trading_time(self) -> bool:
        """거래 가능 시간 확인 (데이트레이딩 시간 고려)
        
        Returns:
            거래 가능 여부
        """
        if not self.is_market_open():
            return False
        
        current_time = now_kst().time()
        
        # 점심시간 체크 (12:00~13:00)
        lunch_start = dt_time(12, 0)
        lunch_end = dt_time(13, 0)
        lunch_trading = self.market_config.get('lunch_break_trading', False)
        
        if not lunch_trading and lunch_start <= current_time <= lunch_end:
            return False
        
        # 데이트레이딩 종료 시간 체크
        if current_time >= self.day_trading_exit_time:
            return False
        
        return True
    
    def get_market_phase(self) -> str:
        """현재 시장 단계 확인
        
        Returns:
            시장 단계 ('opening', 'active', 'lunch', 'pre_close', 'closing', 'closed')
        """
        current_time = now_kst().time()
        
        if not self.is_market_open():
            return 'closed'
        
        if current_time <= dt_time(9, 30):
            return 'opening'
        elif current_time <= dt_time(12, 0):
            return 'active'
        elif current_time <= dt_time(13, 0):
            return 'lunch'
        elif current_time <= self.pre_close_time:
            return 'active'
        elif current_time <= self.day_trading_exit_time:
            return 'pre_close'
        else:
            return 'closing'
    
    def adjust_monitoring_frequency(self):
        """시장 상황에 따른 모니터링 주기 동적 조정"""
        market_phase = self.get_market_phase()
        
        # 기본 모니터링 주기 설정
        if market_phase in ['opening', 'pre_close']:
            # 장 시작과 마감 전에는 빠른 모니터링
            target_interval = self.fast_monitoring_interval
        elif market_phase == 'lunch':
            # 점심시간에는 느린 모니터링
            target_interval = self.normal_monitoring_interval * 2
        else:
            # 일반 시간대
            target_interval = self.normal_monitoring_interval
        
        # 시장 변동성에 따른 추가 조정
        high_volatility_detected = self._detect_high_volatility()
        if high_volatility_detected:
            target_interval = min(target_interval, self.fast_monitoring_interval)
        
        # 모니터링 주기 업데이트
        if self.current_monitoring_interval != target_interval:
            self.current_monitoring_interval = target_interval
            logger.info(f"모니터링 주기 조정: {target_interval}초 (시장단계: {market_phase})")
    
    def _detect_high_volatility(self) -> bool:
        """고변동성 시장 감지 (웹소켓 데이터 기반)
        
        Returns:
            고변동성 여부
        """
        try:
            # 보유 종목들의 변동률 확인 (StockManager 데이터 활용)
            positions = self.stock_manager.get_all_positions()
            high_volatility_count = 0
            
            for position in positions:
                if position.status in [StockStatus.BOUGHT, StockStatus.WATCHING]:
                    # 🔥 웹소켓 실시간 데이터 직접 활용
                    current_price = position.realtime_data.current_price
                    reference_price = position.reference_data.yesterday_close
                    
                    if reference_price > 0:
                        price_change_rate = abs((current_price - reference_price) / reference_price)
                        
                        if price_change_rate >= self.market_volatility_threshold:
                            high_volatility_count += 1
            
            # 30% 이상의 종목이 고변동성이면 전체적으로 고변동성 시장
            return high_volatility_count >= len(positions) * 0.3
            
        except Exception as e:
            logger.error(f"고변동성 감지 오류: {e}")
            return False
    
    def get_realtime_data(self, stock_code: str) -> Optional[Dict]:
        """웹소켓 실시간 데이터 조회 (StockManager 기반)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            실시간 데이터 또는 None
        """
        try:
            # 🔥 StockManager의 실시간 데이터를 직접 활용
            stock = self.stock_manager.get_selected_stock(stock_code)
            if not stock:
                return None
            
            # 웹소켓에서 수신한 실시간 데이터 반환
            return {
                'stock_code': stock_code,
                'current_price': stock.realtime_data.current_price,
                'open_price': stock.reference_data.yesterday_close,  # 기준가로 전일 종가 사용
                'high_price': stock.realtime_data.today_high,
                'low_price': stock.realtime_data.today_low,
                'volume': stock.realtime_data.today_volume,
                'contract_volume': stock.realtime_data.contract_volume,
                'price_change_rate': stock.realtime_data.price_change_rate,
                'volume_spike_ratio': stock.realtime_data.volume_spike_ratio,
                'bid_price': stock.realtime_data.bid_price,
                'ask_price': stock.realtime_data.ask_price,
                'bid_prices': stock.realtime_data.bid_prices,
                'ask_prices': stock.realtime_data.ask_prices,
                'bid_volumes': stock.realtime_data.bid_volumes,
                'ask_volumes': stock.realtime_data.ask_volumes,
                'timestamp': now_kst(),
                'last_updated': stock.realtime_data.last_updated,
                'source': 'websocket'
            }
            
        except Exception as e:
            logger.error(f"실시간 데이터 조회 실패 {stock_code}: {e}")
            return None
    
    def analyze_buy_conditions(self, stock: Stock, realtime_data: Dict) -> bool:
        """매수 조건 분석 (공식 문서 기반 고급 지표 활용)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            
        Returns:
            매수 조건 충족 여부
        """
        try:
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
            market_phase = self.get_market_phase()
            
            # 장 초반에는 더 엄격한 조건
            if market_phase == 'opening':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * 1.5
                price_threshold = 0.015  # 1.5%
                contract_strength_min = self.contract_strength_threshold * 1.2
                buy_ratio_min = self.buy_ratio_threshold * 1.1
            # 마감 전에는 보수적 접근
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
            
            # 중복 신호 방지
            signal_key = f"{stock.stock_code}_buy"
            duplicate_prevention = signal_key not in self.alert_sent
            
            # 🔥 최종 매수 신호 판단 (모든 조건 통합)
            buy_signal = (volume_condition and price_condition and 
                         volume_min_condition and pattern_condition and
                         strength_condition and buy_ratio_condition and
                         market_pressure_condition and spread_condition and
                         duplicate_prevention)
            #buy_signal =  True
            
            if buy_signal:
                self.alert_sent.add(signal_key)
                self.buy_signals_detected += 1
                logger.info(f"🚀 {stock.stock_code}({stock.stock_name}) 매수 신호 ({market_phase}): "
                           f"거래량({volume_spike_ratio:.1f}배≥{volume_threshold:.1f}), "
                           f"상승률({price_change_rate:.2%}≥{price_threshold:.1%}), "
                           f"체결강도({contract_strength:.1f}≥{contract_strength_min:.1f}), "
                           f"매수비율({buy_ratio:.1f}%≥{buy_ratio_min:.1f}%), "
                           f"시장압력({market_pressure}), "
                           f"패턴점수({stock.total_pattern_score:.1f}≥{min_pattern_score})")
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    def analyze_sell_conditions(self, stock: Stock, realtime_data: Dict) -> Optional[str]:
        """매도 조건 분석 (우선순위 기반 개선 버전)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            
        Returns:
            매도 사유 또는 None
        """
        try:
            current_price = realtime_data.get('current_price', stock.close_price)
            market_phase = self.get_market_phase()
            
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
    
    def process_buy_ready_stocks(self) -> Dict[str, int]:
        """매수 준비 상태 종목들 처리 (웹소켓 기반)
        
        Returns:
            처리 결과 딕셔너리 {'checked': 확인한 종목 수, 'signaled': 신호 발생 수, 'ordered': 주문 접수 수}
        """
        result = {'checked': 0, 'signaled': 0, 'ordered': 0}
        
        try:
            # 선정된 종목들 중 매수 준비 상태인 것들 조회
            ready_stocks = self.stock_manager.get_stocks_by_status(StockStatus.WATCHING)
            
            for stock in ready_stocks:
                result['checked'] += 1
                
                try:
                    # 🔥 웹소켓 실시간 데이터 조회 (API 호출 대신)
                    realtime_data = self.get_realtime_data(stock.stock_code)
                    
                    if not realtime_data:
                        continue
                    
                    # 매수 조건 확인
                    if self.analyze_buy_conditions(stock, realtime_data):
                        result['signaled'] += 1
                        
                        # 매수량 계산
                        buy_quantity = self.calculate_buy_quantity(stock)
                        
                        if buy_quantity > 0:
                            # 매수 주문 실행
                            current_positions = len(self.stock_manager.get_stocks_by_status(StockStatus.BOUGHT))
                            success = self.trade_executor.execute_buy_order(
                                stock=stock,
                                price=realtime_data['current_price'],
                                quantity=buy_quantity,
                                current_positions_count=current_positions
                            )
                            
                            if success:
                                # 주문 접수 성공 - 체결은 별도로 웹소켓 체결통보에서 처리
                                result['ordered'] += 1
                                self.buy_orders_submitted += 1  # 클래스 통계 업데이트
                                
                                logger.info(f"📝 매수 주문 접수: {stock.stock_code} "
                                           f"{buy_quantity}주 @{realtime_data['current_price']:,}원 "
                                           f"- 체결 대기 중 (웹소켓 체결통보 대기)")
                                
                            else:
                                # 주문 접수 실패
                                logger.error(f"❌ 매수 주문 접수 실패: {stock.stock_code} "
                                            f"{buy_quantity}주 @{realtime_data['current_price']:,}원")
                        
                except Exception as e:
                    logger.error(f"매수 처리 오류 {stock.stock_code}: {e}")
                    continue
            
            return result
            
        except Exception as e:
            logger.error(f"매수 준비 종목 처리 오류: {e}")
            return result
    
    def process_sell_ready_stocks(self) -> Dict[str, int]:
        """매도 준비 상태 종목들 처리 (웹소켓 기반)
        
        Returns:
            처리 결과 딕셔너리 {'checked': 확인한 종목 수, 'signaled': 신호 발생 수, 'ordered': 주문 접수 수}
        """
        result = {'checked': 0, 'signaled': 0, 'ordered': 0}
        
        try:
            # 보유 중인 종목들 조회
            holding_stocks = self.stock_manager.get_stocks_by_status(StockStatus.BOUGHT)
            
            for stock in holding_stocks:
                result['checked'] += 1
                
                try:
                    # 🔥 웹소켓 실시간 데이터 조회 (API 호출 대신)
                    realtime_data = self.get_realtime_data(stock.stock_code)
                    
                    if not realtime_data:
                        continue
                    
                    # 매도 조건 확인
                    sell_reason = self.analyze_sell_conditions(stock, realtime_data)
                    
                    if sell_reason:
                        result['signaled'] += 1
                        self.sell_signals_detected += 1
                        
                        # 매도 주문 실행
                        success = self.trade_executor.execute_sell_order(
                            stock=stock,
                            price=realtime_data['current_price'],
                            reason=sell_reason
                        )
                        
                        if success:
                            # 주문 접수 성공 - 체결은 별도로 웹소켓 체결통보에서 처리
                            result['ordered'] += 1
                            self.sell_orders_submitted += 1  # 클래스 통계 업데이트
                            
                            # 중복 알림 방지 제거
                            signal_key = f"{stock.stock_code}_buy"
                            self.alert_sent.discard(signal_key)
                            
                            logger.info(f"📝 매도 주문 접수: {stock.stock_code} "
                                       f"@{realtime_data['current_price']:,}원 (사유: {sell_reason}) "
                                       f"- 체결 대기 중 (웹소켓 체결통보 대기)")
                        else:
                            # 주문 접수 실패
                            logger.error(f"❌ 매도 주문 접수 실패: {stock.stock_code} "
                                        f"@{realtime_data['current_price']:,}원 (사유: {sell_reason})")
                        
                except Exception as e:
                    logger.error(f"매도 처리 오류 {stock.stock_code}: {e}")
                    continue
            
            return result
            
        except Exception as e:
            logger.error(f"매도 준비 종목 처리 오류: {e}")
            return result
    
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
    
    def monitor_cycle(self):
        """모니터링 사이클 실행 (웹소켓 기반 최적화)"""
        try:
            self.market_scan_count += 1
            
            # 시장 상황 확인 및 모니터링 주기 조정
            self.adjust_monitoring_frequency()
            
            # 테스트 모드 설정 (config에서 로드)
            test_mode = self.strategy_config.get('test_mode', True)
            
            if not test_mode:
                # 실제 운영 모드: 시장시간 체크
                if not self.is_market_open():
                    if self.market_scan_count % 60 == 0:  # 10분마다 로그
                        logger.info("시장 마감 - 대기 중...")
                    return
                
                # 거래 시간이 아니면 모니터링만
                if not self.is_trading_time():
                    market_phase = self.get_market_phase()
                    if market_phase == 'lunch':
                        if self.market_scan_count % 30 == 0:  # 5분마다 로그
                            logger.info("점심시간 - 모니터링만 실행")
                    elif market_phase == 'closing':
                        logger.info("장 마감 시간 - 보유 포지션 정리 중...")
                        self.process_sell_ready_stocks()  # 마감 시간에는 매도만
                    return
            else:
                # 테스트 모드: 시간 제한 없이 실행
                if self.market_scan_count % 100 == 0:  # 주기적으로 테스트 모드 알림
                    logger.debug("테스트 모드 - 시장시간 무관하게 실행 중")
            
            # 성능 로깅 (5분마다)
            if self.market_scan_count % (300 // self.current_monitoring_interval) == 0:
                self._log_performance_metrics()
            
            # 매수 준비 종목 처리
            buy_result = self.process_buy_ready_stocks()
            
            # 매도 준비 종목 처리  
            sell_result = self.process_sell_ready_stocks()
            
            # 🔧 정체된 주문 타임아웃 체크 (30초마다 - 6회마다 실행)
            if self.market_scan_count % (30 // self.current_monitoring_interval) == 0:
                self._check_stuck_orders()
            
            # 주기적 상태 리포트 (1분마다)
            if self.market_scan_count % (60 // self.current_monitoring_interval) == 0:
                self._log_status_report(buy_result, sell_result)
                
        except Exception as e:
            logger.error(f"모니터링 사이클 오류: {e}")
    
    def _log_performance_metrics(self):
        """성능 지표 로깅 (웹소켓 기반)"""
        try:
            market_phase = self.get_market_phase()
            positions = self.stock_manager.get_all_positions()
            
            # 포지션 상태별 집계
            status_counts = defaultdict(int)
            total_unrealized_pnl = 0
            
            for pos in positions:
                status_counts[pos.status.value] += 1
                if pos.status == StockStatus.BOUGHT:
                    # 🔥 웹소켓 실시간 데이터 직접 활용
                    current_price = pos.realtime_data.current_price
                    unrealized_pnl = pos.calculate_unrealized_pnl(current_price)
                    total_unrealized_pnl += unrealized_pnl
            
            logger.info(f"📊 성능 지표 ({market_phase}): "
                       f"스캔횟수: {self.market_scan_count}, "
                       f"매수신호: {self.buy_signals_detected}, "
                       f"매도신호: {self.sell_signals_detected}, "
                       f"주문실행: {self.orders_executed}, "
                       f"미실현손익: {total_unrealized_pnl:+,.0f}원")
            
            logger.info(f"📈 포지션 현황: " + 
                       ", ".join([f"{status}: {count}개" for status, count in status_counts.items()]))
                       
        except Exception as e:
            logger.error(f"성능 지표 로깅 오류: {e}")
    
    def _check_stuck_orders(self):
        """정체된 주문들 타임아웃 체크 및 자동 복구 (OrderRecoveryManager 사용)"""
        try:
            # OrderRecoveryManager를 통한 자동 복구
            recovered_count = self.order_recovery_manager.auto_recover_stuck_orders()
            
            if recovered_count > 0:
                logger.warning(f"⚠️ 정체된 주문 {recovered_count}건 자동 복구 완료")
            
            # 추가 검증: 비정상적인 상태 전환 체크
            issues = self.order_recovery_manager.validate_stock_transitions()
            if issues:
                logger.warning(f"🚨 비정상적인 상태 전환 감지:")
                for issue in issues[:5]:  # 최대 5개만 로그
                    logger.warning(f"   - {issue}")
                    
        except Exception as e:
            logger.error(f"정체된 주문 체크 오류: {e}")
    
    def _log_status_report(self, buy_result: Dict[str, int], sell_result: Dict[str, int]):
        """상태 리포트 로깅"""
        try:
            current_time = now_kst().strftime("%H:%M:%S")
            market_phase = self.get_market_phase()
            
            logger.info(f"🕐 {current_time} ({market_phase}) - "
                       f"매수(확인:{buy_result['checked']}/신호:{buy_result['signaled']}/주문:{buy_result['ordered']}), "
                       f"매도(확인:{sell_result['checked']}/신호:{sell_result['signaled']}/주문:{sell_result['ordered']}), "
                       f"모니터링주기: {self.current_monitoring_interval}초")
                       
        except Exception as e:
            logger.error(f"상태 리포트 로깅 오류: {e}")
    
    
    def stop_monitoring(self):
        """모니터링 중지"""
        self.is_monitoring = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        # 최종 성능 지표 출력
        self._log_final_performance()
        
        logger.info("⏹️ 실시간 모니터링 중지")
    
    def _log_final_performance(self):
        """최종 성능 지표 출력"""
        try:
            logger.info("=" * 60)
            logger.info("📊 최종 성능 리포트")
            logger.info("=" * 60)
            logger.info(f"총 스캔 횟수: {self.market_scan_count:,}회")
            logger.info(f"매수 신호 감지: {self.buy_signals_detected}건")
            logger.info(f"매도 신호 감지: {self.sell_signals_detected}건")
            logger.info(f"주문 실행: {self.orders_executed}건")
            
            # 거래 통계
            trade_stats = self.trade_executor.get_trade_statistics()
            logger.info(f"거래 성과: 승률 {trade_stats['win_rate']:.1f}%, "
                       f"총 손익 {trade_stats['total_pnl']:+,.0f}원")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"최종 성능 리포트 오류: {e}")
    
    def get_monitoring_status(self) -> Dict:
        """모니터링 상태 정보 반환 (웹소켓 기반 최적화)"""
        # OrderRecoveryManager 통계 포함
        recovery_stats = self.order_recovery_manager.get_recovery_statistics()
        
        return {
            'is_monitoring': self.is_monitoring,
            'is_market_open': self.is_market_open(),
            'is_trading_time': self.is_trading_time(),
            'market_phase': self.get_market_phase(),
            'monitoring_interval': self.current_monitoring_interval,
            'market_scan_count': self.market_scan_count,
            'buy_signals_detected': self.buy_signals_detected,
            'sell_signals_detected': self.sell_signals_detected,
            'orders_executed': self.orders_executed,
            'websocket_stocks': len(self.stock_manager.realtime_data),  # 웹소켓 관리 종목 수
            'alerts_sent': len(self.alert_sent),
            'order_recovery_stats': recovery_stats  # 🆕 주문 복구 통계 추가
        }
    
    def force_sell_all_positions(self) -> int:
        """모든 포지션 강제 매도 (장 마감 전) - 웹소켓 기반
        
        Returns:
            매도 처리된 포지션 수
        """
        logger.info("🚨 모든 포지션 강제 매도 시작")
        
        sold_count = 0
        holding_stocks = self.stock_manager.get_stocks_by_status(StockStatus.BOUGHT)
        
        for stock in holding_stocks:
            try:
                # 🔥 웹소켓 실시간 데이터 활용
                realtime_data = self.get_realtime_data(stock.stock_code)
                current_price = realtime_data['current_price'] if realtime_data else stock.close_price
                
                success = self.trade_executor.execute_sell_order(
                    stock=stock,
                    price=current_price,
                    reason="force_close"
                )
                
                if success:
                    self.trade_executor.confirm_sell_execution(stock, current_price)
                    sold_count += 1
                    logger.info(f"강제 매도: {stock.stock_code}")
                    
            except Exception as e:
                logger.error(f"강제 매도 실패 {stock.stock_code}: {e}")
        
        logger.info(f"강제 매도 완료: {sold_count}개 포지션")
        return sold_count
    
    def __str__(self) -> str:
        """문자열 표현"""
        return (f"RealTimeMonitor(모니터링: {self.is_monitoring}, "
                f"주기: {self.current_monitoring_interval}초, "
                f"스캔횟수: {self.market_scan_count}, "
                f"신호감지: 매수{self.buy_signals_detected}/매도{self.sell_signals_detected}, "
                f"웹소켓종목: {len(self.stock_manager.realtime_data)}개)")
    
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