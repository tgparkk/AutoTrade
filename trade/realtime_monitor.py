"""
장시간 실시간 모니터링을 담당하는 RealTimeMonitor 클래스 (장시간 최적화 버전)
"""

import time
import asyncio
import threading
from typing import Dict, List, Optional, Set
from datetime import datetime, time as dt_time
from collections import defaultdict, deque
from models.position import Position, PositionStatus
from .stock_manager import StockManager
from .trade_executor import TradeExecutor
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader
from api.kis_market_api import get_inquire_price

logger = setup_logger(__name__)


class RealTimeMonitor:
    """장시간 실시간 모니터링을 담당하는 클래스 (최적화 버전)"""
    
    def __init__(self, stock_manager: StockManager, trade_executor: TradeExecutor):
        """RealTimeMonitor 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            trade_executor: 매매 실행자 인스턴스
        """
        self.stock_manager = stock_manager
        self.trade_executor = trade_executor
        
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
        
        # 장시간 성능 최적화
        self.price_cache = {}  # 가격 캐시 (종목코드: 최신 가격)
        self.last_update_time = {}  # 마지막 업데이트 시간
        self.volume_history = defaultdict(lambda: deque(maxlen=10))  # 거래량 히스토리
        self.alert_sent = set()  # 중복 알림 방지
        
        # 장시간 통계
        self.market_scan_count = 0
        self.buy_signals_detected = 0
        self.sell_signals_detected = 0
        self.orders_executed = 0
        
        # 시장 시간 설정
        self.market_open_time = dt_time(9, 0)   # 09:00
        self.market_close_time = dt_time(15, 30)  # 15:30
        self.day_trading_exit_time = dt_time(15, 0)  # 15:00 (데이트레이딩 종료)
        self.pre_close_time = dt_time(14, 50)  # 14:50 (마감 10분 전)
        
        # 장시간 동적 조정
        self.market_volatility_threshold = 0.02  # 2% 이상 변동시 빠른 모니터링
        self.high_volume_threshold = 3.0  # 3배 이상 거래량 증가시 빠른 모니터링
        
        logger.info("RealTimeMonitor 초기화 완료 (장시간 최적화 버전)")
    
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
        """고변동성 시장 감지
        
        Returns:
            고변동성 여부
        """
        try:
            # 보유 종목들의 변동률 확인
            positions = self.stock_manager.get_all_positions()
            high_volatility_count = 0
            
            for position in positions:
                if position.status in [PositionStatus.BOUGHT, PositionStatus.WATCHING]:
                    current_price = self.price_cache.get(position.stock_code, position.close_price)
                    price_change_rate = abs((current_price - position.open_price) / position.open_price)
                    
                    if price_change_rate >= self.market_volatility_threshold:
                        high_volatility_count += 1
            
            # 30% 이상의 종목이 고변동성이면 전체적으로 고변동성 시장
            return high_volatility_count >= len(positions) * 0.3
            
        except Exception as e:
            logger.error(f"고변동성 감지 오류: {e}")
            return False
    
    def fetch_realtime_data(self, stock_code: str) -> Optional[Dict]:
        """실시간 데이터 조회 (장시간 최적화)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            실시간 데이터 또는 None
        """
        try:
            # 캐시된 데이터 확인 (3초 이내면 캐시 사용)
            now = time.time()
            if (stock_code in self.last_update_time and 
                now - self.last_update_time[stock_code] < 3):
                return self._get_cached_data(stock_code)
            
            # 실제 API 호출 (TODO: KIS API 연동)
            realtime_data = self._fetch_from_api(stock_code)
            
            if realtime_data:
                # 캐시 업데이트
                self.price_cache[stock_code] = realtime_data['current_price']
                self.last_update_time[stock_code] = now
                
                # 거래량 히스토리 업데이트
                self.volume_history[stock_code].append(realtime_data['volume'])
                
                # 거래량 급증 감지
                volume_spike_ratio = self._calculate_volume_spike(stock_code)
                realtime_data['volume_spike_ratio'] = volume_spike_ratio
            
            return realtime_data
            
        except Exception as e:
            logger.error(f"실시간 데이터 조회 실패 {stock_code}: {e}")
            return None
    
    def _get_cached_data(self, stock_code: str) -> Optional[Dict]:
        """캐시된 데이터 반환"""
        if stock_code in self.price_cache:
            return {
                'stock_code': stock_code,
                'current_price': self.price_cache[stock_code],
                'is_cached': True,
                'cache_time': self.last_update_time.get(stock_code, 0)
            }
        return None
    
    def _fetch_from_api(self, stock_code: str) -> Optional[Dict]:
        """KIS API에서 실시간 데이터 조회"""
        try:
            # KIS API를 통한 현재가 조회
            price_data = get_inquire_price(div_code="J", itm_no=stock_code)
            
            if price_data is None or price_data.empty:
                logger.warning(f"KIS API 현재가 조회 실패: {stock_code}")
                return None
            
            # DataFrame에서 첫 번째 행 데이터 추출
            data = price_data.iloc[0]
            
            # 필요한 데이터 필드 추출 및 변환
            current_price = float(data.get('stck_prpr', 0))        # 주식 현재가
            open_price = float(data.get('stck_oprc', 0))           # 시가
            high_price = float(data.get('stck_hgpr', 0))           # 고가
            low_price = float(data.get('stck_lwpr', 0))            # 저가
            volume = int(data.get('acml_vol', 0))                  # 누적 거래량
            trading_volume = int(data.get('acml_tr_pbmn', 0))      # 누적 거래대금
            
            # 전일 종가 대비 등락률 계산
            prev_close_price = float(data.get('stck_sdpr', current_price))  # 전일 종가
            price_change_rate = 0.0
            if prev_close_price > 0:
                price_change_rate = (current_price - prev_close_price) / prev_close_price
            
            # 거래량 급증 비율 계산
            volume_spike_ratio = self._calculate_volume_spike(stock_code)
            
            return {
                'stock_code': stock_code,
                'current_price': current_price,
                'open_price': open_price,
                'high_price': high_price,
                'low_price': low_price,
                'volume': volume,
                'trading_volume': trading_volume,
                'price_change_rate': price_change_rate,
                'volume_spike_ratio': volume_spike_ratio,
                'timestamp': now_kst(),
                'raw_data': data.to_dict()  # 원본 데이터 보관
            }
            
        except Exception as e:
            logger.error(f"KIS API 실시간 데이터 조회 오류 {stock_code}: {e}")
            return None
    
    def _calculate_volume_spike(self, stock_code: str) -> float:
        """거래량 급증 비율 계산"""
        volumes = list(self.volume_history[stock_code])
        if len(volumes) < 2:
            return 1.0
        
        recent_avg = sum(volumes[-3:]) / len(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
        previous_avg = sum(volumes[:-3]) / len(volumes[:-3]) if len(volumes) > 3 else volumes[0]
        
        return recent_avg / previous_avg if previous_avg > 0 else 1.0
    
    def analyze_buy_conditions(self, position: Position, realtime_data: Dict) -> bool:
        """매수 조건 분석 (장시간 최적화)
        
        Args:
            position: 포지션 정보
            realtime_data: 실시간 데이터
            
        Returns:
            매수 조건 충족 여부
        """
        try:
            # 기본 조건 체크
            price_change_rate = realtime_data.get('price_change_rate', 0)
            volume_spike_ratio = realtime_data.get('volume_spike_ratio', 1.0)
            
            # 시장 단계별 조건 조정
            market_phase = self.get_market_phase()
            
            # 장 초반에는 더 엄격한 조건
            if market_phase == 'opening':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * 1.5
                price_threshold = 0.015  # 1.5%
            # 마감 전에는 보수적 접근
            elif market_phase == 'pre_close':
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0) * 2.0
                price_threshold = 0.02   # 2%
            else:
                volume_threshold = self.strategy_config.get('volume_increase_threshold', 2.0)
                price_threshold = 0.01   # 1%
            
            # 거래량 급증 조건
            volume_condition = volume_spike_ratio >= volume_threshold
            
            # 가격 상승 조건
            price_condition = price_change_rate >= price_threshold
            
            # 최소 거래량 조건
            min_volume = self.strategy_config.get('volume_min_threshold', 100000)
            volume_min_condition = realtime_data.get('volume', 0) >= min_volume
            
            # 패턴 점수 조건 (시장 단계별 조정)
            min_pattern_score = 70.0 if market_phase != 'opening' else 75.0
            pattern_condition = position.total_pattern_score >= min_pattern_score
            
            # 중복 신호 방지
            signal_key = f"{position.stock_code}_buy"
            duplicate_prevention = signal_key not in self.alert_sent
            
            buy_signal = (volume_condition and price_condition and 
                         volume_min_condition and pattern_condition and duplicate_prevention)
            
            if buy_signal:
                self.alert_sent.add(signal_key)
                self.buy_signals_detected += 1
                logger.info(f"🚀 {position.stock_code} 매수 신호 ({market_phase}): "
                           f"거래량({volume_spike_ratio:.1f}배≥{volume_threshold:.1f}), "
                           f"상승률({price_change_rate:.2%}≥{price_threshold:.1%}), "
                           f"패턴점수({position.total_pattern_score:.1f}≥{min_pattern_score})")
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"매수 조건 분석 오류 {position.stock_code}: {e}")
            return False
    
    def analyze_sell_conditions(self, position: Position, realtime_data: Dict) -> Optional[str]:
        """매도 조건 분석 (장시간 최적화)
        
        Args:
            position: 포지션 정보
            realtime_data: 실시간 데이터
            
        Returns:
            매도 사유 또는 None
        """
        try:
            current_price = realtime_data.get('current_price', position.close_price)
            market_phase = self.get_market_phase()
            
            # 시장 단계별 매도 조건 조정
            if market_phase == 'pre_close':
                # 마감 전에는 보수적으로 매도
                if position.buy_price is not None:
                    unrealized_pnl_rate = (current_price - position.buy_price) / position.buy_price * 100
                    if unrealized_pnl_rate >= 0.5:  # 0.5% 이상 수익시 매도
                        return "pre_close_profit"
            elif market_phase == 'closing':
                # 마감 시간에는 무조건 매도
                return "market_close"
            
            # 기본 매도 조건들
            if position.should_stop_loss(current_price):
                return "stop_loss"
            
            if position.should_take_profit(current_price):
                return "take_profit"
            
            if position.is_holding_period_exceeded():
                return "holding_period"
            
            # 급락 감지 매도 (최근 5분간 3% 이상 하락)
            price_history = self._get_price_history(position.stock_code)
            if self._detect_rapid_decline(price_history, current_price):
                return "rapid_decline"
            
            return None
            
        except Exception as e:
            logger.error(f"매도 조건 분석 오류 {position.stock_code}: {e}")
            return None
    
    def _get_price_history(self, stock_code: str) -> List[float]:
        """가격 히스토리 조회 (임시 구현)"""
        # TODO: 실제 가격 히스토리 저장 및 조회 구현
        return [self.price_cache.get(stock_code, 75000)] * 5
    
    def _detect_rapid_decline(self, price_history: List[float], current_price: float) -> bool:
        """급락 감지"""
        if len(price_history) < 2:
            return False
        
        max_recent_price = max(price_history[-5:])
        decline_rate = (max_recent_price - current_price) / max_recent_price
        
        return decline_rate >= 0.03  # 3% 이상 급락
    
    def process_buy_ready_stocks(self) -> int:
        """매수 준비 상태 종목들 처리 (장시간 최적화)
        
        Returns:
            처리된 종목 수
        """
        processed_count = 0
        
        try:
            # 선정된 종목들 중 매수 준비 상태인 것들 조회
            ready_stocks = self.stock_manager.get_stocks_by_status(PositionStatus.WATCHING)
            
            for position in ready_stocks:
                try:
                    # 실시간 데이터 조회
                    realtime_data = self.fetch_realtime_data(position.stock_code)
                    
                    if not realtime_data:
                        continue
                    
                    # 매수 조건 확인
                    if self.analyze_buy_conditions(position, realtime_data):
                        # 매수량 계산
                        buy_quantity = self.calculate_buy_quantity(position)
                        
                        if buy_quantity > 0:
                            # 매수 주문 실행
                            current_positions = len(self.stock_manager.get_stocks_by_status(PositionStatus.BOUGHT))
                            success = self.trade_executor.execute_buy_order(
                                position=position,
                                price=realtime_data['current_price'],
                                quantity=buy_quantity,
                                current_positions_count=current_positions
                            )
                            
                            if success:
                                # 매수 체결 확인 (실제로는 API 응답 확인)
                                self.trade_executor.confirm_buy_execution(position)
                                processed_count += 1
                                self.orders_executed += 1
                                
                                logger.info(f"✅ 매수 실행: {position.stock_code} {buy_quantity}주 @{realtime_data['current_price']:,}원")
                        
                except Exception as e:
                    logger.error(f"매수 처리 오류 {position.stock_code}: {e}")
                    continue
            
            return processed_count
            
        except Exception as e:
            logger.error(f"매수 준비 종목 처리 오류: {e}")
            return 0
    
    def process_sell_ready_stocks(self) -> int:
        """매도 준비 상태 종목들 처리 (장시간 최적화)
        
        Returns:
            처리된 종목 수
        """
        processed_count = 0
        
        try:
            # 보유 중인 종목들 조회
            holding_stocks = self.stock_manager.get_stocks_by_status(PositionStatus.BOUGHT)
            
            for position in holding_stocks:
                try:
                    # 실시간 데이터 조회
                    realtime_data = self.fetch_realtime_data(position.stock_code)
                    
                    if not realtime_data:
                        continue
                    
                    # 매도 조건 확인
                    sell_reason = self.analyze_sell_conditions(position, realtime_data)
                    
                    if sell_reason:
                        # 매도 주문 실행
                        success = self.trade_executor.execute_sell_order(
                            position=position,
                            price=realtime_data['current_price'],
                            reason=sell_reason
                        )
                        
                        if success:
                            # 매도 체결 확인 (실제로는 API 응답 확인)
                            self.trade_executor.confirm_sell_execution(position, realtime_data['current_price'])
                            processed_count += 1
                            self.orders_executed += 1
                            self.sell_signals_detected += 1
                            
                            # 중복 알림 방지 제거
                            signal_key = f"{position.stock_code}_buy"
                            self.alert_sent.discard(signal_key)
                            
                            logger.info(f"✅ 매도 실행: {position.stock_code} @{realtime_data['current_price']:,}원 (사유: {sell_reason})")
                        
                except Exception as e:
                    logger.error(f"매도 처리 오류 {position.stock_code}: {e}")
                    continue
            
            return processed_count
            
        except Exception as e:
            logger.error(f"매도 준비 종목 처리 오류: {e}")
            return 0
    
    def calculate_buy_quantity(self, position: Position) -> int:
        """매수량 계산 (장시간 최적화)
        
        Args:
            position: 포지션 정보
            
        Returns:
            매수량
        """
        try:
            # 시장 단계별 투자 금액 조정
            market_phase = self.get_market_phase()
            base_amount = 1000000  # 기본 100만원
            
            if market_phase == 'opening':
                # 장 초반에는 50% 투자
                investment_amount = base_amount * 0.5
            elif market_phase == 'pre_close':
                # 마감 전에는 30% 투자
                investment_amount = base_amount * 0.3
            else:
                # 일반 시간대는 100% 투자
                investment_amount = base_amount
            
            # 포지션 크기에 따른 추가 조정
            current_positions = len(self.stock_manager.get_stocks_by_status(PositionStatus.BOUGHT))
            max_positions = self.risk_config.get('max_positions', 5)
            
            if current_positions >= max_positions * 0.8:  # 80% 이상 차면 보수적
                investment_amount *= 0.7
            
            quantity = int(investment_amount / position.close_price)
            return max(quantity, 1)  # 최소 1주
            
        except Exception as e:
            logger.error(f"매수량 계산 오류 {position.stock_code}: {e}")
            return 0
    
    def monitor_cycle(self):
        """모니터링 사이클 실행 (장시간 최적화)"""
        try:
            self.market_scan_count += 1
            
            # 시장 상황 확인 및 모니터링 주기 조정
            self.adjust_monitoring_frequency()
            
            # 시장 열려있지 않으면 대기
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
            
            # 성능 로깅 (5분마다)
            if self.market_scan_count % (300 // self.current_monitoring_interval) == 0:
                self._log_performance_metrics()
            
            # 매수 준비 종목 처리
            buy_processed = self.process_buy_ready_stocks()
            
            # 매도 준비 종목 처리  
            sell_processed = self.process_sell_ready_stocks()
            
            # 주기적 상태 리포트 (1분마다)
            if self.market_scan_count % (60 // self.current_monitoring_interval) == 0:
                self._log_status_report(buy_processed, sell_processed)
                
        except Exception as e:
            logger.error(f"모니터링 사이클 오류: {e}")
    
    def _log_performance_metrics(self):
        """성능 지표 로깅"""
        try:
            market_phase = self.get_market_phase()
            positions = self.stock_manager.get_all_positions()
            
            # 포지션 상태별 집계
            status_counts = defaultdict(int)
            total_unrealized_pnl = 0
            
            for pos in positions:
                status_counts[pos.status.value] += 1
                if pos.status == PositionStatus.BOUGHT:
                    current_price = self.price_cache.get(pos.stock_code, pos.close_price)
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
    
    def _log_status_report(self, buy_processed: int, sell_processed: int):
        """상태 리포트 로깅"""
        try:
            current_time = now_kst().strftime("%H:%M:%S")
            market_phase = self.get_market_phase()
            
            logger.info(f"🕐 {current_time} ({market_phase}) - "
                       f"매수처리: {buy_processed}건, 매도처리: {sell_processed}건, "
                       f"모니터링주기: {self.current_monitoring_interval}초")
                       
        except Exception as e:
            logger.error(f"상태 리포트 로깅 오류: {e}")
    
    def start_monitoring(self):
        """모니터링 시작 (장시간 최적화)"""
        if self.is_monitoring:
            logger.warning("이미 모니터링이 실행 중입니다")
            return
        
        self.is_monitoring = True
        
        # 통계 초기화
        self.market_scan_count = 0
        self.buy_signals_detected = 0
        self.sell_signals_detected = 0
        self.orders_executed = 0
        self.alert_sent.clear()
        
        # 모니터링 스레드 시작
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        
        logger.info("🚀 실시간 모니터링 시작 (장시간 최적화 모드)")
    
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
    
    def _monitoring_loop(self):
        """모니터링 루프 (장시간 최적화)"""
        logger.info("모니터링 루프 시작")
        
        while self.is_monitoring:
            try:
                loop_start_time = time.time()
                
                # 모니터링 사이클 실행
                self.monitor_cycle()
                
                # 실행 시간 측정
                loop_duration = time.time() - loop_start_time
                
                # 동적 대기 시간 계산
                sleep_time = max(0, self.current_monitoring_interval - loop_duration)
                
                # 너무 오래 걸리면 경고
                if loop_duration > self.current_monitoring_interval:
                    logger.warning(f"모니터링 사이클이 지연됨: {loop_duration:.2f}초 > {self.current_monitoring_interval}초")
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    
            except Exception as e:
                logger.error(f"모니터링 루프 오류: {e}")
                time.sleep(self.current_monitoring_interval)
        
        logger.info("모니터링 루프 종료")
    
    def get_monitoring_status(self) -> Dict:
        """모니터링 상태 정보 반환 (장시간 최적화)"""
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
            'cached_stocks': len(self.price_cache),
            'alerts_sent': len(self.alert_sent)
        }
    
    def force_sell_all_positions(self) -> int:
        """모든 포지션 강제 매도 (장 마감 전)
        
        Returns:
            매도 처리된 포지션 수
        """
        logger.info("🚨 모든 포지션 강제 매도 시작")
        
        sold_count = 0
        holding_stocks = self.stock_manager.get_stocks_by_status(PositionStatus.BOUGHT)
        
        for position in holding_stocks:
            try:
                realtime_data = self.fetch_realtime_data(position.stock_code)
                current_price = realtime_data['current_price'] if realtime_data else position.close_price
                
                success = self.trade_executor.execute_sell_order(
                    position=position,
                    price=current_price,
                    reason="force_close"
                )
                
                if success:
                    self.trade_executor.confirm_sell_execution(position, current_price)
                    sold_count += 1
                    logger.info(f"강제 매도: {position.stock_code}")
                    
            except Exception as e:
                logger.error(f"강제 매도 실패 {position.stock_code}: {e}")
        
        logger.info(f"강제 매도 완료: {sold_count}개 포지션")
        return sold_count
    
    def __str__(self) -> str:
        """문자열 표현"""
        return (f"RealTimeMonitor(모니터링: {self.is_monitoring}, "
                f"주기: {self.current_monitoring_interval}초, "
                f"스캔횟수: {self.market_scan_count}, "
                f"신호감지: 매수{self.buy_signals_detected}/매도{self.sell_signals_detected})") 