#!/usr/bin/env python3
"""
Stock 객체 빌더 모듈

StockManager의 Stock 객체 생성 로직을 분리한 내부 헬퍼 클래스
"""

import threading
from typing import Dict, Optional, TYPE_CHECKING
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from models.stock import Stock, StockStatus, ReferenceData, RealtimeData

logger = setup_logger(__name__)


class _StockObjectBuilder:
    """Stock 객체 생성 전담 클래스 (내부 전용)"""
    
    def __init__(self,
                 stock_metadata: Dict[str, dict],
                 reference_stocks: Dict[str, "ReferenceData"],
                 realtime_data: Dict[str, "RealtimeData"], 
                 trading_status: Dict[str, "StockStatus"],
                 trade_info: Dict[str, dict],
                 ref_lock: threading.RLock,
                 realtime_lock: threading.RLock,
                 status_lock: threading.RLock):
        """Stock 객체 빌더 초기화
        
        Args:
            stock_metadata: 종목 메타데이터 딕셔너리
            reference_stocks: 참조 데이터 딕셔너리
            realtime_data: 실시간 데이터 딕셔너리
            trading_status: 거래 상태 딕셔너리
            trade_info: 거래 정보 딕셔너리
            ref_lock: 참조 데이터용 락
            realtime_lock: 실시간 데이터용 락
            status_lock: 상태 데이터용 락
        """
        # 데이터 저장소 참조
        self._stock_metadata = stock_metadata
        self._reference_stocks = reference_stocks
        self._realtime_data = realtime_data
        self._trading_status = trading_status
        self._trade_info = trade_info
        
        # 락 참조
        self._ref_lock = ref_lock
        self._realtime_lock = realtime_lock
        self._status_lock = status_lock
        
        logger.info("StockObjectBuilder 초기화 완료")
    
    def build_stock_object(self, stock_code: str) -> Optional["Stock"]:
        """Stock 객체 생성 (각 데이터 소스에서 조합)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            생성된 Stock 객체 (실패 시 None)
        """
        try:
            # Import를 메서드 내부에서 수행 (순환 import 방지)
            from models.stock import Stock, StockStatus, ReferenceData, RealtimeData
            
            # 1. 기본 정보 확인
            with self._ref_lock:
                if stock_code not in self._stock_metadata:
                    return None
                metadata = self._stock_metadata[stock_code].copy()
                ref_data = self._reference_stocks.get(stock_code)
            
            # 2. 실시간 데이터 조회
            with self._realtime_lock:
                realtime = self._realtime_data.get(stock_code, RealtimeData())
            
            # 3. 거래 정보 조회
            with self._status_lock:
                status = self._trading_status.get(stock_code, StockStatus.WATCHING)
                trade_info = self._trade_info.get(stock_code, {})
            
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
    
    def build_multiple_stocks(self, stock_codes: list) -> Dict[str, Optional["Stock"]]:
        """여러 종목의 Stock 객체를 배치로 생성
        
        Args:
            stock_codes: 종목코드 리스트
            
        Returns:
            종목코드별 Stock 객체 딕셔너리
        """
        result = {}
        
        try:
            for stock_code in stock_codes:
                stock = self.build_stock_object(stock_code)
                result[stock_code] = stock
            
            return result
            
        except Exception as e:
            logger.error(f"복수 Stock 객체 생성 오류: {e}")
            return result
    
    def get_builder_stats(self) -> Dict:
        """빌더 통계 정보 반환
        
        Returns:
            빌더 통계 딕셔너리
        """
        try:
            with self._ref_lock:
                total_metadata = len(self._stock_metadata)
                total_reference = len(self._reference_stocks)
            
            with self._realtime_lock:
                total_realtime = len(self._realtime_data)
            
            with self._status_lock:
                total_status = len(self._trading_status)
                total_trade_info = len(self._trade_info)
            
            return {
                'total_metadata': total_metadata,
                'total_reference': total_reference,
                'total_realtime': total_realtime,
                'total_status': total_status,
                'total_trade_info': total_trade_info,
                'data_consistency': {
                    'metadata_vs_reference': total_metadata == total_reference,
                    'metadata_vs_realtime': total_metadata == total_realtime,
                    'metadata_vs_status': total_metadata == total_status,
                    'metadata_vs_trade_info': total_metadata == total_trade_info
                }
            }
            
        except Exception as e:
            logger.error(f"빌더 통계 조회 오류: {e}")
            return {}
    
    def __str__(self) -> str:
        with self._ref_lock:
            total_stocks = len(self._stock_metadata)
        return f"StockObjectBuilder(관리 종목: {total_stocks}개)" 