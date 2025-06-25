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

# 데이터베이스는 메서드 내부에서 import (경로 문제 해결)

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
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        
        # 성능 설정 로드
        try:
            self.performance_config = self.config_loader.load_performance_config()
            self._cache_ttl = self.performance_config.get('cache_ttl_seconds', 2.0)
            self._enable_cache_debug = self.performance_config.get('enable_cache_debug', False)
            logger.info(f"성능 설정 로드 완료: 캐시 TTL={self._cache_ttl}초, 디버그={self._enable_cache_debug}")
        except Exception as e:
            logger.warning(f"성능 설정 로드 실패, 기본값 사용: {e}")
            self._cache_ttl = 2.0
            self._enable_cache_debug = False
            self.performance_config = {}
        
        # === 5. 락 전략 (세분화 + 데드락 방지) ===
        # 🔥 락 순서 일관성 보장: ref → realtime → status → cache 순서로 고정
        self._ref_lock = threading.RLock()      # 1순위: 참조 데이터용
        self._realtime_lock = threading.RLock() # 2순위: 실시간 데이터용
        self._status_lock = threading.RLock()   # 3순위: 상태 변경용
        self._cache_lock = threading.RLock()    # 4순위: 캐시용
        
        # 🆕 원자적 연산을 위한 추가 락
        self._stats_lock = threading.RLock()    # 통계 업데이트용
        
        # 🆕 스레드 안전한 플래그들 (threading.Event 사용)
        self._shutdown_event = threading.Event()
        
        # 🆕 메모리 가시성 보장을 위한 조건 변수
        self._data_updated = threading.Condition(self._realtime_lock)
        
        # === 6. 🔥 설정 파일 기반 기본 설정 (하드코딩 제거) ===
        self.candidate_stocks: List[str] = []
        # 종목 관리 설정은 performance_config에서 로드
        self.max_selected_stocks = self.performance_config.get('max_premarket_selected_stocks', 10)  # 장전 선정 종목 한도
        
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
                    'detected_time': now_kst(),
                    'updated_at': now_kst(),
                    'is_intraday_added': True  # 🆕 장중 추가 표시
                }
            
            # 8. 캐시 무효화
            self._invalidate_cache(stock_code)
            
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
                stock = self.get_selected_stock(stock_code)
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
    
    # === 빠른 조회 메서드들 (캐시 활용) ===
    
    def get_selected_stock(self, stock_code: str) -> Optional[Stock]:
        """선정된 종목 조회 (캐시 활용으로 빠른 조회)"""
        try:
            # 1. 캐시 확인 (한국시간 기준)
            current_time = now_kst().timestamp()
            with self._cache_lock:
                if (stock_code in self._stock_cache and 
                    stock_code in self._cache_timestamps and
                    current_time - self._cache_timestamps[stock_code] < self._cache_ttl):
                    if self._enable_cache_debug:
                        logger.info(f"Stock 객체 캐시 사용: {stock_code} (TTL: {self._cache_ttl}초)")
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
                buy_order_orgno=trade_info.get('buy_order_orgno'),
                buy_order_time=trade_info.get('buy_order_time'),
                sell_order_id=trade_info.get('sell_order_id'),
                sell_order_orgno=trade_info.get('sell_order_orgno'),
                sell_order_time_api=trade_info.get('sell_order_time_api'),
                
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
                stock = self.get_selected_stock(stock_code)
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
                    stock = self.get_selected_stock(stock_code)
                    if stock:
                        result[status].append(stock)
            
            return result
            
        except Exception as e:
            logger.error(f"배치 상태별 종목 조회 오류: {e}")
            return result
    
    # === 실시간 업데이트 (성능 최적화) ===
    
    def update_stock_price(self, stock_code: str, current_price: float, 
                          today_volume: Optional[int] = None, 
                          price_change_rate: Optional[float] = None):
        """종목 가격 업데이트 (스레드 안전성 개선)"""
        try:
            # 🔥 락 순서 일관성 보장: realtime → status → cache 순서로 고정
            with self._realtime_lock:
                if stock_code not in self.realtime_data:
                    return
                
                realtime = self.realtime_data[stock_code]
                
                # 모든 업데이트를 원자적으로 수행
                old_price = realtime.current_price
                realtime.current_price = current_price
                if today_volume is not None:
                    realtime.today_volume = today_volume
                if price_change_rate is not None:
                    realtime.price_change_rate = price_change_rate
                realtime.update_timestamp()
                
                # 🆕 조건 변수로 데이터 업데이트 알림 (메모리 가시성 보장)
                with self._data_updated:
                    self._data_updated.notify_all()
                
                # 디버그 로그 (큰 가격 변동 감지)
                if old_price > 0:
                    price_change = abs((current_price - old_price) / old_price)
                    if price_change > 0.05:  # 5% 이상 변동
                        logger.info(f"⚡ 큰 가격 변동 감지: {stock_code} "
                                   f"{old_price:,}원 → {current_price:,}원 ({price_change:.1%})")
                
                # 🔥 미실현 손익 계산을 동일한 락 블록 내에서 처리 (원자성 보장)
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
                
                # 🔥 캐시 무효화를 락 내부에서 처리 (원자성 보장)
                with self._cache_lock:
                    self._stock_cache.pop(stock_code, None)
                    self._cache_timestamps.pop(stock_code, None)
            
        except Exception as e:
            logger.error(f"가격 업데이트 오류 {stock_code}: {e}")
    
    def get_stock_snapshot(self, stock_code: str) -> Optional[Dict]:
        """원자적 스냅샷 조회 (매매 전략용) - 데드락 방지 개선
        
        Returns:
            현재 시점의 일관된 데이터 스냅샷
        """
        try:
            # 🔥 락 순서 일관성 보장: ref → realtime → status 순서로 고정
            with self._ref_lock:
                if stock_code not in self.stock_metadata:
                    return None
                metadata = self.stock_metadata[stock_code].copy()
            
            with self._realtime_lock:
                if stock_code not in self.realtime_data:
                    return None
                realtime = self.realtime_data[stock_code]
                
                with self._status_lock:  # 중첩 락을 최소화하여 데드락 위험 감소
                    status = self.trading_status.get(stock_code, StockStatus.WATCHING)
                    trade_info = self.trade_info.get(stock_code, {})
                    
                    # 원자적 스냅샷 생성 (모든 락 보유 상태에서)
                    snapshot = {
                        'stock_code': stock_code,
                        'stock_name': metadata.get('stock_name', ''),
                        'current_price': realtime.current_price,
                        'today_volume': realtime.today_volume,
                        'price_change_rate': realtime.price_change_rate,
                        'bid_price': realtime.bid_price,
                        'ask_price': realtime.ask_price,
                        'status': status,
                        'buy_price': trade_info.get('buy_price'),
                        'buy_quantity': trade_info.get('buy_quantity'),
                        'unrealized_pnl': trade_info.get('unrealized_pnl'),
                        'unrealized_pnl_rate': trade_info.get('unrealized_pnl_rate'),
                        'snapshot_time': now_kst().timestamp(),
                        'last_updated': realtime.last_updated,
                        'contract_strength': realtime.contract_strength,
                        'buy_ratio': realtime.buy_ratio,
                        'market_pressure': realtime.market_pressure,
                        'trading_halt': realtime.trading_halt,
                        'vi_standard_price': realtime.vi_standard_price
                    }
                    
                    return snapshot
                
        except Exception as e:
            logger.error(f"스냅샷 조회 오류 {stock_code}: {e}")
            return None
    
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
    
    # === 기존 호환성 메서드들 ===
    
    def get_all_positions(self) -> List[Stock]:
        return self.get_all_selected_stocks()
    
    def get_all_stock_codes(self) -> List[str]:
        """현재 관리 중인 모든 종목 코드 반환
        
        Returns:
            종목 코드 리스트
        """
        with self._ref_lock:
            return list(self.stock_metadata.keys())
    
    # === 주문 복구 관련 메서드들 (OrderRecoveryManager로 이관됨) ===
    # 이 메서드들은 하위 호환성을 위해 유지하되, 실제 로직은 OrderRecoveryManager에 위임
    
    def validate_stock_transitions(self) -> List[str]:
        """비정상적인 상태 전환 감지 (OrderRecoveryManager에 위임)"""
        if hasattr(self, '_order_recovery_manager') and self._order_recovery_manager:
            return self._order_recovery_manager.validate_stock_transitions()
        else:
            logger.warning("OrderRecoveryManager가 설정되지 않음 - 빈 결과 반환")
            return []
    
    def auto_recover_stuck_orders(self) -> int:
        """정체된 주문들 자동 복구 (OrderRecoveryManager에 위임)"""
        if hasattr(self, '_order_recovery_manager') and self._order_recovery_manager:
            return self._order_recovery_manager.auto_recover_stuck_orders()
        else:
            logger.warning("OrderRecoveryManager가 설정되지 않음 - 복구 건너뜀")
            return 0
    
    def set_order_recovery_manager(self, order_recovery_manager):
        """OrderRecoveryManager 참조 설정"""
        self._order_recovery_manager = order_recovery_manager
        logger.info("✅ OrderRecoveryManager 참조 설정 완료")
    
    def __str__(self) -> str:
        with self._ref_lock:
            total = len(self.stock_metadata)
        bought = len(self.get_bought_stocks())
        return f"StockManager(선정종목: {total}/{self.max_selected_stocks}, 매수완료: {bought})"
    
    # === 웹소켓 실시간 데이터 처리 (최적화) ===
    
    def handle_realtime_price(self, data_type: str, stock_code: str, data: Dict):
        """실시간 가격 데이터 처리 (KIS 공식 문서 기반 고급 지표 포함) - 필드 매핑 개선"""
        try:
            # 빠른 존재 확인 (락 없이)
            if stock_code not in self.realtime_data:
                return
            
            # 🔥 KIS 공식 문서 기반 핵심 데이터 추출 (안전한 변환)
            current_price = float(data.get('current_price', 0))
            acc_volume = int(data.get('acc_volume', 0))
            
            # 기본 가격 정보
            open_price = float(data.get('open_price', 0))
            high_price = float(data.get('high_price', 0))
            low_price = float(data.get('low_price', 0))
            contract_volume = int(data.get('contract_volume', 0))
            
            # 🆕 KIS 공식 문서 기반 고급 지표들 (안전한 변환)
            contract_strength = float(data.get('contract_strength', 100.0))
            buy_ratio = float(data.get('buy_ratio', 50.0))
            market_pressure = data.get('market_pressure', 'NEUTRAL')
            vi_standard_price = float(data.get('vi_standard_price', 0))
            
            # 🔥 거래정지 필드 안전 처리 (다양한 형태 지원)
            trading_halt_raw = data.get('trading_halt', False)
            if isinstance(trading_halt_raw, str):
                trading_halt = trading_halt_raw.upper() in ['Y', 'TRUE', '1']
            elif isinstance(trading_halt_raw, bool):
                trading_halt = trading_halt_raw
            else:
                trading_halt = False
            
            # 전일 대비 정보
            change_sign = data.get('change_sign', '3')
            change_amount = float(data.get('change_amount', 0))
            change_rate = float(data.get('change_rate', 0.0))
            
            # 체결 정보
            weighted_avg_price = float(data.get('weighted_avg_price', 0))
            sell_contract_count = int(data.get('sell_contract_count', 0))
            buy_contract_count = int(data.get('buy_contract_count', 0))
            net_buy_contract_count = int(data.get('net_buy_contract_count', 0))
            
            # 호가 잔량 정보
            total_ask_qty = int(data.get('total_ask_qty', 0))
            total_bid_qty = int(data.get('total_bid_qty', 0))
            
            # 거래량 관련
            volume_turnover_rate = float(data.get('volume_turnover_rate', 0.0))
            prev_same_time_volume = int(data.get('prev_same_time_volume', 0))
            prev_same_time_volume_rate = float(data.get('prev_same_time_volume_rate', 0.0))
            
            # 시간 구분 정보
            hour_cls_code = data.get('hour_cls_code', '0')
            market_operation_code = data.get('market_operation_code', '20')
            
            if current_price <= 0:
                return
            
            # 🔥 실시간 데이터 전체 업데이트 (원자적 처리)
            with self._realtime_lock:
                if stock_code not in self.realtime_data:
                    return
                
                realtime = self.realtime_data[stock_code]
                old_price = realtime.current_price
                
                # 기본 가격 정보 업데이트
                realtime.current_price = current_price
                realtime.today_volume = acc_volume
                realtime.contract_volume = contract_volume
                if high_price > 0:
                    realtime.today_high = max(realtime.today_high, high_price)
                if low_price > 0:
                    realtime.today_low = min(realtime.today_low, low_price) if realtime.today_low > 0 else low_price
                
                # 🆕 KIS 공식 문서 기반 고급 지표 업데이트
                realtime.contract_strength = contract_strength
                realtime.buy_ratio = buy_ratio
                realtime.market_pressure = market_pressure
                realtime.vi_standard_price = vi_standard_price
                realtime.trading_halt = trading_halt
                
                # 전일 대비 정보 업데이트
                realtime.change_sign = change_sign
                realtime.change_amount = change_amount
                realtime.change_rate = change_rate
                
                # 체결 정보 업데이트
                realtime.weighted_avg_price = weighted_avg_price
                realtime.sell_contract_count = sell_contract_count
                realtime.buy_contract_count = buy_contract_count
                realtime.net_buy_contract_count = net_buy_contract_count
                
                # 호가 잔량 정보 업데이트
                realtime.total_ask_qty = total_ask_qty
                realtime.total_bid_qty = total_bid_qty
                
                # 거래량 관련 업데이트
                realtime.volume_turnover_rate = volume_turnover_rate
                realtime.prev_same_time_volume = prev_same_time_volume
                realtime.prev_same_time_volume_rate = prev_same_time_volume_rate
                
                # 시간 구분 정보 업데이트
                realtime.hour_cls_code = hour_cls_code
                realtime.market_operation_code = market_operation_code
                
                # 🆕 호가 정보 업데이트 (웹소켓 체결가 데이터에서 추출)
                ask_price1 = float(data.get('ask_price1', 0))
                bid_price1 = float(data.get('bid_price1', 0))
                if ask_price1 > 0:
                    realtime.ask_price = ask_price1
                if bid_price1 > 0:
                    realtime.bid_price = bid_price1
                
                # 계산 지표 업데이트
                if self.reference_stocks.get(stock_code):
                    ref_data = self.reference_stocks[stock_code]
                    if ref_data.yesterday_close > 0:
                        realtime.price_change_rate = (current_price - ref_data.yesterday_close) / ref_data.yesterday_close * 100
                    if ref_data.avg_daily_volume > 0:
                        realtime.volume_spike_ratio = acc_volume / ref_data.avg_daily_volume
                
                # 🔥 price_change_rate 백업 계산 (웹소켓 데이터 누락 시에만)
                if realtime.price_change_rate == 0 and self.reference_stocks.get(stock_code):
                    ref_data = self.reference_stocks[stock_code] 
                    if ref_data.yesterday_close > 0:
                        calculated_rate = (current_price - ref_data.yesterday_close) / ref_data.yesterday_close * 100
                        realtime.price_change_rate = calculated_rate
                        logger.debug(f"price_change_rate 백업 계산: {stock_code} = {calculated_rate:.2f}%")
                
                # 변동성 계산 (일중 고저 기준)
                if realtime.today_high > 0 and realtime.today_low > 0:
                    realtime.volatility = (realtime.today_high - realtime.today_low) / realtime.today_low * 100
                
                realtime.update_timestamp()
                
                # 디버그 로그 (큰 가격 변동 또는 특이 상황 감지)
                if old_price > 0:
                    price_change = abs((current_price - old_price) / old_price)
                    if price_change > 0.05:  # 5% 이상 변동
                        logger.info(f"⚡ 큰 가격 변동: {stock_code} "
                                   f"{old_price:,}원 → {current_price:,}원 ({price_change:.1%}) "
                                   f"체결강도:{contract_strength:.1f} 매수비율:{buy_ratio:.1f}%")
                
                # 특이 상황 로그
                if trading_halt:
                    logger.warning(f"🚨 거래정지: {stock_code}")
                if vi_standard_price > 0:
                    logger.warning(f"⚠️ VI 발동: {stock_code} 기준가:{vi_standard_price:,}원")
            
            # 미실현 손익 계산 (별도 락으로 분리하여 성능 최적화)
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
            
            # 캐시 무효화 (마지막에 수행)
            self._invalidate_cache(stock_code)
            
        except Exception as e:
            logger.error(f"실시간 가격 처리 오류 [{stock_code}]: {e}")
            logger.debug(f"처리 실패 데이터: {data}")
            # 🆕 데이터 구조 디버깅 정보 추가
            if data:
                logger.debug(f"데이터 키들: {list(data.keys())}")
                logger.debug(f"current_price 타입: {type(data.get('current_price'))}")
                logger.debug(f"trading_halt 타입: {type(data.get('trading_halt'))}")
    
    def handle_realtime_orderbook(self, data_type: str, stock_code: str, data: Dict):
        """실시간 호가 데이터 처리 (필드명 매핑 수정)"""
        try:
            if stock_code not in self.realtime_data:
                return
            
            # 🔥 웹소켓 파서 필드명과 매핑 (ask_price1, bid_price1 등)
            bid_prices = []
            ask_prices = []
            bid_volumes = []
            ask_volumes = []
            
            for i in range(1, 6):
                # 웹소켓 파서가 제공하는 실제 필드명 사용
                bid_price = float(data.get(f'bid_price{i}', 0))
                ask_price = float(data.get(f'ask_price{i}', 0))
                bid_volume = int(data.get(f'bid_qty{i}', 0))
                ask_volume = int(data.get(f'ask_qty{i}', 0))
                
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
                    
                    # 🆕 추가 호가 정보 업데이트 (웹소켓 파서 호환)
                    realtime.total_ask_qty = int(data.get('total_ask_qty', 0))
                    realtime.total_bid_qty = int(data.get('total_bid_qty', 0))
                    
                    realtime.update_timestamp()
            
            # 캐시 무효화
            self._invalidate_cache(stock_code)
            
        except Exception as e:
            logger.error(f"실시간 호가 처리 오류 [{stock_code}]: {e}")
            logger.debug(f"호가 데이터 구조: {data}")
    
    def handle_execution_notice(self, data_type: str, data: Dict):
        """체결 통보 처리 - KIS 공식 문서 기준 필드명 사용"""
        try:
            # 체결통보 데이터는 'data' 키 안에 중첩되어 있을 수 있음
            actual_data = data.get('data', data)
            
            # 데이터가 문자열인 경우 파싱이 필요할 수 있음
            if isinstance(actual_data, str):
                logger.debug(f"체결통보 원본 데이터: {actual_data}")
                
                # 🔥 KIS 공식 문서 기준 체결통보 파싱 (wikidocs 참조)
                # menulist = "고객ID|계좌번호|주문번호|원주문번호|매도매수구분|정정구분|주문종류|주문조건|주식단축종목코드|체결수량|체결단가|주식체결시간|거부여부|체결여부|접수여부|지점번호|주문수량|계좌명|체결종목명|신용구분|신용대출일자|체결종목명40|주문가격"
                parts = actual_data.split('^')
                if len(parts) >= 23:  # 최소 필드 수 확인
                    # KIS 공식 순서대로 파싱
                    customer_id = parts[0]           # 고객ID
                    account_no = parts[1]            # 계좌번호
                    order_no = parts[2]              # 주문번호
                    orig_order_no = parts[3]         # 원주문번호
                    sell_buy_dvsn = parts[4]         # 매도매수구분 (01:매도, 02:매수)
                    ord_dvsn = parts[5]              # 정정구분
                    ord_kind = parts[6]              # 주문종류
                    ord_cond = parts[7]              # 주문조건
                    stock_code = parts[8]            # 주식단축종목코드
                    exec_qty = int(parts[9]) if parts[9] else 0        # 체결수량
                    exec_price = float(parts[10]) if parts[10] else 0  # 체결단가
                    exec_time = parts[11]            # 주식체결시간
                    reject_yn = parts[12]            # 거부여부
                    exec_yn = parts[13]              # 체결여부
                    receipt_yn = parts[14]           # 접수여부
                    branch_no = parts[15]            # 지점번호
                    ord_qty = int(parts[16]) if parts[16] else 0       # 주문수량
                    account_name = parts[17]         # 계좌명
                    exec_stock_name = parts[18]      # 체결종목명
                    credit_dvsn = parts[19]          # 신용구분
                    credit_loan_date = parts[20]     # 신용대출일자
                    exec_stock_name_40 = parts[21]   # 체결종목명40
                    ord_price = float(parts[22]) if parts[22] else 0   # 주문가격
                    
                    # 파싱된 데이터로 체결통보 정보 구성
                    parsed_notice = {
                        'mksc_shrn_iscd': stock_code,        # 종목코드
                        'exec_prce': exec_price,             # 체결가격
                        'exec_qty': exec_qty,                # 체결수량
                        'sll_buy_dvsn_cd': sell_buy_dvsn,    # 매도매수구분
                        'ord_no': order_no,                  # 주문번호
                        'ord_gno_brno': branch_no,           # 주문채번지점번호
                        'exec_time': exec_time,              # 체결시간
                        'reject_yn': reject_yn,              # 거부여부
                        'exec_yn': exec_yn,                  # 체결여부
                        'receipt_yn': receipt_yn,            # 접수여부
                        'account_no': account_no,            # 계좌번호
                        'customer_id': customer_id,          # 고객ID
                        'ord_qty': ord_qty,                  # 주문수량
                        'ord_price': ord_price,              # 주문가격
                        'exec_stock_name': exec_stock_name,  # 종목명
                        'timestamp': now_kst()               # 처리시간
                    }
                    actual_data = parsed_notice
                else:
                    logger.warning(f"체결통보 필드 부족: {len(parts)}개 (최소 23개 필요)")
                    return
            
            # 기존 로직과 호환되도록 처리
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
            import traceback
            logger.debug(f"스택 트레이스: {traceback.format_exc()}")
    
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
                # 🔥 실제 체결 시점에 거래 기록 저장 (데이터베이스 클래스로 위임)
                try:
                    database = self._get_database()
                    metadata = self.stock_metadata.get(stock_code, {})
                    trade_info = self.trade_info.get(stock_code, {})
                    
                    db_id = database.save_buy_execution_to_db(
                        stock_code=stock_code,
                        exec_price=exec_price,
                        exec_qty=exec_qty,
                        stock_metadata=metadata,
                        trade_info=trade_info,
                        get_current_market_phase_func=self._get_current_market_phase
                    )
                    
                    if db_id <= 0:
                        logger.warning(f"⚠️ 매수 체결 DB 저장 실패: {stock_code}")
                        
                except Exception as db_e:
                    logger.error(f"❌ 매수 체결 DB 저장 오류 {stock_code}: {db_e}")
                
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
                # 🔥 실제 체결 시점에 거래 기록 저장 (데이터베이스 클래스로 위임)
                try:
                    database = self._get_database()
                    metadata = self.stock_metadata.get(stock_code, {})
                    trade_info = self.trade_info.get(stock_code, {})
                    
                    db_id = database.save_sell_execution_to_db(
                        stock_code=stock_code,
                        exec_price=exec_price,
                        exec_qty=exec_qty,
                        realized_pnl=realized_pnl,
                        realized_pnl_rate=realized_pnl_rate,
                        stock_metadata=metadata,
                        trade_info=trade_info,
                        get_current_market_phase_func=self._get_current_market_phase
                    )
                    
                    if db_id <= 0:
                        logger.warning(f"⚠️ 매도 체결 DB 저장 실패: {stock_code}")
                        
                except Exception as db_e:
                    logger.error(f"❌ 매도 체결 DB 저장 오류 {stock_code}: {db_e}")
                
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
    
    def _get_database(self):
        """데이터베이스 인스턴스 반환 (싱글톤 패턴)"""
        if not hasattr(self, '_database_instance'):
            import sys
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(current_dir)
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from database.trade_database import TradeDatabase
            self._database_instance = TradeDatabase()
        
        return self._database_instance
    

    def _get_current_market_phase(self) -> str:
        """현재 시장 단계 반환"""
        from datetime import time as dt_time
        
        current_time = now_kst().time()
        
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
    
 