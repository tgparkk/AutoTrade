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
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.market_config = self.config_loader.load_market_schedule_config()
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 🔥 설정 기반 모니터링 주기 (하드코딩 제거)
        self.fast_monitoring_interval = self.strategy_config.get('fast_monitoring_interval', 3)
        self.normal_monitoring_interval = self.strategy_config.get('normal_monitoring_interval', 10)
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
        
        # 🔥 설정 기반 장중 추가 종목 스캔 (하드코딩 제거)
        self.last_intraday_scan_time = None
        self.intraday_scan_interval = self.strategy_config.get('intraday_scan_interval_minutes', 30) * 60  # 분을 초로 변환
        self.max_additional_stocks = self.strategy_config.get('max_additional_stocks', 10)
        self.websocket_slots_minimum_reserve = self.strategy_config.get('websocket_slots_minimum_reserve', 10)
        
        logger.info("RealTimeMonitor 초기화 완료 (웹소켓 기반 최적화 버전 + 장중추가스캔)")
    
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
        """매수 조건 분석 (TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            
        Returns:
            매수 조건 충족 여부
        """
        # 중복 신호 방지
        signal_key = f"{stock.stock_code}_buy"
        duplicate_prevention = signal_key not in self.alert_sent
        
        if not duplicate_prevention:
            return False
        
        # TradingConditionAnalyzer에 위임
        market_phase = self.get_market_phase()
        buy_signal = self.condition_analyzer.analyze_buy_conditions(stock, realtime_data, market_phase)
        
        if buy_signal:
            self.alert_sent.add(signal_key)
            self.buy_signals_detected += 1
        
        return buy_signal
    
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
        """매수량 계산 (TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            
        Returns:
            매수량
        """
        # TradingConditionAnalyzer에 위임
        return self.condition_analyzer.calculate_buy_quantity(stock)
    
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
                test_mode_log_interval = self.strategy_config.get('test_mode_log_interval_cycles', 100)
                if self.market_scan_count % test_mode_log_interval == 0:  # 설정 기반 테스트 모드 알림
                    logger.debug("테스트 모드 - 시장시간 무관하게 실행 중")
            
            # 🔥 설정 기반 성능 로깅 주기
            performance_log_seconds = self.strategy_config.get('performance_log_interval_minutes', 5) * 60
            if self.market_scan_count % (performance_log_seconds // self.current_monitoring_interval) == 0:
                self._log_performance_metrics()
            
            # 매수 준비 종목 처리
            buy_result = self.process_buy_ready_stocks()
            
            # 매도 준비 종목 처리  
            sell_result = self.process_sell_ready_stocks()
            
            # 🆕 장중 추가 종목 스캔
            self._check_and_run_intraday_scan()
            
            # 🔥 설정 기반 정체된 주문 타임아웃 체크
            stuck_order_check_seconds = self.strategy_config.get('stuck_order_check_interval_seconds', 30)
            if self.market_scan_count % (stuck_order_check_seconds // self.current_monitoring_interval) == 0:
                self._check_stuck_orders()
            
            # 🔥 설정 기반 주기적 상태 리포트
            status_report_seconds = self.strategy_config.get('status_report_interval_minutes', 1) * 60
            if self.market_scan_count % (status_report_seconds // self.current_monitoring_interval) == 0:
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
    
    def _check_and_run_intraday_scan(self):
        """장중 추가 종목 스캔 시간 체크 및 실행"""
        try:
            current_time = now_kst()
            market_phase = self.get_market_phase()
            
            # 장중 시간대에만 실행 (점심시간, 마감시간 제외)
            if market_phase not in ['active']:
                return
            
            # 🔥 설정 기반 웹소켓 슬롯 여유 확인 (하드코딩 제거)
            websocket_max = self.strategy_config.get('websocket_max_connections', 41)
            connections_per_stock = self.strategy_config.get('websocket_connections_per_stock', 2)
            system_connections = self.strategy_config.get('websocket_system_connections', 1)
            
            current_websocket_count = len(self.stock_manager.get_all_positions()) * connections_per_stock + system_connections
            available_slots = websocket_max - current_websocket_count
            
            if available_slots < self.websocket_slots_minimum_reserve:
                logger.debug(f"웹소켓 슬롯 부족으로 장중 스캔 생략 (사용:{current_websocket_count}/{websocket_max}, 여유:{available_slots})")
                return
            
            # 30분 간격 체크
            should_scan = False
            if self.last_intraday_scan_time is None:
                # 첫 실행: 10:00 이후부터 시작
                if current_time.hour >= 10:
                    should_scan = True
            else:
                # 마지막 스캔으로부터 30분 경과 체크
                time_elapsed = (current_time - self.last_intraday_scan_time).total_seconds()
                if time_elapsed >= self.intraday_scan_interval:
                    should_scan = True
            
            if should_scan:
                logger.info(f"🔍 장중 추가 종목 스캔 실행 (웹소켓 여유:{available_slots}개)")
                
                # MarketScanner 인스턴스 생성 및 장중 스캔 실행
                from trade.market_scanner import MarketScanner
                market_scanner = MarketScanner(self.stock_manager)
                
                additional_stocks = market_scanner.intraday_scan_additional_stocks(
                    max_stocks=min(self.max_additional_stocks, available_slots // 2)
                )
                
                if additional_stocks:
                    logger.info(f"🎯 장중 추가 종목 후보 {len(additional_stocks)}개 발견:")
                    
                    # 실제 종목 추가 처리
                    added_count = 0
                    for i, (stock_code, score, reasons) in enumerate(additional_stocks, 1):
                        from utils.stock_data_loader import get_stock_data_loader
                        stock_loader = get_stock_data_loader()
                        stock_name = stock_loader.get_stock_name(stock_code)
                        
                        logger.info(f"  {i}. {stock_code}[{stock_name}] - 점수:{score:.1f} ({reasons})")
                        
                        # StockManager에 장중 종목 추가
                        try:
                            # 현재가 조회 (KIS API 사용)
                            from api.kis_market_api import get_inquire_price
                            price_data = get_inquire_price(div_code="J", itm_no=stock_code)
                            
                            if price_data is not None and not price_data.empty:
                                # 첫 번째 행에서 현재가 정보 추출
                                row = price_data.iloc[0]
                                current_price = float(row.get('stck_prpr', 0))  # 현재가
                                
                                if current_price > 0:
                                    # 추가 시장 데이터 준비
                                    market_data = {
                                        'volume': int(row.get('acml_vol', 0)),  # 누적거래량
                                        'high_price': float(row.get('stck_hgpr', current_price)),  # 고가
                                        'low_price': float(row.get('stck_lwpr', current_price)),   # 저가
                                        'open_price': float(row.get('stck_oprc', current_price)),  # 시가
                                        'yesterday_close': float(row.get('stck_sdpr', current_price)),  # 전일종가
                                        'price_change_rate': float(row.get('prdy_ctrt', 0.0)),  # 전일대비율
                                        'volume_spike_ratio': 1.0  # 기본값
                                    }
                                    
                                    # 종목명 안전 처리
                                    safe_stock_name = stock_name if stock_name else f"종목{stock_code}"
                                    
                                    # StockManager에 장중 종목 추가
                                    success = self.stock_manager.add_intraday_stock(
                                        stock_code=stock_code,
                                        stock_name=safe_stock_name,
                                        current_price=current_price,
                                        selection_score=score,
                                        reasons=reasons,
                                        market_data=market_data
                                    )
                                    
                                    if success:
                                        added_count += 1
                                        logger.info(f"✅ 장중 종목 추가 성공: {stock_code}[{safe_stock_name}] @{current_price:,}원")
                                        
                                        # 🔥 웹소켓 구독 추가 (실시간 모니터링 시작)
                                        # StockManager가 웹소켓 매니저를 가지고 있는지 확인
                                        websocket_manager = getattr(self.stock_manager, 'websocket_manager', None)
                                        if websocket_manager:
                                            try:
                                                # 호가 구독
                                                websocket_manager.subscribe_orderbook(stock_code)
                                                # 체결가 구독  
                                                websocket_manager.subscribe_price(stock_code)
                                                logger.info(f"📡 웹소켓 구독 추가: {stock_code} (호가+체결가)")
                                            except Exception as ws_e:
                                                logger.warning(f"웹소켓 구독 실패 {stock_code}: {ws_e}")
                                        else:
                                            logger.debug(f"웹소켓 매니저 없음 - 실시간 구독 생략: {stock_code}")
                                        
                                    else:
                                        logger.warning(f"❌ 장중 종목 추가 실패: {stock_code}[{safe_stock_name}]")
                                
                                else:
                                    logger.warning(f"⚠️ 유효하지 않은 현재가로 추가 생략: {stock_code}[{stock_name}] (가격: {current_price})")
                            
                            else:
                                logger.warning(f"⚠️ 현재가 조회 실패로 추가 생략: {stock_code}[{stock_name}]")
                                
                        except Exception as add_e:
                            logger.error(f"장중 종목 추가 처리 오류 {stock_code}: {add_e}")
                            continue
                    
                    # 추가 결과 요약
                    if added_count > 0:
                        logger.info(f"🎉 장중 종목 추가 완료: {added_count}/{len(additional_stocks)}개 성공")
                        
                        # 장중 추가 종목 요약 출력
                        intraday_summary = self.stock_manager.get_intraday_summary()
                        logger.info(f"📊 장중 추가 종목 현황: 총 {intraday_summary.get('total_count', 0)}개, "
                                   f"평균점수 {intraday_summary.get('average_score', 0):.1f}")
                    else:
                        logger.warning("❌ 장중 종목 추가 실패: 모든 후보 종목 추가 불가")
                else:
                    logger.info("📊 장중 추가 종목 스캔: 조건 만족 종목 없음")
                
                # 마지막 스캔 시간 업데이트
                self.last_intraday_scan_time = current_time
                
        except Exception as e:
            logger.error(f"장중 추가 종목 스캔 오류: {e}")
    
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
        """매도 조건 분석 성과 조회 (TradingConditionAnalyzer 위임)
        
        Returns:
            매도 조건별 성과 분석 딕셔너리
        """
        # TradingConditionAnalyzer에 위임
        return self.condition_analyzer.get_sell_condition_analysis()
    
 