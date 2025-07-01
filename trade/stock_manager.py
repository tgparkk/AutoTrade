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
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from datetime import datetime
from models.stock import Stock, StockStatus, ReferenceData, RealtimeData
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader
# 🆕 유동성 추적기
try:
    from websocket.liquidity_tracker import liquidity_tracker
except ImportError:
    liquidity_tracker = None  # 테스트 환경 대비

# 🆕 내부 모듈 import
from .stock_management._cache_manager import _StockCacheManager
from .stock_management._stock_builder import _StockObjectBuilder
from .stock_management._realtime_processor import _RealtimeProcessor
from .stock_management._execution_processor import _ExecutionProcessor
from .stock_management._stock_lifecycle_manager import _StockLifecycleManager

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
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        
        # 성능 설정 로드
        try:
            self.performance_config = self.config_loader.load_performance_config()
            cache_ttl = self.performance_config.get('cache_ttl_seconds', 2.0)
            cache_debug = self.performance_config.get('enable_cache_debug', False)
            logger.info(f"성능 설정 로드 완료: 캐시 TTL={cache_ttl}초, 디버그={cache_debug}")
        except Exception as e:
            logger.warning(f"성능 설정 로드 실패, 기본값 사용: {e}")
            cache_ttl = 2.0
            cache_debug = False
            self.performance_config = {}
        
        # === 4. 성능 최적화용 캐시 ===
        self._cache_manager = _StockCacheManager(
            cache_ttl_seconds=cache_ttl,
            enable_cache_debug=cache_debug
        )
        
        # === 5. 락 전략 (세분화 + 데드락 방지) ===
        # 🔥 락 순서 일관성 보장: ref → realtime → status 순서로 고정
        # 캐시 락은 _cache_manager 내부에서 관리됨
        self._ref_lock = threading.RLock()      # 1순위: 참조 데이터용
        self._realtime_lock = threading.RLock() # 2순위: 실시간 데이터용
        self._status_lock = threading.RLock()   # 3순위: 상태 변경용
        
        # 🆕 원자적 연산을 위한 추가 락
        self._stats_lock = threading.RLock()    # 통계 업데이트용
        
        # === 6. 기본 컴포넌트 준비 ===
        # 🆕 스레드 안전한 플래그들 (threading.Event 사용)
        self._shutdown_event = threading.Event()
        # TradeExecutor 참조 – RealTimeMonitor 에서 set_trade_executor_ref 로 주입
        from typing import Optional, TYPE_CHECKING
        if TYPE_CHECKING:
            from trade.trade_executor import TradeExecutor
        self._trade_executor: Optional["TradeExecutor"] = None
        
        # 🆕 메모리 가시성 보장을 위한 조건 변수
        self._data_updated = threading.Condition(self._realtime_lock)
        
        # === 7. Stock 객체 빌더 ===
        self._stock_builder = _StockObjectBuilder(
            stock_metadata=self.stock_metadata,
            reference_stocks=self.reference_stocks,
            realtime_data=self.realtime_data,
            trading_status=self.trading_status,
            trade_info=self.trade_info,
            ref_lock=self._ref_lock,
            realtime_lock=self._realtime_lock,
            status_lock=self._status_lock
        )
        
        # === 8. 실시간 데이터 처리기 ===
        self._realtime_processor = _RealtimeProcessor(
            realtime_data=self.realtime_data,
            trading_status=self.trading_status,
            trade_info=self.trade_info,
            reference_stocks=self.reference_stocks,
            realtime_lock=self._realtime_lock,
            status_lock=self._status_lock,
            data_updated=self._data_updated,
            cache_invalidator_func=self._cache_manager.invalidate_cache,
            strategy_config=self.strategy_config,
            trade_executor=None  # 나중에 설정
        )
        
        # === 9. 체결 통보 처리기 ===
        self._execution_processor = _ExecutionProcessor(
            trading_status=self.trading_status,
            trade_info=self.trade_info,
            stock_metadata=self.stock_metadata,
            status_lock=self._status_lock,
            cache_invalidator_func=self._cache_manager.invalidate_cache,
            status_changer_func=self.change_stock_status,
            database_getter_func=self._get_database,
            market_phase_getter_func=self._get_current_market_phase,
            realtime_monitor_ref=None,  # 나중에 설정
            websocket_manager_ref=None  # 나중에 설정
        )
        
        # === 6. 🔥 설정 파일 기반 기본 설정 (하드코딩 제거) ===
        self.candidate_stocks: List[str] = []
        # 종목 관리 설정은 performance_config에서 로드
        self.max_selected_stocks = self.performance_config.get('max_premarket_selected_stocks', 10)  # 장전 선정 종목 한도
        
        # === 10. 종목 라이프사이클 관리자 ===
        self._lifecycle_manager = _StockLifecycleManager(
            stock_metadata=self.stock_metadata,
            reference_stocks=self.reference_stocks,
            realtime_data=self.realtime_data,
            trading_status=self.trading_status,
            trade_info=self.trade_info,
            ref_lock=self._ref_lock,
            realtime_lock=self._realtime_lock,
            status_lock=self._status_lock,
            strategy_config=self.strategy_config,
            performance_config=self.performance_config,
            max_selected_stocks=self.max_selected_stocks,
            cache_invalidator_func=self._cache_manager.invalidate_cache,
            stock_getter_func=self.get_selected_stock
        )
        
        logger.info("StockManager 초기화 완료 (하이브리드 방식, 4단계 모듈화 적용: 캐시/빌더/실시간/체결처리 분리, 성능 최적화)")
    
    # === 종목 추가/제거 ===
    
    def add_selected_stock(self, stock_code: str, stock_name: str, 
                          open_price: float, high_price: float, 
                          low_price: float, close_price: float, 
                          volume: int, selection_score: float,
                          reference_data: Optional[dict] = None) -> bool:
        """선정된 종목 추가 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.add_selected_stock(
            stock_code, stock_name, open_price, high_price, 
            low_price, close_price, volume, selection_score, reference_data
        )
    
    def remove_selected_stock(self, stock_code: str) -> bool:
        """선정된 종목 제거 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.remove_selected_stock(stock_code)
    
    def add_intraday_stock(self, stock_code: str, stock_name: str, 
                          current_price: float, selection_score: float,
                          reasons: str = "", market_data: Optional[Dict] = None) -> bool:
        """장중 추가 종목 등록 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.add_intraday_stock(
            stock_code, stock_name, current_price, selection_score, reasons, market_data
        )
    
    def get_intraday_added_stocks(self) -> List[Stock]:
        """장중 추가된 종목들만 조회 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.get_intraday_added_stocks()
    
    def remove_intraday_stock(self, stock_code: str, reason: str = "manual_removal") -> bool:
        """장중 추가 종목 제거 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.remove_intraday_stock(stock_code, reason)
    
    def get_intraday_summary(self) -> Dict:
        """장중 추가 종목 요약 정보 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.get_intraday_summary()
    
    # === 빠른 조회 메서드들 (캐시 활용) ===
    
    def get_selected_stock(self, stock_code: str) -> Optional[Stock]:
        """선정된 종목 조회 (캐시 활용으로 빠른 조회)"""
        try:
            # 1. 캐시 확인
            cached_stock = self._cache_manager.get_cached_stock(stock_code)
            if cached_stock:
                return cached_stock
            
            # 2. 캐시 미스 - 새로 생성
            stock = self._build_stock_object(stock_code)
            
            # 3. 캐시 업데이트
            if stock:
                self._cache_manager.cache_stock(stock_code, stock)
            
            return stock
            
        except Exception as e:
            logger.error(f"종목 조회 오류 {stock_code}: {e}")
            return None
    
    def _build_stock_object(self, stock_code: str) -> Optional[Stock]:
        """Stock 객체 생성 (빌더에 위임)"""
        return self._stock_builder.build_stock_object(stock_code)
    
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
        """특정 상태의 종목들 반환 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.get_stocks_by_status(status)
    
    def get_stocks_by_status_batch(self, statuses: List[StockStatus]) -> Dict[StockStatus, List[Stock]]:
        """여러 상태의 종목들을 배치로 조회 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.get_stocks_by_status_batch(statuses)
    
    # === 실시간 업데이트 (성능 최적화) ===
    
    def update_stock_price(self, stock_code: str, current_price: float, 
                          today_volume: Optional[int] = None, 
                          price_change_rate: Optional[float] = None):
        """종목 가격 업데이트 (실시간 처리기에 위임)"""
        self._realtime_processor.update_stock_price(stock_code, current_price, today_volume, price_change_rate)
    
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
        """종목 상태 변경 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.change_stock_status(stock_code, new_status, reason, **trade_updates)
    
    def _invalidate_cache(self, stock_code: str):
        """특정 종목 캐시 무효화"""
        self._cache_manager.invalidate_cache(stock_code)
    
    # === 편의 메서드들 (LifecycleManager에 위임) ===
    
    def get_buy_ready_stocks(self) -> List[Stock]:
        return self._lifecycle_manager.get_buy_ready_stocks()
    
    def get_sell_ready_stocks(self) -> List[Stock]:
        return self._lifecycle_manager.get_sell_ready_stocks()
    
    def get_watching_stocks(self) -> List[Stock]:
        return self._lifecycle_manager.get_watching_stocks()
    
    def get_bought_stocks(self) -> List[Stock]:
        return self._lifecycle_manager.get_bought_stocks()
    
    def clear_all_stocks(self):
        """모든 선정 종목 초기화 (LifecycleManager에 위임)"""
        self._lifecycle_manager.clear_all_stocks()
        self._cache_manager.clear_all_cache()
    
    def get_stock_summary(self) -> Dict:
        """종목 관리 요약 정보 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.get_stock_summary()
    
    # === 기존 호환성 메서드들 ===
    
    def get_all_positions(self) -> List[Stock]:
        return self.get_all_selected_stocks()
    
    def get_all_stock_codes(self) -> List[str]:
        """현재 관리 중인 모든 종목 코드 반환 (LifecycleManager에 위임)"""
        return self._lifecycle_manager.get_all_stock_codes()
    
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
        """실시간 가격 데이터 처리 (실시간 처리기에 위임)"""
        self._realtime_processor.handle_realtime_price(data_type, stock_code, data)
    
    def handle_realtime_orderbook(self, data_type: str, stock_code: str, data: Dict):
        """실시간 호가 데이터 처리 (실시간 처리기에 위임)"""
        self._realtime_processor.handle_realtime_orderbook(data_type, stock_code, data)
    
    def handle_execution_notice(self, data_type: str, data: Dict):
        """체결 통보 처리 (ExecutionProcessor에 위임)"""
        self._execution_processor.handle_execution_notice(data_type, data)
    

    
    def set_realtime_monitor_ref(self, realtime_monitor):
        """RealTimeMonitor 참조 설정 (통계 업데이트용)"""
        self._realtime_monitor_ref = realtime_monitor
        # ExecutionProcessor에도 참조 전달
        self._execution_processor.set_realtime_monitor_ref(realtime_monitor)
    
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
        
        # 🆕 RealTimeMonitor에서 웹소켓 상태를 조회할 수 있도록 참조 저장
        # RealTimeMonitor._get_websocket_status_summary()는 self.stock_manager.websocket_manager를 참조하므로
        # 여기서 속성을 생성해두어야 "웹소켓: 미사용" 오표시를 방지할 수 있습니다.
        self.websocket_manager = websocket_manager
        
        # ExecutionProcessor에도 웹소켓 매니저 참조 전달
        self._execution_processor.set_websocket_manager_ref(websocket_manager)
        
        logger.info("✅ StockManager 웹소켓 콜백 등록 완료")
    
    # === 유동성 점수 조회 ===
    def get_liquidity_score(self, stock_code: str) -> float:
        """LiquidityTracker에서 0~10 점수 반환 (없으면 0)"""
        if liquidity_tracker is None:
            return 0.0
        try:
            return liquidity_tracker.get_score(stock_code)
        except Exception:
            return 0.0
    
    def set_trade_executor_ref(self, trade_executor):
        """TradeExecutor 참조 설정 (즉시 매도 용도)"""
        self._trade_executor = trade_executor
        # 실시간 처리기에도 참조 설정
        self._realtime_processor.set_trade_executor_ref(trade_executor)
        logger.info("✅ TradeExecutor 참조 설정 완료 (StockManager + RealtimeProcessor)")
    
 