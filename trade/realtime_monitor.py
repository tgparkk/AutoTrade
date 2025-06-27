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
from .trading_condition_analyzer import TradingConditionAnalyzer
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader
# 🆕 Performance logging helper
from trade.realtime.performance_logger import PerformanceLogger
# 🆕 workers
from trade.realtime.scan_worker import IntradayScanWorker
# 🆕 Subscription manager
from trade.realtime.ws_subscription import SubscriptionManager

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
        
        # 🔥 TradingConditionAnalyzer 초기화 (매매 조건 분석 전담)
        self.condition_analyzer = TradingConditionAnalyzer(stock_manager, trade_executor)
        
        # 🆕 PerformanceLogger 초기화
        self.performance_logger = PerformanceLogger(self)
        
        # 🆕 IntradayScanWorker 초기화
        self.scan_worker = IntradayScanWorker(self)
        
        # 🆕 SubscriptionManager 초기화
        self.sub_manager = SubscriptionManager(self)
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.performance_config = self.config_loader.load_performance_config()  # 🆕 성능 설정 추가
        self.market_config = self.config_loader.load_market_schedule_config()
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 🔥 설정 기반 모니터링 주기 (하드코딩 제거)
        self.fast_monitoring_interval = self.performance_config.get('fast_monitoring_interval', 3)
        self.normal_monitoring_interval = self.performance_config.get('normal_monitoring_interval', 10)
        self.current_monitoring_interval = self.fast_monitoring_interval
        
        # 모니터링 상태 (스레드 안전성 개선)
        self._monitoring_lock = threading.RLock()  # 모니터링 상태 보호용
        self._is_monitoring = threading.Event()    # 스레드 안전한 플래그
        self.monitor_thread = None
        self.websocket_manager = None
        
        # 🆕 원자적 통계 업데이트를 위한 락
        self._stats_lock = threading.RLock()
        
        # 통계 (원자적 접근 보장)
        self._market_scan_count = 0
        self._buy_signals_detected = 0
        self._sell_signals_detected = 0
        self._buy_orders_executed = 0
        self._sell_orders_executed = 0
        self._last_scan_time = None
        
        # 🆕 스레드 안전한 종료 플래그
        self._shutdown_requested = threading.Event()
        
        # 🔥 설정 기반 시장 시간 (하드코딩 제거)
        self.market_open_time = dt_time(
            self.strategy_config.get('market_open_hour', 9), 
            self.strategy_config.get('market_open_minute', 0)
        )
        self.market_close_time = dt_time(
            self.strategy_config.get('market_close_hour', 15), 
            self.strategy_config.get('market_close_minute', 30)
        )
        self.day_trading_exit_time = dt_time(
            self.strategy_config.get('day_trading_exit_hour', 15), 
            self.strategy_config.get('day_trading_exit_minute', 0)
        )
        self.pre_close_time = dt_time(
            self.strategy_config.get('pre_close_hour', 14), 
            self.strategy_config.get('pre_close_minute', 50)
        )
        
        # 🔥 설정 기반 동적 조정 임계값 (하드코딩 제거)
        self.market_volatility_threshold = self.strategy_config.get('market_volatility_threshold', 0.02)
        self.high_volume_threshold = self.strategy_config.get('high_volume_threshold', 3.0)
        self.high_volatility_position_ratio = self.strategy_config.get('high_volatility_position_ratio', 0.3)
        
        # 중복 알림 방지 (유지)
        self.alert_sent = set()
        
        # 🔥 설정 기반 장중 추가 종목 스캔 (하드코딩 제거)
        self.last_intraday_scan_time = None
        self.intraday_scan_interval = self.performance_config.get('intraday_scan_interval_minutes', 30) * 60  # 분을 초로 변환
        self.max_additional_stocks = self.performance_config.get('max_intraday_selected_stocks', 10)
        
        # 🔥 웹소켓 구독 대기열은 SubscriptionManager 로 관리
        
        # 🆕 중복 매수 쿨다운 관리 (Expectancy 개선)
        self._recent_buy_times: Dict[str, datetime] = {}
        self.duplicate_buy_cooldown = self.performance_config.get('duplicate_buy_cooldown_seconds', 10)
        
        # 🆕 BuyProcessor 초기화 (매수 조건/주문 위임)
        from trade.realtime.buy_processor import BuyProcessor
        self.buy_processor = BuyProcessor(
            stock_manager=self.stock_manager,
            trade_executor=self.trade_executor,
            condition_analyzer=self.condition_analyzer,
            performance_config=self.performance_config,
            risk_config=self.risk_config,
            duplicate_buy_cooldown=self.duplicate_buy_cooldown,
        )

        # RealTimeMonitor 와 최근 매수 시각 dict 공유 (기존 로직 호환)
        self.buy_processor._recent_buy_times = self._recent_buy_times
        
        # 🆕 SellProcessor 초기화
        from trade.realtime.sell_processor import SellProcessor
        self.sell_processor = SellProcessor(
            stock_manager=self.stock_manager,
            trade_executor=self.trade_executor,
            condition_analyzer=self.condition_analyzer,
            performance_config=self.performance_config,
            risk_config=self.risk_config,
        )

        logger.info("RealTimeMonitor 초기화 완료 (웹소켓 기반 최적화 버전 + 장중추가스캔)")

        # 🆕 MonitorCore 생성 (legacy monitor_cycle 위임용)
        from trade.realtime.monitor_core import MonitorCore
        self.core = MonitorCore(self)
    
    @property
    def is_monitoring(self) -> bool:
        """모니터링 상태 확인"""
        return self._is_monitoring.is_set()
    
    @is_monitoring.setter
    def is_monitoring(self, value: bool):
        """모니터링 상태 설정"""
        if value:
            self._is_monitoring.set()
        else:
            self._is_monitoring.clear()
    
    @property
    def market_scan_count(self) -> int:
        """시장 스캔 횟수"""
        with self._stats_lock:
            return self._market_scan_count
    
    @market_scan_count.setter
    def market_scan_count(self, value: int):
        """시장 스캔 횟수 설정"""
        with self._stats_lock:
            self._market_scan_count = value
    
    @property
    def buy_signals_detected(self) -> int:
        """매수 신호 탐지 횟수"""
        with self._stats_lock:
            return self._buy_signals_detected
    
    @buy_signals_detected.setter
    def buy_signals_detected(self, value: int):
        """매수 신호 탐지 횟수 설정"""
        with self._stats_lock:
            self._buy_signals_detected = value
    
    @property
    def sell_signals_detected(self) -> int:
        """매도 신호 탐지 횟수"""
        with self._stats_lock:
            return self._sell_signals_detected
    
    @sell_signals_detected.setter
    def sell_signals_detected(self, value: int):
        """매도 신호 탐지 횟수 설정"""
        with self._stats_lock:
            self._sell_signals_detected = value
    
    @property
    def orders_executed(self) -> int:
        """총 주문 체결 수 (매수+매도)"""
        with self._stats_lock:
            return self._buy_orders_executed + self._sell_orders_executed

    @orders_executed.setter
    def orders_executed(self, value: int):
        """총 주문 체결 수 초기화용 (매수 실행 수만 설정, 매도는 0으로 리셋)"""
        with self._stats_lock:
            self._buy_orders_executed = value
            self._sell_orders_executed = 0
    
    @property
    def buy_orders_executed(self) -> int:
        """매수 주문 체결 수"""
        with self._stats_lock:
            return self._buy_orders_executed

    @buy_orders_executed.setter
    def buy_orders_executed(self, value: int):
        with self._stats_lock:
            self._buy_orders_executed = value

    @property
    def sell_orders_executed(self) -> int:
        """매도 주문 체결 수"""
        with self._stats_lock:
            return self._sell_orders_executed

    @sell_orders_executed.setter
    def sell_orders_executed(self, value: int):
        with self._stats_lock:
            self._sell_orders_executed = value
    
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
        
        # 점심시간 거래 제한 없음 (설정 제거됨)
        
        # 데이트레이딩 종료 시간 체크
        if current_time >= self.day_trading_exit_time:
            return False
        
        return True
    
    def get_market_phase(self) -> str:
        """현재 시장 단계 확인 (TradingConditionAnalyzer 위임)
        
        Returns:
            시장 단계 ('opening', 'active', 'lunch', 'pre_close', 'closing', 'closed')
        """
        # TradingConditionAnalyzer의 get_market_phase 사용 (중복 제거)
        return self.condition_analyzer.get_market_phase()
    
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
        #high_volatility_detected = self._detect_high_volatility()
        #if high_volatility_detected:
        #    target_interval = min(target_interval, self.fast_monitoring_interval)
        
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
            
            # 설정 기반 고변동성 종목 비율 임계값
            return high_volatility_count >= len(positions) * self.high_volatility_position_ratio
            
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
        """(Deprecated) 기존 API 호환용 래퍼 – BuyProcessor 로 위임"""
        market_phase = self.get_market_phase()
        return self.buy_processor.analyze_buy_conditions(stock, realtime_data, market_phase)
    
    def analyze_sell_conditions(self, stock: Stock, realtime_data: Dict) -> Optional[str]:
        """매도 조건 분석 (TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            
        Returns:
            매도 사유 또는 None
        """
        # TradingConditionAnalyzer에 위임
        market_phase = self.get_market_phase()
        return self.condition_analyzer.analyze_sell_conditions(stock, realtime_data, market_phase)
    
    def process_buy_ready_stocks(self) -> Dict[str, int]:
        """매수 준비 상태 종목들 처리 (데이트레이딩 최적화 버전)
        
        Returns:
            처리 결과 딕셔너리 {'checked': 확인한 종목 수, 'signaled': 신호 발생 수, 'ordered': 주문 접수 수}
        """
        result = {'checked': 0, 'signaled': 0, 'ordered': 0}
        
        try:
            # 장 마감 임박 시 신규 진입 금지 (데이트레이딩 수익성 보호)
            now_time = now_kst().time()
            if now_time >= self.pre_close_time or now_time >= self.day_trading_exit_time:
                logger.debug("pre_close_time / day_trading_exit_time 이후 - 신규 매수 처리 생략")
                return result
            
            # 🔥 배치 처리로 락 경합 최소화 - 한 번에 두 상태 조회
            from models.stock import StockStatus
            batch_stocks = self.stock_manager.get_stocks_by_status_batch([
                StockStatus.WATCHING, 
                StockStatus.BOUGHT
            ])
            
            ready_stocks = batch_stocks[StockStatus.WATCHING]
            current_positions_count = len(batch_stocks[StockStatus.BOUGHT])
            
            # 빈 리스트면 조기 반환
            if not ready_stocks:
                return result
            
            # 🔥 실시간 데이터를 배치로 미리 수집 (락 경합 방지)
            stock_realtime_data = {}
            for stock in ready_stocks:
                try:
                    realtime_data = self.get_realtime_data(stock.stock_code)
                    if realtime_data:
                        stock_realtime_data[stock.stock_code] = realtime_data
                except Exception as e:
                    logger.debug(f"실시간 데이터 조회 실패 {stock.stock_code}: {e}")
                    continue
            
            # 🆕 데이트레이딩 모드 확인 (빠른 진입 vs 안전한 진입)
            daytrading_mode = self.performance_config.get('daytrading_aggressive_mode', False)
            
            # 🆕 매수 조건 분석 및 주문 실행 (BuyProcessor 위임 + 빠른모드 유지)
            for stock in ready_stocks:
                result['checked'] += 1

                realtime_data = stock_realtime_data.get(stock.stock_code)
                if not realtime_data:
                    continue

                try:
                    # ------------------------------
                    # 1) 매수 신호 판단
                    # ------------------------------
                    if daytrading_mode:
                        # 기존 빠른 진입 로직 유지
                        buy_signal = self._analyze_fast_buy_conditions(stock, realtime_data)
                    else:
                        market_phase = self.get_market_phase()
                        buy_signal = self.buy_processor.analyze_buy_conditions(
                            stock, realtime_data, market_phase
                        )

                    if not buy_signal:
                        continue

                    result['signaled'] += 1

                    # ------------------------------
                    # 2) 주문 실행
                    # ------------------------------
                    if daytrading_mode:
                        # 기존 방식 그대로 실행 (수량 계산 → 매수)
                        buy_quantity = self.calculate_buy_quantity(stock)
                        if buy_quantity <= 0:
                            continue

                        success = self.trade_executor.execute_buy_order(
                            stock=stock,
                            price=realtime_data['current_price'],
                            quantity=buy_quantity,
                            current_positions_count=current_positions_count,
                        )
                        if success:
                            # 중복 방지용 최근 매수 시각 기록
                            self._recent_buy_times[stock.stock_code] = now_kst()
                            logger.info(
                                f"📝 매수 주문 접수: {stock.stock_code} {buy_quantity}주 "
                                f"@{realtime_data['current_price']:,}원 - 체결 대기" )
                    else:
                        success = self.buy_processor.analyze_and_buy(
                            stock=stock,
                            realtime_data=realtime_data,
                            current_positions_count=current_positions_count,
                            market_phase=self.get_market_phase(),
                        )

                    if success:
                        result['ordered'] += 1

                        # 🔥 원자적 통계 업데이트 (스레드 안전)
                        with self._stats_lock:
                            self._buy_orders_executed += 1

                except Exception as e:
                    logger.error(f"매수 처리 오류 {stock.stock_code}: {e}")
                    continue
            
            return result
            
        except Exception as e:
            logger.error(f"매수 준비 종목 처리 오류: {e}")
            return result
    
    def _analyze_fast_buy_conditions(self, stock: Stock, realtime_data: Dict) -> bool:
        """데이트레이딩 빠른 진입용 간소화 매수 조건
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            
        Returns:
            매수 조건 충족 여부
        """
        try:
            # 🚨 필수 안전 체크 (절대 생략 불가)
            current_price = realtime_data.get('current_price', 0)
            if current_price <= 0:
                return False
            
            # 급락 방지 (-3% 이하 제외)
            price_change_rate = realtime_data.get('price_change_rate', 0)
            if price_change_rate <= -3.0:
                logger.debug(f"급락 제외: {stock.stock_code} ({price_change_rate:.1f}%)")
                return False
            
            # 스프레드 가드 (슬리피지 보호)
            bid_p = realtime_data.get('bid_price', 0) or 0
            ask_p = realtime_data.get('ask_price', 0) or 0
            if bid_p > 0 and ask_p > 0:
                spread_pct = (ask_p - bid_p) / bid_p * 100
                if spread_pct > self.performance_config.get('max_spread_threshold', 5.0):
                    logger.debug(f"스프레드 과대({spread_pct:.2f}%) - 매수 스킵: {stock.stock_code}")
                    return False
            
            # 중복 매수 쿨다운
            last_buy_time = self._recent_buy_times.get(stock.stock_code)
            if last_buy_time and (now_kst() - last_buy_time).total_seconds() < self.duplicate_buy_cooldown:
                logger.debug(f"쿨다운 미지남 - 중복 매수 스킵: {stock.stock_code}")
                return False
            
            # 🚀 핵심 조건만 체크 (총 3가지)
            conditions_met = 0
            condition_details = []
            
            # 1. 모멘텀 체크 (가장 중요)
            if price_change_rate >= 0.3:  # 0.3% 이상 상승
                conditions_met += 1
                condition_details.append(f"상승모멘텀({price_change_rate:.1f}%)")
            
            # 2. 거래량 체크
            volume_spike_ratio = realtime_data.get('volume_spike_ratio', 1.0)
            if volume_spike_ratio >= 1.5:  # 1.5배 이상 거래량 증가
                conditions_met += 1
                condition_details.append(f"거래량증가({volume_spike_ratio:.1f}배)")
            
            # 3. 매수세 체크 (호가 데이터가 있을 때만)
            if bid_p > 0 and ask_p > 0:
                bid_qty = realtime_data.get('bid_volumes', [0])[0] if realtime_data.get('bid_volumes') else 0
                ask_qty = realtime_data.get('ask_volumes', [0])[0] if realtime_data.get('ask_volumes') else 0
                
                if bid_qty > 0 and ask_qty > 0:
                    bid_dominance = bid_qty / (bid_qty + ask_qty)
                    if bid_dominance >= 0.4:  # 매수 40% 이상
                        conditions_met += 1
                        condition_details.append(f"매수우세({bid_dominance:.1%})")
            
            # 4. 유동성 체크
            try:
                liq_score = self.stock_manager.get_liquidity_score(stock.stock_code)
            except AttributeError:
                liq_score = 0.0

            if liq_score >= self.performance_config.get('min_liquidity_score_for_buy', 3.0):
                conditions_met += 1
                condition_details.append(f"유동성({liq_score:.1f})")

            # 최소 2가지 조건 만족 (유동성 포함 4개 중)
            buy_signal = conditions_met >= 2
            
            if buy_signal:
                logger.info(f"🚀 {stock.stock_code}({stock.stock_name}) 빠른 매수 신호: "
                           f"{conditions_met}/4개 조건 ({', '.join(condition_details)})")
            else:
                logger.debug(f"❌ {stock.stock_code} 빠른 매수 조건 미달: "
                            f"{conditions_met}/4개 조건 ({', '.join(condition_details)})")
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"빠른 매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    def _analyze_standard_buy_conditions(self, stock: Stock, realtime_data: Dict) -> bool:
        """기존 표준 매수 조건 (기존 TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체  
            realtime_data: 실시간 데이터
            
        Returns:
            매수 조건 충족 여부
        """
        try:
            # 스프레드 가드 (슬리피지 보호)
            bid_p = realtime_data.get('bid_price', 0) or 0
            ask_p = realtime_data.get('ask_price', 0) or 0
            if bid_p > 0 and ask_p > 0:
                spread_pct = (ask_p - bid_p) / bid_p * 100
                if spread_pct > self.performance_config.get('max_spread_threshold', 5.0):
                    logger.debug(f"스프레드 과대({spread_pct:.2f}%) - 매수 스킵: {stock.stock_code}")
                    return False
            
            # 중복 매수 쿨다운
            last_buy_time = self._recent_buy_times.get(stock.stock_code)
            if last_buy_time and (now_kst() - last_buy_time).total_seconds() < self.duplicate_buy_cooldown:
                logger.debug(f"쿨다운 미지남 - 중복 매수 스킵: {stock.stock_code}")
                return False
            
            # 중복 신호 방지
            signal_key = f"{stock.stock_code}_buy"
            if signal_key in self.alert_sent:
                return False
            
            # TradingConditionAnalyzer에 위임
            market_phase = self.get_market_phase()
            buy_signal = self.condition_analyzer.analyze_buy_conditions(stock, realtime_data, market_phase)
            
            if buy_signal:
                self.alert_sent.add(signal_key)
                self._buy_signals_detected += 1
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"표준 매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    def process_sell_ready_stocks(self) -> Dict[str, int]:
        """매도 준비 상태 종목들 처리 (락 최적화 버전)
        
        Returns:
            처리 결과 딕셔너리 {'checked': 확인한 종목 수, 'signaled': 신호 발생 수, 'ordered': 주문 접수 수}
        """
        result = {'checked': 0, 'signaled': 0, 'ordered': 0}
        
        try:
            # 🔥 배치 처리로 락 경합 최소화
            # BOUGHT + PARTIAL_BOUGHT + PARTIAL_SOLD 모두 보유 포지션으로 간주
            holding_stocks = (
                self.stock_manager.get_stocks_by_status(StockStatus.BOUGHT)
                + self.stock_manager.get_stocks_by_status(StockStatus.PARTIAL_BOUGHT)
                + self.stock_manager.get_stocks_by_status(StockStatus.PARTIAL_SOLD)
            )
            
            # 빈 리스트면 조기 반환
            if not holding_stocks:
                return result
            
            # 🔥 실시간 데이터를 배치로 미리 수집 (락 경합 방지)
            stock_realtime_data = {}
            for stock in holding_stocks:
                try:
                    realtime_data = self.get_realtime_data(stock.stock_code)
                    if realtime_data:
                        stock_realtime_data[stock.stock_code] = realtime_data
                except Exception as e:
                    logger.debug(f"실시간 데이터 조회 실패 {stock.stock_code}: {e}")
                    continue
            
            # 🔥 매도 조건 분석 및 주문 실행 (락 최적화)
            for stock in holding_stocks:
                result['checked'] += 1
                
                realtime_data = stock_realtime_data.get(stock.stock_code)
                if not realtime_data:
                    continue
                
                try:
                    # SellProcessor 위임
                    market_phase = self.get_market_phase()
                    prev_sig = result['signaled']
                    success = self.sell_processor.analyze_and_sell(
                        stock=stock,
                        realtime_data=realtime_data,
                        result_dict=result,
                        market_phase=market_phase,
                    )

                    if result['signaled'] > prev_sig:
                        with self._stats_lock:
                            self._sell_signals_detected += 1
                        if success:
                            with self._stats_lock:
                                self._sell_orders_executed += 1
                        # 중복 알림 방지
                        signal_key = f"{stock.stock_code}_buy"
                        self.alert_sent.discard(signal_key)
                 
                except Exception as e:
                    logger.error(f"매도 처리 오류 {stock.stock_code}: {e}")
                    continue
            
            return result
            
        except Exception as e:
            logger.error(f"매도 준비 종목 처리 오류: {e}")
            return result
    
    def calculate_buy_quantity(self, stock: Stock) -> int:
        """매수량 계산 (TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            
        Returns:
            매수량
        """
        # TradingConditionAnalyzer에 위임
        return self.condition_analyzer.calculate_buy_quantity(stock)
    
    def monitor_cycle_legacy(self):
        """메인 모니터링 사이클 (스레드 분리)"""
        # 🔥 동시 실행 방지 (스레드 안전성 보장)
        if hasattr(self, '_cycle_executing') and self._cycle_executing:
            logger.debug("⚠️ 이전 monitor_cycle() 아직 실행 중 - 이번 사이클 건너뜀")
            return
        
        self._cycle_executing = True
        
        try:
            self._market_scan_count += 1
            
            # 시장 상황 확인 및 모니터링 주기 조정
            self.adjust_monitoring_frequency()
            
            # 테스트 모드 설정 (config에서 로드)
            test_mode = self.strategy_config.get('test_mode', True)
            
            if not test_mode:
                # 실제 운영 모드: 시장시간 체크
                if not self.is_market_open():
                    if self._market_scan_count % 60 == 0:  # 10분마다 로그
                        logger.info("시장 마감 - 대기 중...")
                    return
                
                # 거래 시간이 아니면 모니터링만
                if not self.is_trading_time():
                    market_phase = self.get_market_phase()
                    if market_phase == 'lunch':
                        if self._market_scan_count % 30 == 0:  # 5분마다 로그
                            logger.info("점심시간 - 모니터링만 실행")
                    elif market_phase == 'closing':
                        logger.info("장 마감 시간 - 보유 포지션 정리 중...")
                        self.process_sell_ready_stocks()  # 마감 시간에는 매도만
                    return
            else:
                # 테스트 모드: 시간 제한 없이 실행
                test_mode_log_interval = self.strategy_config.get('test_mode_log_interval_cycles', 100)
                if self._market_scan_count % test_mode_log_interval == 0:  # 설정 기반 테스트 모드 알림
                    logger.info("🧪 테스트 모드 실행 중 - 시장시간 무관하게 매수/매도 분석 진행")
            
            # 🔥 설정 기반 성능 로깅 주기 (정확한 시간 간격 계산)
            performance_log_seconds = self.strategy_config.get('performance_log_interval_minutes', 5) * 60
            performance_check_interval = max(1, round(performance_log_seconds / self.current_monitoring_interval))
            if self._market_scan_count % performance_check_interval == 0:
                self._log_performance_metrics()
            
            # 매수 준비 종목 처리
            buy_result = self.process_buy_ready_stocks()
            
            # 매도 준비 종목 처리  
            sell_result = self.process_sell_ready_stocks()
            
            # 🆕 장중 추가 종목 스캔
            self._check_and_run_intraday_scan()
            
            # 🔥 백그라운드 장중 스캔 결과 처리 (큐 기반 스레드 안전)
            self._process_background_scan_results()
            
            # 🔥 대기 중인 웹소켓 구독 처리 (메인 스레드에서 안전하게 처리)
            self.sub_manager.process_pending()
            
            # 🔥 설정 기반 정체된 주문 타임아웃 체크 (정확한 시간 간격 계산)
            stuck_order_check_seconds = self.strategy_config.get('stuck_order_check_interval_seconds', 30)
            stuck_order_check_interval = max(1, round(stuck_order_check_seconds / self.current_monitoring_interval))
            if self._market_scan_count % stuck_order_check_interval == 0:
                self._check_stuck_orders()
            
            # 🔥 설정 기반 주기적 상태 리포트 (정확한 시간 간격 계산)
            status_report_seconds = self.strategy_config.get('status_report_interval_minutes', 1) * 60
            status_report_interval = max(1, round(status_report_seconds / self.current_monitoring_interval))
            if self._market_scan_count % status_report_interval == 0:
                self._log_status_report(buy_result, sell_result)
            
            # 🔥 주기적 메모리 정리 (1시간마다)
            memory_cleanup_seconds = 3600
            memory_cleanup_interval = max(1, round(memory_cleanup_seconds / self.current_monitoring_interval))
            if self._market_scan_count % memory_cleanup_interval == 0:
                self._cleanup_expired_data()
                
            # 🔥 16:00 보고서 자동 출력
            self._check_and_log_daily_report()
            
        except Exception as e:
            logger.error(f"모니터링 사이클 오류: {e}")
        finally:
            # 🔥 반드시 실행 플래그 해제 (예외 발생시에도)
            self._cycle_executing = False
    
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
                       f"스캔횟수: {self._market_scan_count}, "
                       f"매수신호: {self._buy_signals_detected}, "
                       f"매도신호: {self._sell_signals_detected}, "
                       f"주문실행: {self._buy_orders_executed + self._sell_orders_executed}, "
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
        """상태 리포트 출력 – PerformanceLogger 로 위임"""
        self.performance_logger.log_status_report(buy_result, sell_result)
    
    def _get_websocket_status_summary(self) -> str:
        """웹소켓 상태 요약 문자열 반환"""
        try:
            websocket_manager = getattr(self.stock_manager, 'websocket_manager', None)
            if not websocket_manager:
                return "미사용"
            
            # 웹소켓 연결 상태
            is_connected = websocket_manager.is_connected
            is_healthy = websocket_manager.is_websocket_healthy()
            
            # 구독 정보
            subscribed_count = len(websocket_manager.get_subscribed_stocks())
            
            # 메시지 통계
            message_stats = websocket_manager.message_handler.stats
            total_messages = message_stats.get('messages_received', 0)
            last_message_time = message_stats.get('last_message_time')
            
            # 마지막 메시지 수신 시간 계산
            if last_message_time:
                time_since_last = (now_kst() - last_message_time).total_seconds()
                if time_since_last < 60:
                    last_msg_info = f"{time_since_last:.0f}초전"
                else:
                    last_msg_info = f"{time_since_last/60:.1f}분전"
            else:
                last_msg_info = "없음"
            
            # 연결 상태 아이콘
            status_icon = "🟢" if is_connected and is_healthy else "🔴" if is_connected else "⚪"
            
            return f"{status_icon}({subscribed_count}개구독/총{total_messages}건/최근{last_msg_info})"
            
        except Exception as e:
            logger.debug(f"웹소켓 상태 요약 오류: {e}")
            return "오류"
    
    
    def _cleanup_expired_data(self):
        """만료된 데이터 정리 (메모리 누수 방지)"""
        try:
            cleanup_count = 0
            
            # 1. 알림 기록 정리
            if self.alert_sent:
                self.alert_sent.clear()
                cleanup_count += 1
                logger.debug("알림 기록 정리 완료")
            
            # 2. SubscriptionManager cleanup
            cleanup_count += self.sub_manager.cleanup()
            
            # 3. 완료된 백그라운드 스레드 정리 (ScanWorker 사용)
            if self.scan_worker._scan_thread and not self.scan_worker._scan_thread.is_alive():
                self.scan_worker._scan_thread = None
                cleanup_count += 1
            
            if cleanup_count > 0:
                logger.info(f"🧹 메모리 정리 완료: {cleanup_count}개 항목 정리")
                
        except Exception as e:
            logger.error(f"메모리 정리 오류: {e}")
    
    def _check_and_log_daily_report(self):
        """PerformanceLogger 로 위임"""
        self.performance_logger.check_and_log_daily_report()
    
    def stop_monitoring(self):
        """모니터링 중지"""
        self._is_monitoring.clear()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        # 최종 성능 지표 출력
        self._log_final_performance()
        
        logger.info("⏹️ 실시간 모니터링 중지")
    
    def _log_final_performance(self):
        """PerformanceLogger 로 위임"""
        self.performance_logger.log_final_performance()
    
    def get_monitoring_status(self) -> Dict:
        """모니터링 상태 정보 반환 (웹소켓 기반 최적화)"""
        # OrderRecoveryManager 통계 포함
        recovery_stats = self.order_recovery_manager.get_recovery_statistics()
        
        return {
            'is_monitoring': self._is_monitoring.is_set(),
            'is_market_open': self.is_market_open(),
            'is_trading_time': self.is_trading_time(),
            'market_phase': self.get_market_phase(),
            'monitoring_interval': self.current_monitoring_interval,
            'market_scan_count': self._market_scan_count,
            'buy_signals_detected': self._buy_signals_detected,
            'sell_signals_detected': self._sell_signals_detected,
            'orders_executed': self._buy_orders_executed + self._sell_orders_executed,
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
        return (f"RealTimeMonitor(모니터링: {self._is_monitoring.is_set()}, "
                f"주기: {self.current_monitoring_interval}초, "
                f"스캔횟수: {self._market_scan_count}, "
                f"신호감지: 매수{self._buy_signals_detected}/매도{self._sell_signals_detected}, "
                f"웹소켓종목: {len(self.stock_manager.realtime_data)}개)")
    
    def get_sell_condition_analysis(self) -> Dict:
        """매도 조건 분석 성과 조회 (TradingConditionAnalyzer 위임)
        
        Returns:
            매도 조건별 성과 분석 딕셔너리
        """
        # TradingConditionAnalyzer에 위임
        return self.condition_analyzer.get_sell_condition_analysis()
    
    # -------------------------------
    # 🆕 16:00 일일 리포트 자동 기록
    # -------------------------------
    def _check_and_run_intraday_scan(self):
        """장중 스캔 실행 여부 판단 → ScanWorker 위임"""
        self.scan_worker.check_and_run_scan()

    def _process_background_scan_results(self):
        """백그라운드 스캔 결과 처리 위임"""
        self.scan_worker.process_background_results()

    # ------------------------------------------------------------------
    # IntradayStock 추가 (ScanWorker에서 호출)
    # ------------------------------------------------------------------
    def _add_intraday_stock_safely(self, stock_code: str, stock_name: Optional[str], score: float, reasons: str) -> bool:  # type: ignore[override]
        """StockManager.add_intraday_stock 래퍼. 웹소켓 구독 대기열 관리 유지"""
        try:
            if not stock_name:
                from utils.stock_data_loader import get_stock_data_loader
                stock_name = get_stock_data_loader().get_stock_name(stock_code)

            # 기본 시장 데이터 없이 바로 추가 (세부 데이터는 StockManager 내부에서 보완)
            success = self.stock_manager.add_intraday_stock(
                stock_code=stock_code,
                stock_name=str(stock_name),
                current_price=0.0,
                selection_score=score,
                reasons=reasons,
            )

            if success:
                # SubscriptionManager 에 구독 요청 등록
                self.sub_manager.add_pending(stock_code)
            return success
        except Exception as e:
            logger.error(f"_add_intraday_stock_safely 오류 {stock_code}: {e}")
            return False

    # ----------------------------------------------
    # New wrapper – delegates to MonitorCore
    # ----------------------------------------------
    def monitor_cycle(self):
        """MonitorCore.run_cycle 에 위임 (호환용)"""
        return self.core.run_cycle()

 