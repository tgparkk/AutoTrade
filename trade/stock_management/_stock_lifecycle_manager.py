#!/usr/bin/env python3
"""
종목 라이프사이클 관리 전용 모듈

주요 기능:
- 장전/장중 종목 추가 및 제거
- 종목 상태 변경 및 관리  
- 상태별 종목 조회 및 배치 처리
- 종목 요약 정보 생성
- 전체 종목 초기화

성능 최적화:
- 스레드 안전한 상태 관리
- 락 순서 일관성 보장
- 배치 조회로 락 경합 최소화
"""

import threading
from typing import Dict, List, Optional, Callable, TYPE_CHECKING
from datetime import datetime
from models.stock import Stock, StockStatus, ReferenceData, RealtimeData
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from typing import Any

logger = setup_logger(__name__)


class _StockLifecycleManager:
    """종목 라이프사이클 관리 전용 클래스"""
    
    def __init__(self,
                 # 데이터 저장소들
                 stock_metadata: Dict[str, dict],
                 reference_stocks: Dict[str, ReferenceData],
                 realtime_data: Dict[str, RealtimeData],
                 trading_status: Dict[str, StockStatus],
                 trade_info: Dict[str, dict],
                 
                 # 락들
                 ref_lock: threading.RLock,
                 realtime_lock: threading.RLock,
                 status_lock: threading.RLock,
                 
                 # 설정들
                 strategy_config: dict,
                 performance_config: dict,
                 max_selected_stocks: int,
                 
                 # 콜백 함수들
                 cache_invalidator_func: Callable[[str], None],
                 stock_getter_func: Callable[[str], Optional[Stock]]):
        """StockLifecycleManager 초기화
        
        Args:
            stock_metadata: 종목 메타데이터 딕셔너리
            reference_stocks: 기준 데이터 딕셔너리
            realtime_data: 실시간 데이터 딕셔너리
            trading_status: 거래 상태 딕셔너리
            trade_info: 거래 정보 딕셔너리
            ref_lock: 참조 데이터용 락
            realtime_lock: 실시간 데이터용 락
            status_lock: 상태 변경용 락
            strategy_config: 거래 전략 설정
            performance_config: 성능 설정
            max_selected_stocks: 최대 선정 종목 수
            cache_invalidator_func: 캐시 무효화 함수
            stock_getter_func: Stock 객체 조회 함수
        """
        # 데이터 저장소
        self.stock_metadata = stock_metadata
        self.reference_stocks = reference_stocks
        self.realtime_data = realtime_data
        self.trading_status = trading_status
        self.trade_info = trade_info
        
        # 락
        self._ref_lock = ref_lock
        self._realtime_lock = realtime_lock
        self._status_lock = status_lock
        
        # 설정
        self.strategy_config = strategy_config
        self.performance_config = performance_config
        self.max_selected_stocks = max_selected_stocks
        
        # 콜백 함수
        self._cache_invalidator = cache_invalidator_func
        self._stock_getter = stock_getter_func
        
        logger.info("✅ StockLifecycleManager 초기화 완료")
    
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
                    'buy_order_orgno': None,
                    'buy_order_time': None,
                    'sell_order_id': None,
                    'sell_order_orgno': None,
                    'sell_order_time_api': None,
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
                    'ordered_qty': None,
                    'filled_qty': 0,
                    'remaining_qty': None,
                    'avg_exec_price': None,
                    'detected_time': now_kst(),
                    'updated_at': now_kst()
                }
            
            # 5. 캐시 무효화
            self._cache_invalidator(stock_code)
            
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
            self._cache_invalidator(stock_code)
            
            logger.info(f"선정 종목 제거: {stock_code}[{stock_name}]")
            return True
            
        except Exception as e:
            logger.error(f"종목 제거 오류 {stock_code}: {e}")
            return False
    
    def add_intraday_stock(self, stock_code: str, stock_name: str, 
                          current_price: float, selection_score: float,
                          reasons: str = "", market_data: Optional[Dict] = None) -> bool:
        """장중 추가 종목 등록 (기존 선정 종목과 동일하게 관리)
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            current_price: 현재가
            selection_score: 선정 점수
            reasons: 선정 사유
            market_data: 추가 시장 데이터 (옵션)
            
        Returns:
            추가 성공 여부
        """
        try:
            # 1. 중복 확인
            if stock_code in self.reference_stocks:
                logger.warning(f"이미 관리 중인 종목입니다: {stock_code}[{stock_name}] - 장중 추가 생략")
                return False
            
            # 2. 🔥 설정 기반 최대 종목 수 확인 (하드코딩 제거)
            max_intraday_stocks = self.performance_config.get('max_intraday_selected_stocks', 10)
            max_total_stocks = self.max_selected_stocks + max_intraday_stocks  # 장전 선정 + 장중 선정
            if len(self.reference_stocks) >= max_total_stocks:
                logger.warning(f"최대 관리 종목 수 초과: {len(self.reference_stocks)}/{max_total_stocks} - 장중 추가 제한")
                return False
            
            # 3. 시장 데이터 기본값 설정
            if not market_data:
                market_data = {}
            
            # 기본 OHLCV 데이터 (현재가 기준으로 추정)
            open_price = market_data.get('open_price', current_price)
            high_price = market_data.get('high_price', current_price)
            low_price = market_data.get('low_price', current_price)
            volume = market_data.get('volume', 0)
            
            # 4. 기본 메타데이터 저장 (장중 추가 표시)
            with self._ref_lock:
                self.stock_metadata[stock_code] = {
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'created_at': now_kst(),
                    'max_holding_period': self.strategy_config.get('max_holding_days', 1),
                    'is_intraday_added': True,  # 🆕 장중 추가 종목 표시
                    'intraday_reasons': reasons,  # 🆕 추가 사유
                    'intraday_score': selection_score  # 🆕 추가 당시 점수
                }
                
                # 5. 참조 데이터 생성 (장중 추가용)
                ref_data = ReferenceData(
                    pattern_score=selection_score,
                    yesterday_close=market_data.get('yesterday_close', current_price),
                    yesterday_volume=market_data.get('yesterday_volume', volume),
                    yesterday_high=market_data.get('yesterday_high', high_price),
                    yesterday_low=market_data.get('yesterday_low', low_price),
                    sma_20=market_data.get('sma_20', current_price),  # 기본값으로 현재가 사용
                    rsi=market_data.get('rsi', 50.0),
                    macd=market_data.get('macd', 0.0),
                    macd_signal=market_data.get('macd_signal', 0.0),
                    bb_upper=market_data.get('bb_upper', current_price * 1.02),
                    bb_middle=market_data.get('bb_middle', current_price),
                    bb_lower=market_data.get('bb_lower', current_price * 0.98),
                    avg_daily_volume=market_data.get('avg_daily_volume', volume),
                    avg_trading_value=market_data.get('avg_trading_value', volume * current_price),
                    market_cap=market_data.get('market_cap', 0),
                    price_change=market_data.get('price_change', 0),
                    price_change_rate=market_data.get('price_change_rate', 0)
                )
                
                self.reference_stocks[stock_code] = ref_data
            
            # 6. 실시간 데이터 초기화
            with self._realtime_lock:
                self.realtime_data[stock_code] = RealtimeData(
                    current_price=current_price,
                    today_volume=volume,
                    today_high=high_price,
                    today_low=low_price,
                    # 장중 추가 종목은 현재 시점부터 데이터 수집 시작
                    contract_strength=market_data.get('contract_strength', 100.0),
                    buy_ratio=market_data.get('buy_ratio', 50.0),
                    market_pressure=market_data.get('market_pressure', 'NEUTRAL'),
                    volume_spike_ratio=market_data.get('volume_spike_ratio', 1.0),
                    price_change_rate=market_data.get('price_change_rate', 0.0)
                )
            
            # 7. 거래 상태 초기화 (WATCHING 상태로 시작)
            with self._status_lock:
                self.trading_status[stock_code] = StockStatus.WATCHING
                self.trade_info[stock_code] = {
                    'buy_price': None,
                    'buy_quantity': None,
                    'buy_amount': None,
                    'target_price': None,
                    'stop_loss_price': None,
                    'buy_order_id': None,
                    'buy_order_orgno': None,
                    'buy_order_time': None,
                    'sell_order_id': None,
                    'sell_order_orgno': None,
                    'sell_order_time_api': None,
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
                    'ordered_qty': None,
                    'filled_qty': 0,
                    'remaining_qty': None,
                    'avg_exec_price': None,
                    'detected_time': now_kst(),
                    'updated_at': now_kst(),
                    'is_intraday_added': True  # 🆕 장중 추가 표시
                }
            
            # 8. 캐시 무효화
            self._cache_invalidator(stock_code)
            
            logger.info(f"🔥 장중 종목 추가: {stock_code}[{stock_name}] "
                       f"@{current_price:,}원 (점수:{selection_score:.1f}, 사유:{reasons})")
            
            return True
            
        except Exception as e:
            logger.error(f"장중 종목 추가 오류 {stock_code}: {e}")
            return False
    
    def get_intraday_added_stocks(self) -> List[Stock]:
        """장중 추가된 종목들만 조회
        
        Returns:
            장중 추가된 종목 리스트
        """
        intraday_stocks = []
        
        try:
            with self._ref_lock:
                intraday_codes = [
                    code for code, metadata in self.stock_metadata.items()
                    if metadata.get('is_intraday_added', False)
                ]
            
            for stock_code in intraday_codes:
                stock = self._stock_getter(stock_code)
                if stock:
                    intraday_stocks.append(stock)
            
            return intraday_stocks
            
        except Exception as e:
            logger.error(f"장중 추가 종목 조회 오류: {e}")
            return []
    
    def remove_intraday_stock(self, stock_code: str, reason: str = "manual_removal") -> bool:
        """장중 추가 종목 제거 (일반 제거와 동일하지만 로깅 구분)
        
        Args:
            stock_code: 종목코드
            reason: 제거 사유
            
        Returns:
            제거 성공 여부
        """
        try:
            # 장중 추가 종목인지 확인
            with self._ref_lock:
                if stock_code not in self.stock_metadata:
                    return False
                
                metadata = self.stock_metadata[stock_code]
                is_intraday = metadata.get('is_intraday_added', False)
                stock_name = metadata.get('stock_name', 'Unknown')
            
            # 일반 제거 로직 사용
            success = self.remove_selected_stock(stock_code)
            
            if success and is_intraday:
                logger.info(f"🗑️ 장중 추가 종목 제거: {stock_code}[{stock_name}] (사유: {reason})")
            
            return success
            
        except Exception as e:
            logger.error(f"장중 종목 제거 오류 {stock_code}: {e}")
            return False
    
    def get_intraday_summary(self) -> Dict:
        """장중 추가 종목 요약 정보
        
        Returns:
            장중 추가 종목 통계 딕셔너리
        """
        try:
            intraday_stocks = self.get_intraday_added_stocks()
            
            # 상태별 집계
            status_counts = {}
            total_score = 0
            reasons_count = {}
            
            for stock in intraday_stocks:
                # 상태별 집계
                status = stock.status.value
                status_counts[status] = status_counts.get(status, 0) + 1
                
                # 점수 합계
                total_score += stock.reference_data.pattern_score
                
                # 추가 사유별 집계
                with self._ref_lock:
                    metadata = self.stock_metadata.get(stock.stock_code, {})
                    reasons = metadata.get('intraday_reasons', 'unknown')
                    reasons_count[reasons] = reasons_count.get(reasons, 0) + 1
            
            return {
                'total_count': len(intraday_stocks),
                'status_counts': status_counts,
                'average_score': total_score / len(intraday_stocks) if intraday_stocks else 0,
                'reasons_distribution': reasons_count,
                'stock_codes': [stock.stock_code for stock in intraday_stocks]
            }
            
        except Exception as e:
            logger.error(f"장중 추가 종목 요약 오류: {e}")
            return {}
    
    def get_stocks_by_status(self, status: StockStatus) -> List[Stock]:
        """특정 상태의 종목들 반환 (락 최적화 버전)"""
        try:
            # 🔥 락 순서 일관성 보장: status → 배치 조회
            with self._status_lock:
                matching_codes = [code for code, s in self.trading_status.items() if s == status]
            
            # 빈 리스트면 조기 반환 (락 없이)
            if not matching_codes:
                return []
            
            # 🔥 배치 조회로 락 경합 최소화
            stocks = []
            for stock_code in matching_codes:
                stock = self._stock_getter(stock_code)
                if stock:
                    stocks.append(stock)
            
            return stocks
            
        except Exception as e:
            logger.error(f"상태별 종목 조회 오류 {status.value}: {e}")
            return []
    
    def get_stocks_by_status_batch(self, statuses: List[StockStatus]) -> Dict[StockStatus, List[Stock]]:
        """여러 상태의 종목들을 배치로 조회 (락 경합 최소화)
        
        Args:
            statuses: 조회할 상태 리스트
            
        Returns:
            상태별 종목 딕셔너리
        """
        result = {status: [] for status in statuses}
        
        try:
            # 🔥 한 번의 락으로 모든 상태 조회
            with self._status_lock:
                status_mapping = {}
                for code, stock_status in self.trading_status.items():
                    if stock_status in statuses:
                        if stock_status not in status_mapping:
                            status_mapping[stock_status] = []
                        status_mapping[stock_status].append(code)
            
            # 🔥 배치 조회로 락 경합 최소화
            for status, codes in status_mapping.items():
                for stock_code in codes:
                    stock = self._stock_getter(stock_code)
                    if stock:
                        result[status].append(stock)
            
            return result
            
        except Exception as e:
            logger.error(f"배치 상태별 종목 조회 오류: {e}")
            return result
    
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
            self._cache_invalidator(stock_code)
            
            logger.info(f"종목 상태 변경: {stock_code} {old_status.value} → {new_status.value}" + 
                       (f" (사유: {reason})" if reason else ""))
            return True
            
        except Exception as e:
            logger.error(f"상태 변경 오류 {stock_code}: {e}")
            return False
    
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
        
        # 캐시 전체 정리는 별도 메서드로 처리하지 않고 개별 무효화
        # self._cache_manager.clear_all_cache() 는 StockManager에서 직접 호출
        
        logger.info(f"모든 선정 종목 초기화: {count}개 종목 제거")
    
    def get_stock_summary(self) -> Dict:
        """종목 관리 요약 정보 (장중 추가 종목 포함)"""
        with self._status_lock:
            status_counts = {}
            for status in StockStatus:
                count = sum(1 for s in self.trading_status.values() if s == status)
                status_counts[status.value] = count
        
        with self._ref_lock:
            total_selected = len(self.stock_metadata)
            
            # 🆕 장중 추가 종목 집계
            premarket_count = 0
            intraday_count = 0
            intraday_reasons = {}
            
            for metadata in self.stock_metadata.values():
                if metadata.get('is_intraday_added', False):
                    intraday_count += 1
                    reason = metadata.get('intraday_reasons', 'unknown')
                    intraday_reasons[reason] = intraday_reasons.get(reason, 0) + 1
                else:
                    premarket_count += 1
        
        # 🆕 장중 추가 종목 요약 정보
        intraday_summary = self.get_intraday_summary()
        
        return {
            'total_selected': total_selected,
            'max_capacity': self.max_selected_stocks,
            'premarket_selected': premarket_count,
            'intraday_added': intraday_count,
            'status_breakdown': status_counts,
            'utilization_rate': total_selected / self.max_selected_stocks * 100,
            'intraday_details': {
                'count': intraday_count,
                'average_score': intraday_summary.get('average_score', 0),
                'reasons_distribution': intraday_reasons,
                'status_breakdown': intraday_summary.get('status_counts', {})
            }
        }
    
    # === 편의 메서드들 ===
    
    def get_buy_ready_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.BUY_READY)
    
    def get_sell_ready_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.SELL_READY)
    
    def get_watching_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.WATCHING)
    
    def get_bought_stocks(self) -> List[Stock]:
        return self.get_stocks_by_status(StockStatus.BOUGHT)
    
    def get_all_stock_codes(self) -> List[str]:
        """현재 관리 중인 모든 종목 코드 반환
        
        Returns:
            종목 코드 리스트
        """
        with self._ref_lock:
            return list(self.stock_metadata.keys())
