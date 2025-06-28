"""
장시간 실시간 모니터링을 담당하는 RealTimeMonitor 클래스 (웹소켓 기반 최적화 버전)
"""

import time
import asyncio
import threading
from typing import Dict, List, Optional, Set, TYPE_CHECKING
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
from trade.realtime.market_clock import MarketClock
from trade.realtime.realtime_provider import RealtimeProvider
from trade.realtime.stats_tracker import StatsTracker
from trade.realtime.buy_runner import BuyRunner
from trade.realtime.sell_runner import SellRunner

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
        self.daytrading_config = self.config_loader.load_daytrading_config()  # 🆕 데이트레이딩 설정 추가
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
        self.duplicate_buy_cooldown = self.daytrading_config.get('duplicate_buy_cooldown_seconds', 10)
        
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

        # 🆕 모듈화 컴포넌트 실제 초기화 (strategy_config 로딩 이후)
        self.clock = MarketClock(self.strategy_config)
        self.rt_provider = RealtimeProvider(self.stock_manager)
        self.stats_tracker = StatsTracker()

        # 유지보수 및 변동성 모듈 초기화
        from trade.realtime.maintenance import MaintenanceManager
        from trade.realtime.volatility_monitor import VolatilityMonitor

        self.maintenance = MaintenanceManager(self)
        self.vol_monitor = VolatilityMonitor(
            stock_manager=self.stock_manager,
            volatility_threshold=self.market_volatility_threshold,
            high_volatility_position_ratio=self.high_volatility_position_ratio,
        )

        # 🆕 BuyRunner 초기화
        self.buy_runner = BuyRunner(self)
        self.sell_runner = SellRunner(self)
    
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
        return self.stats_tracker.market_scan_count
    
    @market_scan_count.setter
    def market_scan_count(self, value: int):
        """시장 스캔 횟수 설정"""
        # 강제 설정 필요시 StatsTracker 동기화
        diff = value - self.stats_tracker.market_scan_count
        if diff > 0:
            self.stats_tracker.inc_market_scan(diff)
    
    @property
    def buy_signals_detected(self) -> int:
        """매수 신호 탐지 횟수"""
        return self.stats_tracker.buy_signals_detected
    
    @buy_signals_detected.setter
    def buy_signals_detected(self, value: int):
        """매수 신호 탐지 횟수 설정"""
        diff = value - self.stats_tracker.buy_signals_detected
        if diff > 0:
            self.stats_tracker.inc_buy_signal(diff)
    
    @property
    def sell_signals_detected(self) -> int:
        """매도 신호 탐지 횟수"""
        return self.stats_tracker.sell_signals_detected
    
    @sell_signals_detected.setter
    def sell_signals_detected(self, value: int):
        """매도 신호 탐지 횟수 설정"""
        diff = value - self.stats_tracker.sell_signals_detected
        if diff > 0:
            self.stats_tracker.inc_sell_signal(diff)
    
    @property
    def orders_executed(self) -> int:
        """총 주문 체결 수 (매수+매도)"""
        return self.stats_tracker.orders_executed

    @orders_executed.setter
    def orders_executed(self, value: int):
        """총 주문 체결 수 초기화용 (매수 실행 수만 설정, 매도는 0으로 리셋)"""
        # 재설정: StatsTracker 초기화 후 buy 주문 카운터 지정
        self.stats_tracker = StatsTracker()
        if value > 0:
            self.stats_tracker.inc_buy_order(value)
    
    @property
    def buy_orders_executed(self) -> int:
        """매수 주문 체결 수"""
        return self.stats_tracker.buy_orders_executed

    @buy_orders_executed.setter
    def buy_orders_executed(self, value: int):
        diff = value - self.stats_tracker.buy_orders_executed
        if diff > 0:
            self.stats_tracker.inc_buy_order(diff)

    @property
    def sell_orders_executed(self) -> int:
        """매도 주문 체결 수"""
        return self.stats_tracker.sell_orders_executed

    @sell_orders_executed.setter
    def sell_orders_executed(self, value: int):
        diff = value - self.stats_tracker.sell_orders_executed
        if diff > 0:
            self.stats_tracker.inc_sell_order(diff)
    
    def is_market_open(self) -> bool:
        """시장 개장 여부 확인
        
        Returns:
            시장 개장 여부
        """
        # MarketClock 로 위임
        return self.clock.is_market_open()
    
    def is_trading_time(self) -> bool:
        """거래 가능 시간 확인 (데이트레이딩 시간 고려)
        
        Returns:
            거래 가능 여부
        """
        # MarketClock 로 위임
        return self.clock.is_trading_time()
    
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
    
    # ------------------------------------------------------------------
    # Delegated helpers
    # ------------------------------------------------------------------

    def _detect_high_volatility(self) -> bool:
        """VolatilityMonitor 로 위임"""
        return self.vol_monitor.is_high_volatility()
    
    def get_realtime_data(self, stock_code: str) -> Optional[Dict]:
        """웹소켓 실시간 데이터 조회 (StockManager 기반)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            실시간 데이터 또는 None
        """
        # RealtimeProvider 로 위임
        return self.rt_provider.get(stock_code)
    
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
        """BuyRunner.run 위임"""
        return self.buy_runner.run()
    
    def process_sell_ready_stocks(self) -> Dict[str, int]:
        """SellRunner.run 위임"""
        return self.sell_runner.run()
    
    def calculate_buy_quantity(self, stock: Stock) -> int:
        """매수량 계산 (TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            
        Returns:
            매수량
        """
        # TradingConditionAnalyzer에 위임
        return self.condition_analyzer.calculate_buy_quantity(stock)
    
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
                       f"스캔횟수: {self.stats_tracker.market_scan_count}, "
                       f"매수신호: {self.stats_tracker.buy_signals_detected}, "
                       f"매도신호: {self.stats_tracker.sell_signals_detected}, "
                       f"주문실행: {self.stats_tracker.orders_executed}, "
                       f"미실현손익: {total_unrealized_pnl:+,.0f}원")
            
            logger.info(f"📈 포지션 현황: " + 
                       ", ".join([f"{status}: {count}개" for status, count in status_counts.items()]))
                       
        except Exception as e:
            logger.error(f"성능 지표 로깅 오류: {e}")
    
    def _check_stuck_orders(self):
        """MaintenanceManager 로 위임"""
        self.maintenance.check_stuck_orders()
    
    def _log_status_report(self, buy_result: Dict[str, int], sell_result: Dict[str, int]):
        """상태 리포트 출력 – PerformanceLogger 로 위임"""
        self.performance_logger.log_status_report(buy_result, sell_result)
    
    def _get_websocket_status_summary(self) -> str:
        """웹소켓 상태 요약 문자열 반환"""
        try:
            from typing import Optional, TYPE_CHECKING
            if TYPE_CHECKING:
                from websocket.kis_websocket_manager import KISWebSocketManager

            websocket_manager: Optional["KISWebSocketManager"] = getattr(
                self.stock_manager, 'websocket_manager', None
            )
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
        """MaintenanceManager 로 위임"""
        self.maintenance.cleanup()
    
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
            'market_scan_count': self.stats_tracker.market_scan_count,
            'buy_signals_detected': self.stats_tracker.buy_signals_detected,
            'sell_signals_detected': self.stats_tracker.sell_signals_detected,
            'orders_executed': self.stats_tracker.orders_executed,
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
                f"스캔횟수: {self.stats_tracker.market_scan_count}, "
                f"신호감지: 매수{self.stats_tracker.buy_signals_detected}/매도{self.stats_tracker.sell_signals_detected}, "
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

 