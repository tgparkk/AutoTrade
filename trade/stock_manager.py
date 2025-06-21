#!/usr/bin/env python3
"""
종목 관리를 담당하는 StockManager 클래스

주요 기능:
- 선정된 종목들의 생명주기 관리
- 실시간 가격 업데이트 처리  
- 종목 상태 변경 관리
- 웹소켓 실시간 데이터 처리

성능 최적화:
- 읽기 전용 캐시로 락 경합 최소화
- 실시간 데이터는 별도 관리로 빠른 업데이트
- 통합 뷰 제공으로 사용 편의성 유지
"""

import threading
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from models.stock import Stock, StockStatus, ReferenceData, RealtimeData
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class StockManager:
    """종목 관리를 담당하는 클래스 (하이브리드 방식으로 성능 최적화)"""
    
    def __init__(self):
        """StockManager 초기화"""
        
        # === 1. 읽기 전용 데이터 (장 전 준비, 변경 빈도 낮음) ===
        self.reference_stocks: Dict[str, ReferenceData] = {}  # 기준 데이터 캐시
        self.stock_metadata: Dict[str, dict] = {}  # 종목 기본 정보 (코드, 이름 등)
        
        # === 2. 실시간 데이터 (웹소켓, 변경 빈도 높음) ===
        self.realtime_data: Dict[str, RealtimeData] = {}  # 실시간 가격/호가 데이터
        
        # === 3. 거래 상태 (중간 빈도) ===
        self.trading_status: Dict[str, StockStatus] = {}  # 종목별 거래 상태
        self.trade_info: Dict[str, dict] = {}  # 매수/매도 정보
        
        # === 4. 성능 최적화용 캐시 ===
        self._stock_cache: Dict[str, Stock] = {}  # 완전한 Stock 객체 캐시
        self._cache_timestamps: Dict[str, float] = {}  # 캐시 타임스탬프
        self._cache_ttl = 1.0  # 캐시 유효시간 1초
        
        # === 5. 락 전략 (세분화) ===
        self._ref_lock = threading.RLock()      # 참조 데이터용 (읽기 빈도 높음)
        self._realtime_lock = threading.RLock() # 실시간 데이터용 (쓰기 빈도 높음) 
        self._status_lock = threading.RLock()   # 상태 변경용 (중간 빈도)
        self._cache_lock = threading.RLock()    # 캐시용
        
        # === 6. 기본 설정 ===
        self.candidate_stocks: List[str] = []
        self.max_selected_stocks = 15
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        
        logger.info("StockManager 초기화 완료 (하이브리드 방식, 성능 최적화)")
    
    # === 종목 추가/제거 ===
    
    def add_selected_stock(self, stock_code: str, stock_name: str, 
                          open_price: float, high_price: float, 
                          low_price: float, close_price: float, 
                          volume: int, selection_score: float,
                          reference_data: Optional[dict] = None) -> bool:
        """선정된 종목 추가"""
        
        if len(self.reference_stocks) >= self.max_selected_stocks:
            logger.warning(f"최대 선정 종목 수 초과: {len(self.reference_stocks)}/{self.max_selected_stocks}")
            return False
        
        if stock_code in self.reference_stocks:
            logger.warning(f"이미 선정된 종목입니다: {stock_code}")
            return False
        
        try:
            # 1. 기본 메타데이터 저장
            with self._ref_lock:
                self.stock_metadata[stock_code] = {
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'created_at': now_kst(),
                    'max_holding_period': self.strategy_config.get('max_holding_days', 1)
                }
                
                # 2. 참조 데이터 생성 및 저장
                if reference_data:
                    ref_data = ReferenceData(
                        pattern_score=selection_score,
                        **reference_data
                    )
                else:
                    ref_data = ReferenceData(
                        pattern_score=selection_score,
                        yesterday_close=close_price,
                        yesterday_volume=volume,
                        yesterday_high=high_price,
                        yesterday_low=low_price
                    )
                
                self.reference_stocks[stock_code] = ref_data
            
            # 3. 실시간 데이터 초기화
            with self._realtime_lock:
                self.realtime_data[stock_code] = RealtimeData(
                    current_price=close_price,
                    today_volume=volume,
                    today_high=high_price,
                    today_low=low_price
                )
            
            # 4. 거래 상태 초기화
            with self._status_lock:
                self.trading_status[stock_code] = StockStatus.WATCHING
                self.trade_info[stock_code] = {
                    'buy_price': None,
                    'buy_quantity': None,
                    'buy_amount': None,
                    'target_price': None,
                    'stop_loss_price': None,
                    'buy_order_id': None,
                    'sell_order_id': None,
                    'order_time': None,
                    'execution_time': None,
                    'sell_order_time': None,
                    'sell_execution_time': None,
                    'sell_price': None,
                    'sell_reason': None,
                    'unrealized_pnl': None,
                    'unrealized_pnl_rate': None,
                    'realized_pnl': None,
                    'realized_pnl_rate': None,
                    'position_size_ratio': 0.0,
                    'detected_time': now_kst(),
                    'updated_at': now_kst()
                }
            
            # 5. 캐시 무효화
            self._invalidate_cache(stock_code)
            
            logger.info(f"선정 종목 추가: {stock_code}[{stock_name}] (점수: {selection_score:.2f})")
            return True
            
        except Exception as e:
            logger.error(f"종목 추가 오류 {stock_code}: {e}")
            return False
    
    def remove_selected_stock(self, stock_code: str) -> bool:
        """선정된 종목 제거"""
        try:
            stock_name = "Unknown"
            
            # 1. 메타데이터에서 이름 조회
            with self._ref_lock:
                if stock_code in self.stock_metadata:
                    stock_name = self.stock_metadata[stock_code].get('stock_name', 'Unknown')
                    del self.stock_metadata[stock_code]
                
                if stock_code in self.reference_stocks:
                    del self.reference_stocks[stock_code]
                else:
                    return False
            
            # 2. 실시간 데이터 제거
            with self._realtime_lock:
                self.realtime_data.pop(stock_code, None)
            
            # 3. 거래 상태 제거
            with self._status_lock:
                self.trading_status.pop(stock_code, None)
                self.trade_info.pop(stock_code, None)
            
            # 4. 캐시 제거
            with self._cache_lock:
                self._stock_cache.pop(stock_code, None)
                self._cache_timestamps.pop(stock_code, None)
            
            logger.info(f"선정 종목 제거: {stock_code}[{stock_name}]")
            return True
            
        except Exception as e:
            logger.error(f"종목 제거 오류 {stock_code}: {e}")
            return False
    
    # === 빠른 조회 메서드들 (캐시 활용) ===
    
    def get_selected_stock(self, stock_code: str) -> Optional[Stock]:
        """선정된 종목 조회 (캐시 활용으로 빠른 조회)"""
        try:
            # 1. 캐시 확인
            current_time = time.time()
            with self._cache_lock:
                if (stock_code in self._stock_cache and 
                    stock_code in self._cache_timestamps and
                    current_time - self._cache_timestamps[stock_code] < self._cache_ttl):
                    return self._stock_cache[stock_code]
            
            # 2. 캐시 미스 - 새로 생성
            stock = self._build_stock_object(stock_code)
            
            # 3. 캐시 업데이트
            if stock:
                with self._cache_lock:
                    self._stock_cache[stock_code] = stock
                    self._cache_timestamps[stock_code] = current_time
            
            return stock
            
        except Exception as e:
            logger.error(f"종목 조회 오류 {stock_code}: {e}")
            return None
    
    def _build_stock_object(self, stock_code: str) -> Optional[Stock]:
        """Stock 객체 생성 (각 데이터 소스에서 조합)"""
        try:
            # 1. 기본 정보 확인
            with self._ref_lock:
                if stock_code not in self.stock_metadata:
                    return None
                metadata = self.stock_metadata[stock_code].copy()
                ref_data = self.reference_stocks.get(stock_code)
            
            # 2. 실시간 데이터 조회
            with self._realtime_lock:
                realtime = self.realtime_data.get(stock_code, RealtimeData())
            
            # 3. 거래 정보 조회
            with self._status_lock:
                status = self.trading_status.get(stock_code, StockStatus.WATCHING)
                trade_info = self.trade_info.get(stock_code, {})
            
            # 4. Stock 객체 생성
            stock = Stock(
                stock_code=stock_code,
                stock_name=metadata.get('stock_name', ''),
                reference_data=ref_data or ReferenceData(),
                realtime_data=realtime,
                status=status,
                
                # 거래 정보
                buy_price=trade_info.get('buy_price'),
                buy_quantity=trade_info.get('buy_quantity'),
                buy_amount=trade_info.get('buy_amount'),
                target_price=trade_info.get('target_price'),
                stop_loss_price=trade_info.get('stop_loss_price'),
                buy_order_id=trade_info.get('buy_order_id'),
                sell_order_id=trade_info.get('sell_order_id'),
                
                # 시간 정보
                detected_time=trade_info.get('detected_time', now_kst()),
                order_time=trade_info.get('order_time'),
                execution_time=trade_info.get('execution_time'),
                sell_order_time=trade_info.get('sell_order_time'),
                sell_execution_time=trade_info.get('sell_execution_time'),
                
                # 매도 정보
                sell_price=trade_info.get('sell_price'),
                sell_reason=trade_info.get('sell_reason'),
                
                # 손익 정보
                unrealized_pnl=trade_info.get('unrealized_pnl'),
                unrealized_pnl_rate=trade_info.get('unrealized_pnl_rate'),
                realized_pnl=trade_info.get('realized_pnl'),
                realized_pnl_rate=trade_info.get('realized_pnl_rate'),
                
                # 기타
                position_size_ratio=trade_info.get('position_size_ratio', 0.0),
                max_holding_period=metadata.get('max_holding_period', 1),
                created_at=metadata.get('created_at', now_kst()),
                updated_at=trade_info.get('updated_at', now_kst())
            )
            
            return stock
            
        except Exception as e:
            logger.error(f"Stock 객체 생성 오류 {stock_code}: {e}")
            return None
    
    def get_all_selected_stocks(self) -> List[Stock]:
        """모든 선정된 종목 반환"""
        stocks = []
        with self._ref_lock:
            stock_codes = list(self.stock_metadata.keys())
        
        for stock_code in stock_codes:
            stock = self.get_selected_stock(stock_code)
            if stock:
                stocks.append(stock)
        
        return stocks
    
    def get_stocks_by_status(self, status: StockStatus) -> List[Stock]:
        """특정 상태의 종목들 반환"""
        stocks = []
        
        with self._status_lock:
            matching_codes = [code for code, s in self.trading_status.items() if s == status]
        
        for stock_code in matching_codes:
            stock = self.get_selected_stock(stock_code)
            if stock:
                stocks.append(stock)
        
        return stocks
    
    # === 실시간 업데이트 (성능 최적화) ===
    
    def update_stock_price(self, stock_code: str, current_price: float, 
                          today_volume: Optional[int] = None, 
                          price_change_rate: Optional[float] = None):
        """종목 가격 업데이트 (빠른 실시간 업데이트)"""
        try:
            # 실시간 데이터만 빠르게 업데이트
            with self._realtime_lock:
                if stock_code not in self.realtime_data:
                    return
                
                realtime = self.realtime_data[stock_code]
                realtime.current_price = current_price
                if today_volume is not None:
                    realtime.today_volume = today_volume
                if price_change_rate is not None:
                    realtime.price_change_rate = price_change_rate
                realtime.update_timestamp()
            
            # 미실현 손익 계산 (매수 완료 상태만)
            with self._status_lock:
                if (self.trading_status.get(stock_code) == StockStatus.BOUGHT and
                    stock_code in self.trade_info):
                    trade_info = self.trade_info[stock_code]
                    buy_price = trade_info.get('buy_price')
                    buy_quantity = trade_info.get('buy_quantity')
                    
                    if buy_price and buy_quantity:
                        pnl = (current_price - buy_price) * buy_quantity
                        pnl_rate = (current_price - buy_price) / buy_price * 100
                        trade_info['unrealized_pnl'] = pnl
                        trade_info['unrealized_pnl_rate'] = pnl_rate
                        trade_info['updated_at'] = now_kst()
            
            # 캐시 무효화
            self._invalidate_cache(stock_code)
            
        except Exception as e:
            logger.error(f"가격 업데이트 오류 {stock_code}: {e}")
    
    def change_stock_status(self, stock_code: str, new_status: StockStatus, 
                           reason: str = "", **trade_updates) -> bool:
        """종목 상태 변경"""
        try:
            with self._status_lock:
                if stock_code not in self.trading_status:
                    return False
                
                old_status = self.trading_status[stock_code]
                self.trading_status[stock_code] = new_status
                
                # 거래 정보 업데이트
                if stock_code in self.trade_info:
                    trade_info = self.trade_info[stock_code]
                    trade_info.update(trade_updates)
                    trade_info['updated_at'] = now_kst()
            
            # 캐시 무효화
            self._invalidate_cache(stock_code)
            
            logger.info(f"종목 상태 변경: {stock_code} {old_status.value} → {new_status.value}" + 
                       (f" (사유: {reason})" if reason else ""))
            return True
            
        except Exception as e:
            logger.error(f"상태 변경 오류 {stock_code}: {e}")
            return False
    
    def _invalidate_cache(self, stock_code: str):
        """특정 종목 캐시 무효화"""
        with self._cache_lock:
            self._stock_cache.pop(stock_code, None)
            self._cache_timestamps.pop(stock_code, None)
    
    # === 편의 메서드들 ===
    
    def get_buy_ready_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.BUY_READY)
    
    def get_sell_ready_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.SELL_READY)
    
    def get_watching_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.WATCHING)
    
    def get_bought_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.BOUGHT)
    
    def clear_all_stocks(self):
        """모든 선정 종목 초기화"""
        count = 0
        with self._ref_lock:
            count = len(self.stock_metadata)
            self.stock_metadata.clear()
            self.reference_stocks.clear()
        
        with self._realtime_lock:
            self.realtime_data.clear()
        
        with self._status_lock:
            self.trading_status.clear()
            self.trade_info.clear()
        
        with self._cache_lock:
            self._stock_cache.clear()
            self._cache_timestamps.clear()
        
        logger.info(f"모든 선정 종목 초기화: {count}개 종목 제거")
    
    def get_stock_summary(self) -> Dict:
        """종목 관리 요약 정보"""
        with self._status_lock:
            status_counts = {}
            for status in StockStatus:
                count = sum(1 for s in self.trading_status.values() if s == status)
                status_counts[status.value] = count
        
        with self._ref_lock:
            total_selected = len(self.stock_metadata)
        
        return {
            'total_selected': total_selected,
            'max_capacity': self.max_selected_stocks,
            'status_breakdown': status_counts,
            'utilization_rate': total_selected / self.max_selected_stocks * 100
        }
    
    # === 기존 호환성 메서드들 ===
    
    def get_all_positions(self) -> List[Stock]:
        return self.get_all_selected_stocks()
    
    def validate_stock_transitions(self) -> List[str]:
        """비정상적인 상태 전환 감지"""
        issues = []
        current_time = now_kst()
        
        with self._status_lock:
            for stock_code, status in self.trading_status.items():
                if status in [StockStatus.BUY_ORDERED, StockStatus.SELL_ORDERED]:
                    trade_info = self.trade_info.get(stock_code, {})
                    order_time = trade_info.get('order_time')
                    if order_time:
                        minutes_since_order = (current_time - order_time).total_seconds() / 60
                        if minutes_since_order > 3:
                            issues.append(f"{stock_code}: {status.value} 상태 3분 초과")
        
        return issues
    
    def auto_recover_stuck_orders(self) -> int:
        """정체된 주문들 자동 복구"""
        recovered = 0
        current_time = now_kst()
        
        with self._status_lock:
            for stock_code, status in self.trading_status.items():
                trade_info = self.trade_info.get(stock_code, {})
                
                if status == StockStatus.BUY_ORDERED:
                    order_time = trade_info.get('order_time')
                    if order_time and (current_time - order_time).total_seconds() / 60 > 3:
                        logger.warning(f"정체된 매수 주문 복구: {stock_code}")
                        self.change_stock_status(stock_code, StockStatus.BUY_READY, "3분 타임아웃")
                        recovered += 1
                
                elif status == StockStatus.SELL_ORDERED:
                    sell_order_time = trade_info.get('sell_order_time')
                    if sell_order_time and (current_time - sell_order_time).total_seconds() / 60 > 3:
                        logger.warning(f"정체된 매도 주문 복구: {stock_code}")
                        self.change_stock_status(stock_code, StockStatus.BOUGHT, "3분 타임아웃")
                        recovered += 1
        
        if recovered > 0:
            logger.info(f"총 {recovered}개 정체 주문 복구 완료")
        
        return recovered
    
    def __str__(self) -> str:
        with self._ref_lock:
            total = len(self.stock_metadata)
        bought = len(self.get_bought_stocks())
        return f"StockManager(선정종목: {total}/{self.max_selected_stocks}, 매수완료: {bought})"
    
    # === 웹소켓 실시간 데이터 처리 (최적화) ===
    
    def handle_realtime_price(self, data_type: str, stock_code: str, data: Dict):
        """실시간 가격 데이터 처리 (최적화된 버전)"""
        try:
            # 빠른 존재 확인 (락 없이)
            if stock_code not in self.realtime_data:
                return
            
            # 데이터 추출
            current_price = float(data.get('stck_prpr', 0))
            acc_volume = int(data.get('acml_vol', 0))
            
            if current_price <= 0:
                return
            
            # 실시간 데이터만 빠르게 업데이트
            self.update_stock_price(stock_code, current_price, acc_volume)
            
        except Exception as e:
            logger.error(f"실시간 가격 처리 오류 [{stock_code}]: {e}")
    
    def handle_realtime_orderbook(self, data_type: str, stock_code: str, data: Dict):
        """실시간 호가 데이터 처리"""
        try:
            if stock_code not in self.realtime_data:
                return
            
            # 호가 데이터 파싱
            bid_prices = []
            ask_prices = []
            bid_volumes = []
            ask_volumes = []
            
            for i in range(1, 6):
                bid_price = float(data.get(f'bidp{i}', 0))
                ask_price = float(data.get(f'askp{i}', 0))
                bid_volume = int(data.get(f'bidp_rsqn{i}', 0))
                ask_volume = int(data.get(f'askp_rsqn{i}', 0))
                
                bid_prices.append(bid_price)
                ask_prices.append(ask_price)
                bid_volumes.append(bid_volume)
                ask_volumes.append(ask_volume)
            
            # 빠른 호가 업데이트
            with self._realtime_lock:
                if stock_code in self.realtime_data:
                    realtime = self.realtime_data[stock_code]
                    realtime.bid_prices = bid_prices
                    realtime.ask_prices = ask_prices
                    realtime.bid_volumes = bid_volumes
                    realtime.ask_volumes = ask_volumes
                    realtime.bid_price = bid_prices[0] if bid_prices[0] > 0 else realtime.bid_price
                    realtime.ask_price = ask_prices[0] if ask_prices[0] > 0 else realtime.ask_price
                    realtime.update_timestamp()
            
            # 캐시 무효화
            self._invalidate_cache(stock_code)
            
        except Exception as e:
            logger.error(f"실시간 호가 처리 오류 [{stock_code}]: {e}")
    
    def handle_execution_notice(self, data_type: str, data: Dict):
        """체결 통보 처리 - 실제 종목 상태 업데이트"""
        try:
            # 체결통보 데이터는 'data' 키 안에 중첩되어 있을 수 있음
            actual_data = data.get('data', data)
            
            # 데이터가 문자열인 경우 파싱이 필요할 수 있음
            if isinstance(actual_data, str):
                logger.debug(f"체결통보 원본 데이터: {actual_data}")
                return
            
            stock_code = actual_data.get('mksc_shrn_iscd', '').strip()
            if not stock_code or stock_code not in self.trading_status:
                logger.debug(f"체결통보 - 관리 대상이 아닌 종목: {stock_code}")
                return
            
            exec_price = float(actual_data.get('exec_prce', 0))
            exec_qty = int(actual_data.get('exec_qty', 0))
            ord_type = actual_data.get('ord_gno_brno', '')
            sell_buy_dvsn = actual_data.get('sll_buy_dvsn_cd', '')  # 매도매수구분 (01:매도, 02:매수)
            
            if exec_price <= 0 or exec_qty <= 0:
                logger.warning(f"체결통보 - 잘못된 데이터: {stock_code} 가격:{exec_price} 수량:{exec_qty}")
                return
            
            current_status = self.trading_status.get(stock_code)
            logger.info(f"📢 체결 통보: {stock_code} {exec_qty}주 @{exec_price:,}원 "
                       f"구분:{sell_buy_dvsn} 현재상태:{current_status.value if current_status else 'None'}")
            
            # 🔥 실제 종목 상태 업데이트
            if sell_buy_dvsn == '02':  # 매수 체결
                self._handle_buy_execution(stock_code, exec_price, exec_qty, ord_type)
            elif sell_buy_dvsn == '01':  # 매도 체결
                self._handle_sell_execution(stock_code, exec_price, exec_qty, ord_type)
            else:
                logger.warning(f"알 수 없는 매도매수구분: {sell_buy_dvsn}")
            
        except Exception as e:
            logger.error(f"체결 통보 처리 오류: {e}")
            logger.debug(f"체결통보 데이터 구조: {data}")
    
    def _handle_buy_execution(self, stock_code: str, exec_price: float, exec_qty: int, ord_type: str):
        """매수 체결 처리"""
        try:
            current_status = self.trading_status.get(stock_code)
            
            if current_status != StockStatus.BUY_ORDERED:
                logger.warning(f"매수 체결이지만 주문 상태가 아님: {stock_code} 상태:{current_status.value if current_status else 'None'}")
                # 그래도 체결 처리 진행 (상태 불일치 복구)
            
            # 종목 상태를 BOUGHT로 변경하고 체결 정보 업데이트
            success = self.change_stock_status(
                stock_code=stock_code,
                new_status=StockStatus.BOUGHT,
                reason="buy_executed",
                buy_price=exec_price,
                buy_quantity=exec_qty,
                buy_amount=exec_price * exec_qty,
                execution_time=now_kst()
            )
            
            if success:
                # RealTimeMonitor 통계 업데이트 (있는 경우)
                if hasattr(self, '_realtime_monitor_ref'):
                    self._realtime_monitor_ref.buy_orders_executed += 1
                
                logger.info(f"✅ 매수 체결 완료: {stock_code} {exec_qty}주 @{exec_price:,}원")
            else:
                logger.error(f"❌ 매수 체결 상태 업데이트 실패: {stock_code}")
                
        except Exception as e:
            logger.error(f"매수 체결 처리 오류 {stock_code}: {e}")
    
    def _handle_sell_execution(self, stock_code: str, exec_price: float, exec_qty: int, ord_type: str):
        """매도 체결 처리"""
        try:
            current_status = self.trading_status.get(stock_code)
            
            if current_status != StockStatus.SELL_ORDERED:
                logger.warning(f"매도 체결이지만 주문 상태가 아님: {stock_code} 상태:{current_status.value if current_status else 'None'}")
                # 그래도 체결 처리 진행 (상태 불일치 복구)
            
            # 현재 매수 정보 조회 (손익 계산용)
            trade_info = self.trade_info.get(stock_code, {})
            buy_price = trade_info.get('buy_price', 0)
            buy_quantity = trade_info.get('buy_quantity', 0)
            
            # 손익 계산
            realized_pnl = 0
            realized_pnl_rate = 0
            if buy_price > 0 and buy_quantity > 0:
                realized_pnl = (exec_price - buy_price) * exec_qty
                realized_pnl_rate = (exec_price - buy_price) / buy_price * 100
            
            # 종목 상태를 SOLD로 변경하고 체결 정보 업데이트
            success = self.change_stock_status(
                stock_code=stock_code,
                new_status=StockStatus.SOLD,
                reason="sell_executed",
                sell_price=exec_price,
                sell_execution_time=now_kst(),
                realized_pnl=realized_pnl,
                realized_pnl_rate=realized_pnl_rate
            )
            
            if success:
                # RealTimeMonitor 통계 업데이트 (있는 경우)
                if hasattr(self, '_realtime_monitor_ref'):
                    self._realtime_monitor_ref.sell_orders_executed += 1
                
                logger.info(f"✅ 매도 체결 완료: {stock_code} {exec_qty}주 @{exec_price:,}원 "
                           f"손익: {realized_pnl:+,.0f}원 ({realized_pnl_rate:+.2f}%)")
            else:
                logger.error(f"❌ 매도 체결 상태 업데이트 실패: {stock_code}")
                
        except Exception as e:
            logger.error(f"매도 체결 처리 오류 {stock_code}: {e}")
    
    def set_realtime_monitor_ref(self, realtime_monitor):
        """RealTimeMonitor 참조 설정 (통계 업데이트용)"""
        self._realtime_monitor_ref = realtime_monitor
    
    def setup_websocket_callbacks(self, websocket_manager):
        """웹소켓 매니저에 콜백 등록"""
        if not websocket_manager:
            logger.warning("웹소켓 매니저가 없어 콜백 설정을 건너뜁니다")
            return
        
        # 실시간 가격 콜백 등록
        websocket_manager.register_callback('H0STCNT0', self.handle_realtime_price)
        
        # 실시간 호가 콜백 등록
        websocket_manager.register_callback('H0STASP0', self.handle_realtime_orderbook)
        
        # 체결 통보 콜백 등록
        websocket_manager.register_callback('H0STCNI0', self.handle_execution_notice)
        
        logger.info("✅ StockManager 웹소켓 콜백 등록 완료") 